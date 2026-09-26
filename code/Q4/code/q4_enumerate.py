# -*- coding: utf-8 -*-
"""Q4 exhaustive-enumeration baseline over components (validation reference).

With the frozen Q3 main line the 15 service areas collapse into 6 indivisible
components, so the full partition space is S(6,2)+S(6,3) = 31+90 = 121
unlabeled partitions -- small enough for exact enumeration.  ALNS-Q4 must
reproduce the lexicographic optimum of this baseline (gap = 0).

Also reports the WHLi-style selection keys for cross-checking against
members/WHLi/Q4/results/enumeration_summary.json.
"""
import csv
import json
import os

from q4_core import (INVENTORY, RESOURCE_TYPES, Context, evaluate_partition,
                     partition_label)
from alns_q4 import archive_key, _dominates


def set_partitions(elements, k):
    """All partitions of elements into exactly k non-empty unlabeled groups
    (restricted growth strings, element 0 fixed in group 0)."""
    n = len(elements)
    a = [0] * n

    def rec(i, m):
        if i == n:
            if m == k - 1:
                yield tuple(a)
            return
        for g in range(min(m + 2, k)):
            a[i] = g
            for r in rec(i + 1, max(m, g)):
                yield r

    for r in rec(1, 0):
        yield r


def run_enumeration(ctx, out_dir):
    results = {}
    summary = {'components': ctx.components, 'global_counts': ctx.global_counts,
               'inventory': dict(INVENTORY)}
    for k in (2, 3):
        evs = [evaluate_partition(ctx, list(a), k)
               for a in set_partitions(ctx.comp_ids, k)]
        results[k] = evs
        front = [ev for ev in evs
                 if not any(other is not ev and _dominates(other, ev)
                            for other in evs)]
        best = min(evs, key=archive_key)
        summary['k%d' % k] = dict(
            n_partitions=len(evs),
            pareto_front=[partition_label(ctx, ev) for ev in front],
            lexicographic_best=dict(
                partition=partition_label(ctx, best),
                score=list(best['score']),
                shortage=best['shortage'], relay_extra=best['relay_extra'],
                balance_time=best['balance_time'], redundancy=best['redundancy'],
                totals=best['totals'], workload_times=best['workload_times'],
                within_inventory=best['within_inventory']),
            # WHLi-style selections for cross-checking with members/WHLi/Q4
            whli_recommended=_sel(ctx, evs, lambda e: (
                e['relay_extra'], e['totals']['relay'], e['shortage_total'],
                e['balance_time'], e['redundancy_total'])),
            whli_shortage_first=_sel(ctx, evs, lambda e: (
                e['shortage_total'], e['redundancy_total'], e['balance_time'])),
            whli_balance_first=_sel(ctx, evs, lambda e: (
                e['balance_time'], e['shortage_total'], e['redundancy_total'])),
        )

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'enumeration_baseline.csv'), 'w',
              newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['k', 'partition', 'shortage_total', 'relay_extra_copies',
                    'balance_time', 'redundancy_total', 'resources_total',
                    'within_inventory', 'workload_times_s']
                   + ['req_%s' % rt for rt in RESOURCE_TYPES]
                   + ['short_%s' % rt for rt in RESOURCE_TYPES])
        for k in (2, 3):
            for ev in sorted(results[k], key=archive_key):
                w.writerow([k, partition_label(ctx, ev), ev['shortage_total'],
                            ev['relay_extra'], '%.4f' % ev['balance_time'],
                            ev['redundancy_total'], ev['resources_total'],
                            ev['within_inventory'],
                            ';'.join('%.0f' % x for x in ev['workload_times'])]
                           + [ev['totals'][rt] for rt in RESOURCE_TYPES]
                           + [ev['shortage'][rt] for rt in RESOURCE_TYPES])
    with open(os.path.join(out_dir, 'enumeration_summary.json'), 'w',
              encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


def _sel(ctx, evs, key):
    ev = min(evs, key=key)
    return dict(partition=partition_label(ctx, ev),
                shortage_total=ev['shortage_total'],
                relay_extra=ev['relay_extra'],
                balance_time=round(ev['balance_time'], 6),
                redundancy_total=ev['redundancy_total'])


if __name__ == '__main__':
    results = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                           '..', 'results'))
    ctx = Context(os.path.join(results, 'frozen_alns_q3_v2_seed_20260924'))
    s = run_enumeration(ctx, results)
    for k in (2, 3):
        print('K=%d: %d partitions, best=%s' % (
            k, s['k%d' % k]['n_partitions'],
            s['k%d' % k]['lexicographic_best']['partition']))
