# -*- coding: utf-8 -*-
"""ALNS-Q4 core: frozen-solution loading, components, resource accounting, evaluation.

Q4 (task partition & resource allocation) takes the frozen Q3 joint plan and
assigns indivisible components (service areas coupled by shared transport
trips) to K task groups.  With frozen times, same-type resources are
interchangeable, so the minimum count for a group equals the peak concurrency
of its occupancy interval set (interval graphs are perfect -> peak is exact).

Frozen input: members/WHLi/Q4/results/frozen/ (CSV export of the Q3 main line;
weiliu's Q3 refined pipeline selected the same incumbent lineage,
see members/weiliu/Q3/results_alns_q3_refined_full_v2_20260925/global_audit.json
with selected_source=pre_existing_whli_incumbent).

Accounting rules (aligned with members/WHLi/Q4 audited constants):
- transport UAV of model m: [start, return_time]
- battery of model m:       [start, return_time + charge_time(soc_end, T_full_m)]
- relay drone:              [launch, ready]   (ready includes 300 s turnover)
- relay energy component:   [launch, return_time + charge_time(soc_end, 1800)]
- cross-group relay sorties are conservatively replicated once per group.

All times in seconds, energy in kWh, SOC as fraction in [0, 1].
"""
import csv
import json
import os

RESOURCE_TYPES = ['transport_A', 'transport_B', 'transport_C',
                  'battery_A', 'battery_B', 'battery_C',
                  'relay', 'energy_component']

# Inventory verified against 运输无人机数据.xlsx / 中继无人机数据.xlsx
INVENTORY = {
    'transport_A': 4, 'transport_B': 2, 'transport_C': 2,
    'battery_A': 6, 'battery_B': 4, 'battery_C': 4,
    'relay': 2, 'energy_component': 6,
}

# Full-equivalent charge time (s) per battery/component kind
CHARGE_FULL = {'A': 1800.0, 'B': 2400.0, 'C': 3000.0, 'relay_component': 1800.0}
RELAY_TURNOVER_S = 300.0  # relay drone turnaround after return

EPS = 1e-9


def charge_time(soc_fraction, charge_full):
    """Two-stage equivalent charge model: time from soc_fraction to 100%."""
    s = soc_fraction
    if s < 0.9:
        return charge_full * (0.65 * (0.9 - s) / 0.9 + 0.35)
    return charge_full * 0.35 * (1.0 - s) / 0.1


