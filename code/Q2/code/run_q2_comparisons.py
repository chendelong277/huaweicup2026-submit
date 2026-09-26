"""Run the eight Q2 methods under a common budget and seed set.

Example
-------
python run_q2_comparisons.py --data-root ../../../../problem/数据 \
    --output-root ../comparison_results --budget 60 --seeds 0 1 2
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from q2_comparison import ALGORITHMS, run_algorithm


def main():
    parser = argparse.ArgumentParser(description="Run all compact Q2 comparison algorithms")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--budget", type=float, default=60.0)
    parser.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2])
    parser.add_argument("--algorithms", nargs="+", choices=ALGORITHMS,
                        default=list(ALGORITHMS))
    args = parser.parse_args()
    rows = []
    for algorithm in args.algorithms:
        for seed in args.seeds:
            output = args.output_root / algorithm / f"seed_{seed}"
            metadata = run_algorithm(args.data_root, output, algorithm,
                                     args.budget, seed)
            rows.append({"algorithm": algorithm, "seed": seed,
                         "score": metadata["final_score"],
                         "elapsed_s": metadata["elapsed_s"],
                         "initial_source": metadata["initial_source"]})
            print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "summary.json").write_text(
        json.dumps({"algorithms": list(args.algorithms), "seeds": args.seeds,
                    "budget_s": args.budget, "rows": rows},
                   ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
