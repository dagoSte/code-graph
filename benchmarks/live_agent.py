"""Explicit opt-in paired pilot using real installed clients and disposable fixtures."""
import argparse
import ast
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

REPO = Path(__file__).resolve().parents[1]
MAX_CALLS = 24
CALL_SECONDS = 180
REVIEW_BUDGET_USD = 0.6
TOTAL_REVIEW_BUDGET_USD = 5
DRAIN_SECONDS = 30
TASKS = (
    dict(id='refund', intent='Correct the refund eligibility window: day30 is included, negative ages and ages above30 are excluded; only paid or settled payments are eligible. Preserve the existing API and other behavior. Add a regression test.',
         module='domain.refunds', function='refund_allowed', expression="[(refund_allowed(d,s)) for d,s in [(30,'paid'),(30,'settled'),(-1,'paid'),(31,'paid'),(10,'pending')]]", expected=[True, True, False, False, False],
         sources={'domain/__init__.py': '', 'domain/refunds.py': 'def refund_allowed(age_days, status):\n    """Refund a paid or settled order within a nonnegative thirty-day window."""\n    return 0 <= age_days < 30 and status in ("paid", "settled")\n',
                  'application.py': 'from domain.refunds import refund_allowed\ndef cancel_order(order, age_days):\n    return "refund" if refund_allowed(age_days, order["status"]) else "refused"\n',
                  'tests/test_refunds.py': 'import unittest\nfrom application import cancel_order\nclass RefundTests(unittest.TestCase):\n    def test_paid(self): self.assertEqual(cancel_order({"status":"paid"}, 2), "refund")\n    def test_expired(self): self.assertEqual(cancel_order({"status":"paid"}, 31), "refused")\n'}),
    dict(id='falsy', intent='Configuration lookup must preserve stored0,False,empty string andNone. Use the supplied default only when the key is absent. Preserve value types and the public API. Add a regression test.',
         module='domain.settings', function='lookup_config', expression="[lookup_config({'k':v},'k','fallback') for v in [0,False,'',None]]+[lookup_config({},'k','fallback')]", expected=[0, False, '', None, 'fallback'],
         sources={'domain/__init__.py': '', 'domain/settings.py': 'def lookup_config(values, key, default=None):\n    """Read a configured value, falling back only for an absent key."""\n    return values.get(key) or default\n',
                  'application.py': 'from domain.settings import lookup_config\ndef worker_count(config):\n    return lookup_config(config, "workers", 4)\n',
                  'tests/test_settings.py': 'import unittest\nfrom domain.settings import lookup_config\nclass ConfigTests(unittest.TestCase):\n    def test_value(self): self.assertEqual(lookup_config({"k":7},"k",9),7)\n    def test_missing(self): self.assertEqual(lookup_config({},"k",9),9)\n'}),
    dict(id='discount', intent='Loyalty members who spend exactly 100.00 at checkout are charged the full price; they should get the 10% loyalty discount, as they do for larger orders. Non-members and smaller orders are unchanged. Preserve the public API. Add a regression test.',
         module='shop.checkout', function='checkout',
         expression="[checkout([{'price':p,'qty':1}],{'id':1,'member':m})['total'] for p,m in [(100.0,True),(99.99,True),(100.0,False),(150.0,True)]]",
         expected=[90.0, 99.99, 100.0, 135.0],
         sources={'shop/__init__.py': '',
                  'shop/checkout.py': 'from shop.pricing import order_total\ndef checkout(cart, customer):\n    """Close the cart and return the amount to charge."""\n    return {"customer": customer["id"], "total": order_total(cart, customer)}\n',
                  'shop/pricing.py': 'from shop.discounts import member_discount\ndef subtotal(cart):\n    return round(sum(line["price"] * line["qty"] for line in cart), 2)\ndef order_total(cart, customer):\n    amount = subtotal(cart)\n    return round(amount - member_discount(amount, customer), 2)\n',
                  'shop/discounts.py': 'def member_discount(amount, customer):\n    """Members get 10% off orders of 100.00 or more."""\n    if customer.get("member") and amount > 100:\n        return round(amount * 0.10, 2)\n    return 0.0\n',
                  'legacy/__init__.py': '',
                  'legacy/promo_rules.py': 'def loyalty_discount_rate(level):\n    """Retired loyalty discount table, kept for old reports."""\n    return {"gold": 0.15, "silver": 0.10}.get(level, 0.0)\n',
                  'tests/test_checkout.py': 'import unittest\nfrom shop.checkout import checkout\nclass CheckoutTests(unittest.TestCase):\n    def test_member_large_order(self): self.assertEqual(checkout([{"price":150.0,"qty":1}], {"id":1,"member":True})["total"], 135.0)\n    def test_non_member(self): self.assertEqual(checkout([{"price":150.0,"qty":1}], {"id":2,"member":False})["total"], 150.0)\n'}),
    dict(id='reserve', intent='An order may list the same product on several lines. Such an order must be rejected when the combined quantity exceeds the stock, leaving the stock unchanged; today it is accepted and stock can go negative. Orders that fit are unchanged. Preserve the public API. Add a regression test.',
         module='warehouse.orders', function='place_order',
         expression="[[place_order(s,l),s] for s,l in [({'a':3},[{'sku':'a','qty':2},{'sku':'a','qty':2}]),({'a':4},[{'sku':'a','qty':2},{'sku':'a','qty':2}]),({'a':1,'b':1},[{'sku':'a','qty':1},{'sku':'b','qty':1}]),({'a':0},[{'sku':'a','qty':1}])]]",
         expected=[['rejected', {'a': 3}], ['accepted', {'a': 0}], ['accepted', {'a': 0, 'b': 0}], ['rejected', {'a': 0}]],
         sources={'warehouse/__init__.py': '',
                  'warehouse/orders.py': 'from warehouse.reservations import reserve_lines\ndef place_order(stock, lines):\n    """Reserve every order line or nothing."""\n    return "accepted" if reserve_lines(stock, lines) else "rejected"\n',
                  'warehouse/reservations.py': 'from warehouse.stock import available\ndef reserve_lines(stock, lines):\n    for line in lines:\n        if available(stock, line["sku"]) < line["qty"]:\n            return False\n    for line in lines:\n        stock[line["sku"]] -= line["qty"]\n    return True\n',
                  'warehouse/stock.py': 'def available(stock, sku):\n    return stock.get(sku, 0)\n',
                  'warehouse/legacy_allocation.py': 'def allocate_inventory(order_items, inventory):\n    """Old product allocation kept for nightly reports; not used by orders."""\n    return [item for item in order_items if item in inventory]\n',
                  'tests/test_orders.py': 'import unittest\nfrom warehouse.orders import place_order\nclass OrderTests(unittest.TestCase):\n    def test_fits(self): self.assertEqual(place_order({"a":5}, [{"sku":"a","qty":2}]), "accepted")\n    def test_short(self): self.assertEqual(place_order({"a":1}, [{"sku":"a","qty":2}]), "rejected")\n'}),
)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seed(root, task):
    for name, source in task['sources'].items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    for i in range(6):
        (root / ('unrelated_' + str(i) + '.py')).write_text('def helper_' + str(i) + '(value):\n    return value\n')
    (root / '.gitignore').write_text('.code-graph/\n__pycache__/\n*.pyc\n')
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob('*')) if path.is_file()}


