# Q2 五方案统一对比清单

评价顺序为：硬约束违约数 → 加权总延误 WT → 完工时间 → 架次数 → 能耗。所有方案均完成 80 箱唯一配送；Q2 对比不包含问题三的通信连续性约束。

| 算法 | 硬约束违约 | 箱唯一/路线/载荷体积/SOC/资源/充电 | WT (s) | 完工时间 (s) | 架次 | 能耗 (kWh) | 按期箱比例 |
|---|---:|---|---:|---:|---:|---:|---:|
| 贪婪算法 | 0 | 全部通过 | 280481.8 | 9798.7 | 18 | 59.68 | 92.5% |
| q2_order | 0 | 全部通过 | 174093.2 | 9626.5 | 18 | 59.68 | 92.5% |
| q2_full | 0 | 全部通过 | 0.0 | 6374.4 | 24 | 70.72 | 100% |
| q2_memetic.py | 0 | 全部通过 | 0.0 | 10078.7 | 22 | 71.79 | 100% |
| **HD-ALNS** | **0** | **全部通过** | **0.0** | **6374.4** | **24** | **70.72** | **100%** |

## 来源与复核

- 贪婪算法：huaweicup-2026/members/chendelong/results/Q2/solution.json
- q2_order：huaweicup-2026/members/chendelong/experiments/e001/q2_order/seed_2/solution.json
- q2_full：huaweicup-2026/members/chendelong/experiments/e001/q2_full/seed_1/solution.json
- q2_memetic.py：huaweicup-2026/members/WHLi/experiments/q2_memetic_best.json，由 members/WHLi/code/verify.py 独立复核
- HD-ALNS：AAA-2026华为杯-精简/weiliu/Q2/results_v2/，192 项审计全部通过

详细字段、运行预算、运行时间和词典序向量见同目录下的 selected_algorithm_comparison.csv/json。
