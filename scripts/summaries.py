"""Short CLI views of the existing analysis JSON. Rendering never runs a command or changes the data."""
import json
from pathlib import Path
import runpy
import shlex

TEST_ROWS = 20
DEFINITION_ROWS = 8
FINDING_ROWS = 5
COMMAND_CHARS = 800


def one(value, limit=180):
    text = ' '.join(str(value or '').split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def location(node):
    return f"{one(node.get('file', '?'), 100)}:{node.get('line', '?')}"


def kind(item):
    if item.get('distance') == 0:
        return 'target'
    return 'direct' if item.get('priority') == 'direct' else 'transitive'


def command_line(label, plan):
    argv = plan.get('command')
    if not argv:
        return f"{label}: unavailable — {one(plan.get('command_reason') or 'No certain runner command.')}"
    # POSIX quoting for ordinary argv; control characters stay escaped in an explicit JSON argv.
    control = any(any(ord(char) < 32 for char in part) for part in argv)
    rendered = json.dumps(argv, ensure_ascii=False) if control else shlex.join(argv)
    if len(rendered) > COMMAND_CHARS:
        return f"{label} (preview; complete argv in --json): {rendered[:COMMAND_CHARS]}…"
    return f"{label}{' (JSON argv)' if control else ''}: {rendered} (cwd: {one(plan.get('cwd', '.'))})"


def verify(plan, target='the requested code', *, listed=False, caller_names=None):
    tests = plan.get('tests', [])
    groups = {name: [item for item in tests if kind(item) == name] for name in ('target', 'direct', 'transitive')}
    counts = ', '.join(f'{len(items)} {name}' for name, items in groups.items() if name != 'target' or items)
    noun = 'test selection' if any(item.get('role') == 'test_file' for item in tests) else 'test'
    if len(tests) != 1:
        noun += 's'
    verb = 'listed for' if listed else ('reaches' if len(tests) == 1 else 'reach')
    runner = plan.get('command_runner') or 'not established'
    out = [f"{len(tests)} {noun} {verb} {one(target)} ({counts}) · runner {runner}", command_line('run all', plan)]
    if len(tests) > TEST_ROWS:
        direct = groups['target'] + groups['direct']
        aggregate = runpy.run_path(str(Path(__file__).with_name('test_commands.py')))['aggregate']
        out.append(command_line('run listed direct only' if listed else 'run direct only', aggregate(direct)))
    ordered = groups['target'] + groups['direct'] + groups['transitive']
    for item in ordered[:TEST_ROWS]:
        node = item['symbol']
        row = f"  {kind(item):10} {location(node)} {one(node.get('name'))}"
        steps = item.get('chain', [])[1:3]
        if steps:
            names = caller_names if item.get('basis') != 'baseline' else None
            row += ' via ' + ' → '.join((f"{one(step['file'], 100)}:{step['line']} " +
                                        one((names or {}).get(step.get('caller')))).rstrip() for step in steps)
        if item.get('basis') == 'baseline':
            row += ' [baseline]'
        if not item.get('command'):
            row += ' [command unavailable]'
        out.append(row)
    if len(tests) > TEST_ROWS:
        out.append(f'  … {len(tests) - TEST_ROWS} more tests; use --json for every record.')
    reasons = list(dict.fromkeys(item.get('command_reason') for item in tests if item.get('command_reason')))
    if reasons:
        out.append('Command notes: ' + '; '.join(one(reason) for reason in reasons[:2]))
        if len(reasons) > 2:
            out.append(f'  … {len(reasons) - 2} more command reasons in --json.')
    unknown = len(plan.get('unknown_calls', []))
    if unknown:
        out.append(f'Unknown/unresolved call records: {unknown}.')
    if plan.get('broader_verification_required'):
        out.append('Broader verification required: run the wider suite too.')
    if not tests:
        out.append('An empty list does not prove there are no tests.')
    out.append('Proposal only; no tests executed.')
    return '\n'.join(out) + '\n'


def review(result, *, caller_names=None):
    if result.get('status') == 'budget_insufficient':
        return f"Review summary unavailable: budget insufficient ({result.get('max_chars')} characters). Increase --max-chars.\n"
    baseline = result.get('baseline', {})
    detail = baseline.get('commit') or baseline.get('content_revision') or baseline.get('reason')
    out = ['Baseline: ' + one(baseline.get('status', 'absent')) + (f' · {one(detail)}' if detail else '')]
    symbols = result.get('symbols', [])
    out.append(f'Changed definitions listed: {len(symbols)}')
    for node in symbols[:DEFINITION_ROWS]:
        out.append(f"  {location(node)}-{node.get('end_line', '?')} {one(node.get('name'))} · {one(node.get('signature'))}")
    if len(symbols) > DEFINITION_ROWS:
        out.append(f'  … {len(symbols) - DEFINITION_ROWS} more definitions; use --json.')
    if not symbols:
        out.append('  No current definitions listed; inspect the diff, including deletions.')
    summary = result.get('summary', {})
    out.append('Findings: ' + ' · '.join(f'{name} {summary.get(name, 0)}' for name in ('new', 'persistent', 'resolved', 'uncompared')))
    comparison = result.get('comparison', {})
    category = 'new' if summary.get('new') or comparison.get('new') else 'uncompared'
    findings = comparison.get(category, [])
    for item in findings[:FINDING_ROWS]:
        out.append(f"  {category} {one(item.get('severity'))} {location(item)} [{one(item.get('rule_id'))}] {one(item.get('message'))}")
    total = summary.get(category, len(findings))
    if total > min(len(findings), FINDING_ROWS):
        out.append(f'  … {total - min(len(findings), FINDING_ROWS)} more {category} findings; use --json / increase --max-chars.')
    if 'verification' in result:
        out.append('')
        out.append(verify(result['verification'], 'the changed definitions', listed=True, caller_names=caller_names).rstrip())
    output = result.get('output', {})
    if output.get('status') == 'partial':
        out.append(f"Omissions: {output.get('omitted_records', 0)} evidence records removed by the response budget ({output.get('max_chars')} characters).")
        out.append('The aggregate command still covers all selected tests, including omitted test evidence.')
    out.append('Static candidates, not confirmed bugs; read the diff. --json keeps the full structured answer within its budget.')
    return '\n'.join(out) + '\n'
