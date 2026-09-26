"""ALNS-Q3-V2: E007 transport ALNS with E004-style joint relay feedback.

Differences from ALNS-Q3 (one-shot transport-then-decode pipeline):

1. Periodic joint MILP decode inside the search (E004 ``decode_front`` role):
   every ``joint_period_s`` the incumbent transport plan is decoded through the
   relay set-cover + two-airframe occupancy MILP (``q3_solver._relay_sortie_options``),
   so the search observes communication/relay feasibility while it runs.
2. External Pareto archive (E004 archive + repo AGENTS.md section 3.5): only
   joint-feasible solutions are admitted, and all non-dominated points on the
   first two optimization levels (weighted_tardiness, joint_makespan) are kept.
   Total sorties and joint energy remain attached as secondary diagnostics.
3. Direct feedback from joint-feasible solutions (E004 round-robin): every
   third decode, the search state is reset to a perturbation of an archive
   member selected by rotating scalarisation weight lambda in {0.5,1,0,0.25,0.75}.
4. Failure-directed ``relay_shift`` operator (E004/WHLi surgical retiming as an
   operator): when the MILP decode fails, blind trips that are uncovered or sit
   inside >relay_count concurrent blind windows are moved later in the dispatch
   order, which delays their blind windows past the concurrency peak.
5. Blind-signature caching: direct-link blindness is a pure trajectory-geometry
   property, so slices/direct flags/candidate positions are cached per
   (route, vehicle_type, per-stop box counts) and reused across decodes.
6. Hybrid decode trigger (``decode_mode="hybrid"``, the default): a full MILP
   decode is fired only when the coarse blind signature (set of blind trips +
   quantised blind-window endpoints) changes substantively -- a blind trip
   appears/vanishes, or a window shifts by more than ``shift_tol_s`` -- when a
   fresh transport incumbent appears, or when a ``heartbeat_s`` safety interval
   elapses.  ``decode_mode="periodic"`` reproduces the original fixed-cadence
   behaviour.  Rationale: once the transport structure converges, blind windows
   freeze and fixed-cadence decodes re-confirm known results at 3-5 s each.
7. Warm-start modes (``init_mode``): ``auto`` = Q1 batches / local / external
   candidate scoring (original behaviour); ``direct_q2`` = the frozen Q2
   solution's trips AND dispatch order (timing gene inherited);
   ``struct_q2`` = the frozen Q2 solution's trips (box-to-trip assignment and
   clustering inherited) but with the dispatch order re-derived from urgency
   keys, so timing is re-optimised freely by the order warm-up and operators.
8. Hard relay-concurrency cap (``hard_relay_cap=True``, 2026-09-25 directive):
   the cheap ``blind_overload`` proxy (peak concurrent blind trips minus relay
   airframe inventory) is promoted from a soft SA guidance term into the hard
   lexicographic level of the score, so any plan whose blind windows need more
   than ``relay['count']`` simultaneous relays ranks behind every plan that
   fits, and SA's "hard got worse -> reject" rule discards such moves outright.
   ``relay_shift`` stays in the operator pool whenever the current plan is
   overloaded (not only after a failed MILP decode) and is selection-boosted
   3x while an overload persists.  ``--no-hard-relay-cap`` reproduces the
   original soft-guidance behaviour for ablation.

Hard constraints are enforced by rejection (ZeroDecoder-style gating), never by
penalties: transport-infeasible candidates are discarded by ``schedule_eval``
scoring, and joint-infeasible decodes never enter the feasible archive.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


q2 = _load("whli_q2_v2", HERE.parent.parent / "Q2" / "code" / "q2_solver_v2.py")
q3 = _load("whli_q3_solver", HERE / "q3_solver.py")

LAMBDA_CYCLE = (0.5, 1.0, 0.0, 0.25, 0.75)  # E004 inner weight rotation


class Context:
    def __init__(self, data_root: Path):
        (self.nodes, self.vehicles, self.box_map, self.boxes_by_service,
         self.dem, self.uavs, self.batteries, self.files) = q2.load_entities(data_root)
        self.legs = q2.build_legs(self.nodes, self.dem)
        self.relay = q3.load_relay(data_root)
        self.lp = q3.load_link_params(data_root)
        o = self.nodes["O01"]
        self.gateway = q3.Endpoint("gateway", o.lon, o.lat,
                                   o.ground_elevation_m + self.lp["gateway_h"])
        self.blind_cache: dict = {}


def trip_signature(t) -> tuple:
    return (tuple(t.route), t.vehicle_type,
            tuple(len(t.box_ids_by_node[s]) for s in t.route[1:-1]))


def build_blind_info(trip, ctx: Context) -> dict:
    """Relative-time trajectory slices and direct-link flags for one trip.

    Timing offsets inside a trip depend only on the route, vehicle type and the
    number of boxes handed off at each stop, so the result is cached by
    ``trip_signature`` and shifted by the dispatch start time at decode.
    """
    v = ctx.vehicles[trip.vehicle_type]
    rows = []
    elapsed = v.fixed_prep_s + len(trip.box_ids) * v.per_box_load_s
    current = "O01"
    for s in trip.route[1:-1]:
        fs = q2.q1.leg_flight_time_s(v, ctx.legs[(current, s)])
        rows.append({"start_time_s": elapsed, "end_time_s": elapsed + fs,
                     "from_node": current, "to_node": s})
        ids = trip.box_ids_by_node[s]
        elapsed += fs + v.base_handoff_s + len(ids) * v.per_box_handoff_s
        current = s
    fs = q2.q1.leg_flight_time_s(v, ctx.legs[(current, "O01")])
    rows.append({"start_time_s": elapsed, "end_time_s": elapsed + fs,
                 "from_node": current, "to_node": "O01"})
    traj = q3.build_trajectory(rows, ctx.nodes, ctx.legs)
    slices = q3.all_time_slices(traj, ctx.nodes, ctx.legs)
    direct = [q3.link(te, ctx.gateway, "tg", ctx.dem, ctx.lp) for _, te, _ in slices]
    blind_idx = [k for k, x in enumerate(direct) if not x[0]]
    return {"slices_rel": slices, "direct": direct, "blind_idx": blind_idx,
            "candidates": None}


def decode_joint(ev, ctx: Context, full: bool, milp_time_limit: float) -> dict:
    """Decode one evaluated transport plan into a relay/communication plan.

    Returns metrics, feasibility flags, the chosen relay options and the set of
    offending trip ids when infeasible.  Reuses the audited q3_solver MILP.
    """
    _, rows, _, _, ass, au = ev
    starts = {a[0].trip_id: a[3] for a in ass}
    trips = {a[0].trip_id: a[0] for a in ass}
    blind_data: dict = {}
    candidate_pool: list = []
    per_trip_cap = 35 if full else 14
    pool_cap = 80 if full else 48
    for tid, trip in trips.items():
        sig = trip_signature(trip)
        info = ctx.blind_cache.get(sig)
        if info is None:
            info = build_blind_info(trip, ctx)
            ctx.blind_cache[sig] = info
        start = starts[tid]
        slices_abs = [(t + start, ep, meta) for t, ep, meta in info["slices_rel"]]
        blind = [slices_abs[k] for k in info["blind_idx"]]
        blind_data[tid] = {"slices": slices_abs, "direct": info["direct"],
                           "blind": blind}
        if blind:
            if info["candidates"] is None:
                info["candidates"] = q3.candidate_search(
                    tid, slices_abs, ctx.nodes, ctx.dem, ctx.lp, ctx.relay, ctx.legs)
            candidate_pool.extend(info["candidates"][:per_trip_cap])
    t_milp = time.perf_counter()
    chosen, trip_task, diag = q3._relay_sortie_options(
        blind_data, candidate_pool[:pool_cap * max(1, len(trips))], ctx.nodes,
        ctx.dem, ctx.lp, ctx.relay, milp_time_limit=milp_time_limit,
        pool_cap=pool_cap)
    milp_s = time.perf_counter() - t_milp
    # Energy-component assignment and relay airframe overlap audit (same rules
    # as q3_solver.main).
    relay = ctx.relay
    comp_ready = [0.0] * relay["component_count"]
    relay_rows = []
    comp_failed = False
    for o in chosen:
        eligible = [i for i, r in enumerate(comp_ready) if r <= o["launch"] + q3.EPS]
        if not eligible:
            comp_failed = True
            continue
        i = min(eligible, key=lambda j: comp_ready[j])
        soc = 1 - o["energy"] / relay["energy_kwh"]
        charge = relay["charge_s"] if soc < .9 else (1 - soc) / .1 * .35 * relay["charge_s"]
        comp_ready[i] = o["return_time"] + charge
        relay_rows.append({"relay_task_id": o["task_id"], "relay_type": "R",
                           "relay_id": o["relay_id"], "energy_component_id": f"R-E{i:02d}",
                           "x": o["candidate"].lon, "y": o["candidate"].lat,
                           "hover_height_m": o["candidate"].hover_height_m,
                           "depart_time_s": o["launch"], "service_start_s": o["service_start"],
                           "service_end_s": o["service_end"], "return_time_s": o["return_time"],
                           "energy_kwh": o["energy"], "soc_end": soc})
    overlap = []
    for rid in relay["ids"]:
        rr = sorted((x for x in relay_rows if x["relay_id"] == rid),
                    key=lambda x: x["depart_time_s"])
        for a, b in zip(rr, rr[1:]):
            if a["return_time_s"] + relay["turn_s"] > b["depart_time_s"] + q3.EPS:
                overlap.append(rid)
    comm_gap = sum(len(d["blind"]) for tid, d in blind_data.items()
                   if d["blind"] and tid not in trip_task)
    comm_pass = comm_gap == 0
    relay_pass = (not overlap and not comp_failed
                  and len(chosen) == len(relay_rows)
                  and all(r["soc_end"] >= relay["reserve"] - q3.EPS for r in relay_rows))
    transport_energy = sum(float(r["energy_kwh"]) for r in rows)
    relay_energy = sum(r["energy_kwh"] for r in relay_rows)
    joint_makespan = max([float(r["end_time_s"]) for r in rows]
                         + [r["return_time_s"] for r in relay_rows] or [0.0])
    feasible = bool(au.get("overall_pass")) and comm_pass and relay_pass
    offending: set = set()
    if not feasible:
        offending |= {tid for tid, d in blind_data.items()
                      if d["blind"] and tid not in trip_task}
        windows = [(min(x[0] for x in d["blind"]), max(x[0] for x in d["blind"]), tid)
                   for tid, d in blind_data.items() if d["blind"]]
        events = sorted({w[0] for w in windows} | {w[1] for w in windows})
        for a, b in zip(events, events[1:]):
            mid = (a + b) / 2.0
            active = [w[2] for w in windows if w[0] <= mid < w[1]]
            if len(active) > relay["count"]:
                offending.update(active)
    return {"feasible": feasible, "comm_pass": comm_pass, "relay_pass": relay_pass,
            "comm_gap_slices": comm_gap, "relay_rows": relay_rows,
            "trip_task": trip_task, "blind_data": blind_data,
            "relay_sorties": len(relay_rows),
            "joint_makespan_s": joint_makespan,
            "joint_energy_kwh": transport_energy + relay_energy,
            "transport_energy_kwh": transport_energy,
            "relay_energy_kwh": relay_energy,
            "weighted_tardiness": au.get("weighted_tardiness_priority_seconds", 0.0),
            "transport_makespan_s": au.get("makespan_s", 0.0),
            "transport_sorties": len(trips),
            "milp_diag": diag, "milp_runtime_s": milp_s,
            "offending": sorted(offending),
            "overlap_ids": sorted(set(overlap)), "component_failed": comp_failed}


def _dominates(a, b) -> bool:
    return (all(x <= y + 1e-8 for x, y in zip(a, b))
            and any(x < y - 1e-8 for x, y in zip(a, b)))


def blind_overload(ev, ctx: Context) -> int:
    """Peak concurrent blind trips beyond the relay airframe count.

    Cheap per-iteration proxy for relay feasibility (WHLi's 'concurrent blind
    clusters <= relay count' insight).  Guidance only; the MILP decode remains
    the authoritative feasibility test.
    """
    windows = []
    for a in ev[4]:
        trip, start = a[0], a[3]
        sig = trip_signature(trip)
        info = ctx.blind_cache.get(sig)
        if info is None:
            info = build_blind_info(trip, ctx)
            ctx.blind_cache[sig] = info
        if info["blind_idx"]:
            ts = [info["slices_rel"][k][0] for k in info["blind_idx"]]
            windows.append((start + min(ts), start + max(ts)))
    limit = ctx.relay["count"]
    if len(windows) <= limit:
        return 0
    events = sorted({w[0] for w in windows} | {w[1] for w in windows})
    peak = 0
    for x, y in zip(events, events[1:]):
        mid = (x + y) / 2.0
        peak = max(peak, sum(1 for w in windows if w[0] <= mid < w[1]))
    return max(0, peak - limit)


def blind_overload_trips(ev, ctx: Context) -> set:
    """Trip ids whose blind windows participate in a concurrency peak that
    exceeds the relay airframe count.  Empty when there is no overload."""
    windows = []
    for a in ev[4]:
        trip, start = a[0], a[3]
        sig = trip_signature(trip)
        info = ctx.blind_cache.get(sig)
        if info is None:
            info = build_blind_info(trip, ctx)
            ctx.blind_cache[sig] = info
        if info["blind_idx"]:
            ts = [info["slices_rel"][k][0] for k in info["blind_idx"]]
            windows.append((start + min(ts), start + max(ts), trip.trip_id))
    limit = ctx.relay["count"]
    if len(windows) <= limit:
        return set()
    events = sorted({w[0] for w in windows} | {w[1] for w in windows})
    bad = set()
    for x, y in zip(events, events[1:]):
        mid = (x + y) / 2.0
        active = [w[2] for w in windows if w[0] <= mid < w[1]]
        if len(active) > limit:
            bad.update(active)
    return bad


def blind_signature(ev, ctx: Context) -> tuple:
    """Coarse relay-relevant state: blind trip ids plus their blind-window
    endpoints quantised to 60 s buckets.  Relay feasibility depends only on
    these; route/vehicle/box-count changes are captured because the windows
    derive from ``trip_signature``-cached trajectory info."""
    items = []
    for a in ev[4]:
        trip, start = a[0], a[3]
        sig = trip_signature(trip)
        info = ctx.blind_cache.get(sig)
        if info is None:
            info = build_blind_info(trip, ctx)
            ctx.blind_cache[sig] = info
        if info["blind_idx"]:
            ts = [info["slices_rel"][k][0] for k in info["blind_idx"]]
            items.append((trip.trip_id,
                          int(round((start + min(ts)) / 60.0)),
                          int(round((start + max(ts)) / 60.0))))
    return tuple(sorted(items))


def substantive_change(old, new, tol_buckets: int) -> bool:
    """True when a blind trip appeared/vanished or any blind window moved by
    more than ``tol_buckets`` 60 s buckets.  Small SA jitter below the
    tolerance does not justify another MILP decode."""
    if old is None:
        return True
    om, nm = {x[0]: x[1:] for x in old}, {x[0]: x[1:] for x in new}
    if set(om) != set(nm):
        return True
    return any(abs(om[k][0] - nm[k][0]) > tol_buckets
               or abs(om[k][1] - nm[k][1]) > tol_buckets for k in om)


def archive_key(entry) -> tuple:
    return (entry["weighted_tardiness"], entry["joint_makespan_s"],
            entry["sorties_total"], entry["joint_energy_kwh"])


def archive_objective_key(entry) -> tuple:
    """First two lexicographic optimization levels used for the front."""
    return (entry["weighted_tardiness"], entry["joint_makespan_s"])


def archive_insert(archive: list, entry) -> bool:
    key = archive_objective_key(entry)
    for i, e in enumerate(archive):
        ek = archive_objective_key(e)
        if all(abs(x - y) < 1e-8 for x, y in zip(ek, key)):
            # Equal front coordinates need one representative.  Preserve the
            # lexicographically better joint plan for the later two levels.
            if archive_key(entry) < archive_key(e):
                archive[i] = entry
                return True
            return False
        if _dominates(ek, key):
            return False
    archive[:] = [e for e in archive if not _dominates(key, archive_objective_key(e))]
    archive.append(entry)
    archive.sort(key=lambda e: (e["weighted_tardiness"], e["joint_makespan_s"],
                                e["sorties_total"], e["joint_energy_kwh"]))
    return True


Q2_RESULTS = HERE.parent.parent / "Q2" / "results"


def initial_state(ctx: Context, init_mode: str = "auto"):
    if init_mode in ("direct_q2", "struct_q2"):
        trips = q2.load_warm_start(Q2_RESULTS / "trip_summary.csv",
                                   ctx.box_map, ctx.vehicles, ctx.legs)
        if not trips:
            raise ValueError("Q2 frozen solution failed to reload via "
                             f"{Q2_RESULTS / 'trip_summary.csv'}")
        if init_mode == "direct_q2":
            audit_path = Q2_RESULTS / "global_audit.json"
            dispatch = json.loads(audit_path.read_text(encoding="utf-8"))["dispatch_order"]
            by_id = {t.trip_id: t for t in trips}
            order = [by_id[i] for i in dispatch if i in by_id]
            order += [t for t in trips if t.trip_id not in set(dispatch)]
        else:
            # Structural warm start: keep Q2's box-to-trip assignment and
            # clustering, but re-derive the dispatch order from urgency keys so
            # the order warm-up/operators re-time everything freely.
            order = sorted(trips, key=lambda t: q2.trip_order_key(t, ctx.box_map))
        return trips, order, init_mode
    urgent_raw = []
    for s, bs in ctx.boxes_by_service.items():
        urgent_raw += q2.split_service(s, [b.box_id for b in bs], ctx.box_map,
                                       ctx.nodes, ctx.vehicles, ctx.legs)
    urgent_trips = q2.make_trips(urgent_raw, ctx.box_map, ctx.vehicles, ctx.legs)
    candidates = [("urgent", urgent_trips,
                   sorted(urgent_trips, key=lambda t: q2.trip_order_key(t, ctx.box_map)))]
    warm = q2.load_warm_start(HERE.parent.parent / "Q2" / "code" / "alns_warm_start.csv",
                              ctx.box_map, ctx.vehicles, ctx.legs)
    if warm:
        candidates.append(("local_warm", warm, warm))
    external_candidates = [
        REPO / "members" / "chendelong" / "experiments" / "e001" / "q2_full"
        / "seed_1" / "solution.json",
        q2.ROOT / "huaweicup-2026" / "members" / "chendelong" / "experiments"
        / "e001" / "q2_full" / "seed_1" / "solution.json",
    ]
    ext_path = next((p for p in external_candidates if p.exists()), external_candidates[0])
    ext = q2.load_external_solution(ext_path, ctx.box_map, ctx.vehicles, ctx.legs)
    if ext:
        candidates.append(("external_warm", ext, ext))
    scored = []
    for name, cand, order in candidates:
        st = q2.schedule_eval(cand, order, ctx.vehicles, ctx.uavs, ctx.batteries,
                              ctx.box_map, ctx.legs)
        scored.append((st[0], name, cand, order))
    scored.sort(key=lambda z: z[0])
    return scored[0][2], scored[0][3], scored[0][1]


def alns_q3_v2(ctx: Context, initial_trips, initial_order, budget_s: float,
               seed: int, joint_period_s: float, decode_mode: str = "hybrid",
               heartbeat_s: float = 45.0, min_decode_gap_s: float = 6.0,
               shift_tol_s: float = 120.0, hard_relay_cap: bool = True):
    rng = random.Random(seed)
    start_clock = time.perf_counter()
    deadline = start_clock + budget_s
    current_trips = [q2.clone_trip(t) for t in initial_trips]
    by_id = {t.trip_id: t for t in current_trips}
    current_order = [by_id[t.trip_id] for t in initial_order if t.trip_id in by_id]
    current_order += [t for t in current_trips if t not in current_order]

    def evaluate(trips, order):
        return q2.schedule_eval(trips, order, ctx.vehicles, ctx.uavs,
                                ctx.batteries, ctx.box_map, ctx.legs)

    def joint_score(ev):
        # Relay-concurrency cap as a hard lexicographic level (2026-09-25
        # directive): a plan whose coarse blind windows need more concurrent
        # relays than the inventory has ranks behind every plan that fits.
        # Only the MILP decode admits solutions to the feasible archive; this
        # hardened score steers the search away from overload regions.
        if not hard_relay_cap:
            return ev[0]
        s = ev[0]
        return (s[0] + blind_overload(ev, ctx),) + tuple(s[1:])

    cur = evaluate(current_trips, current_order)
    # Short dispatch-order warm-up, same move as q2_solver_v2 but budget-capped.
    warm_deadline = min(deadline, start_clock + max(4.0, budget_s * 0.05))
    while time.perf_counter() < warm_deadline:
        cand_order = current_order[:]
        i, j = rng.sample(range(len(cand_order)), 2)
        if rng.random() < 0.55:
            cand_order[i], cand_order[j] = cand_order[j], cand_order[i]
        else:
            cand_order.insert(j, cand_order.pop(i))
        cand = evaluate(current_trips, cand_order)
        if joint_score(cand) < joint_score(cur):
            current_order, cur = cand_order, cand

    best = {"score": joint_score(cur), "trips": [q2.clone_trip(t) for t in current_trips],
            "order_ids": [t.trip_id for t in current_order], "ev": cur}

    operators = ["random_service_repack", "late_service_repack", "capacity_repack",
                 "cross_service_repack", "order_move", "merge_split",
                 "critical_chain", "deadline_split", "related_rebuild", "relay_shift"]
    weights = {x: 1.0 for x in operators}
    rewards = defaultdict(float)
    uses = defaultdict(int)
    trace = []
    decode_log = []
    archive: list = []
    infeasible_decodes = 0
    last_decode_at: float | None = None
    last_decode_key = None
    last_decode_sig = None
    shift_tol_buckets = int(round(shift_tol_s / 60.0))
    improved_since_decode = False
    offending: set = set()
    decode_rounds = 0
    accepted = 0
    temperature = 0.12
    iteration = 0

    def decode_key(ev):
        # Trip geometry plus quantised dispatch starts: relay feasibility
        # depends on blind-window concurrency, so timing must be part of the key.
        return tuple(sorted((trip_signature(a[0]), round(a[3] / 30.0)) for a in ev[4]))

    while time.perf_counter() < deadline:
        now = time.perf_counter()
        # --- Joint decode trigger.  Target is the search frontier (current
        # state), not only the transport-lexicographic incumbent: the
        # joint-feasible region typically lives slightly off the transport
        # optimum, so decoding only `best` would re-test the same infeasible
        # plan forever.  A fresh incumbent is decoded promptly when it appears.
        # In "hybrid" mode full decodes fire only on substantive blind-signature
        # change (new/vanished blind trip, or a window shifted > shift_tol_s),
        # on a fresh incumbent, or on a heartbeat; small SA jitter no longer
        # re-confirms known results at several seconds per decode.
        target_ev = target_trips = target_order = None
        trigger = None
        since_decode = None if last_decode_at is None else now - last_decode_at
        if improved_since_decode and since_decode is not None and since_decode >= 5.0:
            target_ev, target_trips, target_order = best["ev"], best["trips"], best["order_ids"]
            trigger = "incumbent"
        elif decode_mode == "periodic":
            if last_decode_at is None or since_decode >= joint_period_s:
                target_ev, target_trips = cur, current_trips
                target_order = [t.trip_id for t in current_order]
                trigger = "periodic"
        else:
            if last_decode_at is None:
                trigger = "initial"
            elif since_decode >= min_decode_gap_s and substantive_change(
                    last_decode_sig, blind_signature(cur, ctx), shift_tol_buckets):
                trigger = "signature"
            elif since_decode >= heartbeat_s:
                trigger = "heartbeat"
            if trigger is not None:
                target_ev, target_trips = cur, current_trips
                target_order = [t.trip_id for t in current_order]
        if target_ev is not None and deadline - now > 20.0:
            key = decode_key(target_ev)
            if key != last_decode_key:
                t_dec = time.perf_counter()
                dec = decode_joint(target_ev, ctx, full=False, milp_time_limit=3.0)
                decode_rounds += 1
                last_decode_at = time.perf_counter()
                last_decode_key = key
                last_decode_sig = blind_signature(target_ev, ctx)
                improved_since_decode = False
                offending = set(dec["offending"])
                if dec["feasible"]:
                    entry = {"id": f"J{decode_rounds:03d}", "source": "alns_q3_v2",
                             "iteration": iteration, "decode_round": decode_rounds,
                             "weighted_tardiness": dec["weighted_tardiness"],
                             "makespan_s": dec["joint_makespan_s"],
                             "joint_makespan_s": dec["joint_makespan_s"],
                             "transport_makespan_s": dec["transport_makespan_s"],
                             "sorties_total": dec["transport_sorties"] + dec["relay_sorties"],
                             "transport_sorties": dec["transport_sorties"],
                             "relay_sorties": dec["relay_sorties"],
                             "energy_kwh": dec["joint_energy_kwh"],
                             "joint_energy_kwh": dec["joint_energy_kwh"],
                             "transport_energy_kwh": dec["transport_energy_kwh"],
                             "relay_energy_kwh": dec["relay_energy_kwh"],
                             "trips": [q2.clone_trip(t) for t in target_trips],
                             "order_ids": list(target_order),
                             "ev": target_ev, "decode": dec}
                    archive_insert(archive, entry)
                else:
                    infeasible_decodes += 1
                decode_log.append({"round": decode_rounds, "iteration": iteration,
                                   "elapsed_s": now - start_clock, "trigger": trigger,
                                   "feasible": dec["feasible"],
                                   "comm_gap_slices": dec["comm_gap_slices"],
                                   "relay_sorties": dec["relay_sorties"],
                                   "joint_makespan_s": dec["joint_makespan_s"],
                                   "joint_energy_kwh": dec["joint_energy_kwh"],
                                   "weighted_tardiness": dec["weighted_tardiness"],
                                   "milp_status": dec["milp_diag"].get("status"),
                                   "offending": dec["offending"],
                                   "decode_runtime_s": time.perf_counter() - t_dec})
                # --- E004 direct feedback: perturb from the joint-feasible archive ---
                if decode_rounds % 3 == 0 and archive:
                    lam = LAMBDA_CYCLE[decode_rounds % len(LAMBDA_CYCLE)]
                    parent = min(archive, key=lambda e:
                                 max(lam, 1e-6) * e["joint_makespan_s"] / 10000.0
                                 + max(1e-6, 1.0 - lam) * e["joint_energy_kwh"] / 80.0)
                    current_trips = [q2.clone_trip(t) for t in parent["trips"]]
                    pmap = {t.trip_id: t for t in current_trips}
                    current_order = [pmap[i] for i in parent["order_ids"] if i in pmap]
                    cur = evaluate(current_trips, current_order)
                    # The state reset is intentional even when the archive parent
                    # scores worse than the incumbent: it re-anchors the search
                    # inside the joint-feasible region (E004 direct feedback).
                    if joint_score(cur) < best["score"]:
                        best = {"score": cur[0],
                                "trips": [q2.clone_trip(t) for t in current_trips],
                                "order_ids": [t.trip_id for t in current_order], "ev": cur}
        # --- one E007 ALNS iteration ---
        iteration += 1
        # relay_shift targets: MILP-diagnosed offenders plus (under the hard
        # cap) every trip sitting in an overloaded blind-concurrency window.
        relay_targets = set(offending)
        if hard_relay_cap:
            relay_targets |= blind_overload_trips(cur, ctx)
        relay_boost = 3.0 if (hard_relay_cap and blind_overload(cur, ctx) > 0) else 1.0
        pool = [o for o in operators if weights[o] > 0 and (o != "relay_shift" or relay_targets)]
        u = rng.random() * sum(weights[o] * (relay_boost if o == "relay_shift" else 1.0)
                               for o in pool)
        op = pool[-1]
        for name in pool:
            u -= weights[name] * (relay_boost if name == "relay_shift" else 1.0)
            if u <= 0:
                op = name
                break
        uses[op] += 1
        cand_trips = [q2.clone_trip(t) for t in current_trips]
        cand_order = q2.inherit_order(cand_trips, current_order, ctx.box_map)
        if op == "relay_shift":
            idx = next((i for i, t in enumerate(cand_order) if t.trip_id in relay_targets), None)
            if idx is None:
                off_services = {ctx.box_map[b].service_node
                                for t in current_trips if t.trip_id in relay_targets
                                for b in t.box_ids}
                idx = next((i for i, t in enumerate(cand_order)
                            if any(ctx.box_map[x].service_node in off_services
                                   for x in t.box_ids)), None)
            if idx is None or len(cand_order) < 2:
                continue
            shift = rng.randint(2, min(5, len(cand_order) - 1))
            item = cand_order.pop(idx)
            cand_order.insert(min(len(cand_order), idx + shift), item)
        elif op in ("order_move", "critical_chain") and len(cand_order) > 1:
            i, j = rng.sample(range(len(cand_order)), 2)
            cand_order.insert(j, cand_order.pop(i))
        else:
            if op == "late_service_repack":
                service_scores = defaultdict(float)
                for d in cur[2]:
                    b = ctx.box_map[d["box_id"]]
                    target = b.deadline_s if b.is_first_batch else b.expected_time_s
                    if target is not None:
                        service_scores[b.service_node] += b.priority * max(
                            0.0, float(d["delivery_time_s"]) - target)
                ranked = sorted(ctx.boxes_by_service,
                                key=lambda s: service_scores[s], reverse=True)
                selected = ranked[:1 if rng.random() < 0.75 else 2]
                mode = "mixed"
            elif op in ("cross_service_repack", "related_rebuild"):
                k = 2 if len(ctx.boxes_by_service) >= 2 and rng.random() < 0.85 else 3
                selected = rng.sample(list(ctx.boxes_by_service),
                                      min(k, len(ctx.boxes_by_service)))
                mode = "mixed"
            elif op == "deadline_split":
                service_scores = defaultdict(float)
                for d in cur[2]:
                    b = ctx.box_map[d["box_id"]]
                    target = b.deadline_s if b.is_first_batch else b.expected_time_s
                    if target is not None:
                        service_scores[b.service_node] += b.priority * max(
                            0.0, float(d["delivery_time_s"]) - target)
                selected = [max(ctx.boxes_by_service, key=lambda s: service_scores[s])]
                mode = "mixed"
            else:
                selected = rng.sample(list(ctx.boxes_by_service),
                                      1 if rng.random() < 0.72 else 2)
                mode = {"capacity_repack": "capacity", "merge_split": "random"}.get(op, "mixed")
            try:
                cand_trips = q2.rebuild_services(cand_trips, selected, ctx.box_map,
                                                 ctx.boxes_by_service, ctx.vehicles,
                                                 ctx.legs, rng, mode)
            except ValueError:
                continue
            cand_order = q2.inherit_order(cand_trips, current_order, ctx.box_map)
            if len(cand_order) > 1 and rng.random() < 0.35:
                i, j = rng.sample(range(len(cand_order)), 2)
                cand_order.insert(j, cand_order.pop(i))
        cand = evaluate(cand_trips, cand_order)
        cscore, cur_score = joint_score(cand), joint_score(cur)
        improves = cscore < cur_score
        same_hard = cscore[0] == cur_score[0]
        delta = ((cscore[1] - cur_score[1]) / max(1.0, abs(cur_score[1]))
                 if same_hard else 1e9)
        # Hard-cap mode uses the lexicographic hard level; ablation mode
        # restores the original soft overload guidance term.
        if not hard_relay_cap:
            delta += 0.02 * (blind_overload(cand, ctx) - blind_overload(cur, ctx))
        accept = improves or (same_hard and delta <= 0.08
                              and rng.random() < math.exp(-max(0.0, delta) / max(temperature, 1e-6)))
        if cscore[0] > cur_score[0]:
            accept = False
        if accept:
            current_trips, current_order, cur = cand_trips, cand_order, cand
            accepted += 1
            rewards[op] += 4.0 if improves else 1.0
            if cscore < best["score"]:
                best = {"score": cscore,
                        "trips": [q2.clone_trip(t) for t in cand_trips],
                        "order_ids": [t.trip_id for t in cand_order], "ev": cand}
                improved_since_decode = True
                rewards[op] += 8.0
        temperature *= 0.99985
        if iteration % 100 == 0:
            trace.append({"iteration": iteration, "operator": op, "accepted": accept,
                          "elapsed_s": time.perf_counter() - start_clock,
                          "best_hard": best["score"][0],
                          "best_soft_tardiness": best["score"][1],
                          "best_makespan_s": best["score"][2],
                          "best_trip_count": best["score"][3],
                          "best_energy_kwh": best["score"][4],
                          "best_relay_overload": (blind_overload(best["ev"], ctx)
                                                  if hard_relay_cap else 0),
                          "archive_size": len(archive),
                          "decode_rounds": decode_rounds})
        if iteration % 100 == 99:
            for name in operators:
                weights[name] = 0.2 + rewards[name] / max(1, uses[name])
            if not relay_targets:
                weights["relay_shift"] = 0.0
            elif weights["relay_shift"] <= 0.0:
                weights["relay_shift"] = 1.0
            rewards = defaultdict(float)
            uses = defaultdict(int)

    return best, archive, decode_log, trace, weights, iteration, infeasible_decodes


def write_communication_outputs(out: Path, dec: dict, ctx: Context):
    """Full per-slice communication audit for the final decoded solution."""
    import numpy as np
    by_task = {r["relay_task_id"]: r for r in dec["relay_rows"]}
    dl = np.asarray(ctx.dem["longitude"])[0]
    da = np.asarray(ctx.dem["latitude"])[:, 0]
    raster = np.asarray(ctx.dem["dem"])
    audit_rows = []
    trip_summ = []
    for tid, data in dec["blind_data"].items():
        slices, direct, blind = data["slices"], data["direct"], data["blind"]
        rid = dec["trip_task"].get(tid, "")
        rr = by_task.get(rid)
        rep = None
        if rr is not None:
            ix = int(np.clip(round((rr["x"] - float(dl[0])) / float(dl[1] - dl[0])), 0, len(dl) - 1))
            iy = int(np.clip(round((float(da[0]) - rr["y"]) / float(da[0] - da[1])), 0, len(da) - 1))
            alt = float(raster[iy, ix]) + float(rr["hover_height_m"])
            rep = q3.Endpoint("relay", rr["x"], rr["y"], alt)
        for k, (tm, te, meta) in enumerate(slices):
            ok, m, los = direct[k]
            mode = "direct" if ok else "relay"
            rlos = glos = False
            minm = m
            if not ok:
                if rep is not None and rr is not None:
                    ok1, m1, l1 = q3.link(te, rep, "tr", ctx.dem, ctx.lp)
                    ok2, m2, l2 = q3.link(rep, ctx.gateway, "rg", ctx.dem, ctx.lp)
                    rlos, glos, minm = l1, l2, min(m1, m2)
                    ok = ok1 and ok2 and rr["service_start_s"] <= tm <= rr["service_end_s"]
                else:
                    ok = False
            audit_rows.append({"trip_id": tid, "time_start_s": tm, "time_end_s": tm,
                               "mode": mode, "direct_link_ok": bool(direct[k][0]),
                               "relay_task_id": rid if not direct[k][0] else "",
                               "uav_relay_los_ok": bool(rlos),
                               "relay_gateway_los_ok": bool(glos),
                               "min_link_margin_db": float(minm),
                               "continuous_ok": bool(ok)})
        trip_summ.append({"trip_id": tid,
                          "direct_slices": sum(x[0] for x in direct),
                          "total_slices": len(slices), "blind_slices": len(blind),
                          "relay_tasks": int(bool(rid)),
                          "continuous_ok": not blind or bool(rid)})
    q3.write_csv(out / "communication_audit.csv", audit_rows,
                 ["trip_id", "time_start_s", "time_end_s", "mode", "direct_link_ok",
                  "relay_task_id", "uav_relay_los_ok", "relay_gateway_los_ok",
                  "min_link_margin_db", "continuous_ok"])
    q3.write_csv(out / "trip_communication_summary.csv", trip_summ,
                 ["trip_id", "direct_slices", "total_slices", "blind_slices",
                  "relay_tasks", "continuous_ok"])
    gap = sum(not bool(r["continuous_ok"]) for r in audit_rows)
    return gap


def _trigger_counts(decode_log):
    counts = defaultdict(int)
    for d in decode_log:
        counts[d.get("trigger") or "unknown"] += 1
    return counts


def run(data_root: Path, output_dir: Path, budget_s: float = 300.0,
        seed: int = 20260924, joint_period_s: float = 15.0,
        decode_mode: str = "hybrid", init_mode: str = "auto",
        heartbeat_s: float = 45.0, min_decode_gap_s: float = 6.0,
        shift_tol_s: float = 120.0, hard_relay_cap: bool = True) -> dict:
    t0 = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    ctx = Context(data_root)
    init_trips, init_order, warm_source = initial_state(ctx, init_mode)
    init_s = time.perf_counter() - t0

    best, archive, decode_log, trace, weights, iterations, infeasible_decodes = \
        alns_q3_v2(ctx, init_trips, init_order, budget_s, seed, joint_period_s,
                   decode_mode=decode_mode, heartbeat_s=heartbeat_s,
                   min_decode_gap_s=min_decode_gap_s, shift_tol_s=shift_tol_s,
                   hard_relay_cap=hard_relay_cap)

    # Final selection: lexicographic on (WT, joint makespan, sorties, energy)
    # over the joint-feasible archive; fall back to the best transport plan
    # with a full decode when no joint-feasible solution was found.
    joint_feasible_found = bool(archive)
    if archive:
        chosen = min(archive, key=lambda e: archive_key(e))
        chosen_ev, chosen_trips, chosen_order = chosen["ev"], chosen["trips"], chosen["order_ids"]
    else:
        chosen, chosen_ev = None, best["ev"]
        chosen_trips, chosen_order = best["trips"], best["order_ids"]
    final_dec = decode_joint(chosen_ev, ctx, full=True, milp_time_limit=6.0)
    final_feasible = bool(chosen_ev[5].get("overall_pass")) and final_dec["feasible"]

    # Transport interface files.
    _, rows, delivs, res, ass, au = chosen_ev
    q3.write_csv(output_dir / "transport_plan.csv", rows,
                 ["trip_id", "sequence_no", "vehicle_type", "vehicle_id", "battery_id",
                  "from_node", "to_node", "start_time_s", "end_time_s",
                  "payload_depart_kg", "energy_kwh", "soc_start", "soc_end"])
    q3.write_csv(output_dir / "delivery_timeline.csv", delivs,
                 ["box_id", "trip_id", "service_node", "delivery_time_s",
                  "delivery_class", "deadline_s", "deadline_met"])
    q3.write_csv(output_dir / "transport_resource_timeline.csv", res,
                 ["resource_type", "resource_id", "trip_id", "activity",
                  "start_time_s", "end_time_s", "soc_before", "soc_after"])
    trip_rows = [{"trip_id": t.trip_id, "vehicle_type": t.vehicle_type,
                  "vehicle_id": uid, "battery_id": bid, "route": "->".join(t.route),
                  "box_ids": ";".join(t.box_ids), "box_count": len(t.box_ids),
                  "payload_kg": calc["mass"], "volume_m3": calc["volume"],
                  "energy_kwh": calc["energy"], "duration_s": calc["duration"],
                  "start_time_s": st, "end_time_s": en, "soc_start": ss, "soc_end": se}
                 for t, uid, bid, st, en, calc, seq, ss, se in ass]
    q2.write_csv(output_dir / "trip_summary.csv", trip_rows)
    q3.write_csv(output_dir / "relay_plan.csv", final_dec["relay_rows"],
                 ["relay_task_id", "relay_type", "relay_id", "energy_component_id",
                  "x", "y", "hover_height_m", "depart_time_s", "service_start_s",
                  "service_end_s", "return_time_s", "energy_kwh", "soc_end"])
    final_gap = write_communication_outputs(output_dir, final_dec, ctx)
    q2.write_csv(output_dir / "optimization_trace.csv", trace)
    q2.write_csv(output_dir / "operator_statistics.csv",
                 [{"operator": k, "final_weight": v} for k, v in weights.items()])

    archive_public = [{k: v for k, v in e.items() if k not in ("trips", "order_ids", "ev", "decode")}
                      for e in archive]
    (output_dir / "pareto_archive.json").write_text(
        json.dumps({"objectives": ["weighted_tardiness", "joint_makespan_s"],
                    "secondary_objectives": ["sorties_total", "joint_energy_kwh"],
                    "objective_direction": "minimize", "feasible_only": True,
                    "archive_policy": "all_nondominated_feasible_joint_decodes",
                    "entries": archive_public},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    archive_solution_entries = []
    for e in archive:
        base = {k: v for k, v in e.items()
                if k not in ("trips", "order_ids", "ev", "decode")}
        base["order_ids"] = list(e["order_ids"])
        base["trips"] = [q2.serialize_trip(t) for t in e["trips"]]
        # Persist the joint decoder's selected relay plan as well as the
        # transport state.  Candidate objects in ``decode`` are not JSON
        # serializable, while these rows and assignments are sufficient to
        # trace which relay sorties covered each transport trip.
        base["relay_plan"] = list(e["decode"]["relay_rows"])
        base["trip_relay_assignment"] = dict(e["decode"]["trip_task"])
        base["joint_decode_audit"] = {
            "comm_pass": e["decode"]["comm_pass"],
            "relay_pass": e["decode"]["relay_pass"],
            "comm_gap_slices": e["decode"]["comm_gap_slices"],
            "milp_status": e["decode"]["milp_diag"].get("status"),
        }
        archive_solution_entries.append(base)
    (output_dir / "pareto_archive_solutions.json").write_text(
        json.dumps({"objectives": ["weighted_tardiness", "joint_makespan_s"],
                    "secondary_objectives": ["sorties_total", "joint_energy_kwh"],
                    "feasible_only": True,
                    "entries": archive_solution_entries},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    q3.write_csv(output_dir / "pareto_front.csv",
                 [{"id": e["id"], "iteration": e["iteration"],
                   "decode_round": e["decode_round"],
                   "weighted_tardiness": e["weighted_tardiness"],
                   "joint_makespan_s": e["joint_makespan_s"],
                   "sorties_total": e["sorties_total"],
                   "joint_energy_kwh": e["joint_energy_kwh"]}
                  for e in archive],
                 ["id", "iteration", "decode_round", "weighted_tardiness",
                  "joint_makespan_s", "sorties_total", "joint_energy_kwh"])
    try:
        from plot_pareto_front import plot_archive
        plot_archive(output_dir / "pareto_archive.json", output_dir / "pareto_front",
                     title="Q3 TCJD-ALNS Pareto archive")
    except Exception as exc:
        (output_dir / "pareto_plot_error.txt").write_text(str(exc), encoding="utf-8")
    (output_dir / "decode_log.json").write_text(
        json.dumps(decode_log, ensure_ascii=False, indent=2), encoding="utf-8")

    audit = {"solver": "ALNS-Q3-V2",
             "algorithm_family": "E007-ALNS + E004 joint relay feedback "
                                 "(periodic MILP decode, Pareto archive, "
                                 "lambda-rotated archive perturbation, relay_shift)",
             "random_seed": seed, "budget_s": float(budget_s),
             "joint_period_s": joint_period_s,
             "decode_mode": decode_mode, "init_mode": init_mode,
             "heartbeat_s": heartbeat_s, "min_decode_gap_s": min_decode_gap_s,
             "shift_tol_s": shift_tol_s, "hard_relay_cap": hard_relay_cap,
             "decode_trigger_counts": dict(_trigger_counts(decode_log)),
             "init_runtime_s": init_s, "wall_runtime_s": time.perf_counter() - t0,
             "alns_iterations": iterations, "decode_rounds": len(decode_log),
             "infeasible_decode_rounds": infeasible_decodes,
              "archive_size": len(archive),
              "pareto_objectives": ["weighted_tardiness", "joint_makespan_s"],
              "pareto_archive_policy": "all_nondominated_feasible_joint_decodes",
              "warm_start_source": warm_source,
             "selected_from_archive": joint_feasible_found,
             "transport_audit": au,
             "communication_overall_pass": final_gap == 0 and final_dec["comm_pass"],
             "communication_gap_slices": final_gap,
             "relay_resource_pass": final_dec["relay_pass"],
             "relay_overlap_ids": final_dec["overlap_ids"],
             "relay_component_assignment_failed": final_dec["component_failed"],
             "relay_sorties": final_dec["relay_sorties"],
             "transport_sorties": final_dec["transport_sorties"],
             "relay_energy_kwh": final_dec["relay_energy_kwh"],
             "transport_energy_kwh": final_dec["transport_energy_kwh"],
             "joint_energy_kwh": final_dec["joint_energy_kwh"],
             "joint_makespan_s": final_dec["joint_makespan_s"],
             "weighted_tardiness_priority_seconds": au.get("weighted_tardiness_priority_seconds"),
             "relay_milp": final_dec["milp_diag"],
             "joint_priority_status": {
                 "hard_constraints_pass": bool(au.get("overall_pass"))
                 and final_gap == 0 and final_dec["comm_pass"] and final_dec["relay_pass"],
                 "delivery_timeliness_pass": au.get("hard_deadline_violations", 1) == 0,
                 "all_tasks_completion_s": final_dec["joint_makespan_s"],
                 "energy_kwh": final_dec["joint_energy_kwh"]},
             "joint_feasible_found": joint_feasible_found and final_feasible}
    (output_dir / "global_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit
