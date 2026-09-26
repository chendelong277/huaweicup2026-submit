# -*- coding: utf-8 -*-
"""Full ALNS-Q4 experiment driver: runs, enumeration baseline, outputs, audit.

Usage (from repo root):
    python members/weiliu/Q4/code/run_all.py --budget 60
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                    '..', '..', '..', '..'))
CODE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.abspath(os.path.join(CODE, '..', 'results'))
FROZEN = os.path.join(ROOT, 'members', 'WHLi', 'Q4', 'results', 'frozen')
SEEDS = (20260924, 20260925, 20260926, 20260927, 20260928)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--budget', type=float, default=60.0)
    ap.add_argument('--seeds', type=int, nargs='*', default=list(SEEDS))
    args = ap.parse_args()

    for k in (2, 3):
        for seed in args.seeds:
            out = os.path.join(RESULTS, 'runs', 'k%d_seed%d' % (k, seed))
            print('=== ALNS-Q4 K=%d seed=%d (budget %.0fs) ==='
                  % (k, seed, args.budget))
            subprocess.check_call(
                [sys.executable, os.path.join(CODE, 'run_alns_q4.py'),
                 '--frozen-dir', FROZEN, '--k', str(k),
                 '--budget', str(args.budget), '--seed', str(seed),
                 '--output-dir', out])

    print('=== enumeration baseline ===')
    subprocess.check_call([sys.executable,
                           os.path.join(CODE, 'q4_enumerate.py')])

    print('=== aggregate outputs + audit ===')
    subprocess.check_call([sys.executable,
                           os.path.join(CODE, 'make_outputs.py')])


if __name__ == '__main__':
    main()
