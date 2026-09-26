"""Pareto-archive ALNS for the Q2 bi-objective transport scheduling problem.

Version mo-r1.  Objectives: min (weighted_tardiness_s, makespan_s).

Hard constraints (payload/volume/energy/SOC/resource calendars and the
ORIGINAL hard deadlines) are enforced by chendelong's unchanged base
``Decoder``: an infeasible decode returns None and the trial is rejected.
The per-box expected delivery time is demoted to a SOFT objective and only
appears as weighted tardiness in the score (verified against q2_search.py:
``decode`` compares deliveries against ``deadline_s`` only, and the warm
solution q2_order/seed_0 decodes fine with weighted_tardiness_s = 174093.2).

The controller reuses E005/alns_plain's design, Pareto-ified:

* the same 8 base operators (E005 showed its 3 extra operators have negative
  net value, so they are NOT included);
* adaptive operator weights  w <- 0.95*w + 0.05*reward  with rewards
  8 (archive insert that dominated archive members) / 3 (non-dominated
  insert) / 1 (accepted but not archived) / 0.2 (rejected);
* acceptance: dominating trial -> accept; mutually non-dominating -> accept
  (exploration); dominated trial -> annealed acceptance with
  delta = sum_j max(0, trial_j - current_j)/SCALE[j], P = exp(-delta/T),
  T = 0.012*(1-progress)^2 + 0.00005;
* every feasible trial is offered to a non-dominated archive (limit 32,
  crowding-distance pruning from research/pareto_lns.add_archive);
* stagnation restart: after 80 consecutive iterations without an archive
  insertion, ``current`` is reset to a random archive member.

Compatibility note: this repository runs under Python 3.8 while
chendelong's pipeline sources use PEP 585/604 annotations (``list[str]``,
``dict | None``).  ``install_py38_compat`` recompiles those modules with the
PEP 563 ``annotations`` future flag through a meta-path finder, bypassing
stale bytecode caches.  No file of another member is modified on disk.
"""
from __future__ import annotations

import __future__
import importlib.abc
import importlib.machinery
import json
import math
import random
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
CHENDELONG = REPO / "members" / "chendelong"

VERSION = "mo-r1"
OPERATORS = ["swap_order", "relocate_order", "type", "route",
             "relocate_boxes", "exchange_boxes", "merge", "split"]
SCALE = (174000.0, 6400.0)          # warm-start endpoint magnitudes (WT, makespan)
ARCHIVE_LIMIT = 32
STAGNATION_LIMIT = 80
MAX_STOPS_PER_TRIP = 3
TEMP0 = 0.012
TEMP_MIN = 0.00005
WEIGHT_SMOOTHING = 0.95
REWARD_DOMINATING_INSERT = 8.0
REWARD_NDOM_INSERT = 3.0
REWARD_ACCEPTED = 1.0
REWARD_REJECTED = 0.2


def install_py38_compat(root: Path) -> None:
    """Import chendelong's 3.9+-syntax modules under Python 3.8, read-only."""
    root_str = str(root.resolve())
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    sys.dont_write_bytecode = True  # never write .pyc into another member's tree
    flags = __future__.annotations.compiler_flag

    class LazyAnnotationLoader(importlib.machinery.SourceFileLoader):
        def get_code(self, fullname):
            with open(self.path, "rb") as fh:
                src = fh.read().decode("utf-8")
            return compile(src, self.path, "exec", flags=flags, dont_inherit=True)

    class CompatFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
            if spec and spec.origin and str(Path(spec.origin).resolve()).startswith(root_str):
                spec.loader = LazyAnnotationLoader(fullname, spec.origin)
            return spec

    marker = "_mo_py38_compat_root"
    if not any(getattr(f, marker, None) == root_str for f in sys.meta_path):
        finder = CompatFinder()
        setattr(finder, marker, root_str)
        sys.meta_path.insert(0, finder)


install_py38_compat(CHENDELONG)

