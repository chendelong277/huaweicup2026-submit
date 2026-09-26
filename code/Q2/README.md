# Q2 异构无人机多点多架次运输调度

> **三分钟摘要**：这是 Q2（无人机运输调度）的总入口文档。当前冻结结果是"可集成"：80 个货箱全部按时送达（硬时限零违约），共 24 架次，192 项自动检查全部通过，但这是启发式可行解、不声称全局最优。文档还给出复现命令、结果文件清单和已知限制，想快速了解 Q2 看这一篇就够。术语看不懂可查仓库根目录 `shared/glossary.md`。

## 当前状态

可集成。当前 HD-ALNS（基于分层解码的自适应大邻域搜索算法）在 540 s 搜索预算内完成 80 个货箱的唯一配送，共 24 架次；医疗物资、首批保障货箱及普通物资期望时限均满足，硬时限违约数为 0。总运输能耗为 70.716666 kWh，全部任务完成时间为 6374.351717 s。独立校验共执行 192 项检查，全部通过。

本结果是固定随机种子和给定搜索预算下取得的可行启发式解，不宣称全局最优。Q1 原组批直接排程的能耗略低，但在当前资源日历中无法同时满足全部时限，因此主方案采用跨服务区重组、顺序层保留与资源解码相结合的 HD-ALNS。

## 方法

1. 复用 Q1 的 DEM 航段、飞行时间和载荷相关能耗口径。
2. 先评估 Q1 精确单点组批的资源排程；若硬时限不可行，则按“首批/医疗优先”重新组批。
3. 采用分层交替式 ALNS，交替改变货箱组批、机型选择和架次执行顺序，并根据搜索表现自适应更新算子权重。
4. 使用实体无人机—共享电池资源解码器计算任务起止、SOC、两阶段充电和再可用时刻。
5. 按“硬约束、普通物资加权延误、最大完工时间、架次数、总能耗”的词典序比较候选方案。

## 复现

从项目根目录运行：

    python "# D-项目文件夹-LW/Solution/Q2/code/q2_solver.py" --data-root "# D-项目文件夹-LW/D题题目/数据" --output-dir "# D-项目文件夹-LW/Solution/Q2/results"
    python "# D-项目文件夹-LW/Solution/Q2/code/validate_results.py" --data-root "# D-项目文件夹-LW/D题题目/数据" --results-dir "# D-项目文件夹-LW/Solution/Q2/results"
    python "# D-项目文件夹-LW/Solution/Q2/code/make_outputs.py" --results-dir "# D-项目文件夹-LW/Solution/Q2/results" --q1-results-dir "# D-项目文件夹-LW/Solution/Q1/results"
    node "# D-项目文件夹-LW/Solution/Q2/code/fill_submission.mjs"

最后一条从统一 `结果提交模板.xlsx` 重建 Sheet2/Sheet3，需在 `code/` 的 Node 模块搜索路径中提供 `@oai/artifact-tool`。本次使用 Codex 捆绑依赖完成填表；未把依赖目录复制进交付包。

## 主要输出

- `results/transport_plan.csv`：逐架次逐航段运输计划，符合强制接口。
- `results/delivery_timeline.csv`：80 箱逐箱送达时刻和目标时间判定。
- `results/transport_resource_timeline.csv`：无人机、电池飞行与充电资源日历。
- `results/trip_summary.csv`：架次路线、资源、载荷、能耗、起止和 SOC 汇总。
- `results_v2/constraint_audit.csv`、`validation_summary.json`：192 项独立约束审计。
- `results_v2/pareto_archive.json`、`pareto_front.csv`、`pareto_front.pdf/.png`：HD-ALNS 搜索过程中按 $(WT,C_{\max})$ 维护的全部可行非支配解及其图件。
- `results/plan_comparison.csv`：Q1 原组批排程与 ALNS 方案对比。
- `results/optimization_trace.csv`、`operator_statistics.csv`：ALNS 搜索轨迹和算子统计。
- `results/route_map.png`、`resource_gantt.png`、`plan_comparison.png`：路线、资源时序和方案对比图。
- `results/Q2_results.xlsx`：全部 CSV 的便览工作簿。
- `results/Q2_结果提交.xlsx`：按统一模板填入的 Sheet2 架次和 Sheet3 逐箱交付结果。
- `results/Q2_方案对比报告.html`：与 `huaweicup-2026/华为杯D题_Q1-Q4解题报告.html` 的 Q2 主线 v2 在统一口径下的指标、迟到来源和验证边界对比；`code/compare_q2_sources.py` 可复算表中数值。

## 已知限制

尚未建立 MILP 下界，因此不能声称获得全局最优。普通物资期望时间为软目标，主方案有 6 箱晚于期望时间；硬时限无违约。

## Q2 对比算法（已整合）

对比算法统一位于 `code/q2_comparison.py`，物理计算、资源排程和词典序评价均复用 `q2_solver_v2.py`，避免不同算法使用不同评价口径。当前整合的八种方法为：

| 代码名称 | 论文中的对比名称 | 方法定位 |
|---|---|---|
| `greedy` | 贪婪单点组批基线 | 各服务区独立组批，按时限和资源可用时间安排 |
| `q2_order` | 顺序优化算法 | 仅改变架次执行顺序 |
| `q2_full` | 联合邻域搜索算法 | 联合改变顺序、航点、机型和货箱重组 |
| `sa` | 模拟退火 | 退火接受机制 |
| `ils` | 迭代局部搜索 | 严格改进与停滞扰动 |
| `vns` | 变邻域搜索 | 多邻域循环切换 |
| `memetic` | 模因搜索算法 | 小规模种群、交叉和局部改进 |
| `hill` | 爬山法控制组 | 只接受严格改进 |

单个算法运行示例：

    python code/q2_comparison.py --data-root problem/数据 --output-dir weiliu/Q2/comparison_results/sa/seed_0 --algorithm sa --budget 60 --seed 0

批量运行全部方法：

    python code/run_q2_comparisons.py --data-root problem/数据 --output-root weiliu/Q2/comparison_results --budget 60 --seeds 0 1 2

每个方法的输出目录包含 `transport_plan.csv`、`delivery_timeline.csv`、`transport_resource_timeline.csv`、`trip_summary.csv`、`global_audit.json`、`solution.json` 和 `run.json`。这些方法只验证 Q2 运输约束，不代表通过 Q3 通信连续性审计。
