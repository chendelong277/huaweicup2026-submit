#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")/../../../.."
DATA="problem/数据"
BASE="members/weiliu/Q3/results_alns_q3_v2_hardcap_600s"
export PYTHONIOENCODING=utf-8
mkdir -p "$BASE"
for SEED in 20260924 20260925 20260926 20260927 20260928; do
  OUT="$BASE/seed_$SEED"
  mkdir -p "$OUT"
  echo "=== seed $SEED: ALNS ==="
  python members/weiliu/Q3/code/run_alns_q3_v2.py --data-root "$DATA" --output-dir "$OUT" \
    --budget 600 --seed "$SEED" --decode-mode hybrid --init auto \
    --heartbeat-s 45 --min-decode-gap-s 6 --shift-tol-s 120 > "$OUT.run.log" 2>&1
  echo "=== seed $SEED: WHLi strict gate (dense 1 s) ==="
  python members/weiliu/Q3/code/whli_q3_gate.py --data-root "$DATA" --q2-dir "$OUT" \
    --output-dir "$OUT/gate_whli_strict_1s" --dense --dense-step 1.0 > "$OUT.gate.log" 2>&1
  python - "$OUT" <<'EOF'
import json, sys
out = sys.argv[1]
g = json.load(open(out + "/gate_whli_strict_1s/gate_audit.json", encoding="utf-8"))
a = json.load(open(out + "/global_audit.json", encoding="utf-8"))
print(out, "| ALNS joint_feasible:", a.get("joint_feasible_found"),
      "| gate:", g.get("status"),
      "| dense outages:", g.get("dense_check", {}).get("outage_samples"),
      "| WT:", a.get("weighted_tardiness_priority_seconds"),
      "| ALNS internal joint makespan:", a.get("joint_makespan_s"),
      "| strict assembled joint makespan:", g.get("assembled", {}).get("joint_makespan_s"))
EOF
done
echo CAMPAIGN_DONE
