# -*- coding: utf-8 -*-
"""Exact Q4 preference scenarios over the frozen Q3 candidate set."""
import csv
import hashlib
import json
import os

from q4_core import INVENTORY, RESOURCE_TYPES, evaluate_partition, partition_label
from q4_enumerate import set_partitions

SCENARIOS = ('shortage_first', 'balance_first', 'balanced_shortage_first', 'pareto')
SCENARIO_NAMES = {
    'shortage_first': '缺口优先',
    'balance_first': '工作量均衡优先',
    'balanced_shortage_first': '均衡约束下缺口优先',
    'pareto': '非支配前沿',
}
THRESHOLDS = {2: 1.5, 3: 2.0}


def _round(x):
    return round(float(x), 6)


def _dominates(a, b):
    va = (a['shortage_total'], a['balance_time'], a['relay_extra'], a['redundancy_total'])
    vb = (b['shortage_total'], b['balance_time'], b['relay_extra'], b['redundancy_total'])
    return all(x <= y for x, y in zip(va, vb)) and any(x < y for x, y in zip(va, vb))


def pareto_front(evs):
    return [ev for ev in evs if not any(other is not ev and _dominates(other, ev)
                                      for other in evs)]


def scenario_key(name, ev):
    hard = ev['hard_violations']
    if name == 'shortage_first':
        return (hard, ev['shortage_total'], ev['relay_extra'],
                _round(ev['balance_time']), ev['redundancy_total'])
    if name == 'balance_first':
        return (hard, max(ev['workload_times'], default=0.0),
                _round(ev['balance_time']), ev['shortage_total'],
                ev['relay_extra'], ev['redundancy_total'])
    if name == 'balanced_shortage_first':
        return (hard, ev['shortage_total'], ev['relay_extra'],
                _round(ev['balance_time']), ev['redundancy_total'])
    raise ValueError(name)


def evaluate_all(ctx):
    return {k: [evaluate_partition(ctx, list(a), k)
                for a in set_partitions(ctx.comp_ids, k)] for k in (2, 3)}


def _summary_ev(ctx, ev, scenario, k, role='selected'):
    return {
        'scenario': scenario,
        'scenario_name': SCENARIO_NAMES[scenario],
        'k': k,
        'role': role,
        'partition': partition_label(ctx, ev),
        'assignment': list(ev['assignment']),
        'hard_violations': ev['hard_violations'],
        'shortage_total': ev['shortage_total'],
        'relay_extra': ev['relay_extra'],
        'balance_time': _round(ev['balance_time']),
        'max_group_workload_s': _round(max(ev['workload_times'], default=0.0)),
        'redundancy_total': ev['redundancy_total'],
        'workload_times_s': [_round(x) for x in ev['workload_times']],
        'shortage': ev['shortage'],
        'redundancy': ev['redundancy'],
        'totals': ev['totals'],
        'within_inventory': ev['within_inventory'],
        'score': list(ev['score']),
    }


def choose_scenarios(ctx, all_evs):
    result = {}
    for name in SCENARIOS:
        result[name] = {}
        for k in (2, 3):
            evs = all_evs[k]
            eligible = evs
            threshold = None
            if name == 'balanced_shortage_first':
                threshold = THRESHOLDS[k]
                eligible = [ev for ev in evs if ev['balance_time'] <= threshold + 1e-9]
            if name == 'pareto':
                front = pareto_front([ev for ev in evs if ev['hard_violations'] == 0])
                front.sort(key=lambda ev: (ev['shortage_total'], _round(ev['balance_time']),
                                           ev['relay_extra'], ev['redundancy_total'],
                                           partition_label(ctx, ev)))
                shortage = min(front, key=lambda ev: (ev['shortage_total'], ev['relay_extra'],
                                                       _round(ev['balance_time']),
                                                       ev['redundancy_total'])) if front else None
                balance = min(front, key=lambda ev: (_round(ev['balance_time']),
                                                      ev['shortage_total'], ev['relay_extra'],
                                                      ev['redundancy_total'])) if front else None
                threshold_evs = [ev for ev in front if ev['balance_time'] <= THRESHOLDS[k] + 1e-9]
                balanced = min(threshold_evs, key=lambda ev: (ev['shortage_total'],
                                                                ev['relay_extra'],
                                                                _round(ev['balance_time']),
                                                                ev['redundancy_total'])) if threshold_evs else None
                result[name][k] = {
                    'candidate_count': len(evs), 'eligible_count': len(front),
                    'threshold': THRESHOLDS[k],
                    'threshold_candidate_count': len(threshold_evs),
                    'no_candidate': not bool(front),
                    'front': front,
                    'representatives': {
                        'shortage_first': shortage,
                        'balance_first': balance,
                        'balanced_threshold': balanced,
                    },
                }
                continue
            selected = min(eligible, key=lambda ev: scenario_key(name, ev)) if eligible else None
            result[name][k] = {
                'candidate_count': len(evs), 'eligible_count': len(eligible),
                'threshold': threshold,
                'threshold_candidate_count': len(eligible) if threshold is not None else None,
                'no_candidate': selected is None,
                'selected': selected,
            }
    return result


