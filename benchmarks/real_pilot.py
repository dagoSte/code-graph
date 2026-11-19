"""Opt-in paired historical SymPy pilot; preparation and dry runs start no models."""
import argparse
import ast
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

# Load by path: the CLI and tests also run with Python -I.
import importlib.util
REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('real_pilot_shared', REPO / 'benchmarks/live_agent.py')
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)
DATA = REPO / 'benchmarks/real-cases'
CALL_SECONDS = 600
TEST_SECONDS = 120


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cases():
    data = json.loads((DATA / 'cases.json').read_text())
    for task in data['cases']:
        if digest(DATA / task['hidden_patch']) != task['hidden_patch_sha256']:
            raise ValueError('Historical test patch hash mismatch')
        if hashlib.sha256(task['issue_body'].encode()).hexdigest() != task['issue_body_sha256']:
            raise ValueError('Original issue hash mismatch')
    return [dict(task, source_paths=data['source_paths']) for task in data['cases']]


def public_argv(python, task):
    return [str(python), '-I', str(REPO / 'benchmarks/real_pytest.py'), '--ids', json.dumps(task['smoke_ids'])]


def test_environment(python):
    code = ('import sys,json,importlib.metadata as m;'
            'print(json.dumps(dict(python=sys.version.split()[0],packages={n:m.version(n) '
            'for n in ("pytest","mpmath","hypothesis")})))')
    run = subprocess.run([str(python), '-I', '-c', code], capture_output=True, text=True, check=True, timeout=10)
    return json.loads(run.stdout)


def test_run(root, python, ids):
    argv = [str(python), '-I', str(REPO / 'benchmarks/real_pytest.py'), '--ids', json.dumps(ids)]
    try:
        run = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=TEST_SECONDS)
        lines = [line for line in run.stdout.splitlines() if line.startswith('CG_REAL_TEST=')]
        result = json.loads(lines[-1].split('=', 1)[1]) if lines else dict(error_code='missing_test_receipt')
        result['exit_code'] = run.returncode
        return result
    except subprocess.TimeoutExpired:
        return dict(error_code='test_timeout', timed_out=True, passed=0)
    except (OSError, ValueError):
        return dict(error_code='test_start_or_receipt', passed=0)


def green(result):
    return (result.get('exit_code') == 0 and result.get('executed', 0) > 0
            and result.get('passed') == result['executed']
            and result.get('failed') == result.get('errors') == result.get('skipped') == 0)


def archive(cache, commit, destination, *, allow_symlinks=False):
    """Export only this tree; no history/future fix objects reach the agent."""
    env = dict(os.environ, GIT_NO_LAZY_FETCH='1')
    data = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'protocol.allow=never', '-C', str(cache), 'archive', commit],
                          capture_output=True, check=True, env=env).stdout
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as bundle:
        for member in bundle:
            parts = PurePosixPath(member.name).parts
            if not parts or member.name.startswith('/') or '..' in parts or parts[0] == '.git':
                raise ValueError('Unsafe archive path')
            target = destination.joinpath(*parts)
            if any(destination.joinpath(*parts[:i]).is_symlink() for i in range(1, len(parts))):
                raise ValueError('Archive entry has a symlink ancestor')
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.extractfile(member).read())
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
            elif member.issym() and allow_symlinks:
                if (PurePosixPath(member.linkname).is_absolute() or
                        not (target.parent / member.linkname).resolve().is_relative_to(destination.resolve())):
                    raise ValueError('Archive symlink escapes the exported tree')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(member.linkname)
            else:
                raise ValueError('Only regular files/directories are supported by this pilot')


def seed(cache, destination, task, *, exporter=None):
    (exporter or archive)(cache, task['parent'], destination)
    shared.git(destination, 'init', '-b', 'main')
    for key, value in [('user.name', 'code-graph real pilot'), ('user.email', 'pilot@example.invalid'),
                       ('core.autocrlf', 'false')]:
        shared.git(destination, 'config', key, value)
    shared.git(destination, 'add', '-f', '--all', '.')
    tree = shared.git(destination, 'write-tree').stdout.strip()
    if tree != task['parent_tree']:
        raise ValueError('Exported tree differs from historical parent')
    shared.git(destination, 'commit', '-m', 'Frozen historical parent tree (no future history)')
    (destination / '.git/info/exclude').write_text('.code-graph/\n__pycache__/\n.hypothesis/\n.pytest_cache/\n')


