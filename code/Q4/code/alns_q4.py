# -*- coding: utf-8 -*-
"""ALNS-Q4: Adaptive Large Neighbourhood Search for Q4 task partitioning.

Framework ported from members/weiliu/Q3/code/alns_q3_v2.py (ALNS-Q3-V2):
- roulette-wheel operator selection with segment-wise adaptive weights
  (every SEGMENT_LEN iterations, weight = 0.2 + rewards/uses, rewards
  +4 improvement / +1 accepted / +8 new best);
- simulated-annealing acceptance on the relative delta of a fixed
  scalarization, with hard-violation increases strictly rejected;
- external non-dominated archive (Pareto) maintained over the Q4 objective
  vector, plus periodic re-anchoring of the search to archive parents chosen
  by a rotating scalarization weight (LAMBDA_CYCLE);
- failure-guided operator gating (shortage-guided destroy only competes when
  the incumbent still has shortage), mirroring relay_shift in ALNS-Q3-V2.

What changed versus ALNS-Q3-V2 (see ALNS-Q4.md for the full list):
- solution = component -> group assignment (dict over 6 components), not
  (trips, order); evaluation is an exact closed-form peak-concurrency
  accounting (microseconds), so the whole joint-decode layer (decode_joint,
  blind signatures, hybrid triggers, relay_shift) is removed;
- destroy/repair operator pairs over assignments replace the
  service-repack / order-move operators.

Lexicographic score (AGENTS.md 3.5 chain specialised to Q4; WT / makespan /
sorties / energy of the frozen plan are partition-invariant and recorded in
the archive instead of being searched):
    (hard_violations, shortage_total, relay_extra, balance_time, redundancy_total)
"""
import itertools
import json
import math
import os
import random
import time

from q4_core import (INVENTORY, RESOURCE_TYPES, Context, evaluate_partition,
                     partition_label)

SEGMENT_LEN = 100          # adaptive-weight segment length (as ALNS-Q3-V2)
WEIGHT_FLOOR = 0.2         # weight floor (as ALNS-Q3-V2)
REWARD_IMPROVE = 4.0
REWARD_ACCEPT = 1.0
REWARD_BEST = 8.0
SA_T0 = 0.12               # initial temperature (relative-delta scale, as V2)
SA_DECAY = 0.99985
SA_SIDEWAYS = 0.08         # sideways-walk threshold on relative delta
ARCHIVE_LIMIT = 32         # external archive cap (as ALNS-Q3-V2)
REANCHOR_PERIOD = 250      # iterations between archive re-anchoring events
LAMBDA_CYCLE = (0.5, 1.0, 0.0, 0.25, 0.75)  # rotating scalarization (as V2)

DESTROY_OPS = ['random_removal', 'related_removal',
               'shortage_targeted_removal', 'balance_targeted_removal']
REPAIR_OPS = ['greedy_insertion', 'regret2_insertion', 'optimal_insertion',
              'random_insertion']


# ---------------------------------------------------------------- archive ----

ARCHIVE_OBJ = ('shortage_total', 'balance_time', 'redundancy_total', 'relay_extra')


def _obj_vec(ev):
    return tuple(ev[k] for k in ARCHIVE_OBJ)


def _dominates(a, b):
    """a Pareto-dominates b over ARCHIVE_OBJ (all <=, at least one <)."""
    va, vb = _obj_vec(a), _obj_vec(b)
    return all(x <= y for x, y in zip(va, vb)) and any(x < y for x, y in zip(va, vb))


def archive_insert(archive, ev, seen):
    """Insert a hard-feasible evaluation into the external archive."""
    if ev['hard_violations'] > 0:
        return False
    key = ev['assignment']
    if key in seen:
        return False
    for old in archive:
        if _dominates(old, ev):
            return False
    archive[:] = [old for old in archive if not _dominates(ev, old)]
    seen.add(key)
    archive.append(ev)
    if len(archive) > ARCHIVE_LIMIT:
        # diversity eviction with champion protection: never evict the
        # lexicographic best entry; among the rest, evict the worst balance
        # (keeps both ends of the Pareto front).
        champion = min(range(len(archive)), key=lambda i: archive_key(archive[i]))
        worst = max((i for i in range(len(archive)) if i != champion),
                    key=lambda i: archive[i]['balance_time'])
        archive.pop(worst)
    return True