def serialize_scenario_results(ctx, chosen):
    out = {}
    for name in SCENARIOS:
        out[name] = {}
        for k in (2, 3):
            item = chosen[name][k]
            if name == 'pareto':
                out[name][str(k)] = {
                    key: ([_summary_ev(ctx, ev, name, k, 'pareto_front') for ev in val]
                          if key == 'front' else
                          {role: (_summary_ev(ctx, ev, name, k, role)
                                  if ev is not None else None)
                           for role, ev in val.items()})
                    if key in ('front', 'representatives') else val
                    for key, val in item.items() if key not in ('front', 'representatives')
                }
                out[name][str(k)]['front'] = [_summary_ev(ctx, ev, name, k, 'pareto_front')
                                              for ev in item['front']]
                out[name][str(k)]['representatives'] = {
                    role: (_summary_ev(ctx, ev, name, k, role) if ev is not None else None)
                    for role, ev in item['representatives'].items()
                }
            else:
                out[name][str(k)] = dict(
                    candidate_count=item['candidate_count'],
                    eligible_count=item['eligible_count'],
                    threshold=item['threshold'],
                    threshold_candidate_count=item['threshold_candidate_count'],
                    no_candidate=item['no_candidate'],
                    selected=(_summary_ev(ctx, item['selected'], name, k)
                              if item['selected'] is not None else None))
    return out


def candidate_row(ctx, k, ev):
    return {
        'k': k, 'partition': partition_label(ctx, ev),
        'assignment': ';'.join(str(x) for x in ev['assignment']),
        'hard_violations': ev['hard_violations'],
        'shortage_total': ev['shortage_total'],
        'relay_extra': ev['relay_extra'],
        'balance_time': _round(ev['balance_time']),
        'max_group_workload_s': _round(max(ev['workload_times'], default=0.0)),
        'redundancy_total': ev['redundancy_total'],
        'workload_times_s': ';'.join('%.6f' % x for x in ev['workload_times']),
        'within_inventory': str(bool(ev['within_inventory'])).lower(),
    }


def source_manifest(frozen_dir):
    files = {}
    for name in sorted(os.listdir(frozen_dir)):
        path = os.path.join(frozen_dir, name)
        if os.path.isfile(path):
            h = hashlib.sha256()
            with open(path, 'rb') as f:
                for block in iter(lambda: f.read(1024 * 1024), b''):
                    h.update(block)
            files[name] = h.hexdigest()
    return files


def write_csv(path, fieldnames, rows):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def write_partition_interfaces(ctx, ev, out_dir, k):
    os.makedirs(out_dir, exist_ok=True)
    part_rows = []
    for g in ev['groups']:
        for cid in g['components']:
            for site in ctx.components[cid]:
                part_rows.append({
                    'partition_count': k, 'group_id': 'G%d' % (g['group'] + 1),
                    'service_node': site, 'component_id': cid,
                    'workload_time_s': '%.6f' % g['workload_time_s'],
                    'workload_energy_kwh': '%.6f' % g['workload_energy_kwh'],
                    'box_mass_kg': '%.6f' % g['box_mass_kg'],
                })
    write_csv(os.path.join(out_dir, 'partition_result.csv'), list(part_rows[0]) if part_rows else [], part_rows)
    fields = ['partition_count', 'group_id', 'resource_type', 'required_count',
              'inventory_count', 'redundancy_count', 'shortage_count', 'shortage_reason']
    rows = []
    for g in ev['groups']:
        for rt in RESOURCE_TYPES:
            rows.append({'partition_count': k, 'group_id': 'G%d' % (g['group'] + 1),
                         'resource_type': rt, 'required_count': g['counts'][rt],
                         'inventory_count': INVENTORY[rt], 'redundancy_count': '',
                         'shortage_count': '', 'shortage_reason': ''})
    for rt in RESOURCE_TYPES:
        reason = ''
        if ev['shortage'][rt]:
            peaks = ['G%d需%d(峰值t≈%.0fs)' % (g['group'] + 1, g['counts'][rt],
                                                g['witnesses'][rt])
                     for g in ev['groups'] if g['counts'][rt]]
            reason = '；'.join(peaks) + '，合计%d>库存%d，任务执行期间不得跨组调配' % (
                ev['totals'][rt], INVENTORY[rt])
        rows.append({'partition_count': k, 'group_id': 'TOTAL', 'resource_type': rt,
                     'required_count': ev['totals'][rt], 'inventory_count': INVENTORY[rt],
                     'redundancy_count': ev['redundancy'][rt],
                     'shortage_count': ev['shortage'][rt], 'shortage_reason': reason})
    write_csv(os.path.join(out_dir, 'resource_gap.csv'), fields, rows)
