"""Visualize an ALNS-Q3-V2 solution with WHLi's Q3 map/timeline drawing code.

Reuses (read-only) the WHLi plotting helpers from
``members/WHLi/code/plot_q2q3_maps.py`` (hillshaded DEM base map with
roads/rivers/waters/villages, route drawing, scale bar, colour scheme) and the
WHLi data loader ``engine.load_all``.  Trajectory samples and communication
modes come from this module's own audited pipeline (``q3_solver``), so the
drawn blind windows match the solution's ``communication_audit.csv`` exactly.

Outputs (default ``members/weiliu/Q3/figures_alns_q3_v2/``):
- fig_q2_route_map_alns_q3_v2.png      transport routes over the geodata base
- fig_q3_comm_map_alns_q3_v2.png       relay stations, relay-covered segments, backhaul links
- fig_q3_comm_timeline_alns_q3_v2.png  per-sortie direct/relay intervals

Usage:
    python plot_alns_q3_v2_maps.py --solution-dir <results_alns_q3_v2_600s/seed_x>
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
WH_CODE = REPO / "members" / "WHLi" / "code"
sys.path.insert(0, str(WH_CODE))

import plot_q2q3_maps as whp  # noqa: E402  (WHLi drawing helpers)
from engine import load_all   # noqa: E402  (WHLi contest-data loader)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


q3 = _load("whli_q3_solver_plot", HERE / "q3_solver.py")

RS_IDS = ["RS01", "RS02", "RS03", "RS04", "RS05", "RS06"]


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def build_slices(data_root: Path, sol_dir: Path):
    """Rebuild the exact trajectory samples the audit was generated from."""
    nodes, dem, _ = q3.load_nodes(data_root)
    legs = {}
    for a in nodes.values():
        for b in nodes.values():
            if a.node_id != b.node_id:
                legs[(a.node_id, b.node_id)] = q3.q1.build_leg(
                    a, b, q3.op_alt(nodes, a.node_id), q3.op_alt(nodes, b.node_id), dem)
    bytrip = defaultdict(list)
    for r in read_csv(sol_dir / "transport_plan.csv"):
        bytrip[r["trip_id"]].append(r)
    slices = {}
    for trip_id, rows in bytrip.items():
        rows = sorted(rows, key=lambda r: int(r["sequence_no"]))
        slices[trip_id] = q3.all_time_slices(q3.build_trajectory(rows, nodes, legs),
                                             nodes, legs)
    return slices


def relay_runs(sol_dir: Path, slices_by_trip):
    """Group consecutive relay-mode audit slices into drawable polyline runs."""
    audit = defaultdict(list)
    for r in read_csv(sol_dir / "communication_audit.csv"):
        audit[r["trip_id"]].append(r)
    runs = []  # (relay_task_id, [(lon, lat), ...], t0, t1, trip_id)
    for trip_id, rows in audit.items():
        slices = slices_by_trip[trip_id]
        assert len(rows) == len(slices), (trip_id, len(rows), len(slices))
        cur_id, cur_pts, cur_t = None, [], None
        for r, (tm, ep, _meta) in zip(rows, slices):
            assert abs(float(r["time_start_s"]) - tm) < 1.0, (trip_id, r["time_start_s"], tm)
            rid = r["relay_task_id"]
            if rid:
                if rid != cur_id:
                    if cur_id:
                        runs.append((cur_id, cur_pts, cur_t, prev_t, trip_id))
                    cur_id, cur_pts, cur_t = rid, [], tm
                cur_pts.append((ep.lon, ep.lat))
                prev_t = tm
            elif cur_id:
                runs.append((cur_id, cur_pts, cur_t, prev_t, trip_id))
                cur_id = None
        if cur_id:
            runs.append((cur_id, cur_pts, cur_t, prev_t, trip_id))
    return audit, runs


def rs_color_map(relay_rows):
    tasks = sorted({r["relay_task_id"] for r in relay_rows})
    return {t: whp.RS_COLOR[RS_IDS[i % len(RS_IDS)]] for i, t in enumerate(tasks)}, \
           {t: RS_IDS[i % len(RS_IDS)] for i, t in enumerate(tasks)}


def fig_route_map(nodes, boxes, demand, trips, deliveries, dem_whl, geo, extent,
                  audit_summary, out: Path):
    fig, ax = plt.subplots(figsize=(12.5, 10))
    whp.draw_base(ax, dem_whl, geo, extent)
    for t in trips:
        pts = [nodes[s] for s in t["route_full"]]
        whp.draw_route(ax, pts, whp.MODEL_COLOR[t["vehicle_type"]], 1.7, 0.85, 5, arrow=True)
    first_deliver = {}
    for d in deliveries:
        s = d["service_node"]
        first_deliver[s] = min(first_deliver.get(s, 1e18), float(d["delivery_time_s"]))
    for s, (x, y, _) in nodes.items():
        if not s.startswith("S"):
            continue
        n_box = len(boxes[s])
        hard = any(demand[b["id"]]["first"] or demand[b["id"]]["medical"] for b in boxes[s])
        ax.scatter(x, y, s=26 + 22 * n_box, facecolor="white",
                   edgecolor="#C0392B" if hard else "#333333",
                   linewidth=2.0 if hard else 1.0, zorder=6)
        ax.annotate(f"{s}\n首达{first_deliver[s] / 60:.1f}分", (x, y),
                    xytext=(7, 5), textcoords="offset points", fontsize=8.5,
                    fontweight="bold", zorder=7,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.65))
    o = nodes["O01"]
    ax.scatter(*o[:2], marker="*", s=340, c="#FFD700", edgecolor="k", lw=1.2, zorder=8)
    ax.annotate("O01 调度中心 / G01 网关", o[:2], xytext=(8, -12),
                textcoords="offset points", fontsize=10, fontweight="bold", zorder=8,
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="k", alpha=0.85, lw=0.6))
    handles = [Line2D([], [], color=c, lw=2.5, label=f"{m} 型运输无人机")
               for m, c in whp.MODEL_COLOR.items()]
    handles += [Line2D([], [], marker="o", ls="", mfc="white", mec="#C0392B", mew=2, ms=10,
                       label="服务区（红边=含硬时限箱，大小∝箱数）"),
                Line2D([], [], marker="*", ls="", mfc="#FFD700", mec="k", ms=16, label="O01 / G01")]
    ax.legend(handles=handles, loc="lower right", fontsize=9.5, framealpha=0.92)
    ax.set_title(f"ALNS-Q3-V2 运输方案航线图：{audit_summary['transport_sorties']} 架次 · "
                 f"运输完工 {audit_summary['transport_makespan_s']:.1f} s · 硬时限 0 违约 · "
                 f"运输能耗 {audit_summary['transport_energy_kwh']:.2f} kWh",
                 fontsize=13, pad=10)
    whp.scale_bar(ax)
    fig.tight_layout()
    fig.savefig(out / "fig_q2_route_map_alns_q3_v2.png", dpi=200)
    plt.close(fig)


def fig_comm_map(nodes, trips, relay_rows, runs, rs_color, rs_name, dem_whl, geo,
                 extent, audit_summary, out: Path):
    fig, ax = plt.subplots(figsize=(12.5, 10))
    whp.draw_base(ax, dem_whl, geo, extent)
    for t in trips:
        pts = [nodes[s] for s in t["route_full"]]
        whp.draw_route(ax, pts, "#555555", 1.0, 0.4, 4)
    relay_pos = {}
    for r in relay_rows:
        relay_pos[r["relay_task_id"]] = (float(r["x"]), float(r["y"]),
                                         float(r["hover_height_m"]))
    for rid, pts, t0, t1, trip_id in runs:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ax.plot(xs, ys, color=rs_color[rid], lw=3.2, solid_capstyle="round", zorder=6)
        ax.scatter(xs, ys, s=14, c=rs_color[rid], zorder=6)
        mid = pts[len(pts) // 2]
        rp = relay_pos[rid]
        ax.plot([mid[0], rp[0]], [mid[1], rp[1]], color=rs_color[rid], lw=0.7,
                alpha=0.55, zorder=5)
    o = nodes["O01"]
    for r in relay_rows:
        rid = r["relay_task_id"]
        x, y, h = relay_pos[rid]
        ax.plot([x, o[0]], [y, o[1]], color="k", lw=1.3, ls=(0, (6, 4)), alpha=0.75, zorder=5)
        ax.scatter(x, y, marker="^", s=260, c=rs_color[rid], edgecolor="k", lw=1.2, zorder=8)
        ax.annotate(f"{rs_name[rid]}（{r['relay_id']}）\nAGL {h:.0f} m", (x, y),
                    xytext=(9, 6), textcoords="offset points", fontsize=9.5,
                    fontweight="bold", zorder=9,
                    bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=rs_color[rid], alpha=0.9))
    ax.scatter(*o[:2], marker="*", s=340, c="#FFD700", edgecolor="k", lw=1.2, zorder=8)
    ax.annotate("O01 调度中心 / G01 网关", o[:2], xytext=(8, -12),
                textcoords="offset points", fontsize=10, fontweight="bold", zorder=8,
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="k", alpha=0.85, lw=0.6))
    handles = [Line2D([], [], color=rs_color[t], lw=3.2,
                      label=f"{rs_name[t]} 中继保障航段（{t}）") for t in sorted(rs_color)]
    handles += [Line2D([], [], color="#555555", lw=1.0, alpha=0.5,
                       label=f"直连航段（全部 {audit_summary['transport_sorties']} 架次）"),
                Line2D([], [], color="k", lw=1.3, ls="--", label="中继回传链路 → G01"),
                Line2D([], [], marker="^", ls="", mfc="#888888", mec="k", ms=13,
                       label="中继悬停点")]
    ax.legend(handles=handles, loc="upper left", fontsize=9.5, framealpha=0.92)
    ax.set_title(f"ALNS-Q3-V2 通信保障部署：中继 2 机 {audit_summary['relay_sorties']} 架次 · "
                 f"{audit_summary['communication_slices']} 个采样时间片 0 空档（5 点/航段口径）",
                 fontsize=13, pad=10)
    whp.scale_bar(ax)
    fig.tight_layout()
    fig.savefig(out / "fig_q3_comm_map_alns_q3_v2.png", dpi=200)
    plt.close(fig)


def fig_comm_timeline(trips, audit, relay_rows, rs_color, rs_name, audit_summary, out: Path):
    trips = sorted(trips, key=lambda t: t["start_time_s"])
    n = len(trips)
    fig, ax = plt.subplots(figsize=(13.5, 9.5))
    for row, t in enumerate(trips):
        y = n - row
        ax.barh(y, t["end_time_s"] - t["start_time_s"], left=t["start_time_s"],
                height=0.62, color=whp.DIRECT_COLOR, edgecolor="white", lw=0.3, zorder=3)
        # overlay relay-covered runs (consecutive relay-mode samples)
        rows = audit[t["trip_id"]]
        cur_id, cur_t0, prev_t = None, None, None
        for r in rows:
            rid = r["relay_task_id"]
            tm = float(r["time_start_s"])
            if rid:
                if rid != cur_id:
                    if cur_id:
                        ax.barh(y, prev_t - cur_t0, left=cur_t0, height=0.62,
                                color=rs_color[cur_id], edgecolor="white", lw=0.3, zorder=4)
                    cur_id, cur_t0 = rid, tm
                prev_t = tm
            elif cur_id:
                ax.barh(y, prev_t - cur_t0, left=cur_t0, height=0.62,
                        color=rs_color[cur_id], edgecolor="white", lw=0.3, zorder=4)
                cur_id = None
        if cur_id:
            ax.barh(y, prev_t - cur_t0, left=cur_t0, height=0.62,
                    color=rs_color[cur_id], edgecolor="white", lw=0.3, zorder=4)
        ax.text(t["start_time_s"] - 120, y, t["trip_id"].replace("Q2-T", "T"),
                ha="right", va="center", fontsize=8.5)
    for k, r in enumerate(sorted(relay_rows, key=lambda x: float(x["depart_time_s"]))):
        rid = r["relay_task_id"]
        y = n + 2 + (len(relay_rows) - 1 - k) * 1.2
        launch, ret = float(r["depart_time_s"]), float(r["return_time_s"])
        ss, se = float(r["service_start_s"]), float(r["service_end_s"])
        ax.barh(y, ret - launch, left=launch, height=0.62,
                color=rs_color[rid], alpha=0.28, zorder=2)
        ax.barh(y, se - ss, left=ss, height=0.62, color=rs_color[rid], zorder=3)
        ax.text(launch - 120, y, f"{rs_name[rid]}（{r['relay_id']}）",
                ha="right", va="center", fontsize=9, fontweight="bold")
    joint = audit_summary["joint_makespan_s"]
    ax.axvline(joint, color="k", ls="--", lw=1.2, zorder=4)
    ax.text(joint, n + 2.9, f"联合完工 {joint:.1f} s", ha="right", fontsize=9.5,
            fontweight="bold", bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="k", lw=0.5))
    ax.set_yticks([])
    ax.set_xlim(-900, joint * 1.04)
    ax.set_xlabel("自方案起始时刻（s）")
    ax.set_title(f"ALNS-Q3-V2 通信保障时间线：{n} 个运输架次全程无通信空档（采样口径）\n"
                 "（浅色段 = 中继往返/待命，深色段 = 中继在位服务窗口）", fontsize=13, pad=10)
    ax.grid(axis="x", alpha=0.3, zorder=0)
    handles = [Patch(fc=whp.DIRECT_COLOR, label="直连 G01")]
    handles += [Patch(fc=rs_color[t], label=f"{rs_name[t]} 中继保障") for t in sorted(rs_color)]
    ax.legend(handles=handles, loc="upper left", fontsize=9.5, framealpha=0.92, ncol=4)
    fig.tight_layout()
    fig.savefig(out / "fig_q3_comm_timeline_alns_q3_v2.png", dpi=200)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-root", type=Path, default=REPO / "problem" / "数据")
    p.add_argument("--solution-dir", type=Path,
                   default=HERE.parent / "results_alns_q3_v2_600s" / "seed_20260926")
    p.add_argument("--output-dir", type=Path, default=HERE.parent / "figures_alns_q3_v2")
    a = p.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=True)
    whp.GEO = str(a.data_root / "镇龙乡地理空间数据" / "镇龙乡及周边地理数据")

    audit_summary = json.loads((a.solution_dir / "global_audit.json").read_text(encoding="utf-8"))
    audit_summary["communication_slices"] = sum(
        1 for _ in read_csv(a.solution_dir / "communication_audit.csv"))
    audit_summary["transport_makespan_s"] = max(
        float(r["end_time_s"]) for r in read_csv(a.solution_dir / "transport_plan.csv"))

    nodes, models, boxes, dem_whl, demand = load_all(str(a.data_root))
    geo = whp.load_geo()

    trips = []
    for r in read_csv(a.solution_dir / "trip_summary.csv"):
        trips.append({"trip_id": r["trip_id"], "vehicle_type": r["vehicle_type"],
                      "route_full": r["route"].split("->"),
                      "start_time_s": float(r["start_time_s"]),
                      "end_time_s": float(r["end_time_s"])})
    deliveries = read_csv(a.solution_dir / "delivery_timeline.csv")
    relay_rows = read_csv(a.solution_dir / "relay_plan.csv")

    slices_by_trip = build_slices(a.data_root, a.solution_dir)
    audit, runs = relay_runs(a.solution_dir, slices_by_trip)
    rs_color, rs_name = rs_color_map(relay_rows)

    relay_xy = [(float(r["x"]), float(r["y"])) for r in relay_rows]
    extent = whp.map_extent(nodes, relay_xy)

    print(f"trips: {len(trips)}, relay sorties: {len(relay_rows)}, "
          f"relay-covered runs: {len(runs)}")
    fig_route_map(nodes, boxes, demand, trips, deliveries, dem_whl, geo, extent,
                  audit_summary, a.output_dir)
    fig_comm_map(nodes, trips, relay_rows, runs, rs_color, rs_name, dem_whl, geo,
                 extent, audit_summary, a.output_dir)
    fig_comm_timeline(trips, audit, relay_rows, rs_color, rs_name, audit_summary, a.output_dir)
    print("figures written to", a.output_dir)


if __name__ == "__main__":
    main()
