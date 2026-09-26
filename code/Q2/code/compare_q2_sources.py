"""Read-only metric reconciliation for the two Q2 plans; run from project root."""

import csv
import json
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook


ROOT = Path.cwd()
BASE = ROOT / "# D-项目文件夹-LW" / "Solution" / "Q2" / "results"
PEER = ROOT / "huaweicup-2026" / "members" / "WHLi" / "experiments" / "q2_multipoint_best_v2.json"
DEMAND = ROOT / "# D-项目文件夹-LW" / "D题题目" / "数据" / "无人机应急物资运输基础数据" / "物资需求与配送时限.xlsx"


def read_csv(name):
    with (BASE / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


raw = list(load_workbook(DEMAND, read_only=True, data_only=True)["逐箱货箱清单"].values)
demand = {r[0]: {"site": r[1], "kind": r[2], "first": r[5] == "是",
                 "first_deadline": r[6], "expected": r[7], "priority": r[8]}
          for r in raw[1:] if r[0]}
current_trips = read_csv("trip_summary.csv")
current_delivery_rows = read_csv("delivery_timeline.csv")
current_deliveries = {r["box_id"]: float(r["delivery_time_s"]) for r in current_delivery_rows}
peer = json.loads(PEER.read_text(encoding="utf-8"))
peer_deliveries = {}
peer_assignment_count = Counter()
for trip in peer["trips"]:
    for box_id in trip["boxes"]:
        peer_assignment_count[box_id] += 1
        peer_deliveries[box_id] = float(trip["per_stop_deliveries"][demand[box_id]["site"]])


def summarize(deliveries, trips, assignment_count, peer_format=False):
    assert set(deliveries) == set(demand), (len(deliveries), len(demand))
    assert all(assignment_count[b] == 1 for b in demand)
    late = []
    late_details = []
    hard = []
    weighted = 0.0
    for box_id, d in demand.items():
        delivered = deliveries[box_id]
        delay = max(0.0, delivered - float(d["expected"]))
        weighted += float(d["priority"]) * delay
        if delay > 1e-6:
            late.append(box_id)
            late_details.append({"box_id": box_id, "delay_s": delay,
                                 "weighted_delay": float(d["priority"]) * delay})
        if d["first"] and delivered > float(d["first_deadline"]) + 1e-6:
            hard.append(box_id)
        if d["kind"] == "医疗物资" and delivered > float(d["expected"]) + 1e-6:
            hard.append(box_id)
    if peer_format:
        energy = sum(float(t["energy"]) for t in trips)
        finish = max(float(t["return_time"]) for t in trips)
        models = Counter(t["model"] for t in trips)
        multipoint = sum(len(t["route"]) > 1 for t in trips)
        vehicles = sorted(set(t["drone"] for t in trips))
    else:
        energy = sum(float(t["energy_kwh"]) for t in trips)
        finish = max(float(t["end_time_s"]) for t in trips)
        models = Counter(t["vehicle_type"] for t in trips)
        multipoint = sum(len(t["route"].split("->")) > 3 for t in trips)
        vehicles = sorted(set(t["vehicle_id"] for t in trips))
    return {"boxes": len(deliveries), "trips": len(trips), "weighted_tardiness": weighted,
            "late_boxes": len(late), "late_box_ids": sorted(late),
            "late_details": sorted(late_details, key=lambda x: x["box_id"]),
            "hard_deadline_violations": len(set(hard)),
            "hard_box_ids": sorted(set(hard)), "makespan_s": finish, "energy_kwh": energy,
            "models": dict(sorted(models.items())), "multipoint_trips": multipoint,
            "vehicles_used": vehicles}


current_count = Counter(r["box_id"] for r in current_delivery_rows)
current = summarize(current_deliveries, current_trips, current_count)
html_plan = summarize(peer_deliveries, peer["trips"], peer_assignment_count, True)
delta = {k: current[k] - html_plan[k] for k in
         ["trips", "weighted_tardiness", "late_boxes", "makespan_s", "energy_kwh", "multipoint_trips"]}
percent = {k: delta[k] / html_plan[k] * 100 for k in
           ["trips", "weighted_tardiness", "makespan_s", "energy_kwh"]}
print(json.dumps({"current": current, "html_v2": html_plan, "delta_current_minus_html": delta,
                  "percent_current_minus_html": percent,
                  "peer_json_matches_report": {"wt": abs(html_plan["weighted_tardiness"] - peer["weighted_tardiness"]) < 1e-4,
                                                "energy": abs(html_plan["energy_kwh"] - peer["energy"]) < 1e-6,
                                                "makespan": abs(html_plan["makespan_s"] - peer["makespan"]) < 1e-6}},
                 ensure_ascii=False, indent=2))