def frozen_tests(candidate, baseline, task, python, hidden=False):
    """Replace test modules only in an external host copy, never the agent tree."""
    with tempfile.TemporaryDirectory(prefix='cg-real-score-') as folder:
        root = Path(folder) / 'repo'
        shutil.copytree(candidate, root, ignore=shutil.ignore_patterns('.git', '.code-graph', '__pycache__', '.hypothesis', '.pytest_cache'))
        for name in task['test_files']:
            (root / name).write_bytes((baseline / name).read_bytes())
        if hidden:
            shared.git(root, 'apply', '--check', str(DATA / task['hidden_patch']))
            shared.git(root, 'apply', str(DATA / task['hidden_patch']))
        return test_run(root, python, task['hidden_ids'] if hidden else task['smoke_ids'])


def qualify(cache, task, python):
    with tempfile.TemporaryDirectory(prefix='cg-real-qualify-') as folder:
        parent, fix = Path(folder) / 'parent', Path(folder) / 'fix'
        archive(cache, task['parent'], parent)
        archive(cache, task['fix'], fix)
        result = dict(parent_public=test_run(parent, python, task['smoke_ids']),
                      parent_hidden=frozen_tests(parent, parent, task, python, hidden=True),
                      fix_public=test_run(fix, python, task['smoke_ids']),
                      fix_hidden=test_run(fix, python, task['hidden_ids']))
        red = result['parent_hidden']
        result['qualified'] = (all(green(result[key]) for key in ('parent_public', 'fix_public', 'fix_hidden'))
                               and red.get('failed', 0) > 0 and red.get('errors') == red.get('skipped') == 0
                               and red.get('executed') == result['fix_hidden'].get('executed'))
        return result


def statement_counts(function):
    """Separate compound headers from nested statements, retaining multiplicities."""
    children = {'body', 'orelse', 'finalbody', 'handlers', 'cases'}
    headers = (ast.stmt, ast.ExceptHandler, ast.match_case)
    counts = Counter()
    for node in ast.walk(function):
        if not isinstance(node, headers):
            continue
        header = ast.dump(type(node)(**{
            key: [] if key in children and isinstance(value, list) else value
            for key, value in ast.iter_fields(node)}))
        counts[header] += 1
        # Bare else/finally clauses have no separate AST node.
        for clause in ('orelse', 'finalbody'):
            if getattr(node, clause, None):
                counts[(clause, header)] += 1
    return counts


def retained(candidate, baseline, changed):
    """Preserve the statement multiset and block/function headers; allow additions."""

    missing_tests = []
    source = {}
    for name in changed:
        if not name.endswith('.py') or not (baseline / name).is_file():
            continue
        old = (baseline / name).read_text()
        if '/tests/' not in name:
            source[name] = old
            continue
        try:
            def functions(text):
                return {n.name: n for n in ast.parse(text).body
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith('test')}
            before = functions(old)
            after = functions((candidate / name).read_text())
            for key, node in before.items():
                actual = after.get(key)
                if actual is None or statement_counts(node) - statement_counts(actual):
                    missing_tests.append(name + '::' + key)
        except (OSError, SyntaxError):
            missing_tests.append(name)
    return dict(tests_preserved=not missing_tests, weakened_test_ids=missing_tests,
                top_level_api=shared.api_retention(candidate, dict(sources=source)))


