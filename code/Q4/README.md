# Q4 任务分区与资源配置（问题四）

> **三分钟摘要**：这是问题四（任务分区与资源配置）模块的总览文档。主线算法为多偏好分区搜索算法（MPPS，代码 `code/alns_q4.py` + `code/q4_core.py`）：在问题三冻结的联合方案上，把 15 个服务区压缩为 9 个不可拆组件，搜索 K=2/K=3 的任务分区。本题规模下全枚举（255/3,025 种分区）即为严格最优，ALNS 与全枚举结果一致（gap=0），作为正确性验证。本文档给出输入、复现命令和结果清单。

## 当前状态

可运行。已完成 K=2 / K=3 × 5 个种子的 ALNS 实验、全枚举基线对照、四种目标偏好的全枚举候选分析与分区审计，全部结果见 `results/`。

## 输入

- 冻结 Q3 联合方案：`results/frozen_alns_q3_v2_seed_20260924/`（transport_trips.csv / relay_plan.csv / communication_links.csv / delivery_timeline.csv / audit_freeze.json；24 个运输架次、3 个中继架次、9 个不可拆组件）。算法只读这些文件，不改路线、时刻、组批与通信关系。
- 库存与充电参数：运输机 A/B/C = 4/2/2 架，电池 A/B/C = 6/4/4 块，中继无人机 2 架，能源组件 6 个；电池充满时间 1800/2400/3000 s，能源组件 1800 s，中继返航周转 300 s（题面附件口径，硬编码于 `q4_core.py`）。

## 复现

从仓库根目录运行：

```bash
# 全部实验（K=2,3 × 5 种子 × 60 s + 枚举基线 + 汇总审计）
python code/Q4/code/run_all.py --budget 60

# 单次 ALNS 运行
python code/Q4/code/run_alns_q4.py \
    --frozen-dir code/Q4/results/frozen_alns_q3_v2_seed_20260924 \
    --k 2 --budget 60 --seed 20260924 \
    --output-dir code/Q4/results/runs/k2_seed20260924

# 仅枚举基线 / 仅汇总输出与审计
python code/Q4/code/q4_enumerate.py
python code/Q4/code/make_outputs.py

# 四种目标偏好的全枚举候选分析（写入 results/objective_scenarios/，不覆盖既有结果）
python -B code/Q4/code/run_scenarios.py
```

## 输出（results/）

- 接口文件：`partition_result.csv`、`resource_gap.csv`（逐组 8 类资源需求、库存、冗余、缺口及含峰值时刻的缺口原因）。
- 算法证据：`pareto_archive.json`（跨种子合并的 K=2/K=3 非支配分区，附冻结方案的加权延误/完工时间/架次数/能耗不变量）、逐种子目录 `runs/k{2,3}_seed*/`（solution.json、搜索轨迹、算子统计、含输入 SHA-256 的 run.json）。
- 验证：`enumeration_baseline.csv`、`enumeration_summary.json`（全枚举分区表与多种口径选解）、`global_audit.json`（分区唯一性、同架次同组、资源重算一致性等审计 + ALNS 与枚举 gap=0 核验）。
- 偏好对照：`objective_scenarios/`（缺口优先 / 均衡优先 / 均衡约束下缺口优先 / 非支配前沿四种口径的全枚举候选分析）。
- 提交：`Q4_结果提交.xlsx`（按统一模板填写的分区配置页）、`run_manifest.json`。

## 主要结论

缺口优先口径下：K=2 推荐 `{S012}` 独立成组（缺口 4、均衡比 1.917）；K=3 推荐 `{S012}`、`{S015}` 各自独立（缺口 6、均衡比 2.757）。全部 255/3,025 种分区均超出库存——根源是全局协同方案已用满 A/B/C 型机、B/C 型电池与中继机的库存峰值，且 A 型电池需 7 块、分组之前已超过库存 6 块。四种偏好口径的逐方案数值见 `objective_scenarios/` 与论文第 8 章。

## 依赖

Python 3.8 及以上，标准库 + openpyxl（仅结果模板填充使用）。

## 已知限制

- 本规模（9 组件、255/3,025 分区）下全枚举即为严格最优；ALNS 的价值定位是可扩展启发式框架与非支配档案证据链，论文中不宣称其在本规模上优于枚举。
- 本包按"硬违约 → 总缺口 → 中继复制 → 均衡比 → 冗余"的词典序选解；其他口径（如零中继复制优先）的选解在 `enumeration_summary.json` 的 `whli_recommended` 字段中一并给出，各口径结果均可复现。
