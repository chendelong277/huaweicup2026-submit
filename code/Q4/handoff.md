# Q4 handoff

> **三分钟摘要**：这是问题四的交接清单，列出全部产出文件（分区结果、资源缺口表、候选方案档案、审计报告、提交用 Excel 等）的位置、字段含义和单位口径，写论文或后续模块可直接引用。本次没有改动任何接口格式；若上游定稿方案更新，重跑 `run_all.py` 即可重新生成。术语看不懂可查仓库根目录 `shared/glossary.md`。

## 数据版本

- 冻结输入：`members/WHLi/Q4/results/frozen/`（与本成员 Q3 refined_full_v2 选定方案同源；各输入文件 sha256 见 `results/runs/*/run.json`）。
- 代码版本：本目录 code/（提交后补 commit id）。

## 输出文件（results/）

| 文件 | 内容 | 口径/单位 |
|---|---|---|
| `partition_result.csv` | AGENTS §4.5：partition_count, group_id, service_node, component_id, workload_time_s, workload_energy_kwh, box_mass_kg | 时间 s、能量 kWh、质量 kg；组级量按箱质量份额摊到服务区 |
| `resource_gap.csv` | §4.5：逐组 8 类资源需求 + TOTAL 行（需求合计、库存、冗余、缺口、缺口原因含峰值时刻） | 计数为架/组数；原因文本中文 |
| `pareto_archive.json` | §3.5 外部档案：按 K 分组的非支配解集，目标向量 (shortage_total, balance_time, redundancy_total, relay_extra)，附冻结不变量 (WT, makespan, sorties, energy) | 均衡比为 max/mean，无量纲 |
| `global_audit.json` | §8.3 审计（6 项检查/K）、ALNS vs 枚举 gap=0 核验、逐种子最优、枚举最优对照 | — |
| `enumeration_baseline.csv` / `enumeration_summary.json` | 121 个分区全表 + Pareto 前沿 + 本口径与 WHLi 口径的四种选解 | — |
| `runs/k{2,3}_seed*/` | 逐种子 solution.json、pareto_archive.json、optimization_trace.csv、operator_statistics.csv、run.json（含输入 sha256、迭代数、耗时） | — |
| `Q4_结果提交.xlsx` | 题目模板 Q4_分区配置 sheet 已填（K×组 每行：服务区列表 + 8 类资源数） | — |
| `run_manifest.json` | 运行汇总与选解记录 | — |

## 可供后续模块使用的范围

- 论文 Q4 章节的两种口径结果（本包 lexicographic 与 WHLi recommended）均可引用，引用时注明口径差异（assumptions.md A3）。
- `enumeration_summary.json` 的 Pareto 前沿可用于论文"分区方式权衡"图。
- 逐种子一致性、gap=0 核验、算子统计可作为算法正确性与自适应机制证据。

## 破坏性说明

无接口变更；`resource_gap.csv` 严格按 §4.5 字段（未加 WHLi 的 global_shared_count 扩展列）。冻结输入若更新（dense check 后重排时刻），重跑 `run_all.py` 即可，组件结构可能变化（断点：同架次耦合依赖 route）。

## 2026-09-25：ALNS-Q3-V2 自产运输 + 严格重装配中继版本

- **新冻结输入**：`results/frozen_alns_q3_v2_seed_20260924/`，从 `members/weiliu/Q3/results_alns_q3_v2_hardcap_600s/seed_20260924/` 的运输方案与 `gate_whli_strict_1s/assembled.json` 的严格重装配中继/通信关系导出；门控状态 `fully_audited_feasible`、1 s 密检 0 断链、0.05 s 闭包数值通过。`audit_freeze.json` 标记真实生成链，5 个文件 SHA256 在新目录逐种子 `run.json` 记录。冻结 WT=0、联合完工 6,606.55 s、运输/中继 24/3 架次、总能耗 74.0224 kWh。
- **新结果目录**：`results/strict_q3_v2_seed_20260924/`，接口 `partition_result.csv`、`resource_gap.csv`、`pareto_archive.json`、`global_audit.json`、`enumeration_baseline.csv`、`enumeration_summary.json`、`run_manifest.json`、`runs/k{2,3}_seed*/`；字段和上表相同。复现命令：`python members/weiliu/Q4/code/run_strict_frozen.py --frozen-dir members/weiliu/Q4/results/frozen_alns_q3_v2_seed_20260924 --results-dir members/weiliu/Q4/results/strict_q3_v2_seed_20260924 --budget 60`。
- 新输入压缩为 9 个组件（非旧 6 个），K=2/3 全枚举分别 255/3,025 个；各 K 五种子分区审计全部通过、枚举 gap=0。本口径 K=2 缺口 4（`{S012}|{其余14区}`），K=3 缺口 6（`{S012}|{S015}|{其余13区}`）。旧根目录 `results/`（WHLi 冻结输入、缺口 3/5）仅为另版本对照，不应与本节数值混用。
- 未改 §4.5 接口字段和含义；这是**上游冻结输入与结果目录新增**，而非静默替换历史文件。上述结果新目录未包含 `Q4_结果提交.xlsx`，如需模板须明确指定该输入并由对应结果生成，不可沿用旧模板。

## 偏好场景候选分析交接

- **输入**：`members/weiliu/Q4/results/frozen_alns_q3_v2_seed_20260924/`，代码会记录 5 个冻结文件 SHA256；冻结不变量为 WT=0、联合完工 6,606.55 s、运输/中继架次 24/3、总能耗 74.0224 kWh。
- **运行**：`python -B members/weiliu/Q4/code/run_scenarios.py`；可选 `--run-alns` 只重评估 `strict_q3_v2_seed_20260924/runs/k{2,3}_seed*/solution.json`，不启动 ALNS。
- **输出**：`results/objective_scenarios/` 下有 `scenario_summary.csv`、`all_partitions.csv`、`pareto_front.csv`、`scenario_results.json`、`components.json`、`run_manifest.json`、README，以及每个场景/K 的 `partition_result.csv` 和 `resource_gap.csv`。启用对比后另有 `saved_alns_candidate_comparison.json`。
- **候选规模**：K=2/K=3 全枚举 255/3,025；Pareto 前沿 18/29；均衡阈值筛后候选 180/2,403。阈值为 1.5/2.0，未自动放宽。
- **代表指标**：缺口优先为 (缺口 4/6，均衡比 1.917119/2.757457)；工作量均衡优先为 (16/19，1.003357/1.061178)；均衡约束下缺口优先为 (8/10，1.482317/1.980932)。代表点均 `within_inventory=false`。
- **使用边界**：新目录是冻结输入上的本地候选偏好分析，完整枚举是本规模权威候选集；它不自动替代已有正式结果、Q3 冻结方案或论文最终结论。