class RealRunner(shared.PilotRunner):
    def __init__(self, *args, test_argv, **kwargs):
        super().__init__(*args, **kwargs)
        self.test_argv, self.phases, self.phase = test_argv, [], None

    def argv(self, run, folder):
        argv = self.original_argv(run, folder)
        command = sys.executable
        arguments = ['-I', str(REPO / 'benchmarks/real_pilot_mcp.py'), '--skill-root', str(self.skill),
                     '--root', run._root, '--arm', self.arm]
        if run.client == 'codex':
            for index, value in enumerate(argv):
                if value.startswith('mcp_servers.code-graph.command='):
                    argv[index] = 'mcp_servers.code-graph.command=' + json.dumps(command)
                elif value.startswith('mcp_servers.code-graph.args='):
                    argv[index] = 'mcp_servers.code-graph.args=' + json.dumps(arguments)
        else:
            path = folder / 'mcp.json'
            config = json.loads(path.read_text())
            config['mcpServers']['code-graph'].update(command=command, args=arguments)
            path.write_text(json.dumps(config))
        return argv

    def _start(self, client, kwargs):
        prompt = kwargs['prompt'].removeprefix(self.flow_module._RETRIEVAL_GUIDANCE + '\n\n')
        if self.phase in ('edit', 'review') and '\n{' in prompt:
            header, payload = prompt.split('\n{', 1)
            prompt = header + '\n' + json.dumps(shared.strip_host_graph(json.loads('{' + payload)))
        if self.arm == 'source_only':
            prompt = 'Use text_search to search literal text and cg_read to read source. No graph tools are available.\n\n' + prompt.replace('Use code-graph MCP for evidence.', 'Use text_search and cg_read for source evidence.')
            prompt = prompt.replace('cg_read/cg_source', 'cg_read')
        else:
            prompt = self.flow_module._RETRIEVAL_GUIDANCE + '\n\n' + prompt
        prompt += '\nBoth evidence arms provide text_search: bounded literal text search of tracked files, with optional path_glob.'
        label = {'python': 'Python', 'typescript': 'TypeScript', 'go': 'Go'}[self.task.get('language', 'python')]
        if self.task.get('language') == 'go' and any(name.endswith('.y') for name in self.task['source_paths']):
            label += ' and tracked .y grammar'
        if not self.task.get('inventory_at_plan_only') or self.phase not in ('edit', 'review'):
            prompt += '\nExisting ' + label + ' file inventory: ' + json.dumps(self.task['source_paths'])
        prompt += '\nOnly existing ' + label + ' files from this inventory may change. Preserve existing tests and public APIs. Never run commands.'
        prompt += '\nApproved public test argv (host only): ' + json.dumps(self.test_argv)
        kwargs.update(prompt=prompt, timeout=min(kwargs['timeout'], CALL_SECONDS))
        try:
            run = self.agents.start(client, **kwargs)
        except Exception as error:
            self.start_failures.append(type(error).__name__)
            raise
        self.runs.append(run)
        self.phases.append(self.phase)
        return run


