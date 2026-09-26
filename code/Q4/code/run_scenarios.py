# -*- coding: utf-8 -*-
"""Generate exact Q4 objective-preference comparisons for the frozen Q3 plan."""
import argparse
import csv
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from q4_core import Context, evaluate_partition, partition_label  # noqa: E402
from q4_scenarios import (SCENARIOS, SCENARIO_NAMES, THRESHOLDS, candidate_row,
                          choose_scenarios, pareto_front, scenario_key,
                          serialize_scenario_results, source_manifest,
                          write_csv, write_partition_interfaces, evaluate_all)  # noqa: E402


def _load_saved_alns(ctx, runs_dir):
    entries = []
    for path in sorted(glob.glob(os.path.join(runs_dir, 'k*_seed*', 'solution.json'))):
        with open(path, encoding='utf-8') as f:
            obj = json.load(f)
        k = int(obj['k'])
        if k not in (2, 3):
            continue
        assignment = obj.get('best', {}).get('assignment')
        if not isinstance(assignment, list) or len(assignment) != len(ctx.comp_ids):
            continue
        ev = evaluate_partition(ctx, assignment, k)
        entries.append({'k': k, 'seed': obj.get('seed'),
                        'source': os.path.relpath(path, os.getcwd()).replace('\\', '/'),
                        'evaluation': ev})
    return entries


def _scenario_select(name, k, evs):
    candidates = evs
    if name == 'balanced_shortage_first':
        candidates = [e for e in evs if e['balance_time'] <= THRESHOLDS[k] + 1e-9]
    if name == 'pareto':
        return pareto_front([e for e in evs if e['hard_violations'] == 0])
    return [min(candidates, key=lambda ev: scenario_key(name, ev))] if candidates else []


def _alns_comparison(ctx, saved, all_evs):
    comparison = {'label': 'saved_alns_candidate_comparison',
                  'method_note': '既有 ALNS 解仅重新评估；枚举仍为全局候选集权威，不是重新运行 ALNS。',
                  'entries': []}
    for name in SCENARIOS:
        for k in (2, 3):
            candidates = [x for x in saved if x['k'] == k]
            if name == 'balanced_shortage_first':
                candidates = [x for x in candidates
                              if x['evaluation']['balance_time'] <= THRESHOLDS[k] + 1e-9]
            if name == 'pareto':
                front = pareto_front([x['evaluation'] for x in candidates
                                      if x['evaluation']['hard_violations'] == 0])
                selected = [x for x in candidates if x['evaluation'] in front]
            else:
                selected = [min(candidates,
                                key=lambda x: scenario_key(name, x['evaluation']))] if candidates else []
            enum_selected = _scenario_select(name, k, all_evs[k])
            def obj_record(entry):
                ev = entry['evaluation'] if isinstance(entry, dict) and 'evaluation' in entry else entry
                return {'partition': partition_label(ctx, ev),
                        'score': list(ev['score']),
                        'shortage_total': ev['shortage_total'],
                        'relay_extra': ev['relay_extra'],
                        'balance_time': round(ev['balance_time'], 6),
                        'redundancy_total': ev['redundancy_total'],
                        'within_inventory': ev['within_inventory']}
            comparison['entries'].append({
                'scenario': name, 'scenario_name': SCENARIO_NAMES[name], 'k': k,
                'saved_alns_candidate_count': len(candidates),
                'saved_alns_best': [dict(seed=x['seed'], source=x['source'],
                                         **obj_record(x)) for x in selected],
                'enumeration_best': [obj_record(x) for x in enum_selected],
                'alns_candidate_matches_enumeration': bool(selected and enum_selected and
                    any(obj_record(x)['score'] == obj_record(y)['score']
                        for x in selected for y in enum_selected)),
            })
    return comparison


def _csv_rows(ctx, chosen):
    summary = []
    fronts = []
    for name in SCENARIOS:
        for k in (2, 3):
            item = chosen[name][k]
            if name == 'pareto':
                for ev in item['front']:
                    row = candidate_row(ctx, k, ev)
                    row.update({'scenario': name, 'scenario_name': SCENARIO_NAMES[name]})
                    fronts.append(row)
                for role, ev in item['representatives'].items():
                    summary.append(_summary_row(ctx, name, k, item, ev, role))
            else:
                summary.append(_summary_row(ctx, name, k, item, item['selected'], 'selected'))
    return summary, fronts


