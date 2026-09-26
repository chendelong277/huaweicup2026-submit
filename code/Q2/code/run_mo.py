"""Entry point for the mo-r1 Pareto-ALNS experiment (Q2 bi-objective).

Usage:
    python run_mo.py [--data PATH] [--budget 120] [--seeds 0 1 2] [--out DIR]

Writes protocol.json, front.json/front.csv, summary.csv/summary.json and one
seed_<i>/ directory (solution_archive.json, run.json, evaluation.json) per seed.
"""
from __future__ import annotations

import argparse
import csv
import json
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mo_pareto_alns as mo  # installing py38 compat happens at import time

REPO = mo.REPO
ANCHORS = [
    ("WHLi_anchor_1_wt0", 0.0, 9530.2),
    ("WHLi_anchor_2", 35871.1, 9830.1),
    ("WHLi_anchor_3", 46207.0, 10129.8),
    ("E005_alns_plain", 0.0, 6043.32),
]


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list, fields: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def dominates(a, b, eps=1e-8):
    return all(x <= y + eps for x, y in zip(a, b)) and any(x < y - eps for x, y in zip(a, b))


def merged_front(points: list) -> list:
    """Non-dominated subset on (weighted_tardiness_s, makespan_s); dedupe by energy."""
    front = []
    for p in points:
        key = (p["weighted_tardiness_s"], p["makespan_s"])
        if any(dominates((q["weighted_tardiness_s"], q["makespan_s"]), key)
               for q in points if q is not p):
            continue
        front.append(p)
    dedup = {}
    for p in front:
        key = (round(p["weighted_tardiness_s"], 8), round(p["makespan_s"], 8))
        if key not in dedup or p["energy_kwh"] < dedup[key]["energy_kwh"]:
            dedup[key] = p
    return sorted(dedup.values(), key=lambda p: (p["weighted_tardiness_s"], p["makespan_s"]))


def check_pairwise_nondominance(front: list) -> dict:
    bad = []
    for i, a in enumerate(front):
        for j, b in enumerate(front):
            if i != j and dominates((a["weighted_tardiness_s"], a["makespan_s"]),
                                    (b["weighted_tardiness_s"], b["makespan_s"])):
                bad.append([i, j])
    return {"pairwise_nondominated": not bad, "violating_pairs": bad}


