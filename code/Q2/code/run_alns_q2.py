from __future__ import annotations
import argparse
from pathlib import Path
from alns_q2 import run

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="ALNS-Q2 migrated from chendelong E007")
    p.add_argument("--data-root", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--time-limit", type=float, default=100.0)
    p.add_argument("--seed", type=int, default=20260924)
    a = p.parse_args(); run(a.data_root, a.output_dir, a.time_limit, a.seed)
