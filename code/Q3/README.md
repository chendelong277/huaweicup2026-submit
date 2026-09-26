# Q3 通信约束下的运输与中继联合调度

> **三分钟摘要**：本目录是问题三（通信约束下的运输与中继联合调度）的交付包，这里说明当前状态、运行命令和主要输出文件。基线结果已实现 198 个通信时间片全覆盖、空档为 0，共需 14 个中继任务，但按题面 2 架中继无人机库存执行会出现任务时间重叠，且修复时序后仍有 2 箱首批物资略超时限，因此还不能算作满足全部硬约束的最终方案。目录下另有 ALNS-Q3 和 ALNS-Q3-V2 两个改进算法变体的说明与结果。术语看不懂可查仓库根目录 `shared/glossary.md`。

## 当前状态

已完成 Q2 运输初解的 DEM 链路审计、中继候选位置搜索、通信覆盖表、联合能耗统计和结果模板填充。当前 18 个运输架次共形成 198 个连续时间片，通信空档为 0；共需 14 个中继任务。按题面库存的 2 架中继无人机执行时，存在中继任务时间重叠，且对 Q2 原运输资源日历做保守时序修复后仍有 2 箱 S002 首批物资晚于 3600 s（约 19.8 s）。因此本结果应视为“通信覆盖可行、两架中继库存下联合资源仍需进一步改序/改航段”的可复现实验结果，不把它表述为已满足全部硬约束的最终可行解。

## 运行

```text
python "members/weiliu/Q3/code/q3_solver.py" --data-root "<题目数据目录>" --output-dir "members/weiliu/Q3/results"
python "members/weiliu/Q3/code/finalize_q3_schedule.py" --q2-results "members/weiliu/Q2/results" --q3-results "members/weiliu/Q3/results"
```

## 主要输出

- `results/relay_plan.csv`：中继任务及位置、离地高度、服务窗口、能耗和 SOC。
- `results/communication_audit.csv`：逐运输架次逐时间片的直连/中继状态和链路余量。
- `results/transport_plan.csv`、`delivery_timeline.csv`、`transport_resource_timeline.csv`：Q3 时序修复后的运输接口副本。
- `results/global_audit.json`：通信连续性、资源冲突、能耗和时限审计。
- 新版运行会输出 `pareto_archive.json`、`pareto_archive_solutions.json`、`pareto_front.csv`、`pareto_front.pdf/.png`：按 $(WT,C_{\mathrm{joint}})$ 保存全部联合可行非支配解、决策快照与图件。历史 `results_alns_q3_v2_hardcap_600s/seed_20260924/` 的原档案仍为四目标口径；同目录的 `pareto_archive_2d_from_legacy.json` 和 `pareto_front.csv` 是其二目标重筛结果，用于论文图 11。
- `results/Q3_结果提交.xlsx`：按统一结果模板填写的 Q3 中继架次与通信保障页。

## 算法变体

- `code/q3_solver.py`：基线一次性"运输 → 通信解码"管线（结果见 `results/`）。
- `code/alns_q3.py`：ALNS-Q3，E007 运输 ALNS + 事后通信解码（`ALNS-Q3.md`）。
- `code/alns_q3_v2.py`：ALNS-Q3-V2，E007 ALNS 内核 + E004 式联合中继反馈
  （周期 MILP 解码、Pareto 外部档案、λ 轮换档案扰动、relay_shift 修复算子），
  设计与运行说明见 `ALNS-Q3-V2.md`，结果见 `results_alns_q3_v2_*/`。

## 已知限制

候选中继搜索采用有限位置—高度候选集，并对每个运输航段使用 5 个轨迹采样点；接近遮挡边界的区间需用更细步长复核。当前结果已明确暴露两架中继库存和 Q2 时限排程之间的冲突，后续若作为问题四冻结输入，应先完成一轮带运输改序/机型重配的联合修复。
