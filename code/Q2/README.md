# Q2 异构无人机多点多架次运输调度

> **三分钟摘要**：这是 Q2（无人机运输调度）模块的总览文档。主线算法为基于分层解码的自适应大邻域搜索算法（HD-ALNS），冻结结果：80 个货箱全部按时送达（硬时限零违约），共 24 架次，192 项自动检查全部通过；这是启发式可行解，不声称全局最优。本文档给出复现命令、结果文件清单和已知限制。

## 当前状态

可集成。HD-ALNS 在 540 s 搜索预算内完成 80 个货箱的唯一配送，共 24 架次；医疗物资、首批保障货箱及普通物资期望时限均满足，硬时限违约数为 0。总运输能耗 70.716666 kWh，全部任务完成时间 6374.351717 s。独立校验共执行 192 项检查，全部通过。

本结果是固定随机种子和给定搜索预算下取得的可行启发式解，不宣称全局最优。Q1 原组批直接排程的能耗略低，但在当前资源日历中无法同时满足全部时限，因此主方案采用跨服务区重组、顺序层保留与资源解码相结合的 HD-ALNS。

## 方法

1. 复用 Q1 的 DEM 航段、飞行时间和载荷相关能耗口径。
2. 先评估 Q1 精确单点组批的资源排程；若硬时限不可行，则按“首批/医疗优先”重新组批。
3. 采用分层交替式 ALNS，交替改变货箱组批、机型选择和架次执行顺序，并根据搜索表现自适应更新算子权重。
4. 使用实体无人机—共享电池资源解码器计算任务起止、SOC、两阶段充电和再可用时刻。
5. 按“硬约束、普通物资加权延误、最大完工时间、架次数、总能耗”的词典序比较候选方案。

## 复现

从仓库根目录运行，将 `<题目数据目录>` 替换为原始附件目录（含节点、需求、运输无人机表与 30 m DEM）：

    python "code/Q2/code/q2_solver_v2.py" --data-root "<题目数据目录>" --output-dir "code/Q2/results_v2"

搜索预算与随机种子由环境变量控制（默认 540 s / 20260923）：

    set Q2_TIME_LIMIT_S=540
    set Q2_SEED=20260923

独立复核已生成的结果：

    python "code/Q2/code/validate_results.py" --data-root "<题目数据目录>" --results-dir "code/Q2/results_v2"

## 主要输出（results_v2/）

- `transport_plan.csv`：逐架次逐航段运输计划（机型、实体机、电池、起止时刻、载重、能耗、SOC）。
- `delivery_timeline.csv`：80 箱逐箱送达时刻和目标时间判定；`delivery_audit.csv` 为逐箱时限审计。
- `transport_resource_timeline.csv`：无人机、电池飞行与充电资源日历。
- `trip_summary.csv`：架次路线、资源、载荷、能耗、起止和 SOC 汇总。
- `constraint_audit.csv`、`validation_summary.json`：192 项独立约束审计。
- `pareto_archive.json`、`pareto_front.csv`、`pareto_front.pdf/.png`：搜索过程中按 (加权延误, 最大完工时间) 维护的全部可行非支配解及图件。
- `plan_comparison.csv`：Q1 原组批排程与 HD-ALNS 方案对比。
- `optimization_trace.csv`、`operator_statistics.csv`：搜索轨迹和算子自适应权重统计。
- `global_audit.json`、`runtime_manifest.json`：全局审计结论与含输入文件 SHA-256 的复现清单。

## 其他结果目录

- `results_pareto_540s_20260926/`、`results_pareto_90s_20260926/`：不同搜索预算下的独立运行，作为 Pareto 证据与稳健性对照。
- `results_mo/`：双目标 Pareto-ALNS 实验变体（期望送达时间降为软目标）的结果，属对照实验，不是主线方案；结论为该变体在本题数据上无额外收益。
- `comparison_results/`：八种对照算法的批量运行结果（见下节）。

## 对照算法

对照算法统一位于 `code/q2_comparison.py`，物理计算、资源排程和词典序评价均复用 `q2_solver_v2.py`，避免不同算法使用不同评价口径。整合的八种方法（贪婪单点组批基线、顺序优化、联合邻域搜索、模拟退火、迭代局部搜索、变邻域搜索、模因搜索、爬山法控制组）的来源与适配关系见 `comparison_algorithms.md`。

单个算法运行示例：

    python "code/Q2/code/q2_comparison.py" --data-root "<题目数据目录>" --output-dir "code/Q2/comparison_results/sa/seed_0" --algorithm sa --budget 60 --seed 0

批量运行全部方法：

    python "code/Q2/code/run_q2_comparisons.py" --data-root "<题目数据目录>" --output-root "code/Q2/comparison_results" --budget 60 --seeds 0 1 2

这些对照只验证 Q2 运输约束，不代表通过 Q3 通信连续性审计。

## 已知限制

尚未建立 MILP 下界，因此不能声称获得全局最优。普通物资期望时间为软目标，主方案有 6 箱晚于期望时间；硬时限无违约。
