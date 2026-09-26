# Q2 交接说明

> **三分钟摘要**：这个文档告诉下游（Q3、Q4）Q2 交付了哪些文件、每个文件是什么、能怎么用。状态是可集成，硬约束审计全部通过。要特别注意的边界：Q3 可以拿它当初解再改，Q4 不能直接用本方案，只能用 Q3 冻结后的方案。术语看不懂可查仓库根目录 `shared/glossary.md`。

## 状态

可集成。主方案硬约束审计通过，`validation_summary.json` 的 `overall_pass` 为 true。

## 正式接口

- `results/transport_plan.csv`：符合 AGENTS.md 第 4.3 节字段定义，一行表示一个架次的一个有向航段。
- `results/delivery_timeline.csv`：包含 80 箱送达时刻。`deadline_s` 对首批箱为首批截止时间，对其他箱为期望送达时间；`deadline_met` 表示对应目标时间是否达到。
- `results/transport_resource_timeline.csv`：记录实体无人机占用、电池占用及充电区间。

## 扩展接口

- `trip_summary.csv`：Q3 可直接读取路线、货箱清单、机型、实体机、电池、时刻和 SOC。
- `constraint_audit.csv`：逐项审计结果。
- `plan_comparison.csv`：不可行低能耗基线与最终可行方案的权衡证据。

## 后续使用范围

Q3 可将本方案作为无通信约束初解和对照，但可为连续通信重新组批、改序或平移时刻。Q4 不得直接使用本 Q2 方案；只能使用 Q3 冻结后的最终联合方案。

## 关键结果

24 架次，70.716666 kWh，最大完工时间 6374.351717 s；80 箱均满足对应送达时限，硬时限违约为 0。HD-ALNS 使用 540 s 搜索预算，192 项独立检查全部通过。冻结结果文件位于 `results_v2/`；`comparison_results/algorithm_comparison.csv` 保存八种对比算法与 HD-ALNS 的统一复核结果。
