# Q4 objective preference scenarios

本目录是在冻结 Q3 输入上对所有组件分区进行完整枚举后的偏好对照；枚举结果是本规模下的权威候选集。本次没有运行优化求解器。它是本地候选分析，不自动取代正式冻结分区或论文最终结论。

- K=2、K=3 候选数分别为 255、3025。
- `shortage_first`：总缺口优先，其次中继复制、均衡比、冗余。
- `balance_first`：先最小化最大组工作量，再比较均衡比、缺口、中继复制、冗余。
- `balanced_shortage_first`：仅接受 K=2 均衡比不超过 1.5、K=3 不超过 2.0 的候选，不自动放宽阈值。
- `pareto`：硬约束可行候选按总缺口、均衡比、中继复制、冗余求非支配前沿，并附三类代表点。
- `saved_alns_candidate_comparison.json`（可选）：仅重评估已保存 ALNS 解，不是重新运行 ALNS。

主要文件：`scenario_summary.csv`、`all_partitions.csv`、`pareto_front.csv`、`scenario_results.json`、`components.json`、`run_manifest.json`。场景/K 子目录中的 `partition_result.csv`、`resource_gap.csv` 采用 Q4 接口列。