def archive_key(ev):
    """Lexicographic selection key over the archive (hard level already 0)."""
    return (ev['shortage_total'], ev['relay_extra'],
            round(ev['balance_time'], 6), ev['redundancy_total'])


def sa_scalar(ev):
    """Fixed scalarization used ONLY for the SA acceptance temperature scale."""
    return (100.0 * ev['shortage_total'] + 10.0 * ev['relay_extra']
            + ev['balance_time'] + 0.01 * ev['redundancy_total'])


# ------------------------------------------------------------ initial state ----

def initial_assignments(ctx, k, rng, coords=None):
    """Diverse starts: relay-cluster aligned / geographic cluster / random.

    Deliberately NOT warm-started from any known optimum (ALNS-Q3-V2 ablation
    showed warm-start basin locking).
    """
    cands = []

    # 1) relay-cluster aligned: group by the frozen relay sortie covering each
    #    component; uncovered components join the currently smallest group.
    sorties = sorted({s for ss in ctx.comp_to_sorties.values() for s in ss})
    if sorties:
        a = {}
        sizes = [0] * k
        for cid in ctx.comp_ids:
            ss = sorted(ctx.comp_to_sorties[cid])
            if ss:
                g = sorties.index(ss[0]) % k
            else:
                g = sizes.index(min(sizes))
            a[cid] = g
            sizes[g] += 1
        cands.append(_fix_empty(a, ctx, k))

    # 2) geographic cluster: k-means on component centroids (if coords given)
    if coords:
        cents = {}
        for cid in ctx.comp_ids:
            pts = [coords[s] for s in ctx.components[cid] if s in coords]
            if pts:
                cents[cid] = (sum(p[0] for p in pts) / len(pts),
                              sum(p[1] for p in pts) / len(pts))
        if len(cents) == len(ctx.comp_ids):
            ids = sorted(cents, key=lambda c: cents[c][0])
            seeds = [cents[ids[int(i * (len(ids) - 1) / max(1, k - 1))]]
                     for i in range(k)]
            for _ in range(20):
                assign = {}
                for cid, c in cents.items():
                    assign[cid] = min(range(k),
                                      key=lambda g: (c[0] - seeds[g][0]) ** 2
                                      + (c[1] - seeds[g][1]) ** 2)
                for g in range(k):
                    members = [cents[c] for c in cents if assign[c] == g]
                    if members:
                        seeds[g] = (sum(p[0] for p in members) / len(members),
                                    sum(p[1] for p in members) / len(members))
            cands.append(_fix_empty(assign, ctx, k))

    # 3) pure random
    a = {cid: rng.randrange(k) for cid in ctx.comp_ids}
    cands.append(_fix_empty(a, ctx, k))

    return cands


def _fix_empty(assign, ctx, k):
    """Reassign components from the largest group until no group is empty."""
    a = dict(assign)
    while True:
        sizes = [0] * k
        for cid in ctx.comp_ids:
            sizes[a[cid]] += 1
        if 0 not in sizes:
            return a
        donor = sizes.index(max(sizes))
        target = sizes.index(0)
        for cid in sorted(ctx.comp_ids,
                          key=lambda c: -ctx.comp_workload[c]):
            if a[cid] == donor:
                a[cid] = target
                break


def _to_list(assign, ctx):
    return [assign[c] for c in ctx.comp_ids]


def _to_dict(vec, ctx):
    return {c: vec[i] for i, c in enumerate(ctx.comp_ids)}


# ---------------------------------------------------------------- operators ----

def destroy_random(state, ctx, k, rng, ev):
    n = rng.choice((1, 1, 2, 2, 3, 3, 4))
    return set(rng.sample(ctx.comp_ids, min(n, len(ctx.comp_ids) - k + 1)))


