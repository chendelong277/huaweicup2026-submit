from __future__ import annotations
import argparse, json
from pathlib import Path
from alns_q3 import run

if __name__ == "__main__":
    p=argparse.ArgumentParser(description="ALNS-Q3: ALNS-Q2 + communication feedback decoder")
    p.add_argument("--data-root",type=Path,required=True); p.add_argument("--output-dir",type=Path,required=True)
    p.add_argument("--budget",type=float,default=100.0); p.add_argument("--seed",type=int,default=20260924)
    p.add_argument("--full-audit",action="store_true",help="run dense DEM and continuity closure even if total wall time exceeds search budget")
    a=p.parse_args(); print(json.dumps(run(a.data_root,a.output_dir,a.budget,a.seed,a.full_audit),ensure_ascii=False,indent=2))