def anchor_relations(front: list) -> list:
    rows = []
    for name, wt, ms in ANCHORS:
        ours_dominating = [p for p in front
                           if dominates((p["weighted_tardiness_s"], p["makespan_s"]), (wt, ms))]
        dominated_by_anchor = [p for p in front
                               if dominates((wt, ms), (p["weighted_tardiness_s"], p["makespan_s"]))]
        best = None
        if ours_dominating:
            best = min(ours_dominating,
                       key=lambda p: (p["weighted_tardiness_s"] - wt) + (p["makespan_s"] - ms))
        rows.append(dict(
            anchor=name, anchor_weighted_tardiness_s=wt, anchor_makespan_s=ms,
            relation=("dominated_by_our_front" if ours_dominating
                      else "dominates_some_our_point" if dominated_by_anchor
                      else "incomparable"),
            dominating_point=(dict(weighted_tardiness_s=best["weighted_tardiness_s"],
                                   makespan_s=best["makespan_s"], seed=best["seed"])
                              if best else None),
            our_points_dominated_by_anchor=len(dominated_by_anchor)))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="mo-r1 Pareto-ALNS for Q2")
    parser.add_argument("--data", default=str(REPO / "problem" / "数据"))
    parser.add_argument("--budget", type=float, default=120.0)
    parser.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument("--out", default=str(REPO / "members" / "weiliu" / "Q2" / "results_mo"))
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    load_started = time.perf_counter()
    instance = mo.load_instance(str(Path(args.data)))
    data_load_s = time.perf_counter() - load_started
    warm = mo.load_warm_starts(instance)
    warm_audit_s = 0.0  # included in data_load_s split below
    print("data+warm-start audit: %.1fs; warm starts: %s"
          % (data_load_s, [(w["label"], round(w["weighted_tardiness_s"], 1),
                            round(w["makespan_s"], 1)) for w in warm]))

    protocol = dict(
        version=mo.VERSION,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        python=platform.python_version(),
        data_dir=str(Path(args.data)),
        budget_s_per_seed=args.budget, seeds=args.seeds,
        data_load_and_warm_audit_s=round(data_load_s, 3),
        objectives=dict(minimize=["weighted_tardiness_s", "makespan_s"],
                        weighted_tardiness_note="普通物资期望送达时间为软目标（加权延误）；"
                                                "医疗/首批等原始硬截止仍为硬约束，由 base Decoder 拒绝违反者"),
        decoder="members/chendelong/pipeline/algorithms/q2_search.py::Decoder (base, 未修改)",
        controller=dict(operators=mo.OPERATORS, weight_smoothing=mo.WEIGHT_SMOOTHING,
                        rewards=dict(dominating_insert=mo.REWARD_DOMINATING_INSERT,
                                     nondominated_insert=mo.REWARD_NDOM_INSERT,
                                     accepted=mo.REWARD_ACCEPTED, rejected=mo.REWARD_REJECTED),
                        annealing=dict(scale=list(mo.SCALE), temp0=mo.TEMP0, temp_min=mo.TEMP_MIN),
                        archive_limit=mo.ARCHIVE_LIMIT, stagnation_limit=mo.STAGNATION_LIMIT,
                        max_stops_per_trip=mo.MAX_STOPS_PER_TRIP),
        warm_starts=[dict(label=w["label"], path=str(Path(w["path"]).relative_to(REPO)),
                          sha256=w["sha256"], weighted_tardiness_s=w["weighted_tardiness_s"],
                          makespan_s=w["makespan_s"]) for w in warm],
        scope_statement="Q2 纯运输调度：不含 Q3 通信连续性验证；暖启动解的生成成本不计入本实验预算。",
    )
    write_json(out / "protocol.json", protocol)

    warm_tasks = [(w["label"], mo.tasks_of(instance, w["solution"])) for w in warm]
    all_points, summary = [], dict(seeds=[])
    for seed in args.seeds:
        run = mo.run_seed(instance, warm_tasks, seed, args.budget)
        archive_rows = mo.materialize_archive(run)
        evaluations = []
        for row in archive_rows:
            solution = dict(schema_version="1", question="Q2", trips=row["trips"])
            ev = mo.evaluate_transport(instance, solution)
            if ev["feasible"] is not True:
                raise RuntimeError("seed %d archive point failed independent evaluation: %s"
                                   % (seed, ev["violations"][:3]))
            unique_ok = ev["metrics"]["delivered_box_count"] == len(instance.data["boxes"])
            if not unique_ok:
                raise RuntimeError("seed %d archive point does not deliver all boxes exactly once" % seed)
            evaluations.append(dict(weighted_tardiness_s=row["weighted_tardiness_s"],
                                    makespan_s=row["makespan_s"], feasible=ev["feasible"],
                                    violations=ev["violations"], metrics=ev["metrics"],
                                    all_80_boxes_delivered_once=unique_ok))
        seed_dir = out / ("seed_%d" % seed)
        write_json(seed_dir / "solution_archive.json",
                   dict(seed=seed, version=mo.VERSION, archive=archive_rows))
        stats = dict(run["stats"])
        stats.pop("trace", None)
        write_json(seed_dir / "run.json", dict(stats, trace=run["stats"]["trace"]))
        write_json(seed_dir / "evaluation.json",
                   dict(seed=seed, evaluator="pipeline.evaluation.evaluate_transport",
                        all_feasible=all(e["feasible"] for e in evaluations), points=evaluations))
        for row in archive_rows:
            all_points.append(dict(seed=seed, **{k: row[k] for k in (
                "weighted_tardiness_s", "makespan_s", "energy_kwh", "sorties", "origin")}))
        summary["seeds"].append(dict(
            seed=seed, budget_s=args.budget, elapsed_s=round(run["stats"]["elapsed_s"], 3),
            iterations=run["stats"]["iterations"], evaluations=run["stats"]["evaluations"],
            restarts=run["stats"]["restarts"], archive_size=len(archive_rows),
            min_weighted_tardiness_s=min(r["weighted_tardiness_s"] for r in archive_rows),
            min_makespan_s=min(r["makespan_s"] for r in archive_rows),
            final_weights={op: run["stats"]["operator_stats"][op]["final_weight"]
                           for op in mo.OPERATORS}))
        print("seed %d done: %d iterations, %d evaluations, archive %d, WT in [%.1f, %.1f], "
              "makespan in [%.1f, %.1f]"
              % (seed, run["stats"]["iterations"], run["stats"]["evaluations"], len(archive_rows),
                 min(r["weighted_tardiness_s"] for r in archive_rows),
                 max(r["weighted_tardiness_s"] for r in archive_rows),
                 min(r["makespan_s"] for r in archive_rows),
                 max(r["makespan_s"] for r in archive_rows)))

    front = merged_front(all_points)
    for p in front:
        p["on_merged_front"] = True
    front_keys = {(round(p["weighted_tardiness_s"], 8), round(p["makespan_s"], 8), p["seed"])
                  for p in front}
    for p in all_points:
        p["on_merged_front"] = (round(p["weighted_tardiness_s"], 8),
                                round(p["makespan_s"], 8), p["seed"]) in front_keys
    nondom_check = check_pairwise_nondominance(front)
    anchors = anchor_relations(front)
    write_json(out / "front.json",
               dict(version=mo.VERSION, seeds=args.seeds, point_count=len(front),
                    dominance_check=nondom_check, anchor_relations=anchors, front=front))
    fields = ["weighted_tardiness_s", "makespan_s", "energy_kwh", "sorties", "seed", "origin"]
    write_csv(out / "front.csv", [{k: p[k] for k in fields} for p in front], fields)
    write_csv(out / "summary.csv", [{k: p[k] for k in fields + ["on_merged_front"]}
                                    for p in sorted(all_points, key=lambda p: (
                                        p["seed"], p["weighted_tardiness_s"], p["makespan_s"]))],
              fields + ["on_merged_front"])
    summary["merged"] = dict(archive_points_total=len(all_points), front_points=len(front),
                             dominance_check=nondom_check,
                             min_weighted_tardiness_s=min(p["weighted_tardiness_s"] for p in front),
                             min_makespan_s=min(p["makespan_s"] for p in front),
                             anchor_relations=anchors)
    write_json(out / "summary.json", dict(version=mo.VERSION, **summary))
    print("merged front: %d points, pairwise nondominance: %s"
          % (len(front), nondom_check["pairwise_nondominated"]))
    for p in front:
        print("  WT=%12.1f  makespan=%9.2f  energy=%7.2f  sorties=%2d  seed=%d"
              % (p["weighted_tardiness_s"], p["makespan_s"], p["energy_kwh"],
                 p["sorties"], p["seed"]))


if __name__ == "__main__":
    main()