def destroy_related(state, ctx, k, rng, ev):
    """Remove components coupled through a shared frozen relay sortie."""
    sorties = sorted({s for ss in ctx.comp_to_sorties.values() for s in ss})
    if not sorties:
        return destroy_random(state, ctx, k, rng, ev)
    s = rng.choice(sorties)
    picked = {cid for cid in ctx.comp_ids if s in ctx.comp_to_sorties[cid]}
    if not picked:
        return destroy_random(state, ctx, k, rng, ev)
    return picked


def destroy_shortage(state, ctx, k, rng, ev):
    """Failure-guided (mirrors relay_shift gating): remove components whose
    trips are active at the peak witness time of the most shortaged resource."""
    if ev['shortage_total'] <= 0:
        return destroy_random(state, ctx, k, rng, ev)
    rt = max(RESOURCE_TYPES, key=lambda r: (ev['shortage'][r], ev['totals'][r]))
    picked = set()
    for g in ev['groups']:
        if g['counts'][rt] <= 0:
            continue
        w = g['witnesses'][rt]
        for tid in g['trips']:
            t = next(x for x in ctx.trips if x['trip'] == tid)
            if t['start'] - 1e-6 <= w <= t['return_time'] + 1e-6:
                picked.add(ctx.trip_to_component[tid])
    if not picked:
        return destroy_random(state, ctx, k, rng, ev)
    return picked


def destroy_balance(state, ctx, k, rng, ev):
    """Remove 1-2 components from the heaviest group (balance oriented)."""
    g = max(ev['groups'], key=lambda x: x['workload_time_s'])
    if not g['components']:
        return destroy_random(state, ctx, k, rng, ev)
    n = min(len(g['components']), rng.choice((1, 2)))
    return set(rng.sample(sorted(g['components']), n))


DESTROYS = {'random_removal': destroy_random,
            'related_removal': destroy_related,
            'shortage_targeted_removal': destroy_shortage,
            'balance_targeted_removal': destroy_balance}


def repair_greedy(removed, state, ctx, k, rng):
    """Insert each removed component (heaviest first) into the group giving
    the best lexicographic score."""
    a = dict(state)
    todo = sorted(removed, key=lambda c: -ctx.comp_workload[c])
    for cid in todo:
        a[cid] = None
    for i, cid in enumerate(todo):
        best_g, best_score = None, None
        for g in range(k):
            a[cid] = g
            ev = evaluate_partition(ctx, _to_list(a, ctx), k)
            if best_score is None or ev['score'] < best_score:
                best_score, best_g = ev['score'], g
        a[cid] = best_g
    return a


def repair_regret2(removed, state, ctx, k, rng):
    """Regret-2 insertion: place the component with the largest gap between
    its best and second-best insertion score first."""
    a = dict(state)
    todo = list(removed)
    for cid in todo:
        a[cid] = None
    while todo:
        best_cid, best_g, best_regret = None, None, None
        for cid in todo:
            scored = []
            for g in range(k):
                a[cid] = g
                ev = evaluate_partition(ctx, _to_list(a, ctx), k)
                scored.append((ev['score'], g))
            scored.sort(key=lambda x: x[0])
            regret = sa_scalar_diff(scored[1][0], scored[0][0]) if k > 1 else 0.0
            if best_regret is None or regret > best_regret:
                best_regret, best_cid, best_g = regret, cid, scored[0][1]
        a[best_cid] = best_g
        todo.remove(best_cid)
    return a


def sa_scalar_diff(s1, s0):
    """Numeric gap between two score tuples (for regret estimation)."""
    w = (100000.0, 100.0, 10.0, 1.0, 0.01)
    return sum(wi * (x - y) for wi, x, y in zip(w, s1, s0))


def repair_optimal(removed, state, ctx, k, rng):
    """Exact insertion: enumerate all k^|removed| joint reassignments (for
    |removed| <= 4) and take the lexicographic best; falls back to greedy for
    larger removal sets.  Escapes basins that sequential greedy misses."""
    removed = sorted(removed)
    if len(removed) > 4:
        return repair_greedy(set(removed), state, ctx, k, rng)
    best_a, best_score = None, None
    for gs in itertools.product(range(k), repeat=len(removed)):
        a = dict(state)
        a.update(dict(zip(removed, gs)))
        ev = evaluate_partition(ctx, _to_list(a, ctx), k)
        if best_score is None or ev['score'] < best_score:
            best_score, best_a = ev['score'], a
    return best_a