def _summary_row(ctx, name, k, item, ev, role):
    row = {
        'scenario': name, 'scenario_name': SCENARIO_NAMES[name], 'k': k,
        'selection_role': role, 'candidate_count': item['candidate_count'],
        'eligible_count': item['eligible_count'], 'threshold': item['threshold'],
        'threshold_candidate_count': item['threshold_candidate_count'],
        'no_candidate': str(bool(item['no_candidate'])).lower(),
    }
    if ev is not None:
        row.update(candidate_row(ctx, k, ev))
    else:
        row.update({'partition': '', 'assignment': '', 'hard_violations': '',
                    'shortage_total': '', 'relay_extra': '', 'balance_time': '',
                    'max_group_workload_s': '', 'redundancy_total': '',
                    'workload_times_s': '', 'within_inventory': ''})
    return row


def main():
    root = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
    default_frozen = os.path.join(HERE, '..', 'results', 'frozen_alns_q3_v2_seed_20260924')
    default_out = os.path.join(HERE, '..', 'results', 'objective_scenarios')
    default_alns = os.path.join(HERE, '..', 'results', 'strict_q3_v2_seed_20260924', 'runs')
    ap = argparse.ArgumentParser()
    ap.add_argument('--frozen-dir', default=default_frozen)
    ap.add_argument('--output-dir', default=default_out)
    ap.add_argument('--run-alns', action='store_true',
                    help='compare existing saved ALNS candidates; does not run ALNS')
    ap.add_argument('--alns-runs-dir', default=default_alns)
    args = ap.parse_args()
    frozen_dir = os.path.abspath(args.frozen_dir)
    out_dir = os.path.abspath(args.output_dir)
    if os.path.exists(out_dir) and os.listdir(out_dir) and not args.run_alns:
        raise RuntimeError('Refusing to overwrite non-empty output directory: %s' % out_dir)
    os.makedirs(out_dir, exist_ok=True)

    ctx = Context(frozen_dir)
    if len(ctx.comp_ids) != 9 or len(ctx.trips) != 24 or len(ctx.relays) != 3:
        raise RuntimeError('Unexpected frozen-input invariants: components=%d trips=%d relays=%d' %
                           (len(ctx.comp_ids), len(ctx.trips), len(ctx.relays)))
    all_evs = evaluate_all(ctx)
    expected = {2: 255, 3: 3025}
    counts = {str(k): len(all_evs[k]) for k in (2, 3)}
    if any(counts[str(k)] != expected[k] for k in (2, 3)):
        raise RuntimeError('Enumeration count mismatch: %r' % counts)
    chosen = choose_scenarios(ctx, all_evs)

    rows_all = []
    for k in (2, 3):
        for ev in all_evs[k]:
            rows_all.append(candidate_row(ctx, k, ev))
    all_fields = ['k', 'partition', 'assignment', 'hard_violations', 'shortage_total',
                  'relay_extra', 'balance_time', 'max_group_workload_s',
                  'redundancy_total', 'workload_times_s', 'within_inventory']
    write_csv(os.path.join(out_dir, 'all_partitions.csv'), all_fields, rows_all)
    summary, front_rows = _csv_rows(ctx, chosen)
    summary_fields = ['scenario', 'scenario_name', 'k', 'selection_role', 'candidate_count',
                      'eligible_count', 'threshold', 'threshold_candidate_count', 'no_candidate',
                      'partition', 'assignment', 'hard_violations', 'shortage_total',
                      'relay_extra', 'balance_time', 'max_group_workload_s',
                      'redundancy_total', 'workload_times_s', 'within_inventory']
    write_csv(os.path.join(out_dir, 'scenario_summary.csv'), summary_fields, summary)
    front_fields = ['scenario', 'scenario_name'] + all_fields
    write_csv(os.path.join(out_dir, 'pareto_front.csv'), front_fields, front_rows)

    for name in SCENARIOS:
        for k in (2, 3):
            item = chosen[name][k]
            selected = item.get('selected')
            if name == 'pareto':
                selected = item['representatives']['shortage_first']
            if selected is not None:
                write_partition_interfaces(ctx, selected,
                    os.path.join(out_dir, name, 'k%d' % k), k)

    scenario_json = serialize_scenario_results(ctx, chosen)
    with open(os.path.join(out_dir, 'scenario_results.json'), 'w', encoding='utf-8') as f:
        json.dump(scenario_json, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, 'components.json'), 'w', encoding='utf-8') as f:
        json.dump({'components': ctx.components, 'site_to_component': ctx.site_to_component,
                   'trip_to_component': ctx.trip_to_component}, f, ensure_ascii=False, indent=2)

    alns_comparison = None
    if args.run_alns:
        saved = _load_saved_alns(ctx, os.path.abspath(args.alns_runs_dir))
        alns_comparison = _alns_comparison(ctx, saved, all_evs)
        if not saved:
            alns_comparison['skipped_reason'] = '未找到可读取的既有 ALNS solution.json 候选。'
        with open(os.path.join(out_dir, 'saved_alns_candidate_comparison.json'), 'w',
                  encoding='utf-8') as f:
            json.dump(alns_comparison, f, ensure_ascii=False, indent=2)

    meta = ctx.invariants()
    manifest = {
        'source_frozen_dir': os.path.relpath(frozen_dir, root).replace('\\', '/'),
        'source_files_sha256': source_manifest(frozen_dir),
        'enumeration_counts': counts,
        'expected_enumeration_counts': {str(k): expected[k] for k in (2, 3)},
        'components_count': len(ctx.comp_ids), 'transport_trips': len(ctx.trips),
        'relay_sorties': len(ctx.relays), 'global_counts': ctx.global_counts,
        'inventory': __import__('q4_core').INVENTORY,
        'frozen_invariants': meta, 'scenarios': list(SCENARIOS),
        'balance_thresholds': {str(k): THRESHOLDS[k] for k in (2, 3)},
        'optimizer_run': False, 'authority': 'full_exhaustive_enumeration',
        'saved_alns_candidate_comparison': bool(args.run_alns),
    }
    with open(os.path.join(out_dir, 'run_manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    readme = '''# Q4 objective preference scenarios\n\n本目录是在冻结 Q3 输入上对所有组件分区进行完整枚举后的偏好对照；枚举结果是本规模下的权威候选集。本次没有运行优化求解器。它是本地候选分析，不自动取代正式冻结分区或论文最终结论。\n\n- K=2、K=3 候选数分别为 255、3025。\n- `shortage_first`：总缺口优先，其次中继复制、均衡比、冗余。\n- `balance_first`：先最小化最大组工作量，再比较均衡比、缺口、中继复制、冗余。\n- `balanced_shortage_first`：仅接受 K=2 均衡比不超过 1.5、K=3 不超过 2.0 的候选，不自动放宽阈值。\n- `pareto`：硬约束可行候选按总缺口、均衡比、中继复制、冗余求非支配前沿，并附三类代表点。\n- `saved_alns_candidate_comparison.json`（可选）：仅重评估已保存 ALNS 解，不是重新运行 ALNS。\n\n主要文件：`scenario_summary.csv`、`all_partitions.csv`、`pareto_front.csv`、`scenario_results.json`、`components.json`、`run_manifest.json`。场景/K 子目录中的 `partition_result.csv`、`resource_gap.csv` 采用 Q4 接口列。\n'''
    with open(os.path.join(out_dir, 'README.md'), 'w', encoding='utf-8') as f:
        f.write(readme)
    print('Enumeration counts: K2=%d K3=%d' % (len(all_evs[2]), len(all_evs[3])))
    for row in summary:
        if row['selection_role'] in ('selected', 'shortage_first', 'balance_first', 'balanced_threshold'):
            print('%s K=%s role=%s candidates=%s partition=%s shortage=%s balance=%s' %
                  (row['scenario'], row['k'], row['selection_role'], row['eligible_count'],
                   row['partition'], row['shortage_total'], row['balance_time']))
    if args.run_alns:
        print('Saved ALNS candidate comparison entries: %d' % len(alns_comparison['entries']))


if __name__ == '__main__':
    main()
