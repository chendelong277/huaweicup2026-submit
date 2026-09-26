# mo-r1：Q2 双目标（加权延误 × makespan）非支配档案式 Pareto-ALNS 实验结果

版本 `mo-r1`，2026-09-24 运行。本目录为真实实验输出，未手工修改。

## 口径声明

- **普通物资期望送达时间为软目标**（进入加权延误 WT）；医疗/首批等原始硬截止仍为硬约束，由未修改的 `members/chendelong/pipeline/algorithms/q2_search.py::Decoder`（base 解码器）把关，`decode` 返回 None 即拒绝。该口径已核实：base `decode` 只对照 `deadline_s` 判定，且暖启动 `q2_order/seed_0` 在本管线下解码得 WT=174093.2，与 chendelong 公布值一致。
- Q2 纯运输调度：**不含 Q3 通信连续性验证**；暖启动解的历史生成成本不计入本实验预算。
- 控制器复用 E005/alns_plain 设计（8 基础算子、自适应权重、退火接受），接受准则与奖励改为非支配语义；E005 的三个专用算子因其消融结论为负净收益而未纳入。

## 实验设置

- 种子 0/1/2，每种子壁钟预算 120 s，串行运行；数据加载与暖启动审计 0.4 s（单独计时）。
- 暖启动（均通过 `evaluate_transport` 独立复核 feasible=true 后入档，SHA-256 见 `protocol.json`）：
  - `e001_q2_full/seed_1`：WT=0、makespan 6374.35 s（WT=0 端点）
  - `e001_q2_order/seed_0`：WT=174093.2、9626.53 s（高延误端点）
  - `e001_q2_energy/seed_0`：WT=25408.2、9233.03 s（中间点）

## 核心结果：前沿塌缩为单点

三种子合计约 1,875,285 次迭代、1,678,384 次解码评估，合并前沿只有 **1 个点**：

| WT (加权秒) | makespan (s) | 能耗 (kWh) | 架次 | 来源 |
|---:|---:|---:|---:|---|
| **0.0** | **6346.43** | 70.91 | 24 | seed 1（三种子收敛一致） |

- WT=0 暖启动入档即支配另外两个暖启动点（0<25408 且 6374<9233），档案从 t=0 起就只有 1 个成员；
- 档案插入事件全部发生在 **前 2.8 s 内**（6374.35→6373.1→6360.0→6346.43，算子依次为 relocate_order/type/swap_order），此后约 117 s、~60 万次迭代/种子未再产生任何非支配点；
- 每种子停滞重启 7,563–7,969 次，全部重启回同一个档案点，搜索退化为围绕单点的随机重启。

## 与外部锚点的支配关系（程序自检，见 `front.json` / `summary.json`）

| 锚点 | (WT, makespan) | 关系 |
|---|---|---|
| WHLi s3 挑战者 | (0, 9530.2) | 被本前沿支配 |
| WHLi s5 | (35871.1, 9830.1) | 被本前沿支配 |
| WHLi 冻结主线 v4 | (46207, 10129.8) | 被本前沿支配 |
| E005 alns_plain 最佳 | (0, 6043.32) | **支配本前沿**（本实验未复现该点） |

两两互不支配自检：通过（`dominance_check.pairwise_nondominated=true`）。档案每解独立复核：3/3 种子 `all_feasible=true`、80 箱唯一配送（见各 `seed_*/evaluation.json`）。

## 解读（如实声明）

1. **本实例中 WT 与 makespan 实质不构成双目标权衡**：找到的最小 makespan 解恰好 WT=0，任何 WT>0 的点都被支配。这与赛题结构一致——迟到与完工拖延同源（C 机串行链 + 电池周转瓶颈），压短关键链同时改善两者。本实验以约 168 万次评估的搜索量为该判断提供了经验证据：**在本实例上做 (WT × makespan) 的 Pareto/MOEA-D 没有增量价值，字典序（WT 优先）已足够**。
2. **本实验未复现 E005 alns_plain 的 6043.32 s**（差距 303.1 s，4.8%）。可能原因（未逐一验证）：E005 的 ZeroDecoder 硬零延误把搜索限制在 WT=0 流形上、空间更小更聚焦；E005 该点为 1/3 种子的最佳值；本算法的单点档案重启机制缺少向 makespan 单方向的强化压力。
3. 若论文需要 WT×makespan 前沿图，本结果说明应改选 **WT×能耗/架次**（已有证据：WT=0 需 23–24 架次/70.7–76.6 kWh，能耗优先为 18 架次/59.5 kWh）或 WT×通信可行性作为展示冲突的目标对。
4. 局限：3 种子、单实例、暖启动成本未计入；24 架次/70.91 kWh 方案未做 Q3 中继可行性验证，不能直接作为交付方案。

## 算子权重终值（三种子一致模式）

`type`（0.55–0.80）> `relocate_order`（0.44–0.51）> `swap_order`（0.30–0.40）≈ `route`（0.33–0.43）> 箱级四算子（≈0.20，拒绝水平基线）。收敛后有效改进全部来自**任务排序与机型选择**，箱级重组（relocate/exchange/merge/split）在 WT=0 流形附近几乎不产生可行改进。

## 复现

```bash
cd code/Q2
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python code/run_mo.py --budget 120 --seeds 0 1 2
```

入口 `code/run_mo.py`，算法 `code/mo_pareto_alns.py`（含为 Python 3.8 只读兼容 chendelong pipeline 3.9+ 注解语法的 meta-path loader，不修改任何他人文件）。

## 文件清单

- `protocol.json`：版本、口径、参数、暖启动来源与 SHA-256；
- `front.json` / `front.csv`：合并前沿与锚点支配关系；
- `summary.json` / `summary.csv`：逐种子统计与全部档案点；
- `seed_{0,1,2}/solution_archive.json`：档案解完整决策；`run.json`：迭代/评估/算子统计/收敛轨迹；`evaluation.json`：独立复核。
