from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from q1_solver import audit_batches, build_leg, load_inputs


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main(data_root: Path, results_dir: Path) -> None:
    nodes, vehicles, boxes_by_service, dem, _ = load_inputs(data_root)
    depot = nodes["O01"]
    legs = {}
    for service in sorted(boxes_by_service):
        target = nodes[service]
        legs[("O01", service)] = build_leg(
            depot, target, depot.ground_elevation_m, target.ground_elevation_m + 30.0, dem
        )
        legs[(service, "O01")] = build_leg(
            target, depot, target.ground_elevation_m + 30.0, depot.ground_elevation_m, dem
        )

    box_rows = read_rows(results_dir / "batching_baseline.csv")
    summary_rows = {row["batch_id"]: row for row in read_rows(results_dir / "batch_summary.csv")}
    grouped = {}
    for row in box_rows:
        grouped.setdefault(row["batch_id"], []).append(row["box_id"])
    batches = []
    for batch_id, box_ids in sorted(grouped.items()):
        row = summary_rows[batch_id]
        batches.append(
            {
                "batch_id": batch_id,
                "service_node": row["service_node"],
                "vehicle_type": row["vehicle_type"],
                "box_ids": box_ids,
                "batch_total_mass_kg": float(row["batch_total_mass_kg"]),
                "batch_total_volume_m3": float(row["batch_total_volume_m3"]),
                "energy_total_kwh": float(row["batch_energy_kwh"]),
                "operation_time_s": float(row["operation_time_s"]),
            }
        )
    audit_rows, global_audit = audit_batches(batches, boxes_by_service, vehicles, legs, 0.2)
    global_audit["checked_batch_count"] = len(audit_rows)
    print(json.dumps(global_audit, ensure_ascii=False, indent=2))
    if not global_audit["overall_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, required=True)
    args = parser.parse_args()
    main(args.data_root.resolve(), args.results_dir.resolve())