from pipeline.data import load_instance  # noqa: E402
from pipeline.algorithms.q2_search import Decoder, _task, _mutate  # noqa: E402
from pipeline.evaluation import evaluate_transport  # noqa: E402
from research.pareto_lns import add_archive, dominates  # noqa: E402

WARM_STARTS = [
    ("e001_q2_full_seed_1", CHENDELONG / "experiments/e001/q2_full/seed_1/solution.json"),
    ("e001_q2_order_seed_0", CHENDELONG / "experiments/e001/q2_order/seed_0/solution.json"),
    ("e001_q2_energy_seed_0", CHENDELONG / "experiments/e001/q2_energy/seed_0/solution.json"),
]


def tasks_of(instance, solution) -> tuple:
    return tuple(_task(instance.data, p["box_ids"], p["route"], p["vehicle_type"])
                 for p in solution["trips"])


def load_warm_starts(instance, paths=None):
    """Decode and independently audit each warm start; refuse infeasible ones."""
    import hashlib
    warm = []
    for label, path in (paths or WARM_STARTS):
        blob = Path(path).read_bytes()
        solution = json.loads(blob.decode("utf-8"))
        evaluation = evaluate_transport(instance, solution)
        if evaluation["feasible"] is not True:
            raise ValueError("warm start %s failed evaluate_transport: %s"
                             % (label, evaluation["violations"][:3]))
        warm.append(dict(label=label, path=str(path), sha256=hashlib.sha256(blob).hexdigest(),
                         solution=solution,
                         weighted_tardiness_s=evaluation["metrics"]["weighted_tardiness_s"],
                         makespan_s=evaluation["metrics"]["makespan_s"]))
    return warm