def holdout(root, task):
    code = ('import sys,json;sys.path.insert(0,sys.argv[1]);from ' + task['module'] + ' import ' + task['function']
            + ';values=' + task['expression'] + ';print(json.dumps(values))')
    try:
        run = subprocess.run([sys.executable, '-I', '-c', code, str(root)], capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return dict(passed=False, actual=None, expected=task['expected'], exit_code=None, timed_out=True)
    try:
        actual = json.loads(run.stdout) if run.returncode == 0 else None
    except ValueError:
        actual = None
    expected = task['expected']
    passed = (type(actual) is list and len(actual) == len(expected)
              and all(type(a) is type(b) and a == b for a, b in zip(actual, expected)))
    return dict(passed=passed, actual=actual, expected=expected, exit_code=run.returncode)


def unittest_run(root, tests, names=None):
    code = ('import sys,unittest,json;sys.path.insert(0,sys.argv[1]);sys.path.insert(0,sys.argv[2]);'
            'names=json.loads(sys.argv[3]);suite=unittest.defaultTestLoader.loadTestsFromNames(names) if names else unittest.defaultTestLoader.discover(sys.argv[2]);'
            'r=unittest.TextTestRunner(verbosity=2).run(suite);'
            'print("CG_PUBLIC="+json.dumps(dict(passed=r.wasSuccessful() and r.testsRun>0 and not r.skipped,'
            'executed=r.testsRun,failed=len(r.failures),errors=len(r.errors),skipped=len(r.skipped))))')
    try:
        run = subprocess.run([sys.executable, '-I', '-c', code, str(root), str(tests), json.dumps(names)], capture_output=True, text=True, timeout=30)
        lines = [line for line in run.stdout.splitlines() if line.startswith('CG_PUBLIC=')]
        return json.loads(lines[-1].removeprefix('CG_PUBLIC=')) if lines else dict(passed=False, exit_code=run.returncode)
    except (subprocess.TimeoutExpired, ValueError):
        return dict(passed=False, error='frozen_public_timeout_or_invalid_output')

def public_tests(root, task):
    """Run the original public suite from outside the mutable agent worktree."""
    with tempfile.TemporaryDirectory(prefix='cg-frozen-public-') as folder:
        tests = Path(folder)
        for name, source in task['sources'].items():
            if name.startswith('tests/'):
                (tests / Path(name).name).write_text(source)
        return unittest_run(root, tests)


def test_statements(source):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    return {node.name + '.' + method.name: [ast.dump(statement) for statement in method.body]
            for node in tree.body if isinstance(node, ast.ClassDef)
            for method in node.body if isinstance(method, ast.FunctionDef) and method.name.startswith('test')}


def test_retention(root, task):
    missing, added = [], []
    for name, source in task['sources'].items():
        if not name.startswith('tests/'):
            continue
        path = root / name
        actual = test_statements(path.read_text()) if path.is_file() else {}
        original = test_statements(source)
        added.extend(name + ':' + method for method in actual.keys() - original.keys())
        for method, statements in original.items():
            remaining = iter(actual.get(method, []))
            if any(not any(candidate == statement for candidate in remaining) for statement in statements):
                missing.append(name + ':' + method)
    return dict(passed=not missing, removed_or_weakened=missing, regression_test_added=bool(added))


def api_retention(root, task):
    def signatures(source):
        functions = {}
        for node in ast.parse(source).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for argument in ast.walk(node.args):
                    if isinstance(argument, ast.arg):
                        argument.annotation = argument.type_comment = None
                functions[node.name] = ast.dump(node.args)
        return functions
    changed = []
    for name, source in task['sources'].items():
        if not name.endswith('.py') or name.startswith('tests/'):
            continue
        try:
            actual = signatures((root / name).read_text())
        except (OSError, SyntaxError):
            actual = {}
        changed.extend(name + ':' + function for function, signature in signatures(source).items()
                       if actual.get(function) != signature)
    return dict(passed=not changed, changed_signatures=changed)


def regression_probe(root, task):
    names = []
    for name, source in task['sources'].items():
        if name.startswith('tests/') and (root / name).is_file():
            added = test_statements((root / name).read_text()).keys() - test_statements(source).keys()
            names.extend(Path(name).stem + '.' + method for method in sorted(added))
    if not names:
        return dict(passed=False, reason='no_new_test')
    candidate = unittest_run(root, root / 'tests', names)
    with tempfile.TemporaryDirectory(prefix='cg-regression-original-') as folder:
        original = Path(folder)
        seed(original, task)
        for name in task['sources']:
            if name.startswith('tests/') and (root / name).is_file():
                (original / name).write_bytes((root / name).read_bytes())
        baseline = unittest_run(original, original / 'tests', names)
    detects = (baseline.get('executed') == len(names) and baseline.get('failed', 0) > 0
               and baseline.get('errors') == baseline.get('skipped') == 0)
    return dict(passed=bool(detects and candidate.get('passed')), test_ids=names,
                original=baseline, candidate=candidate)


def strip_host_graph(value):
    if isinstance(value, dict):
        return {k: strip_host_graph(v) for k, v in value.items() if k not in ('checked', 'static', 'graph')}
    if isinstance(value, list):
        return [strip_host_graph(v) for v in value]
    return value


class PilotRunner:
    def __init__(self, agents, flow_module, skill, task, arm, counter):
        self.agents, self.flow_module, self.skill = agents, flow_module, skill
        self.task, self.arm, self.counter, self.runs = task, arm, counter, []
        self._lock = threading.Lock()
        self.closed, self.start_failures = False, []
        self.original_argv = agents.AgentRun._argv
        agents.AgentRun._argv = lambda run, folder: self.argv(run, folder)

    def argv(self, run, folder):
        argv = self.original_argv(run, folder)
        if self.arm == 'source_only':
            command = sys.executable
            arguments = ['-I', str(REPO / 'benchmarks/source_only_mcp.py'), '--skill-root', str(self.skill), '--root', run._root]
            if run.client == 'codex':
                for i, value in enumerate(argv):
                    if value.startswith('mcp_servers.code-graph.command='):
                        argv[i] = 'mcp_servers.code-graph.command=' + json.dumps(command)
                    elif value.startswith('mcp_servers.code-graph.args='):
                        argv[i] = 'mcp_servers.code-graph.args=' + json.dumps(arguments)
            else:
                path = folder / 'mcp.json'
                config = json.loads(path.read_text())
                config['mcpServers']['code-graph'].update(command=command, args=arguments)
                path.write_text(json.dumps(config))
        return argv

    def start(self, client, **kwargs):
        with self._lock:
            if self.closed:
                raise RuntimeError('Pilot runner is closed')
            if self.counter[0] >= MAX_CALLS:
                raise RuntimeError('Live start cap reached')
            self.counter[0] += 1
            return self._start(client, kwargs)

    def _start(self, client, kwargs):
        prompt = kwargs['prompt'].removeprefix(self.flow_module._RETRIEVAL_GUIDANCE + '\n\n')
        if '\n{' in prompt:
            header, payload = prompt.split('\n{', 1)
            prompt = header + '\n' + json.dumps(strip_host_graph(json.loads('{' + payload)))
        if self.arm == 'source_only':
            prompt = prompt.replace('Use code-graph MCP for evidence.', 'Use only cg_read for source evidence.')
            prompt = 'Read the available source files using cg_read; no graph tools are available.\n\n' + prompt
        else:
            prompt = self.flow_module._RETRIEVAL_GUIDANCE + '\n\n' + prompt
        prompt += '\n\nFixture file list: ' + json.dumps(sorted(self.task['sources']) + ['unrelated_' + str(i) + '.py' for i in range(6)])
        prompt += '\nPreserve existing tests and API. Never run commands. Only Python source/test files in this fixture may change.'
        prompt += '\nAllowed change files (no new files or unrelated helpers): ' + json.dumps(sorted(self.task['sources']))
        prompt += '\nApproved test argv (executed by the host): ' + json.dumps([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'])
        kwargs.update(prompt=prompt, timeout=min(kwargs['timeout'], CALL_SECONDS))
        try:
            run = self.agents.start(client, **kwargs)
        except Exception as error:
            self.start_failures.append(type(error).__name__)
            raise
        self.runs.append(run)
        return run

    def stop_starts(self):
        with self._lock:
            self.closed = True

    def close(self, restore=True):
        self.stop_starts()
        drained = True
        for run in self.runs:
            if run.status in ('starting', 'running'):
                run.cancel()
            if hasattr(run, 'wait'):
                run.wait(timeout=10)
            thread = getattr(run, '_thread', None)
            drained &= run.status not in ('starting', 'running') and not (thread is not None and thread.is_alive())
        if restore and drained:
            self.agents.AgentRun._argv = self.original_argv
        return drained


def git(root, *arguments):
    return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.excludesFile=/dev/null', '-c', 'commit.gpgsign=false', '-C', str(root), *arguments],
                          capture_output=True, text=True, check=True)


def client_metadata():
    result = {}
    for name in ('codex', 'claude'):
        path = shutil.which(name)
        if path:
            run = subprocess.run([path, '--version'], capture_output=True, text=True, timeout=10)
            result[name] = dict(version=run.stdout.strip().splitlines()[0] if run.stdout.strip() else None,
                                version_exit=run.returncode, model_override=None)
        else:
            result[name] = dict(version=None, available=False)
    return result


def log_folder(run):
    if run._log_dir is None:
        return None
    return run._log_dir if run._log_dir.is_dir() else run._log_dir.parent / run.id


def search_payload(value):
    if isinstance(value, str):
        try:
            return search_payload(json.loads(value))
        except ValueError:
            return None
    if isinstance(value, list):
        return next((found for item in value if (found := search_payload(item)) is not None), None)
    if isinstance(value, dict):
        if str(value.get('format', '')).startswith('codegraph-search/'):
            return value
        for key in ('content', 'text', 'structuredContent', 'structured_content'):
            if key in value:
                found = search_payload(value[key])
                if found is not None:
                    return found
    return None


def native_metadata(run):
    models, cost, usage, tools, pending = set(), None, [], [], {}
    if run._log_dir is not None:
        folder = log_folder(run)
        path = folder / 'raw.jsonl'
        if path.is_file():
            for line in path.read_text(errors='replace').splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if isinstance(event.get('usage'), dict):
                    keys = ('input_tokens', 'output_tokens', 'cached_input_tokens', 'cache_read_input_tokens',
                            'cache_creation_input_tokens', 'reasoning_output_tokens', 'cache_write_input_tokens')
                    usage.append({key: event['usage'].get(key) if type(event['usage'].get(key)) is int and event['usage'][key] >= 0 else None for key in keys})
                blocks = []
                item = event.get('item', {})
                if isinstance(item, dict) and item.get('type') == 'mcp_tool_call':
                    blocks = [dict(type='codex_tool', id=item.get('id'), name=item.get('tool'),
                                   status='started' if event.get('type') != 'item.completed' else 'error' if item.get('error') or item.get('status') == 'failed' or (isinstance(item.get('result'), dict) and item['result'].get('isError')) else 'ok',
                                   result=item.get('result'))]
                elif event.get('type') in ('assistant', 'user'):
                    content = event.get('message', {}).get('content', [])
                    blocks = content if isinstance(content, list) else []
                for block in blocks:
                    if not isinstance(block, dict) or block.get('type') not in ('codex_tool', 'tool_use', 'tool_result'):
                        continue
                    key = block.get('tool_use_id', block.get('id'))
                    if key is None:
                        key = 'unpaired-' + str(len(tools))
                    if key not in pending:
                        pending[key] = dict(id=len(tools)+1, name=block.get('name', 'unknown'), status='started', graph_evidence_observed=None)
                        tools.append(pending[key])
                    tool = pending[key]
                    tool['status'] = block.get('status', 'error' if block.get('is_error') else 'ok' if block.get('type') == 'tool_result' else 'started')
                    if str(tool['name']).endswith('cg_search') and tool['status'] != 'started':
                        payload = search_payload(block.get('result', block.get('content')))
                        tool['graph_evidence_observed'] = (any(item.get('graph', {}).get('chain') for item in payload['items'] if item.get('graph'))
                                                           if payload is not None and 'items' in payload else False if tool['status'] == 'error' else None)
                model = event.get('model')
                if isinstance(model, str) and re.fullmatch(r'[A-Za-z0-9._/+:\-]{1,160}', model):
                    models.add(model)
                if (event.get('type') == 'result' and type(event.get('total_cost_usd')) in (int, float)
                        and math.isfinite(event['total_cost_usd']) and event['total_cost_usd'] >= 0):
                    cost = event['total_cost_usd']
        stderr = folder / 'stderr.txt'
        if stderr.is_file():
            for line in stderr.read_text(errors='replace').splitlines():
                match = re.fullmatch(r'model:\s*([A-Za-z0-9._/+:\-]{1,160})', line.strip())
                if match:
                    models.add(match.group(1))
    return dict(resolved_models=sorted(models) or None, cli_reported_cost_usd=cost,
                native_usage=usage or None, native_tools=tools or None)


def access_failure(runner):
    texts = [str(event.get('text', '')) for run in runner.runs for event in run.events() if event.get('kind') == 'error']
    for run in runner.runs:
        folder = log_folder(run)
        if folder is not None and (folder / 'stderr.txt').is_file():
            texts.append((folder / 'stderr.txt').read_text(errors='replace'))
    text = ' '.join(texts).lower()
    if any(word in text for word in ('quota', 'usage limit', 'rate_limit', 'rate limit', 'insufficient credits',
                                     'weekly limit', 'monthly limit', 'daily limit')):
        return 'quota_or_rate_limit'
    if any(word in text for word in ('not logged in', 'authentication', 'login required', 'unauthorized', 'invalid api key', 'expired token')):
        return 'authentication'
    if runner.start_failures or any(getattr(run, '_process', False) is None and run.status == 'failed' for run in runner.runs):
        return 'client_startup'
    return None


def test_summary(tests):
    return {phase: ({key: result.get(key) for key in ('status', 'summary', 'duration_s', 'exit')}
                    if isinstance(result, dict) else None)
            for phase, result in (tests or {}).items() if phase in ('baseline', 'after')}


def drain(flow, record, runner):
    """Prevent late starts before cancellation; retain the redirect until all workers stop."""
    runner.stop_starts()
    flow_drained = True
    if record is not None:
        current = flow.get(record['id'])
        if current['state'] in runner.flow_module.ACTIVE:
            flow.cancel(record['id'])
        deadline = time.monotonic() + DRAIN_SECONDS
        for thread in threading.enumerate():
            if thread.name == 'cg-change-' + record['id']:
                thread.join(max(0, deadline - time.monotonic()))
        current = flow.get(record['id'])
        flow_drained = not flow._worker_alive(current)
    clients_drained = runner.close(restore=False)
    if flow_drained and clients_drained:
        runner.agents.AgentRun._argv = runner.original_argv
    return flow_drained and clients_drained


def run_case(skill, directory, task, arm, counter):
    directory.mkdir(parents=True)
    source_hashes = seed(directory, task)
    git(directory, 'init', '-b', 'main')
    git(directory, 'config', 'user.name', 'code-graph pilot')
    git(directory, 'config', 'user.email', 'pilot@example.invalid')
    git(directory, 'add', '.')
    git(directory, 'commit', '-m', 'Frozen synthetic benchmark fixture')
    engine = load(skill / 'scripts/cg.py', 'pilot_engine')
    flow_module = load(skill / 'scripts/change_flow.py', 'pilot_flow')
    agents = load(skill / 'scripts/agents.py', 'pilot_agents')
    flow_module.AGENT_TIMEOUT = CALL_SECONDS
    runner = PilotRunner(agents, flow_module, skill, task, arm, counter)
    flow = flow_module.ChangeFlow(engine, directory, runner=runner)
    row = dict(task=task['id'], arm=arm, fixture_sha256=hashlib.sha256(json.dumps(source_hashes, sort_keys=True).encode()).hexdigest(),
               baseline_holdout=holdout(directory, task), baseline_public=public_tests(directory, task),
               stages=[], outcome='incomplete', calls=[])
    started = time.monotonic()
    record = None
    try:
        record = flow.create(task['intent'], 'codex', 'claude', budget_usd=REVIEW_BUDGET_USD)
        cid = record['id']
        for name, expected in (('plan', 'planned'), ('edit', 'edited'), ('verify', 'verified'), ('review', 'reviewed')):
            if name == 'edit':
                allowed = {name for name in task['sources'] if name.endswith('.py')}
                if any(item['path'] not in allowed for field in ('touch', 'tests')
                       for item in record['plan']['agent'][field]):
                    row['outcome'] = 'plan_outside_fixture_scope'
                    break
                flow.approve(cid, [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'])
            step_started = time.monotonic()
            getattr(flow, name)(cid)
            record = flow.wait(cid, timeout=CALL_SECONDS + 60)
            row['stages'].append(dict(stage=name, state=record['state'], seconds=round(time.monotonic()-step_started, 3),
                                      error_code=(record.get('error') or {}).get('code')))
            if record['state'] != expected:
                row['outcome'] = name + '_failed'
                break
        else:
            row['verification_ok'] = record['verify']['ok']
            row['review_verdict'] = record['review']['verdict']
            row['changed_files'] = [item['path'] for item in flow._files(record)]
            row['candidate_scope_ok'] = set(row['changed_files']) <= set(task['sources'])
            candidate = Path(record['worktree'])
            row['candidate_holdout'] = holdout(candidate, task)
            row['candidate_public'] = public_tests(candidate, task)
            row['test_retention'] = test_retention(candidate, task)
            row['api_retention'] = api_retention(candidate, task)
            row['regression_probe'] = regression_probe(candidate, task)
            if (record['verify']['ok'] and record['review']['verdict'] == 'accept'
                    and row['candidate_holdout']['passed'] and row['candidate_public']['passed']
                    and row['test_retention']['passed'] and row['test_retention']['regression_test_added']
                    and row['api_retention']['passed'] and row['regression_probe']['passed']
                    and row['candidate_scope_ok']):
                flow.integrate(cid, mode='merge')
                row['integrated_holdout'] = holdout(directory, task)
                row['integrated_public'] = public_tests(directory, task)
                row['outcome'] = 'success' if row['integrated_holdout']['passed'] and row['integrated_public']['passed'] else 'integration_failed'
            else:
                row['outcome'] = 'verification_review_or_holdout_failed'
    except Exception as error:
        row['error'] = type(error).__name__
        row['error_code'] = getattr(error, 'code', None)
    finally:
        row['cleanup_drained'] = drain(flow, record, runner)
        if record is not None:
            final = flow.get(record['id'])
            row['final_state'] = final['state']
            row['workflow_tests'] = test_summary(final.get('tests'))
            if final.get('worktree') and Path(final['worktree']).is_dir():
                candidate = Path(final['worktree'])
                row['candidate_public'] = public_tests(candidate, task)
                row['candidate_holdout'] = holdout(candidate, task)
                row['test_retention'] = test_retention(candidate, task)
                row['api_retention'] = api_retention(candidate, task)
                row['changed_files'] = [item['path'] for item in flow._files(final)]
        row['seconds'] = round(time.monotonic()-started, 3)
        for run in runner.runs:
            events = run.events()
            row['calls'].append(dict(client=run.client, mode=run.mode, status=run.status, usage=run.usage, **native_metadata(run),
                tool_calls=[event.get('data', {}).get('name') for event in events if event.get('kind') == 'tool' and event.get('data', {}).get('status') == 'started']))
        row['access_failure'] = access_failure(runner)
    return row


def reported_review_cost(row):
    """Claude's own reported cost for a case; an unreported review counts its whole cap."""
    total = 0.0
    for call in row.get('calls', []):
        if call.get('client') == 'claude':
            cost = call.get('cli_reported_cost_usd')
            total += cost if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0 else REVIEW_BUDGET_USD
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skill-root', type=Path, default=REPO)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--live', action='store_true', help='explicitly start at most 24 installed-client calls')
    args = parser.parse_args()
    skill = args.skill_root.resolve(strict=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = dict(format='codegraph-live-pilot/2', live=args.live, max_calls=MAX_CALLS, call_seconds=CALL_SECONDS,
                  claude_review_budget_usd=REVIEW_BUDGET_USD, claude_total_budget_usd=TOTAL_REVIEW_BUDGET_USD,
                  tokenizer_estimates=False, cases=[],
                  environment=dict(python=platform.python_version(), system=platform.system(), machine=platform.machine()),
                  engine_files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((skill / 'scripts').glob('*.py'))})
    if args.live:
        if args.work_dir is None or args.work_dir.exists():
            parser.error('--live requires a new --work-dir to preserve every attempt')
        args.work_dir.mkdir(parents=True)
        result['clients'] = client_metadata()
        counter = [0]
        stop = False
        spent = 0.0
        for index, task in enumerate(TASKS):
            arms = ('source_only', 'graph_assisted') if index % 2 == 0 else ('graph_assisted', 'source_only')
            for arm in arms:
                if not stop and spent + REVIEW_BUDGET_USD > TOTAL_REVIEW_BUDGET_USD:
                    stop, result['stop_reason'] = True, 'claude_total_budget'
                row = (dict(task=task['id'], arm=arm, outcome='not_started_after_stop_rule', calls=[])
                       if stop else run_case(skill, args.work_dir / (task['id'] + '-' + arm), task, arm, counter))
                spent += reported_review_cost(row)
                result['claude_reported_cost_usd'] = round(spent, 6)
                stop |= bool(row.get('access_failure')) or row.get('cleanup_drained') is False
                result['cases'].append(row)
                result['live_starts'] = counter[0]
                args.output.write_text(json.dumps(result, indent=2) + '\n')
                print(task['id'], arm, row['outcome'], flush=True)
    else:
        with tempfile.TemporaryDirectory(prefix='cg-live-pilot-dry-') as temporary:
            for task in TASKS:
                root = Path(temporary) / task['id']
                root.mkdir()
                hashes = seed(root, task)
                public = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=root, capture_output=True, timeout=30)
                hidden = holdout(root, task)
                if public.returncode or hidden['passed']:
                    raise AssertionError('Fixture must pass public baseline and fail the intended holdout')
                result['cases'].append(dict(task=task['id'], source_hashes=hashes, public_baseline_passed=True, holdout=hidden))
        result['live_starts'] = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print('Pilot receipt:', args.output)


if __name__ == '__main__':
    main()