def repair_random(removed, state, ctx, k, rng):
    a = dict(state)
    for cid in removed:
        a[cid] = rng.randrange(k)
    return _fix_empty(a, ctx, k)


REPAIRS = {'greedy_insertion': repair_greedy,
           'regret2_insertion': repair_regret2,
           'optimal_insertion': repair_optimal,
           'random_insertion': repair_random}


# ------------------------------------------------------------------- main ----

def _roulette(names, weights, rng):
    tot = sum(weights[n] for n in names)
    r = rng.random() * tot
    acc = 0.0
    for n in names:
        acc += weights[n]
        if r <= acc:
            return n
    return names[-1]


def alns_q4(ctx, k, budget_s, seed, log=print):
    """Run ALNS-Q4 for one (k, seed).  Returns a result dict."""
    rng = random.Random(seed)
    t0 = time.perf_counter()
    deadline = t0 + budget_s

    coords = _try_load_coords(ctx)
    cands = initial_assignments(ctx, k, rng, coords)
    evs = [evaluate_partition(ctx, _to_list(a, ctx), k) for a in cands]
    cur = min(evs, key=lambda e: e['score'])
    cur_a = _to_dict(list(cur['assignment']), ctx)
    best = cur
    log('  init candidates: %s -> start score %s'
        % ([tuple(round(x, 4) if isinstance(x, float) else x for x in e['score'])
            for e in evs], best['score']))

    archive, seen = [], set()
    archive_insert(archive, cur, seen)
    best_ever = cur if cur['hard_violations'] == 0 else None

    dw = {n: 1.0 for n in DESTROY_OPS}
    rw = {n: 1.0 for n in REPAIR_OPS}
    drewards = {n: 0.0 for n in DESTROY_OPS}
    rrewards = {n: 0.0 for n in REPAIR_OPS}
    duses = {n: 0 for n in DESTROY_OPS}
    ruses = {n: 0 for n in REPAIR_OPS}
    dacc = {n: 0 for n in DESTROY_OPS}
    racc = {n: 0 for n in REPAIR_OPS}
    duses_t = {n: 0 for n in DESTROY_OPS}   # cumulative (survive segment resets)
    ruses_t = {n: 0 for n in REPAIR_OPS}
    dacc_t = {n: 0 for n in DESTROY_OPS}
    racc_t = {n: 0 for n in REPAIR_OPS}

    trace = []
    it = 0
    T = SA_T0
    while time.perf_counter() < deadline:
        it += 1
        # failure-guided gating: shortage destroy only competes when the
        # incumbent still has shortage (same pattern as relay_shift in V2)
        dpool = list(DESTROY_OPS)
        if best['shortage_total'] <= 0:
            dpool = [n for n in dpool if n != 'shortage_targeted_removal']
        dname = _roulette(dpool, dw, rng)
        rname = _roulette(list(REPAIR_OPS), rw, rng)
        duses[dname] += 1
        ruses[rname] += 1
        duses_t[dname] += 1
        ruses_t[rname] += 1

        removed = DESTROYS[dname](cur_a, ctx, k, rng, cur)
        removed = {c for c in removed if c in cur_a}
        if not removed or len(removed) > len(ctx.comp_ids) - k + 1:
            removed = set(rng.sample(ctx.comp_ids, 1))
        cand_a = REPAIRS[rname](removed, {c: v for c, v in cur_a.items()
                                          if c not in removed}, ctx, k, rng)
        cand = evaluate_partition(ctx, _to_list(cand_a, ctx), k)

        cscore, cur_score = cand['score'], cur['score']
        improves = cscore < cur_score
        same_hard = cscore[0] == cur_score[0]
        delta = (sa_scalar(cand) - sa_scalar(cur)) / max(1.0, abs(sa_scalar(cur)))
        accept = improves or (same_hard and delta <= SA_SIDEWAYS
                              and rng.random() < math.exp(-max(0.0, delta) / T))
        if cscore[0] > cur_score[0]:
            accept = False  # hard-violation increase is strictly rejected

        reward = 0.0
        if accept:
            cur, cur_a = cand, cand_a
            dacc[dname] += 1
            racc[rname] += 1
            dacc_t[dname] += 1
            racc_t[rname] += 1
            reward = REWARD_IMPROVE if improves else REWARD_ACCEPT
            if improves and cscore < best['score']:
                best = cand
                reward += REWARD_BEST
        archive_insert(archive, cand, seen)
        if cand['hard_violations'] == 0 and (
                best_ever is None or archive_key(cand) < archive_key(best_ever)):
            best_ever = cand
        drewards[dname] += reward
        rrewards[rname] += reward
        T *= SA_DECAY

        # periodic re-anchoring: reset the search to an archive parent chosen
        # by a rotating scalarization weight (archive feedback from V2)
        if it % REANCHOR_PERIOD == 0 and archive:
            lam = LAMBDA_CYCLE[(it // REANCHOR_PERIOD) % len(LAMBDA_CYCLE)]
            parent = min(archive, key=lambda e: lam * e['shortage_total']
                         + (1.0 - lam) * e['balance_time'])
            cur = parent
            cur_a = _to_dict(list(parent['assignment']), ctx)

        if it % 20 == 0:
            trace.append(dict(
                iter=it, elapsed_s=round(time.perf_counter() - t0, 3),
                destroy=dname, repair=rname, accepted=int(accept),
                cur_hard=cur_score[0], cur_shortage=cur_score[1],
                cur_relay_extra=cur_score[2], cur_balance=cur_score[3],
                cur_redundancy=cur_score[4],
                best_shortage=best['score'][1], best_relay_extra=best['score'][2],
                best_balance=best['score'][3], best_redundancy=best['score'][4],
                temperature=round(T, 6), archive_size=len(archive)))

        if it % SEGMENT_LEN == 0:
            for n in DESTROY_OPS:
                dw[n] = (WEIGHT_FLOOR + drewards[n] / duses[n]) if duses[n] else 1.0
                if best['shortage_total'] <= 0 and n == 'shortage_targeted_removal':
                    dw[n] = 0.0
                drewards[n] = 0.0
                duses[n] = 0
            for n in REPAIR_OPS:
                rw[n] = (WEIGHT_FLOOR + rrewards[n] / ruses[n]) if ruses[n] else 1.0
                rrewards[n] = 0.0
                ruses[n] = 0

    cands_final = list(archive)
    if best_ever is not None:
        cands_final.append(best_ever)
    final = min(cands_final, key=archive_key) if cands_final else best
    op_stats = []
    for n in DESTROY_OPS:
        op_stats.append(dict(kind='destroy', operator=n, uses=duses_t[n],
                             accepted=dacc_t[n], final_weight=round(dw[n], 4)))
    for n in REPAIR_OPS:
        op_stats.append(dict(kind='repair', operator=n, uses=ruses_t[n],
                             accepted=racc_t[n], final_weight=round(rw[n], 4)))

    return dict(k=k, seed=seed, iterations=it,
                runtime_s=round(time.perf_counter() - t0, 3),
                best=final, incumbent=best, archive=archive,
                trace=trace, operator_stats=op_stats,
                label=partition_label(ctx, final))


def _try_load_coords(ctx):
    """Site coordinates from 调度中心与服务区.xlsx (optional; for geo init)."""
    try:
        import openpyxl
    except Exception:
        return None
    root = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                        '..', '..', '..', '..'))
    path = os.path.join(root, 'problem', '数据', '调度中心与服务区.xlsx')
    if not os.path.exists(path):
        return None
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        coords = {}
        for ws in wb.worksheets:
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue
            head = [str(h) if h is not None else '' for h in rows[0]]
            for row in rows[1:]:
                rid = str(row[0]) if row and row[0] is not None else ''
                if rid.startswith('S') and rid[1:].isdigit():
                    nums = [v for v in row[1:] if isinstance(v, (int, float))]
                    if len(nums) >= 2:
                        coords[rid] = (float(nums[0]), float(nums[1]))
        wb.close()
        return coords or None
    except Exception:
        return None
