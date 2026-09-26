# ALNS-Q4：任务分区与资源配置（问题四）

> **三分钟摘要**：这是问题四（任务分区与资源配置）的模块说明书。除既有 ALNS（自适应大邻域搜索）实验外，现新增基于最新 Q3 冻结输入的四种目标偏好全枚举对照，K=2/3 分别评估 255/3,025 个分区。新结果位于 `results/objective_scenarios/`，属于候选偏好分析，不自动取代正式冻结结果或论文结论。运行命令和历史结果版本均在下文注明。术语看不懂可查仓库根目录 `shared/glossary.md`。

## 当前状态

可运行。ALNS-Q4（基于本成员 Q3 的 ALNS-Q3-V2 框架改造）已实现并完成 K=2 / K=3 × 5 种子实验、全枚举基线对照与 §8.3 分区审计。

## 输入

- 冻结 Q3 联合方案：`members/WHLi/Q4/results/frozen/`（transport_trips.csv / relay_plan.csv / communication_links.csv / delivery_timeline.csv / audit_freeze.json）。
  选择依据：本成员 Q3 最新主线 `members/weiliu/Q3/results_alns_q3_refined_full_v2_20260925/global_audit.json` 中 `selected_source=pre_existing_whli_incumbent`、`relay_gate_status=fully_audited_feasible`，即最终选定方案与该 frozen 同源（22 个运输架次、2 个中继架次、18 条盲区链路）。
- 库存与充电参数：`problem/数据`（常数经 `members/WHLi/Q4/code/q4_common.py` 审定口径核对）。

## 输出（results/）

- 接口文件（AGENTS.md §4.5，UTF-8 无 BOM）：`partition_result.csv`、`resource_gap.csv`
- 算法证据：`pareto_archive.json`（按 §3.5 维护的非支配解外部档案，含冻结方案 WT/makespan/架次/能耗不变量）、`optimization_trace.csv`、`operator_statistics.csv`（含使用/接受次数）
- 验证：`enumeration_baseline.csv`、`enumeration_summary.json`（121 分区全枚举）、`global_audit.json`（§8.3 审计 + ALNS vs 枚举 gap=0 核验 + 逐种子最优对比）
- 提交：`Q4_结果提交.xlsx`（模板 Q4_分区配置 sheet）、`run_manifest.json`
- 逐种子运行：`runs/k{2,3}_seed*/`（solution.json / pareto_archive.json / trace / 算子统计 / run.json 含输入 sha256）

## 运行命令

```bash
# 全部实验（K=2,3 × 5 种子 × 60 s + 枚举 + 汇总审计），仓库根目录下：
python members/weiliu/Q4/code/run_all.py --budget 60

# 单次运行：
python members/weiliu/Q4/code/run_alns_q4.py \
    --frozen-dir members/WHLi/Q4/results/frozen \
    --k 2 --budget 60 --seed 20260924 \
    --output-dir members/weiliu/Q4/results/runs/k2_seed20260924

# 仅枚举基线 / 仅汇总：
python members/weiliu/Q4/code/q4_enumerate.py
python members/weiliu/Q4/code/make_outputs.py

# 最新 Q3 冻结输入上的四种偏好全枚举（生成到独立新目录，不覆盖旧结果）：
python -B members/weiliu/Q4/code/run_scenarios.py

# 可选：重评估既有 ALNS solution.json，与同一全枚举候选集对照；不会重新运行 ALNS：
python -B members/weiliu/Q4/code/run_scenarios.py --run-alns
```

## 偏好场景候选分析

输入为 `results/frozen_alns_q3_v2_seed_20260924/`（9 个不可拆组件、24 个运输架次、3 个中继架次）。`results/objective_scenarios/` 保存全枚举候选、四种偏好选解、非支配前沿、逐方案接口表和输入 SHA256 清单。K=2/K=3 枚举数为 255/3,025；运行未调用优化求解器。

| 偏好 | K | 分区代表 | 缺口总数 | 均衡比 | 结果说明 |
|---|---:|---|---:|---:|---|
| 缺口优先 | 2 | `{S012}|{其余14区}` | 4 | 1.917119 | 不在库存内 |
| 缺口优先 | 3 | `{S012}|{S015}|{其余13区}` | 6 | 2.757457 | 不在库存内 |
| 工作量均衡优先 | 2 | `{S001,S006,S007,S009,S010,S012,S013,S014,S015}|{其余6区}` | 16 | 1.003357 | 不在库存内 |
| 工作量均衡优先 | 3 | `{S001,S006,S007,S013,S014}|{S002,S004,S005,S010,S012,S015}|{S003,S008,S009,S011}` | 19 | 1.061178 | 不在库存内 |
| 均衡约束下缺口优先 | 2 | `{S001,S003,S004,S005,S006,S007,S009,S010,S012,S013,S014,S015}|{S002,S008,S011}` | 8 | 1.482317 | 阈值 1.5；180 个候选 |
| 均衡约束下缺口优先 | 3 | `{S001,S003,S004,S005,S006,S007,S009,S010,S013,S014}|{S002,S008,S011,S015}|{S012}` | 10 | 1.980932 | 阈值 2.0；2,403 个候选 |

上表服务区集合是完整分区表达；其余区集合由 S001–S015 中未列出的节点组成。Pareto 前沿按缺口、均衡比、中继复制、冗余比较，K=2/K=3 分别有 18/29 个非支配点。`--run-alns` 输出的 `saved_alns_candidate_comparison.json` 仅重评估已保存 ALNS 解，不代表重跑 ALNS 或替代全枚举。

## 依赖

Python 3.8（本仓库 weiliu 代码统一版本），标准库 + openpyxl（仅模板填充可选步骤使用）。无其他第三方依赖。

## 已知限制

- 本规模（6 组件、121 分区）下全枚举即为严格最优；ALNS-Q4 的价值定位是可扩展启发式框架与按 §3.5 输出的非支配档案证据链，论文中不应宣称其在本规模上优于枚举。ALNS 结果与枚举最优的一致性（gap=0）已作为正确性证据写入 `global_audit.json`。
- 词典序口径与 WHLi 主线不同（见 `assumptions.md` A3）：本包按"硬违约→总缺口→中继复制→均衡比→冗余"选解；WHLi 推荐解（零中继复制优先）也在 `enumeration_summary.json` 的 `whli_recommended` 字段中给出，两种口径结果均可复现。
- 冻结输入的 `audit_freeze.json` meta 标注 `retimed_stage5_pending_dense_check`（WHLi 侧时刻重排后的稠密通信复检状态），该风险随冻结输入继承，与本包分区算法无关。
