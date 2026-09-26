# Q2 对比算法整合说明

本目录整合了 `huaweicup-2026/members/chendelong` 中已经运行过的 Q2 对比思路，并将其适配到当前精简工作区的 `q2_solver_v2.py` 物理模型和资源解码器中。

## 统一比较口径

- 货箱唯一配送；
- 航线从 `O01` 出发并返回 `O01`；
- 载荷、体积、SOC、无人机与电池时间冲突由同一调度器审计；
- 按“硬时限违约数 → 加权软延误 → makespan → 架次数 → 能耗”的词典序保存解；
- 对比结果仅表示 Q2 运输方案，不包含中继和通信连续性验证。

## 方法来源与适配关系

| 方法 | 原始来源 | 当前实现 |
|---|---|---|
| 贪婪单点组批基线 | `members/chendelong/baselines/v1/algorithms/q2_greedy.py` | `q2_comparison.py::_greedy_state` |
| `q2_order` | `members/chendelong/pipeline/algorithms/q2_search.py` 的 `order_only` | `q2_comparison.py::propose(..., "order_only")` |
| `q2_full` | 同文件的 `full` 邻域 | `q2_comparison.py::propose(..., "full")` |
| SA、ILS、VNS、Memetic、Hill | `members/chendelong/research/q2_frameworks.py` 与 E007 运行入口 | `q2_comparison.py::search` |

当前实现没有复制 chendelong 的公共评价器，而是重新调用精简工作区的 Q2 物理和排程函数。这样可以保证对比结果和主线结果的单位、资源库存、SOC 规则及审计字段一致。

## 结果解释边界

`q2_full` 是联合邻域搜索对照，`q2_order` 是只改变调度顺序的弱对照；两者不应与论文正式名称“基于分层解码的自适应大邻域搜索算法”混用。SA、ILS、VNS、Memetic 和 Hill 是搜索控制器对照，用于分析自适应抽样、接受机制、邻域切换和种群重组的影响。它们不是对算法家族在所有问题上的普遍排名。

运行脚本会在每个算法目录下保存完整方案和审计记录；推荐至少使用相同预算和多个随机种子后再制作论文对比表。

## 论文对比结果

统一复核后的代表解清单保存在：

- `comparison_results/algorithm_comparison.csv`
- `comparison_results/algorithm_comparison.json`

两份文件均包含 8 种对比算法和 HD-ALNS 的约束审计字段（唯一配送、路线闭合、载荷/体积、能量/SOC、资源不重叠、充电合规、硬时限违约）以及词典序目标字段（加权迟到、makespan、架次数、能耗）和按期箱比例。论文 Q2 章节的表 8、表 9 由该清单整理得到。
