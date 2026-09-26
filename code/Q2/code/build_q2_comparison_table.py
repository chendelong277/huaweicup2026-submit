"""Re-evaluate archived Q2 solutions with the compact-workspace evaluator.

The archived chendelong solutions are selected by their recorded
lexicographic score within each method, then all selected solutions are
re-decoded by ``q2_solver_v2.py`` before the comparison CSV is written.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from q2_comparison import load_context, q2


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def overlap(intervals):
    xs = sorted(intervals)
    return any(xs[i][0] < xs[i - 1][1] - 1e-8 for i in range(1, len(xs)))


def evaluate_solution(ctx, path: Path):
    if path.name == "trip_summary.csv":
        trips = q2.load_warm_start(path, ctx.box_map, ctx.vehicles, ctx.legs)
    else:
        trips = q2.load_external_solution(path, ctx.box_map, ctx.vehicles, ctx.legs)
    if not trips:
        return {"feasible": False, "source": str(path), "error": "solution adapter rejected input"}
    order = list(trips)
    score, rows, deliveries, resources, assigned, audit = q2.schedule_eval(
        trips, order, ctx.vehicles, ctx.uavs, ctx.batteries, ctx.box_map, ctx.legs)
    payload_ok = all(float(t.mass_kg) <= ctx.vehicles[t.vehicle_type].max_payload_kg + 1e-8
                     for t in trips)
    volume_ok = all(float(t.volume_m3) <= ctx.vehicles[t.vehicle_type].max_volume_m3 + 1e-8
                    for t in trips)
    energy_ok = all(float(x[-1]) >= q2.RESERVE - 1e-8 for x in assigned)
    by_res = defaultdict(list)
    for row in resources:
        by_res[(row["resource_type"], row["resource_id"])].append(
            (float(row["start_time_s"]), float(row["end_time_s"])))
    resource_ok = all(not overlap(v) for v in by_res.values())
    charge_ok = True
    for (typ, rid), ints in by_res.items():
        if typ != "battery":
            continue
        vt = rid.split("-")[0]
        full = ctx.batteries[vt]["full_charge_s"]
        for row in resources:
            if row["resource_type"] != typ or row["resource_id"] != rid:
                continue
            if row["activity"] == "charging":
                charge_ok = charge_ok and abs(
                    (float(row["end_time_s"]) - float(row["start_time_s"])) -
                    q2.charge_time(float(row["soc_before"]), full)
                ) < 1e-6
    return {
        "source": str(path), "feasible": bool(audit["overall_pass"]),
        "box_unique": bool(audit["delivered_unique"]), "route_closed": bool(audit["route_closed"]),
        "payload_ok": payload_ok, "volume_ok": volume_ok, "energy_reserve_ok": energy_ok,
        "resource_nonoverlap": resource_ok, "charging_ok": charge_ok,
        "hard_deadline_violations": audit["hard_deadline_violations"],
        "delivered_box_count": audit["box_count"], "expected_time_misses": audit["expected_time_misses"],
        "on_time_box_ratio": 1.0 - audit["expected_time_misses"] / max(1, audit["box_count"]),
        "weighted_tardiness_s": audit["weighted_tardiness_priority_seconds"],
        "makespan_s": audit["makespan_s"], "trip_count": audit["trip_count"],
        "energy_kwh": audit["total_energy_kwh"], "score": list(score),
    }


def source_groups(project_root: Path):
    ch = project_root / "huaweicup-2026" / "members" / "chendelong"
    groups = {
        "greedy": [ch / "results" / "Q2" / "solution.json"],
        "q2_order": sorted((ch / "experiments" / "e001" / "q2_order").glob("seed_*/solution.json")),
        "q2_full": sorted((ch / "experiments" / "e001" / "q2_full").glob("seed_*/solution.json")),
        "sa": sorted((ch / "experiments" / "e007" / "sa").glob("seed_*/solution.json")),
        "ils": sorted((ch / "experiments" / "e007" / "ils").glob("seed_*/solution.json")),
        "vns": sorted((ch / "experiments" / "e007" / "vns").glob("seed_*/solution.json")),
        "memetic": sorted((ch / "experiments" / "e007" / "memetic").glob("seed_*/solution.json")),
        "hill": sorted((ch / "experiments" / "e007" / "hill").glob("seed_*/solution.json")),
    }
    hd = project_root / "AAA-2026华为杯-精简" / "weiliu" / "Q2" / "results_v2" / "trip_summary.csv"
    groups["HD-ALNS"] = [hd]
    return groups


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ctx = load_context(args.data_root)
    rows = []
    for method, paths in source_groups(args.project_root).items():
        candidates = [evaluate_solution(ctx, p) for p in paths if p.exists()]
        if not candidates:
            continue
        chosen = min(candidates, key=lambda x: tuple(x.get("score", [10**9] * 5)))
        chosen["algorithm"] = method
        chosen["candidate_count"] = len(candidates)
        rows.append(chosen)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({k for row in rows for k in row})
    with args.output.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    args.output.with_suffix(".json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