def call_metadata(run, phase, allowed):
    result = dict(phase=phase, client=run.client, mode=run.mode, status=run.status,
                  usage=run.usage, **shared.native_metadata(run))
    reads = set()
    for event in run.events():
        tool = event.get('data', {})
        if event.get('kind') != 'tool' or tool.get('status') != 'ok':
            continue
        name, args = tool.get('name', ''), tool.get('input', {})
        if not isinstance(args, dict) or not (name.endswith(('cg_read', 'cg_source')) or name == 'Read'):
            continue
        path = args.get('path', args.get('file_path'))
        if not isinstance(path, str):
            continue
        if path.startswith('/'):
            try:
                path = str(Path(path).resolve().relative_to(Path(run.cwd).resolve()))
            except ValueError:
                continue
        else:
            path = str(PurePosixPath(path))
        if path in allowed:
            reads.add(path)
    result['explicit_source_files_read'] = sorted(reads)
    mentions = set()

    def files(value):
        if isinstance(value, str):
            try:
                files(json.loads(value))
            except ValueError:
                pass
        elif isinstance(value, list):
            for item in value:
                files(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key in ('file', 'path') and isinstance(item, str) and item in allowed:
                    mentions.add(item)
                else:
                    files(item)

    folder = shared.log_folder(run)
    if folder is not None and (folder / 'raw.jsonl').is_file():
        for line in (folder / 'raw.jsonl').read_text(errors='replace').splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            item = event.get('item', {})
            if (event.get('type') == 'item.completed' and isinstance(item, dict)
                    and item.get('type') == 'mcp_tool_call' and not item.get('error') and item.get('status') != 'failed'):
                files(item.get('result'))
            elif event.get('type') == 'user':
                for block in event.get('message', {}).get('content', []):
                    if isinstance(block, dict) and block.get('type') == 'tool_result' and not block.get('is_error'):
                        files(block.get('content'))
    result['tool_returned_file_mentions'] = sorted(mentions)
    result['seconds'] = next((event.get('data', {}).get('duration_s') for event in reversed(run.events()) if event.get('kind') == 'end'), None)
    result['command_events'] = sum(event.get('kind') == 'command' for event in run.events())
    return result


def run_case(skill, cache, directory, task, arm, counter, python, *, hooks=None):
    hooks = hooks or dict(public_argv=public_argv, frozen_tests=frozen_tests, retained=retained)
    setup_started = time.monotonic()
    hooks.get('seed', seed)(cache, directory, task)
    baseline_tests = directory.parent / (directory.name + '-frozen-tests')
    for name in task['test_files']:
        if not (directory / name).is_file():
            continue
        path = baseline_tests / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((directory / name).read_bytes())
    engine = shared.load(skill / 'scripts/cg.py', 'real_pilot_engine')
    flow_module = shared.load(skill / 'scripts/change_flow.py', 'real_pilot_flow')
    agents = shared.load(skill / 'scripts/agents.py', 'real_pilot_agents')
    flow_module.AGENT_TIMEOUT = CALL_SECONDS
    if 'configure_flow' in hooks:
        hooks['configure_flow'](flow_module, engine)
    argv = hooks['public_argv'](python, task)
    runner = hooks.get('runner', RealRunner)(agents, flow_module, skill, task, arm, counter, test_argv=argv)
    flow = flow_module.ChangeFlow(engine, directory, runner=runner)
    row = dict(task=task['id'], arm=arm, historical_parent=task['parent'], parent_tree=task['parent_tree'],
               setup_seconds=round(time.monotonic() - setup_started, 3), stages=[], calls=[], outcome='incomplete')
    started, record = time.monotonic(), None
    allowed = set(task['source_paths'])
    readable = set(shared.git(directory, 'ls-files').stdout.splitlines())
    try:
        record = flow.create(task['issue_title'] + '\n\n' + task['issue_body'], 'codex', 'claude', budget_usd=shared.REVIEW_BUDGET_USD)
        cid = record['id']
        for name, expected in (('plan', 'planned'), ('edit', 'edited'), ('verify', 'verified'), ('review', 'reviewed')):
            runner.phase = name
            if name == 'edit':
                if any(item['path'] not in allowed for field in ('touch', 'tests') for item in record['plan']['agent'][field]):
                    row['outcome'] = 'plan_outside_package_scope'
                    break
                flow.approve(cid, argv)
            step = time.monotonic()
            getattr(flow, name)(cid)
            record = flow.wait(cid, timeout=hooks.get('step_seconds', CALL_SECONDS + 60))
            row['stages'].append(dict(stage=name, state=record['state'], seconds=round(time.monotonic() - step, 3),
                                      error_code=(record.get('error') or {}).get('code')))
            if record['state'] != expected:
                row['outcome'] = name + '_failed'
                break
            if any(event.get('kind') == 'command' for run in runner.runs for event in run.events()):
                row['outcome'] = 'agent_command_violation'
                break
        else:
            candidate = Path(record['worktree'])
            row['changed_files'] = [item['path'] for item in flow._files(record)]
            row['candidate_scope_ok'] = set(row['changed_files']) <= allowed
            row['candidate_public'] = hooks['frozen_tests'](candidate, baseline_tests, task, python)
            row['candidate_hidden'] = hooks['frozen_tests'](candidate, baseline_tests, task, python, hidden=True)
            row['retention'] = hooks['retained'](candidate, directory, row['changed_files'])
            row['verification_ok'], row['review_verdict'] = record['verify']['ok'], record['review']['verdict']
            api = row['retention'].get('top_level_api')
            api_ok = api.get('passed') is True if isinstance(api, dict) else hooks.get('check_api', True) is False
            if (row['verification_ok'] and row['review_verdict'] == 'accept' and row['candidate_scope_ok']
                    and green(row['candidate_public']) and green(row['candidate_hidden'])
                    and row['retention']['tests_preserved'] and api_ok):
                flow.integrate(cid, mode='merge')
                row['integrated_hidden'] = hooks['frozen_tests'](directory, baseline_tests, task, python, hidden=True)
                row['outcome'] = 'success' if green(row['integrated_hidden']) else 'integration_failed'
            else:
                row['outcome'] = 'verification_review_or_hidden_failed'
    except Exception as error:
        row['outcome'] = 'harness_failed'
        row['error_code'] = type(error).__name__
    finally:
        try:
            row['cleanup_drained'] = shared.drain(flow, record, runner)
        except Exception:
            row['cleanup_drained'] = False
        if record is not None:
            final = flow.get(record['id'])
            row['final_state'] = final['state']
            row['workflow_tests'] = shared.test_summary(final.get('tests'))
        row['seconds'] = round(time.monotonic() - started, 3)
        row['calls'] = [call_metadata(run, phase, readable) for run, phase in zip(runner.runs, runner.phases)]
        row['access_failure'] = shared.access_failure(runner)
        row['agent_command_violation'] = any(call['command_events'] for call in row['calls'])
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-cache', type=Path, required=True, help='prewarmed local SymPy Git cache; no runtime download')
    parser.add_argument('--test-python', type=Path, required=True, help='isolated pilot venv, not the product venv')
    parser.add_argument('--skill-root', type=Path, default=REPO)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--live', action='store_true', help='requires a separately accepted Phase A and owner announcement')
    args = parser.parse_args()
    args.test_python = args.test_python.absolute()
    args.repo_cache = args.repo_cache.resolve()
    args.skill_root = args.skill_root.resolve()
    tasks = cases()
    result = dict(format='codegraph-real-pilot/1', live=args.live, live_starts=0, cases=[],
                  cases_sha256=digest(DATA / 'cases.json'), selection_sha256=digest(DATA / 'selection-rule.json'),
                  call_seconds=CALL_SECONDS, max_calls=shared.MAX_CALLS, test_seconds=TEST_SECONDS,
                  claude_review_budget_usd=shared.REVIEW_BUDGET_USD, claude_total_budget_usd=shared.TOTAL_REVIEW_BUDGET_USD,
                  test_environment=test_environment(args.test_python),
                  engine_files={p.name: digest(p) for p in sorted((args.skill_root / 'scripts').glob('*.py'))},
                  harness_files={p.name: digest(p) for p in [Path(__file__), REPO / 'benchmarks/real_pytest.py',
                                                         REPO / 'benchmarks/real_pilot_mcp.py', REPO / 'benchmarks/live_agent.py']})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.live:
        if args.work_dir is None or args.work_dir.exists():
            parser.error('--live requires a new --work-dir')
        args.work_dir.mkdir(parents=True)
        result['clients'] = shared.client_metadata()
        counter, spent, stop = [0], 0.0, False
        for index, task in enumerate(tasks):
            for arm in (('source_only', 'graph_assisted') if index % 2 == 0 else ('graph_assisted', 'source_only')):
                if not stop and spent + shared.REVIEW_BUDGET_USD > shared.TOTAL_REVIEW_BUDGET_USD:
                    stop, result['stop_reason'] = True, 'claude_total_budget'
                row = dict(task=task['id'], arm=arm, outcome='not_started_after_stop_rule', calls=[])
                if not stop:
                    try:
                        row = run_case(args.skill_root, args.repo_cache, args.work_dir / (task['id'] + '-' + arm), task, arm, counter, args.test_python)
                    except Exception as error:
                        row.update(outcome='harness_failed', error_code=type(error).__name__,
                                   cleanup_drained=False, calls_receipt_complete=False)
                spent += shared.reported_review_cost(row)
                stop |= bool(row.get('access_failure')) or row.get('cleanup_drained') is False or bool(row.get('agent_command_violation'))
                result.update(live_starts=counter[0], claude_reported_cost_usd=round(spent, 6))
                result['cases'].append(row)
                args.output.write_text(json.dumps(result, indent=2) + '\n')
    else:
        for task in tasks:
            try:
                proof = qualify(args.repo_cache, task, args.test_python)
            except Exception as error:
                proof = dict(qualified=False, error_code=type(error).__name__)
            result['cases'].append(dict(task=task['id'], **proof))
            args.output.write_text(json.dumps(result, indent=2) + '\n')
        if not all(row['qualified'] for row in result['cases']):
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
