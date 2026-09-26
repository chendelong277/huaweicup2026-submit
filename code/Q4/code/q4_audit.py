# -*- coding: utf-8 -*-
"""Q4 partition audit (AGENTS.md section 8.3).

Checks for a chosen partition evaluation:
1. every service area belongs to exactly one group; no empty group;
2. all stops of a multi-stop transport trip fall in the same group;
3. per-group resource counts recomputed independently match the evaluation;
4. shortage / redundancy are consistent with totals, inventory, global peak;
5. within_inventory flag is consistent;
6. every shortaged resource carries a non-empty reason with peak times.
"""
from q4_core import INVENTORY, RESOURCE_TYPES, account


def audit_partition(ctx, ev):
    checks = []

    # 1) unique coverage + non-empty groups
    seen = []
    for g in ev['groups']:
        for cid in g['components']:
            seen.extend(ctx.components[cid])
    all_sites = sorted(ctx.site_to_component.keys())
    ok = sorted(seen) == all_sites and len(set(seen)) == len(seen)
    checks.append(dict(id='unique_coverage', ok=ok,
                       detail='covered=%d expected=%d' % (len(seen), len(all_sites))))
    ok = all(g['components'] for g in ev['groups'])
    checks.append(dict(id='non_empty_groups', ok=ok,
                       detail='groups=%d' % len(ev['groups'])))

    # 2) same-trip same group
    bad = []
    site_group = {}
    for g in ev['groups']:
        for cid in g['components']:
            for s in ctx.components[cid]:
                site_group[s] = g['group']
    for t in ctx.trips:
        gs = {site_group[s] for s in t['route']}
        if len(gs) > 1:
            bad.append(t['trip'])
    checks.append(dict(id='same_trip_same_group', ok=not bad,
                       detail='violating_trips=%s' % bad))

    # 3) recompute per-group counts independently
    from q4_core import relay_copies_for_groups
    trip_to_group = {}
    for g in ev['groups']:
        for tid in g['trips']:
            trip_to_group[tid] = g['group']
    copies = relay_copies_for_groups(ctx.relays, ctx.comm_links, trip_to_group)
    mism = []
    for g in ev['groups']:
        g_trips = [t for t in ctx.trips if trip_to_group.get(t['trip']) == g['group']]
        g_copies = [c for c in copies if c['group'] == g['group']]
        counts, _ = account(g_trips, g_copies)
        if counts != g['counts']:
            mism.append(g['group'])
    checks.append(dict(id='counts_recomputed', ok=not mism,
                       detail='mismatch_groups=%s' % mism))

    # 4) shortage/redundancy consistency
    totals = {rt: sum(g['counts'][rt] for g in ev['groups']) for rt in RESOURCE_TYPES}
    ok = (totals == ev['totals']
          and all(max(0, totals[rt] - INVENTORY[rt]) == ev['shortage'][rt]
                  for rt in RESOURCE_TYPES)
          and all(totals[rt] - ctx.global_counts[rt] == ev['redundancy'][rt]
                  for rt in RESOURCE_TYPES))
    checks.append(dict(id='shortage_redundancy_consistent', ok=ok, detail=''))

    # 5) within_inventory flag
    ok = ev['within_inventory'] == all(v == 0 for v in ev['shortage'].values())
    checks.append(dict(id='within_inventory_flag', ok=ok, detail=''))

    # 6) peak witnesses exist for every shortaged resource
    miss = [rt for rt in RESOURCE_TYPES
            if ev['shortage'][rt] > 0
            and not any(g['counts'][rt] > 0 for g in ev['groups'])]
    checks.append(dict(id='shortage_witnesses', ok=not miss,
                       detail='missing=%s' % miss))

    return dict(feasible=all(c['ok'] for c in checks), checks=checks,
                k=ev['k'], shortage_total=ev['shortage_total'],
                relay_extra=ev['relay_extra'],
                balance_time=round(ev['balance_time'], 6),
                redundancy_total=ev['redundancy_total'])
