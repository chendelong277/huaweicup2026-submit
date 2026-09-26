# Q3 交接说明

> **三分钟摘要**：本文档是问题三交给下游（尤其是问题四）的接口说明：列出已生成的中继计划、通信审计等结果文件及其字段出处，并给出运输架次、能耗、完工时间等关键数值。请特别注意"当前冻结边界"一节：通信覆盖结果可作候选使用，但因中继资源审计未通过，这些任务关系还不能作为问题四的最终输入。术语看不懂可查仓库根目录 `shared/glossary.md`。

## 已生成接口

- `results/relay_plan.csv`：符合 AGENTS.md 第 4.4 节中继计划字段。
- `results/communication_audit.csv`：符合 AGENTS.md 第 4.4 节通信审计字段；所有 198 个时间片均保留。
- `results/transport_plan.csv`、`delivery_timeline.csv`、`transport_resource_timeline.csv`：Q3 时序修复后的运输接口副本。
- `results/Q3_结果提交.xlsx`：统一结果模板的 Q3 两个工作表。

## 当前冻结边界

通信覆盖关系可以作为候选结果使用；但由于 `global_audit.json` 中 `relay_resource_pass=false`，本文件不声明 Q3 已形成可供 Q4 冻结的最终联合方案。Q4 只有在中继冲突和首批时限违约修复后才能使用这些任务关系。

## 关键数值

- 运输架次：18；中继任务：14。
- 通信时间片：198；通信空档：0。
- 运输能耗：59.675462 kWh；中继能耗：5.466719 kWh；联合能耗：65.142181 kWh。
- 当前保守时序修复后的联合完工时间：11363.242869 s。
- 首批时限违约：2 箱，均为 S002，约晚 19.8 s。

## 2026-09-25 ALNS-Q3-V2 严格过门方案（替代上文旧 `results/` 的冻结状态）

- 最终运输来源：`results_alns_q3_v2_hardcap_600s/seed_20260924/`，硬并发代理启用，种子 20260924、600 s；运输及每箱时序见 `trip_summary.csv`、`transport_plan.csv`、`delivery_timeline.csv`、`transport_resource_timeline.csv`、`global_audit.json`；搜索期仅五点链路采样的非支配档案见 `pareto_archive.json`（**不能视为严格通信可行的档案**）。
- 独立严格门控：同目录 `gate_whli_strict_1s/`（`gate_audit.json`、`q2_transport_whli.json`、`assembled.json`、`dense1p0s_strict.json`、`continuity_closure.json`）。两机 MILP 重新装配后的中继与严格盲区通信关系以 `assembled.json` 为准，**不应以 ALNS 输出的五点 `relay_plan.csv` 直接冻结**。1 s 严格密检 37,115 样本无断链、0.05 s 闭包通过；数值最小余量 +0.000928 dB，仍需提示鲁棒性风险。
- 冻结接口：`members/weiliu/Q4/results/frozen_alns_q3_v2_seed_20260924/` 的五份 UTF-8 CSV/JSON（`transport_trips.csv`、`relay_plan.csv`、`communication_links.csv`、`delivery_timeline.csv`、`audit_freeze.json`），用 `code/export_frozen_for_q4.py` 从上述通过 1 s 门禁的运输+严格装配结果生成；Q4 主线求解结果在 `members/weiliu/Q4/results/strict_q3_v2_seed_20260924/`。冻结输入为 24 运输架次、3 中继架次，WT=0、联合完工 6,606.55 s、能耗 74.0224 kWh。
- 上文历史 `results/` 所述未通过中继/时限审计是原始早期求解器版本的状态，不适用于本节新方案；旧 WHLi 冻结方案亦未被覆盖。