def _read_csv(path):
    with open(path, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def load_frozen(frozen_dir):
    """Load frozen transport trips / relay sorties / comm links from CSV exports.

    Returns (trips, relays, comm_links, box_attrs, meta):
    - trips: list of dicts trip, route, model, drone, battery, start,
      return_time, energy, soc, boxes
    - relays: list of dicts sortie_id, relay_drone, launch, service_start,
      service_end, return_time, ready, energy, soc, covers (list of int trip ids)
    - comm_links: list of dicts transport_trip, blind_start, blind_end, relay_sortie
    - box_attrs: box_id -> dict(site, mass)
    - meta: joint-level invariants (weighted_tardiness, makespan, energy, sorties)
    """
    trips = []
    for r in _read_csv(os.path.join(frozen_dir, 'transport_trips.csv')):
        trips.append(dict(
            trip=int(r['trip_id']),
            route=[s for s in r['route'].split('>') if s],
            model=r['model'],
            drone=r['drone_id'],
            battery=r['battery_id'],
            start=float(r['start_time_s']),
            return_time=float(r['return_time_s']),
            energy=float(r['energy_kwh']),
            soc=float(r['soc_end']),
            boxes=[b for b in r['box_ids'].split(';') if b],
        ))
    relays = []
    for r in _read_csv(os.path.join(frozen_dir, 'relay_plan.csv')):
        relays.append(dict(
            sortie_id=r['relay_task_id'],
            relay_drone=r['relay_id'],
            launch=float(r['depart_time_s']),
            service_start=float(r['service_start_s']),
            service_end=float(r['service_end_s']),
            return_time=float(r['return_time_s']),
            ready=float(r['ready_time_s']),
            energy=float(r['energy_kwh']),
            soc=float(r['soc_end']),
            covers=[int(x) for x in r['covers_trips'].split(';') if x],
        ))
    comm_links = []
    for r in _read_csv(os.path.join(frozen_dir, 'communication_links.csv')):
        comm_links.append(dict(
            transport_trip=int(r['trip_id']),
            blind_start=float(r['blind_start_s']),
            blind_end=float(r['blind_end_s']),
            relay_sortie=r['relay_task_id'],
        ))
    box_attrs = {}
    for r in _read_csv(os.path.join(frozen_dir, 'delivery_timeline.csv')):
        box_attrs[r['box_id']] = dict(site=r['service_node'],
                                      mass=float(r['box_mass_kg']))

    audit_path = os.path.join(frozen_dir, 'audit_freeze.json')
    ameta = {}
    if os.path.exists(audit_path):
        with open(audit_path, encoding='utf-8') as f:
            ameta = json.load(f).get('meta', {})
    meta = dict(
        weighted_tardiness_s=float(ameta.get('weighted_tardiness', 0.0)),
        transport_makespan_s=float(ameta.get('makespan_s',
            max(t['return_time'] for t in trips))),
        joint_makespan_s=float(ameta.get('joint_makespan_s',
            max([t['return_time'] for t in trips] + [r['ready'] for r in relays]))),
        transport_energy_kwh=float(ameta.get('transport_energy_kwh',
            sum(t['energy'] for t in trips))),
        relay_energy_kwh=float(ameta.get('relay_energy_kwh',
            sum(r['energy'] for r in relays))),
        sorties_total=len(trips) + len(relays),
        status=ameta.get('status', 'unknown'),
    )
    meta['joint_energy_kwh'] = meta['transport_energy_kwh'] + meta['relay_energy_kwh']
    return trips, relays, comm_links, box_attrs, meta


def build_components(trips):
    """Union-find merge of service areas sharing a transport trip.

    Rule (problem statement): areas visited by the same transport trip must
    belong to the same task group.  Returns (components, site_to_component,
    trip_to_component) with component ids C1..Cn ordered by min site number.
    """
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for t in trips:
        for s in t['route']:
            parent.setdefault(s, s)
        for s in t['route'][1:]:
            union(t['route'][0], s)

    roots = sorted({find(s) for s in parent}, key=lambda r: min(
        int(s[1:]) for s in parent if find(s) == r))
    comp_of_root = {r: 'C%d' % (i + 1) for i, r in enumerate(roots)}
    components = {}
    site_to_component = {}
    for s in parent:
        cid = comp_of_root[find(s)]
        components.setdefault(cid, []).append(s)
        site_to_component[s] = cid
    for cid in components:
        components[cid].sort()
    trip_to_component = {t['trip']: site_to_component[t['route'][0]] for t in trips}
    return components, site_to_component, trip_to_component


def peak_concurrency(intervals):
    """Peak overlap of half-open intervals [(s, e), ...]; also the witness time."""
    events = []
    for s, e in intervals:
        if e <= s + EPS:
            continue
        events.append((s, 1))
        events.append((e, -1))
    events.sort(key=lambda x: (x[0], x[1]))
    cur = best = 0
    best_t = 0.0
    for t, d in events:
        cur += d
        if cur > best:
            best, best_t = cur, t
    return best, best_t


def trip_occupancy(trips):
    occ = {k: [] for k in RESOURCE_TYPES}
    for t in trips:
        m = t['model']
        occ['transport_' + m].append((t['start'], t['return_time']))
        chg = charge_time(t['soc'], CHARGE_FULL[m])
        occ['battery_' + m].append((t['start'], t['return_time'] + chg))
    return occ


def relay_occupancy(sorties):
    occ = {'relay': [], 'energy_component': []}
    for r in sorties:
        occ['relay'].append((r['launch'], r['ready']))
        chg = charge_time(r['soc'], CHARGE_FULL['relay_component'])
        occ['energy_component'].append((r['launch'], r['return_time'] + chg))
    return occ


def account(trips, relay_sorties):
    """Minimum required counts for one group executing trips + relay sorties.

    Returns (counts dict, witnesses dict resource_type -> peak time).
    """
    occ = trip_occupancy(trips)
    rocc = relay_occupancy(relay_sorties)
    occ['relay'] = rocc['relay']
    occ['energy_component'] = rocc['energy_component']
    counts, witnesses = {}, {}
    for rt in RESOURCE_TYPES:
        n, t_peak = peak_concurrency(occ[rt])
        counts[rt] = n
        witnesses[rt] = t_peak
    return counts, witnesses


def relay_copies_for_groups(relays, comm_links, trip_to_group):
    """Conservative cross-group replication: one copy of a frozen sortie per
    group that contains at least one covered trip (position/window unchanged)."""
    link_by_sortie = {}
    for c in comm_links:
        link_by_sortie.setdefault(c['relay_sortie'], []).append(c['transport_trip'])
    copies = []
    for r in relays:
        groups = sorted({trip_to_group[tid] for tid in link_by_sortie.get(r['sortie_id'], [])
                         if tid in trip_to_group and trip_to_group[tid] is not None})
        if not groups and trip_to_group:
            continue
        for g in groups:
            cp = dict(r)
            cp['group'] = g
            cp['copy_of'] = r['sortie_id']
            copies.append(cp)
    return copies


class Context(object):
    """Static data shared by every evaluation."""

    def __init__(self, frozen_dir):
        self.frozen_dir = frozen_dir
        self.trips, self.relays, self.comm_links, self.box_attrs, self.meta = \
            load_frozen(frozen_dir)
        self.components, self.site_to_component, self.trip_to_component = \
            build_components(self.trips)
        self.comp_ids = sorted(self.components.keys())
        self.global_counts, self.global_witnesses = account(self.trips, self.relays)
        # component <-> relay coverage (for related/failure-guided operators)
        trip_to_sortie = {}
        for c in self.comm_links:
            trip_to_sortie.setdefault(c['transport_trip'], set()).add(c['relay_sortie'])
        self.comp_to_sorties = {cid: set() for cid in self.comp_ids}
        for t in self.trips:
            cid = self.trip_to_component[t['trip']]
            self.comp_to_sorties[cid] |= trip_to_sortie.get(t['trip'], set())
        # per-component static workload (trip duration sum) for operator heuristics
        self.comp_workload = {cid: 0.0 for cid in self.comp_ids}
        for t in self.trips:
            self.comp_workload[self.trip_to_component[t['trip']]] += \
                t['return_time'] - t['start']

    def invariants(self):
        """Frozen-plan metrics that do not depend on the partition (AGENTS 3.5)."""
        m = self.meta
        return dict(weighted_tardiness_s=m['weighted_tardiness_s'],
                    makespan_s=m['joint_makespan_s'],
                    sorties_total=m['sorties_total'],
                    energy_kwh=m['joint_energy_kwh'])


def evaluate_partition(ctx, assignment, k):
    """Evaluate one assignment (list of group ids aligned with ctx.comp_ids).

    `k` is the required number of task groups; any of the k groups left empty
    (or any component left unassigned, i.e. None) counts as a hard violation.
    Returns a detail dict whose lexicographic score is
        (hard_violations, shortage_total, relay_extra, balance_time, redundancy_total)
    Unassigned components (None) are counted in hard_violations and their trips
    are excluded from every group (used transiently inside repair operators).
    """
    n_unassigned = sum(1 for v in assignment if v is None)
    comp_to_group = {c: assignment[i] for i, c in enumerate(ctx.comp_ids)}
    trip_to_group = {t['trip']: comp_to_group[ctx.trip_to_component[t['trip']]]
                     for t in ctx.trips
                     if comp_to_group[ctx.trip_to_component[t['trip']]] is not None}
    copies = relay_copies_for_groups(ctx.relays, ctx.comm_links, trip_to_group)

    groups = []
    for g in range(k):
        g_trips = [t for t in ctx.trips if trip_to_group.get(t['trip']) == g]
        g_copies = [c for c in copies if c['group'] == g]
        counts, witnesses = account(g_trips, g_copies)
        groups.append(dict(
            group=g,
            components=sorted(c for c in ctx.comp_ids if comp_to_group[c] == g),
            trips=sorted(t['trip'] for t in g_trips),
            counts=counts, witnesses=witnesses,
            workload_time_s=sum(t['return_time'] - t['start'] for t in g_trips),
            workload_energy_kwh=sum(t['energy'] for t in g_trips),
            box_mass_kg=sum(ctx.box_attrs[b]['mass'] for t in g_trips for b in t['boxes']),
        ))

    totals = {rt: sum(g['counts'][rt] for g in groups) for rt in RESOURCE_TYPES}
    shortage = {rt: max(0, totals[rt] - INVENTORY[rt]) for rt in RESOURCE_TYPES}
    redundancy = {rt: totals[rt] - ctx.global_counts[rt] for rt in RESOURCE_TYPES}
    w_times = [g['workload_time_s'] for g in groups]
    mean_w = sum(w_times) / k if k else 0.0

    hard = 0
    hard += sum(1 for g in groups if not g['components'])      # empty group
    hard += sum(1 for v in assignment if v is None or v < 0)   # unassigned

    ev = dict(
        k=k, assignment=tuple(assignment), groups=groups, totals=totals,
        shortage=shortage, redundancy=redundancy,
        shortage_total=sum(shortage.values()),
        redundancy_total=sum(redundancy.values()),
        resources_total=sum(totals.values()),
        relay_copy_total=len(copies), relay_extra=len(copies) - len(ctx.relays),
        balance_time=(max(w_times) / mean_w) if mean_w else 1.0,
        workload_times=w_times,
        within_inventory=all(v == 0 for v in shortage.values()),
        hard_violations=hard,
    )
    ev['score'] = (ev['hard_violations'], ev['shortage_total'], ev['relay_extra'],
                   round(ev['balance_time'], 6), ev['redundancy_total'])
    return ev


def partition_label(ctx, ev):
    parts = []
    for g in ev['groups']:
        sites = []
        for c in g['components']:
            sites.extend(ctx.components[c])
        parts.append('{%s}' % ','.join(sorted(sites)))
    return '|'.join(parts)