def run_seed(instance, warm_tasks, seed: int, budget_s: float,
             archive_limit: int = ARCHIVE_LIMIT) -> dict:
    """One Pareto-ALNS run.  ``warm_tasks``: list of (label, tasks)."""
    started = time.perf_counter()
    deadline = started + budget_s
    rng = random.Random(seed)
    decoder = Decoder(instance)
    archive = []  # entries: ((wt, makespan), payload dict)
    evaluations = 0

    def decode(tasks):
        nonlocal evaluations
        evaluations += 1
        return decoder.decode(tasks)

    for label, tasks in warm_tasks:
        score = decode(tasks)
        if score is None:
            raise ValueError("warm start %s does not decode under the base Decoder" % label)
        add_archive(archive, (score[0], score[1]),
                    dict(tasks=tasks, origin=label, found_iteration=0,
                         elapsed_s=time.perf_counter() - started), limit=archive_limit)
    if not archive:
        raise RuntimeError("empty initial archive")

    current_entry = rng.choice(archive)
    current_tasks, current_obj = current_entry[1]["tasks"], current_entry[0]
    weights = {op: 1.0 for op in OPERATORS}
    attempted, mutation_failed, infeasible, feasible = Counter(), Counter(), Counter(), Counter()
    accepted, inserts_dominating, inserts_nondominated = Counter(), Counter(), Counter()
    reward_sum = Counter()
    iterations, restarts, stagnation = 0, 0, 0
    trace = []
    last_snapshot = started

    while time.perf_counter() < deadline:
        iterations += 1
        op = rng.choices(OPERATORS, [weights[o] for o in OPERATORS])[0]
        attempted[op] += 1
        proposed = _mutate(current_tasks, op, rng, decoder, MAX_STOPS_PER_TRIP)
        if proposed is None:
            mutation_failed[op] += 1
            reward = REWARD_REJECTED
        else:
            score = decode(tuple(proposed))
            if score is None:
                infeasible[op] += 1
                reward = REWARD_REJECTED
            else:
                feasible[op] += 1
                trial, obj = tuple(proposed), (score[0], score[1])
                dominates_existing = any(dominates(obj, entry[0]) for entry in archive)
                inserted = add_archive(
                    archive, obj,
                    dict(tasks=trial, origin="seed_%d" % seed,
                         found_iteration=iterations,
                         elapsed_s=time.perf_counter() - started),
                    limit=archive_limit)
                if inserted:
                    if dominates_existing:
                        inserts_dominating[op] += 1
                        reward = REWARD_DOMINATING_INSERT
                    else:
                        inserts_nondominated[op] += 1
                        reward = REWARD_NDOM_INSERT
                    trace.append(dict(iteration=iterations,
                                      elapsed_s=round(time.perf_counter() - started, 3),
                                      operator=op, kind="dominating" if dominates_existing else "nondominated",
                                      weighted_tardiness_s=obj[0], makespan_s=obj[1],
                                      archive_size=len(archive)))
                # Acceptance on the two objectives.
                if dominates(obj, current_obj) or not dominates(current_obj, obj):
                    accept = True  # dominating trial, equal, or mutually non-dominating
                else:
                    progress = min(1.0, (time.perf_counter() - started) / budget_s)
                    temperature = TEMP0 * (1.0 - progress) ** 2 + TEMP_MIN
                    delta = sum(max(0.0, (obj[j] - current_obj[j]) / SCALE[j]) for j in range(2))
                    accept = rng.random() < math.exp(-delta / temperature)
                if accept:
                    accepted[op] += 1
                    current_tasks, current_obj = trial, obj
                    if not inserted:
                        reward = REWARD_ACCEPTED
                elif not inserted:
                    reward = REWARD_REJECTED
        reward_sum[op] += reward
        weights[op] = WEIGHT_SMOOTHING * weights[op] + (1.0 - WEIGHT_SMOOTHING) * reward
        if reward in (REWARD_DOMINATING_INSERT, REWARD_NDOM_INSERT):
            stagnation = 0
        else:
            stagnation += 1
            if stagnation >= STAGNATION_LIMIT:
                current_entry = rng.choice(archive)
                current_tasks, current_obj = current_entry[1]["tasks"], current_entry[0]
                stagnation = 0
                restarts += 1
        now = time.perf_counter()
        if now - last_snapshot >= 5.0:
            last_snapshot = now
            trace.append(dict(iteration=iterations, elapsed_s=round(now - started, 3),
                              snapshot=True, archive_size=len(archive),
                              min_weighted_tardiness_s=min(e[0][0] for e in archive),
                              min_makespan_s=min(e[0][1] for e in archive)))

    stats = dict(version=VERSION, seed=seed, budget_s=budget_s,
                 elapsed_s=time.perf_counter() - started,
                 iterations=iterations, evaluations=evaluations, restarts=restarts,
                 archive_size=len(archive), profile_cache_size=len(decoder.profiles),
                 operators=OPERATORS,
                 operator_stats={op: dict(attempted=attempted[op],
                                          mutation_failed=mutation_failed[op],
                                          infeasible=infeasible[op], feasible=feasible[op],
                                          accepted=accepted[op],
                                          inserts_dominating=inserts_dominating[op],
                                          inserts_nondominated=inserts_nondominated[op],
                                          reward_total=round(reward_sum[op], 4),
                                          final_weight=round(weights[op], 6))
                                 for op in OPERATORS},
                 trace=trace)
    return dict(archive=archive, stats=stats, decoder=decoder)


def materialize_archive(run: dict) -> list:
    """Materialise full decisions for every archived solution."""
    decoder = run["decoder"]
    out = []
    for obj, payload in run["archive"]:
        score, solution = decoder.decode(payload["tasks"], materialize=True)
        if abs(score[0] - obj[0]) > 1e-6 or abs(score[1] - obj[1]) > 1e-6:
            raise RuntimeError("archive score mismatch on re-decode: %s vs %s" % (score, obj))
        out.append(dict(weighted_tardiness_s=score[0], makespan_s=score[1],
                        energy_kwh=score[2], sorties=len(solution["trips"]),
                        origin=payload["origin"], found_iteration=payload["found_iteration"],
                        found_elapsed_s=payload["elapsed_s"],
                        tasks=[[list(t[0]), list(t[1]), t[2]] for t in payload["tasks"]],
                        trips=solution["trips"]))
    out.sort(key=lambda r: (r["weighted_tardiness_s"], r["makespan_s"]))
    return out
