"""Standalone Q2 comparison algorithms.

This module ports the runnable Q2 comparators developed in
``members/chendelong`` to the compact WHLi workspace.  All methods use the
same physical model, resource scheduler and lexicographic evaluator from
``q2_solver_v2.py``; only the construction/search controller is changed.

Algorithms exposed by :func:`run_algorithm`:

``greedy``
    single-service first-fit batching and earliest-deadline scheduling;
``q2_order``
    order-only neighbourhood search;
``q2_full``
    joint order/route/type/regroup neighbourhood search;
``sa``, ``ils``, ``vns``, ``memetic``, ``hill``
    the E007-style search-controller comparisons.

The generated files follow the Q2 canonical CSV/JSON interface.  This file is
deliberately independent of the original chendelong package so it can be
copied and run from the compact workspace.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("q2_solver_v2_comparison_base", HERE / "q2_solver_v2.py")
if SPEC is None or SPEC.loader is None:
    raise ImportError("cannot load q2_solver_v2.py")
q2 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = q2
SPEC.loader.exec_module(q2)

ALGORITHMS = ("greedy", "q2_order", "q2_full", "sa", "ils", "vns", "memetic", "hill")


@dataclass
class Context:
    data_root: Path
    nodes: Any
    vehicles: dict
    box_map: dict
    boxes_by_service: dict
    dem: Any
    uavs: dict
    batteries: dict
    legs: dict
    initial_trips: list
    initial_order: list
    initial_score: tuple
    initial_source: str


def clone_trip(t):
    return q2.clone_trip(t)


def _score(ctx: Context, trips: list, order: list):
    return q2.schedule_eval(trips, order, ctx.vehicles, ctx.uavs, ctx.batteries,
                            ctx.box_map, ctx.legs)


def _greedy_state(ctx: Context):
    """Construct the chendelong-style single-service greedy state."""
    raw = []
    for service, boxes in sorted(ctx.boxes_by_service.items()):
        ids = [b.box_id for b in boxes]
        raw.extend(q2.split_service(service, ids, ctx.box_map, ctx.nodes,
                                    ctx.vehicles, ctx.legs))
    trips = q2.make_trips(raw, ctx.box_map, ctx.vehicles, ctx.legs)
    order = sorted(trips, key=lambda t: q2.trip_order_key(t, ctx.box_map))
    return trips, order, _score(ctx, trips, order)


def load_context(data_root: Path) -> Context:
    nodes, vehicles, box_map, boxes_by_service, dem, uavs, batteries, _ = q2.load_entities(data_root)
    legs = q2.build_legs(nodes, dem)
    provisional = Context(data_root, nodes, vehicles, box_map, boxes_by_service, dem,
                          uavs, batteries, legs, [], [], (), "")
    greedy_trips, greedy_order, greedy_eval = _greedy_state(provisional)

    # Use the compact workspace warm start when it is valid.  This mirrors the
    # common strong warm start used in E005/E007, while retaining a deterministic
    # greedy fallback for clean environments.
    warm_path = HERE / "alns_warm_start.csv"
    warm_trips = q2.load_warm_start(warm_path, box_map, vehicles, legs)
    candidates = [(greedy_eval[0], "greedy", greedy_trips, greedy_order)]
    if warm_trips:
        warm_order = list(warm_trips)
        warm_eval = q2.schedule_eval(warm_trips, warm_order, vehicles, uavs,
                                     batteries, box_map, legs)
        candidates.append((warm_eval[0], "warm_start", warm_trips, warm_order))
    best_score, source, trips, order = min(candidates, key=lambda x: x[0])
    provisional.initial_trips = [clone_trip(t) for t in trips]
    provisional.initial_order = [next(x for x in provisional.initial_trips if x.trip_id == t.trip_id)
                                 for t in order]
    provisional.initial_score = best_score
    provisional.initial_source = source
    return provisional


def _order_mutation(order: list, rng: random.Random, mode: str = "mixed"):
    if len(order) < 2:
        return list(order)
    out = list(order)
    i, j = rng.sample(range(len(out)), 2)
    if mode == "swap" or (mode == "mixed" and rng.random() < 0.45):
        out[i], out[j] = out[j], out[i]
    elif mode == "reverse":
        lo, hi = sorted((i, j))
        out[lo:hi + 1] = reversed(out[lo:hi + 1])
    else:
        item = out.pop(i)
        out.insert(j, item)
    return out


def _rebuild_selected(ctx: Context, trips: list, selected: list[str], rng: random.Random,
                      mode: str = "mixed"):
    return q2.rebuild_services([clone_trip(t) for t in trips], selected,
                               ctx.box_map, ctx.boxes_by_service, ctx.vehicles,
                               ctx.legs, rng, mode)


def _change_type(ctx: Context, trips: list, rng: random.Random):
    if not trips:
        return trips
    out = [clone_trip(t) for t in trips]
    idx = rng.randrange(len(out))
    t = out[idx]
    options = list(ctx.vehicles)
    rng.shuffle(options)
    for vehicle_type in options:
        if vehicle_type == t.vehicle_type:
            continue
        m = q2.calc_route(t.route, t.box_ids_by_node, ctx.box_map,
                          ctx.vehicles[vehicle_type], ctx.legs)
        if m is not None:
            t.vehicle_type = vehicle_type
            t.mass_kg, t.volume_m3 = m["mass"], m["volume"]
            t.energy_kwh, t.duration_s = m["energy"], m["duration"]
            return out
    return out


def _change_route(ctx: Context, trips: list, rng: random.Random):
    candidates = [i for i, t in enumerate(trips) if len(t.route[1:-1]) > 1]
    if not candidates:
        return [clone_trip(t) for t in trips]
    out = [clone_trip(t) for t in trips]
    idx = rng.choice(candidates)
    t = out[idx]
    services = list(t.route[1:-1])
    rng.shuffle(services)
    route = ["O01", *services, "O01"]
    m = q2.calc_route(route, t.box_ids_by_node, ctx.box_map,
                      ctx.vehicles[t.vehicle_type], ctx.legs)
    if m is None:
        return out
    t.route = route
    t.mass_kg, t.volume_m3 = m["mass"], m["volume"]
    t.energy_kwh, t.duration_s = m["energy"], m["duration"]
    return out


def propose(ctx: Context, trips: list, order: list, rng: random.Random,
            variant: str = "full"):
    """Create one valid genotype mutation.

    ``order_only`` is the direct port of ``q2_order``.  ``full`` enables the
    complete neighbourhood used by ``q2_full`` and the E007 controller tests.
    """
    out_trips = [clone_trip(t) for t in trips]
    out_order = []
    for t in order:
        match = next((x for x in out_trips if x.trip_id == t.trip_id), None)
        if match is not None:
            out_order.append(match)
    if len(out_order) < len(out_trips):
        out_order += [t for t in out_trips if t not in out_order]
    if variant == "order_only":
        return out_trips, _order_mutation(out_order, rng)

    op = rng.choice(("order", "type", "route", "repack", "merge_split"))
    if op == "order":
        out_order = _order_mutation(out_order, rng)
    elif op == "type":
        out_trips = _change_type(ctx, out_trips, rng)
        out_order = q2.inherit_order(out_trips, out_order, ctx.box_map)
    elif op == "route":
        out_trips = _change_route(ctx, out_trips, rng)
        out_order = q2.inherit_order(out_trips, out_order, ctx.box_map)
    else:
        services = list(ctx.boxes_by_service)
        if not services:
            return out_trips, out_order
        count = 1 if len(services) == 1 or rng.random() < 0.7 else 2
        selected = rng.sample(services, count)
        try:
            out_trips = _rebuild_selected(ctx, out_trips, selected, rng,
                                          "capacity" if op == "merge_split" else "mixed")
            out_order = q2.inherit_order(out_trips, out_order, ctx.box_map)
        except (KeyError, ValueError, RuntimeError):
            pass
    return out_trips, out_order


def _lex_better(a: tuple, b: tuple) -> bool:
    return a < b


def _accept_sa(current: tuple, candidate: tuple, elapsed: float, budget: float,
               rng: random.Random):
    if candidate[0] > current[0]:
        return False
    if candidate < current:
        return True
    if candidate[0] != current[0]:
        return False
    norm = ((candidate[1] - current[1]) / 6400.0 +
            (candidate[2] - current[2]) / 24.0 +
            (candidate[3] - current[3]) / 24.0 +
            (candidate[4] - current[4]) / 71.0)
    temperature = 0.015 * (1.0 - min(1.0, elapsed / max(budget, 1e-9))) ** 2 + 1e-5
    return rng.random() < math.exp(-max(0.0, norm) / temperature)


def _crossover(ctx: Context, left: tuple[list, list], right: tuple[list, list], rng: random.Random):
    """Feasible trip-level crossover for the light memetic comparator."""
    a_trips, a_order = left
    b_trips, _ = right
    chosen = set(rng.sample(range(len(a_trips)), max(1, len(a_trips) // 3))) if a_trips else set()
    raw = []
    used = set()
    for i, t in enumerate(a_trips):
        if i not in chosen:
            continue
        ids = [x for x in t.box_ids if x not in used]
        if not ids:
            continue
        used.update(ids)
        label = "+".join(t.route[1:-1])
        m = q2.calc_route(t.route, t.box_ids_by_node, ctx.box_map,
                          ctx.vehicles[t.vehicle_type], ctx.legs)
        if m is not None:
            raw.append((label, ids, t.vehicle_type, m))
    for t in b_trips:
        ids = [x for x in t.box_ids if x not in used]
        if not ids:
            continue
        used.update(ids)
        by_node = {s: [] for s in t.route[1:-1]}
        for bid in ids:
            by_node.setdefault(ctx.box_map[bid].service_node, []).append(bid)
        route = ["O01", *[s for s in t.route[1:-1] if by_node.get(s)], "O01"]
        m = q2.calc_route(route, by_node, ctx.box_map, ctx.vehicles[t.vehicle_type], ctx.legs)
        if m is not None:
            raw.append(("+".join(route[1:-1]), ids, t.vehicle_type, m))
    missing = set(ctx.box_map) - used
    for bid in sorted(missing):
        service = ctx.box_map[bid].service_node
        packed = q2.split_service(service, [bid], ctx.box_map, ctx.nodes,
                                   ctx.vehicles, ctx.legs)
        raw.extend(packed)
        used.add(bid)
    child = q2.make_trips(raw, ctx.box_map, ctx.vehicles, ctx.legs)
    order = q2.inherit_order(child, a_order, ctx.box_map)
    return child, order


def search(ctx: Context, algorithm: str, budget_s: float, seed: int,
           start_from_warm: bool = True):
    rng = random.Random(seed)
    if start_from_warm:
        trips = [clone_trip(t) for t in ctx.initial_trips]
        order = [next(x for x in trips if x.trip_id == t.trip_id) for t in ctx.initial_order]
    else:
        trips, order, _ = _greedy_state(ctx)
        trips = [clone_trip(t) for t in trips]
        order = [next(x for x in trips if x.trip_id == t.trip_id) for t in order]
    current = _score(ctx, trips, order)
    best_score, best_trips, best_order = current[0], trips, order
    start = time.perf_counter()
    deadline = start + max(0.05, budget_s)
    trace = [{"elapsed_s": 0.0, "score": list(best_score), "operator": "initial"}]
    stale = 0
    neighborhood = 0
    population = [(best_score, best_trips, best_order)]
    iterations = 0
    variant = "order_only" if algorithm == "q2_order" else "full"
    while time.perf_counter() < deadline:
        iterations += 1
        if algorithm == "memetic":
            parent = min(population, key=lambda x: x[0])
            base = (parent[1], parent[2])
            if len(population) > 1 and rng.random() < 0.35:
                other = rng.choice(population)
                cand_trips, cand_order = _crossover(ctx, base, (other[1], other[2]), rng)
            else:
                cand_trips, cand_order = propose(ctx, base[0], base[1], rng, "full")
        else:
            base = (trips, order)
            cand_trips, cand_order = propose(ctx, base[0], base[1], rng, variant)
        cand = _score(ctx, cand_trips, cand_order)
        elapsed = time.perf_counter() - start
        accepted = False
        if algorithm in ("sa", "q2_full", "q2_order"):
            accepted = _accept_sa(current[0], cand[0], elapsed, budget_s, rng)
        elif algorithm == "vns":
            accepted = cand[0] < current[0]
            if accepted:
                neighborhood = 0
            else:
                neighborhood += 1
        else:  # ILS and Hill use strict improvement; ILS perturbs after stagnation.
            accepted = cand[0] < current[0]
        if accepted:
            trips, order, current = cand_trips, cand_order, cand
            stale = 0 if cand[0] < best_score else stale + 1
        else:
            stale += 1
        if cand[0] < best_score:
            best_score, best_trips, best_order = cand[0], [clone_trip(t) for t in cand_trips], list(cand_order)
            trace.append({"elapsed_s": elapsed, "score": list(best_score), "operator": algorithm})
            population.append((best_score, best_trips, best_order))
            population = sorted(population, key=lambda x: x[0])[:8]
        if algorithm == "vns" and neighborhood >= 8:
            variant = "full"
            neighborhood = 0
        if algorithm == "ils" and stale >= 80:
            trips = [clone_trip(t) for t in best_trips]
            order = list(best_order)
            for _ in range(3):
                trips, order = propose(ctx, trips, order, rng, "full")
            current = _score(ctx, trips, order)
            stale = 0
    final = _score(ctx, best_trips, best_order)
    return best_trips, best_order, final, {
        "algorithm": algorithm,
        "seed": seed,
        "budget_s": budget_s,
        "elapsed_s": time.perf_counter() - start,
        "iterations": iterations,
        "initial_score": list(ctx.initial_score if start_from_warm else _greedy_state(ctx)[2][0]),
        "final_score": list(final[0]),
        "initial_source": ctx.initial_source if start_from_warm else "greedy",
        "trace": trace,
        "objective_order": ["hard_deadline_violations", "weighted_tardiness_priority_seconds",
                            "makespan_s", "trip_count", "energy_kwh"],
        "communication_checked": False,
    }


def _write_result(ctx: Context, algorithm: str, out: Path, trips: list, order: list,
                  state: tuple, metadata: dict):
    out.mkdir(parents=True, exist_ok=True)
    score, rows, deliveries, resources, assigned, audit = state
    q2.write_csv(out / "transport_plan.csv", rows,
                 ["trip_id", "sequence_no", "vehicle_type", "vehicle_id", "battery_id",
                  "from_node", "to_node", "start_time_s", "end_time_s", "payload_depart_kg",
                  "energy_kwh", "soc_start", "soc_end"])
    q2.write_csv(out / "delivery_timeline.csv", deliveries,
                 ["box_id", "trip_id", "service_node", "delivery_time_s", "delivery_class",
                  "deadline_s", "deadline_met"])
    q2.write_csv(out / "transport_resource_timeline.csv", resources,
                 ["resource_type", "resource_id", "trip_id", "activity", "start_time_s",
                  "end_time_s", "soc_before", "soc_after"])
    trip_rows = []
    for t, uid, bid, st, en, calc, _seq, soc_start, soc_end in assigned:
        trip_rows.append({"trip_id": t.trip_id, "vehicle_type": t.vehicle_type,
                          "vehicle_id": uid, "battery_id": bid, "route": "->".join(t.route),
                          "box_ids": ";".join(t.box_ids), "box_count": len(t.box_ids),
                          "payload_kg": calc["mass"], "volume_m3": calc["volume"],
                          "energy_kwh": calc["energy"], "duration_s": calc["duration"],
                          "start_time_s": st, "end_time_s": en, "soc_start": soc_start,
                          "soc_end": soc_end})
    q2.write_csv(out / "trip_summary.csv", trip_rows)
    q2.write_csv(out / "optimization_trace.csv", metadata.get("trace", []))
    result_audit = dict(audit)
    result_audit.update({"algorithm": algorithm, "score": list(score), **metadata})
    (out / "global_audit.json").write_text(json.dumps(result_audit, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "solution.json").write_text(json.dumps({
        "schema_version": "1", "question": "Q2", "algorithm": algorithm,
        "trips": [{"trip_id": t.trip_id, "vehicle_type": t.vehicle_type,
                    "route": t.route[1:-1], "box_ids": t.box_ids}
                   for t in trips]
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def run_algorithm(data_root: Path, output_dir: Path, algorithm: str,
                  budget_s: float = 60.0, seed: int = 0):
    if algorithm not in ALGORITHMS:
        raise ValueError(f"unknown algorithm {algorithm!r}; choose from {ALGORITHMS}")
    ctx = load_context(data_root)
    started = time.perf_counter()
    if algorithm == "greedy":
        trips, order, state = _greedy_state(ctx)
        metadata = {"algorithm": algorithm, "seed": seed, "budget_s": 0.0,
                    "elapsed_s": time.perf_counter() - started,
                    "initial_source": "greedy", "final_score": list(state[0]),
                    "objective_order": ["hard_deadline_violations", "weighted_tardiness_priority_seconds",
                                        "makespan_s", "trip_count", "energy_kwh"],
                    "communication_checked": False, "trace": []}
    else:
        trips, order, state, metadata = search(ctx, algorithm, budget_s, seed,
                                                start_from_warm=True)
    _write_result(ctx, algorithm, output_dir, trips, order, state, metadata)
    return metadata


def main():
    parser = argparse.ArgumentParser(description="Run one compact-workspace Q2 comparison algorithm")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--algorithm", choices=ALGORITHMS, required=True)
    parser.add_argument("--budget", type=float, default=60.0)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    metadata = run_algorithm(args.data_root, args.output_dir, args.algorithm,
                             args.budget, args.seed)
    print(json.dumps(metadata, ensure_ascii=False))


if __name__ == "__main__":
    main()
