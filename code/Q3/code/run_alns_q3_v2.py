from __future__ import annotations
import argparse, json
from pathlib import Path
from alns_q3_v2 import run

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="ALNS-Q3-V2: E007 ALNS + E004-style joint relay MILP feedback")
    p.add_argument("--data-root", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--budget", type=float, default=300.0)
    p.add_argument("--seed", type=int, default=20260924)
    p.add_argument("--joint-period", type=float, default=15.0,
                   help="seconds between decodes in periodic mode (legacy cadence)")
    p.add_argument("--decode-mode", choices=["hybrid", "periodic"], default="hybrid",
                   help="hybrid: decode only on substantive blind-signature change / "
                        "fresh incumbent / heartbeat; periodic: fixed cadence (V2 original)")
    p.add_argument("--init", dest="init_mode",
                   choices=["auto", "direct_q2", "struct_q2"], default="auto",
                   help="auto: Q1/local/external candidates; direct_q2: frozen Q2 "
                        "solution trips + Q2 dispatch order; struct_q2: frozen Q2 "
                        "trips with urgency re-derived dispatch order (timing free)")
    p.add_argument("--heartbeat-s", type=float, default=45.0,
                   help="hybrid mode: max seconds between decodes even without change")
    p.add_argument("--min-decode-gap-s", type=float, default=6.0,
                   help="hybrid mode: min seconds between two decodes")
    p.add_argument("--shift-tol-s", type=float, default=120.0,
                   help="hybrid mode: blind-window shift that counts as substantive")
    p.add_argument("--no-hard-relay-cap", dest="hard_relay_cap",
                   action="store_false",
                   help="ablation: keep relay-concurrency overload as soft SA "
                        "guidance instead of a hard lexicographic level")
    a = p.parse_args()
    print(json.dumps(run(a.data_root, a.output_dir, a.budget, a.seed, a.joint_period,
                         decode_mode=a.decode_mode, init_mode=a.init_mode,
                         heartbeat_s=a.heartbeat_s,
                         min_decode_gap_s=a.min_decode_gap_s,
                         shift_tol_s=a.shift_tol_s,
                         hard_relay_cap=a.hard_relay_cap),
                     ensure_ascii=False, indent=2))
