"""Re-run Q4 against a strictly gated ALNS-Q3-V2 frozen input.

Usage: python members/weiliu/Q4/code/run_strict_frozen.py \
    --frozen-dir members/weiliu/Q4/results/frozen_alns_q3_v2_seed_20260924 \
    --results-dir members/weiliu/Q4/results/strict_q3_v2_seed_20260924 \
    --budget 60
"""
import argparse
import os
import subprocess
import sys

from q4_core import Context
from q4_enumerate import run_enumeration
from make_outputs import run_outputs


CODE = os.path.dirname(os.path.abspath(__file__))
SEEDS = (20260924, 20260925, 20260926, 20260927, 20260928)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--frozen-dir', required=True)
    ap.add_argument('--results-dir', required=True)
    ap.add_argument('--budget', type=float, default=60.0)
    ap.add_argument('--seeds', type=int, nargs='*', default=list(SEEDS))
    args = ap.parse_args()
    ctx = Context(args.frozen_dir)
    os.makedirs(args.results_dir, exist_ok=True)
    for k in (2, 3):
        for seed in args.seeds:
            out = os.path.join(args.results_dir, 'runs', 'k%d_seed%d' % (k, seed))
            print('ALNS-Q4 K=%d seed=%d frozen=%s' % (k, seed, args.frozen_dir), flush=True)
            subprocess.check_call([sys.executable, os.path.join(CODE, 'run_alns_q4.py'),
                                   '--frozen-dir', args.frozen_dir, '--k', str(k),
                                   '--budget', str(args.budget), '--seed', str(seed),
                                   '--output-dir', out])
    run_enumeration(ctx, args.results_dir)
    audit = run_outputs(ctx, args.results_dir)
    for k in (2, 3):
        a = audit['k%d' % k]
        print('K=%d feasible=%s gap0=%s partition=%s' %
              (k, a['feasible'], a.get('alns_equals_enumeration'), a['partition']))
        if not a['feasible'] or not a.get('alns_equals_enumeration'):
            raise RuntimeError('Q4 frozen solution failed partition audit or enumeration')


if __name__ == '__main__':
    main()
