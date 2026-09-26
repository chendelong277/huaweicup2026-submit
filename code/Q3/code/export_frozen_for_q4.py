"""Export an ALNS-Q3-V2 solution directory as a Q4 frozen-input bundle.

Converts the weiliu Q3 transport outputs and the strictly re-assembled WHLi
gate relay solution (not the five-point ALNS relay plan) into the
frozen CSV layout that members/weiliu/Q4/code/q4_core.load_frozen expects
(transport_trips.csv / relay_plan.csv / communication_links.csv /
delivery_timeline.csv / audit_freeze.json).

Usage:
    python export_frozen_for_q4.py --data-root <problem/数据> \
        --solution-dir <seed dir> --output-dir <frozen dir>
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


q2 = _load("whli_q2_v2_freeze", HERE.parent.parent / "Q2" / "code" / "q2_solver_v2.py")


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--solution-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--gate-dir", type=Path,
                    help="defaults to SOLUTION_DIR/gate_whli_strict_1s")
    args = ap.parse_args()
    src = args.solution_dir
    out = args.output_dir
    gate_dir = args.gate_dir or src / "gate_whli_strict_1s"
    gate = json.loads((gate_dir / "gate_audit.json").read_text(encoding="utf-8"))
    if gate.get("status") != "fully_audited_feasible" or not gate.get(
            "continuity_closure", {}).get("continuous_feasible"):
        raise ValueError("Q3 solution has not passed the strict 1 s gate and continuity closure")
    if gate.get("dense_check_step_s") != 1.0 or gate.get(
            "dense_check", {}).get("outage_samples") != 0:
        raise ValueError("strict gate did not pass the requested 1 s dense check")
    assembled = gate["assembled"]
    out.mkdir(parents=True, exist_ok=True)

    (_, _, box_map, _, _, _, _, _) = q2.load_entities(args.data_root)

    # Transport trips: numeric ids in dispatch (start-time) order.
    trip_rows = read_csv(src / "trip_summary.csv")
    # The gate numbers trips in original trip_summary.csv row order, not by
    # dispatch start time; preserving this order is essential for covers_trips.
    trip_no = {r["trip_id"]: i + 1 for i, r in enumerate(trip_rows)}
    trips = []
    for r in trip_rows:
        trips.append({
            "trip_id": trip_no[r["trip_id"]],
            # Frozen format carries service nodes only; the depot O01 is
            # implied (matches members/WHLi/Q4/results/frozen convention and
            # keeps q4_core.build_components union-find on service areas).
            "route": ">".join(s for s in r["route"].split("->") if s != "O01"),
            "model": r["vehicle_type"],
            "drone_id": r["vehicle_id"],
            "battery_id": r["battery_id"],
            "start_time_s": r["start_time_s"],
            "return_time_s": r["end_time_s"],
            "energy_kwh": r["energy_kwh"],
            "soc_end": r["soc_end"],
            "box_ids": r["box_ids"],
        })
    write_csv(out / "transport_trips.csv", trips,
              ["trip_id", "route", "model", "drone_id", "battery_id",
               "start_time_s", "return_time_s", "energy_kwh", "soc_end",
               "box_ids"])

    # All relay assignments and blind intervals come from the strict gate,
    # not the ALNS five-point communication_audit.csv.
    gate_trips = json.loads((gate_dir / "q2_transport_whli.json").read_text(
        encoding="utf-8"))["trips"]
    gate_map = {int(r["trip"]): r for r in gate_trips}
    if len(gate_trips) != len(trips) or len(assembled["communications"]) != assembled["blind_transport_trips"]:
        raise ValueError("gate and transport trip counts disagree")
    for i, r in enumerate(trip_rows, 1):
        gt = gate_map[i]
        if (sorted(gt["boxes"]) != sorted(r["box_ids"].split(";"))
                or gt["route"] != r["route"].split("->")[1:-1]
                or abs(float(gt["start"]) - float(r["start_time_s"])) > 1e-6):
            raise ValueError("strict gate used different Q3 transport trip %d" % i)
    links = [{"trip_id": r["transport_trip"],
              "blind_start_s": r["blind_start"],
              "blind_end_s": r["blind_end"],
              "relay_task_id": r["relay_sortie"]}
             for r in assembled["communications"]]
    write_csv(out / "communication_links.csv", links,
              ["trip_id", "blind_start_s", "blind_end_s", "relay_task_id"])

    relay_rows = [{"relay_task_id": r["sortie_id"],
                   "relay_id": r["relay_drone"],
                   "depart_time_s": r["launch"],
                   "service_start_s": r["service_start"],
                   "service_end_s": r["service_end"],
                   "return_time_s": r["return_time"],
                   "ready_time_s": r["ready"],
                   "energy_kwh": r["energy"],
                   "soc_end": float(r["soc"]) / 100.0,
                   "covers_trips": ";".join(r["covers"])}
                  for r in assembled["relay_sortie_plan"]]
    write_csv(out / "relay_plan.csv", relay_rows,
              ["relay_task_id", "relay_id", "depart_time_s", "service_start_s",
               "service_end_s", "return_time_s", "ready_time_s", "energy_kwh",
               "soc_end", "covers_trips"])

    deliv = [{"box_id": r["box_id"], "service_node": r["service_node"],
              "box_mass_kg": f"{box_map[r['box_id']].mass_kg:.6f}"}
             for r in read_csv(src / "delivery_timeline.csv")]
    write_csv(out / "delivery_timeline.csv", deliv,
              ["box_id", "service_node", "box_mass_kg"])

    ga = json.loads((src / "global_audit.json").read_text(encoding="utf-8"))
    meta = {
        "weighted_tardiness": ga.get("weighted_tardiness_priority_seconds", 0.0),
        "makespan_s": max(float(t["return_time_s"]) for t in trips),
        "joint_makespan_s": assembled["joint_makespan_s"],
        "transport_energy_kwh": assembled["transport_energy_kwh"],
        "relay_energy_kwh": assembled["relay_energy_kwh"],
        "status": "frozen_weiliu_alns_q3_v2_strict_gate",
        "source_dir": src.as_posix(),
        "strict_gate_dir": gate_dir.as_posix(),
        "random_seed": ga.get("random_seed"),
        "hard_relay_cap": ga.get("hard_relay_cap"),
        "strict_gate_status": gate["status"],
        "dense_check_step_s": gate["dense_check_step_s"],
        "continuity_closure_feasible": gate["continuity_closure"]["continuous_feasible"],
    }
    (out / "audit_freeze.json").write_text(
        json.dumps({"meta": meta}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(json.dumps({"frozen_dir": str(out), "trips": len(trips),
                      "relay_sorties": len(relay_rows),
                      "comm_links": len(links)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
