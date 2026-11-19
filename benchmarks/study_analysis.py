"""Predeclared paired analysis; consumes receipts, starts no clients."""
import math
import random
import statistics

ARMS = ('source_only', 'graph_assisted')
REPOSITORIES = ('django', 'typescript-eslint', 'prometheus')
SEED = 20261113
REPLICATES = 10000


def exact_p(source_only, graph_only):
    n = source_only + graph_only
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(min(source_only, graph_only) + 1)) / 2 ** n)


def analyze(tasks, rows, stopped=False):
    by_pair = {}
    for row in rows:
        key = row['task'], row['arm']
        if key in by_pair:
            raise ValueError('Duplicate study path')
        by_pair[key] = row
    assigned = {(task['id'], arm) for task in tasks for arm in ARMS}
    if not by_pair.keys() <= assigned:
        raise ValueError('Unassigned study path')
    complete = (not stopped and by_pair.keys() == assigned and bool(tasks)
                and all(row.get('outcome') not in ('not_started_after_stop_rule', 'incomplete')
                        and row.get('cleanup_drained') is True for row in rows))
    categories = dict(both=0, source_only=0, graph_only=0, neither=0, missing=0)
    strata = {name: [] for name in REPOSITORIES}
    for task in tasks:
        pair = [by_pair.get((task['id'], arm)) for arm in ARMS]
        if any(row is None or row.get('outcome') == 'not_started_after_stop_rule' for row in pair):
            categories['missing'] += 1
            continue
        source, graph = [row.get('outcome') == 'success' for row in pair]
        categories['both' if source and graph else 'source_only' if source else 'graph_only' if graph else 'neither'] += 1
        strata[task['repository']].append(int(graph) - int(source))
    difference = categories['graph_only'] - categories['source_only']
    observed = len(tasks) - categories['missing']
    result = dict(assigned_pairs=len(tasks), complete=complete, paired_categories=categories,
                  primary_success_difference=difference / len(tasks) if complete else None,
                  observed_complete_pairs=observed, observed_pair_difference=difference / observed if observed else None,
                  confirmatory=None, secondary=[])
    if complete:
        rng = random.Random(SEED)
        bootstrap = sorted(sum(sum(rng.choices(values, k=len(values))) for values in strata.values() if values) / len(tasks)
                           for _ in range(REPLICATES))
        result['confirmatory'] = dict(
            exact_mcnemar_p=exact_p(categories['source_only'], categories['graph_only']), alpha=0.05,
            success_difference_95_interval=[bootstrap[249], bootstrap[9749]],
            bootstrap=dict(unit='bug pair, stratified by repository', replicates=REPLICATES, seed=SEED,
                           percentile='nearest-rank 2.5 and 97.5 percent'))
    for client, phase in (('codex', 'plan'), ('codex', 'edit'), ('claude', 'review')):
        values = {}
        for key, row in by_pair.items():
            calls = [call for call in row.get('calls', []) if call.get('client') == client and call.get('phase') == phase]
            counters = [unit.get('input_tokens') for call in calls for unit in (call.get('native_usage') or [])]
            values[key] = sum(counters) if counters and all(type(n) is int and n >= 0 for n in counters) else None
        pairs = [(values.get((task['id'], ARMS[0])), values.get((task['id'], ARMS[1]))) for task in tasks]
        known = [(s, g) for s, g in pairs if s is not None and g is not None]
        totals = {arm: sum(v for (tid, side), v in values.items() if side == arm and v is not None) for arm in ARMS}
        ratios = [g / s for s, g in known if s]
        result['secondary'].append(dict(client=client, phase=phase, unit='native input tokens',
            pairs_with_both_counters=len(known), missing_pairs=len(tasks)-len(known), totals_of_observed=totals,
            paired_median_difference=statistics.median(g-s for s, g in known) if known else None,
            paired_median_ratio=statistics.median(ratios) if ratios else None,
            aggregate_ratio_of_observed=totals[ARMS[1]] / totals[ARMS[0]] if totals[ARMS[0]] else None))
    return result
