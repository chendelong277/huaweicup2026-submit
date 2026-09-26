# Q3 通信约束下的运输与中继联合调度

> **三分钟摘要**：这是 Q3（通信约束下的运输与中继联合调度）模块的总览文档。主线算法为基于运输-通信联合解码的自适应大邻域搜索算法（TCJD-ALNS，代码 `code/alns_q3_v2.py`），最终方案：2 架中继无人机 3 个中继架次保障全部 19 个存在直连盲区的运输架次，全程通信零断链，联合完工时间 6606.6 s，总能耗 74.02 kWh。本文档给出复现命令、结果文件清单和已知限制。

## 当前状态

可集成。主线算法在 600 s × 5 个种子的正式复跑中均找到联合可行解；每个种子的方案都经过与搜索解耦的严格口径复核（逐像元 DEM 视距、集合覆盖 MILP 重解、1 s 步长密检与 0.05 s 临界闭包，见各种子目录的 `gate_whli_strict_1s/`）。问题四的冻结输入取自种子 20260924 的运行，为"ALNS 自产运输方案 + 严格门控重新装配中继"，已随 `code/Q4/results/frozen_alns_q3_v2_seed_20260924/` 一并提交。

## 方法要点

通信可行性不作为事后补加：运输 ALNS 搜索过程中，按盲区组合实质变化、出现新最优、定时兜底等条件周期触发"中继集合覆盖 MILP"联合解码；只有运输审计通过且全程通信零空档的联合可行解才能进入 Pareto 外部档案；"并发盲区架次数不超过中继库存"作为词典序第 0 级硬层级进入评分；失败导向的挪时算子把造成并发超载的架次定向后移。模型与算法细节见 `model.md`，算子逻辑与输入输出见论文附录 C。

## 复现

从仓库根目录运行，将 `<题目数据目录>` 替换为原始附件目录（另需 `中继无人机数据.xlsx`、`通信链路参数.xlsx` 与 30 m DEM）：

    python "code/Q3/code/run_alns_q3_v2.py" --data-root "<题目数据目录>" --output-dir "<输出目录>" --budget 600 --seed 20260924

常用参数：`--decode-mode hybrid`（默认，按盲区组合变化触发联合解码）或 `periodic`（固定 15 s 周期）；`--init auto`（默认）/`direct_q2`/`struct_q2`（热启动消融）；`--no-hard-relay-cap`（关闭并发硬层级，供消融）。运输层内核复用 `code/Q2/code/q2_solver_v2.py`，请保持 Q1、Q2 目录相对位置不变。

## 主要输出（每个运行目录）

- `transport_plan.csv`、`delivery_timeline.csv`、`transport_resource_timeline.csv`、`trip_summary.csv`：运输侧接口文件。
- `relay_plan.csv`：中继任务的位置、悬停高度、起飞/服务窗/返航时刻、能耗、SOC 与能源组件分配。
- `communication_audit.csv`：逐架次逐时间片的直连/中继模式、两段视距判定、最小链路余量与连续性结论；`trip_communication_summary.csv` 为逐架次汇总。
- `pareto_archive.json`、`pareto_front.csv`、`pareto_front.pdf/.png`：按 (加权延误, 联合完工时间) 维护的联合可行非支配解档案与前沿图件。
- `decode_log.json`：逐轮联合解码的触发来源、可行性、MILP 状态与责任架次。
- `optimization_trace.csv`、`operator_statistics.csv`：搜索轨迹与算子自适应权重。
- `global_audit.json`：算法族、种子、预算、触发计数与运输/通信/中继三类审计结论。

## 结果目录说明

- `results_alns_q3_v2_hardcap_600s/`：正式复跑（600 s × 种子 20260924–20260928），含逐种子结果、`*.run.log` 运行日志与 `*.gate.log` 严格门控复核日志。
- `results_alns_q3_v2_ablation/`：消融实验（三种初始化解法 × 多个种子的对照）。
- `results_pareto_600s_20260926/`、`results_pareto_smoke_20260926/`、`results_pareto_smoke_ext_20260926/`：Pareto 档案口径的补充运行与冒烟测试。

## 对照与工具代码

- `code/q3_solver.py`：早期"运输 → 通信"一次性贪心基线；其链路与中继候选组件被主线算法复用。
- `code/alns_q3.py`：ALNS-Q3 旧版（运输 ALNS + 事后通信解码），作为对照保留。
- `code/whli_q3_gate.py`：与搜索解耦的严格口径验证门禁，用于最终方案的验收复核。
- `code/export_frozen_for_q4.py`：把选定方案导出为问题四冻结输入。

## 已知限制

候选中继位置来自有限的"位置 × 高度"候选集，且每个运输航段按 5 个轨迹采样点判定直连；接近地形遮挡边界的区间需用更细步长复核——最终方案的验收以 `whli_q3_gate.py` 的严格口径（1 s 密检 + 0.05 s 闭包）为准，搜索内部的采样判定不构成通信连续性的充分条件。
