# -*- coding: utf-8 -*-
"""CLI entry: one ALNS-Q4 run for a given K and seed.

Example:
    python members/weiliu/Q4/code/run_alns_q4.py \
        --frozen-dir members/WHLi/Q4/results/frozen \
        --k 2 --budget 60 --seed 20260924 \
        --output-dir members/weiliu/Q4/results/runs/k2_seed20260924
"""
import argparse
import csv
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from q4_core import Context, partition_label          # noqa: E402
from alns_q4 import alns_q4                            # noqa: E402


def ev_to_json(ev):
    out = dict(ev)
    out['score'] = list(ev['score'])
    out['assignment'] = list(ev['assignment'])
    return out


def sha256_dir(path):
    h = {}
    for name in sorted(os.listdir(path)):
        p = os.path.join(path, name)
        if os.path.isfile(p):
            with open(p, 'rb') as f:
                h[name] = hashlib.sha256(f.read()).hexdigest()
    return h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--frozen-dir', required=True)
    ap.add_argument('--k', type=int, choices=(2, 3), required=True)
    ap.add_argument('--budget', type=float, default=60.0)
    ap.add_argument('--seed', type=int, default=20260924)
    ap.add_argument('--output-dir', required=True)
    args = ap.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    ctx = Context(args.frozen_dir)
    print('components: %s' % ctx.components)
    print('global counts: %s' % ctx.global_counts)

    res = alns_q4(ctx, args.k, args.budget, args.seed)
    best = res['best']
    print('K=%d seed=%d iters=%d best=%s score=%s'
          % (args.k, args.seed, res['iterations'], res['label'],
             best['score']))

    with open(os.path.join(args.output_dir, 'solution.json'), 'w',
              encoding='utf-8') as f:
        json.dump(dict(k=args.k, seed=args.seed, iterations=res['iterations'],
                       runtime_s=res['runtime_s'], label=res['label'],
                       best=ev_to_json(best),
                       invariants=ctx.invariants()),
                  f, ensure_ascii=False, indent=2)

    with open(os.path.join(args.output_dir, 'pareto_archive.json'), 'w',
              encoding='utf-8') as f:
        json.dump(dict(k=args.k, seed=args.seed,
                       objective_vector=['shortage_total', 'balance_time',
                                         'redundancy_total', 'relay_extra'],
                       invariants=ctx.invariants(),
                       entries=[dict(assignment=list(ev['assignment']),
                                     label=partition_label(ctx, ev),
                                     score=list(ev['score']),
                                     shortage_total=ev['shortage_total'],
                                     balance_time=ev['balance_time'],
                                     redundancy_total=ev['redundancy_total'],
                                     relay_extra=ev['relay_extra'],
                                     within_inventory=ev['within_inventory'])
                                for ev in res['archive']]),
                  f, ensure_ascii=False, indent=2)

    with open(os.path.join(args.output_dir, 'optimization_trace.csv'), 'w',
              newline='', encoding='utf-8') as f:
        if res['trace']:
            w = csv.DictWriter(f, fieldnames=list(res['trace'][0].keys()))
            w.writeheader()
            w.writerows(res['trace'])

    with open(os.path.join(args.output_dir, 'operator_statistics.csv'), 'w',
              newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['kind', 'operator', 'uses',
                                          'accepted', 'final_weight'])
        w.writeheader()
        w.writerows(res['operator_stats'])

    with open(os.path.join(args.output_dir, 'run.json'), 'w',
              encoding='utf-8') as f:
        json.dump(dict(k=args.k, seed=args.seed, budget_s=args.budget,
                       frozen_dir=os.path.abspath(args.frozen_dir),
                       input_sha256=sha256_dir(args.frozen_dir),
                       runtime_s=res['runtime_s'], iterations=res['iterations']),
                  f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
