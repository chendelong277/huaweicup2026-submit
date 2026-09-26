# ALNS-Q4 设计文档：相对 ALNS-Q3-V2 的改动

> **三分钟摘要**：这份文档说明 ALNS-Q4 算法是怎么从问题三的 ALNS-Q3-V2 框架改造来的：哪些机制原样保留、哪些删掉、哪些新增。核心变化是决策对象从"货箱怎么装箱飞路线"换成"组件分到哪个任务组"；正确性用穷举全部 121 种分区来校验，算法结果与穷举最优完全一致。术语看不懂可查仓库根目录 `shared/glossary.md`。

ALNS-Q4 是 ALNS-Q3-V2（`members/weiliu/Q3/code/alns_q3_v2.py`，设计文档 `members/weiliu/Q3/ALNS-Q3-V2.md`）在问题四（任务分区与资源配置）上的框架迁移版本。本文档只列改动；未提及的机制与 V2 一致。

## 1. 问题层差异

| 维度 | ALNS-Q3-V2 | ALNS-Q4 |
|---|---|---|
| 决策 | 箱-架次组批、多点路线、派发顺序（隐式确定中继与时刻） | 组件→任务组指派 π: C→{0..K-1}，K∈{2,3} |
| 输入 | 原始题面数据（箱/机型/链路/DEM） | Q3 冻结联合方案（路线/时刻/中继/通信关系均不可改） |
| 评价 | 资源排程解码器 + 周期 MILP 联合解码（秒级） | 峰值并发闭式核算（微秒级，精确） |
| 硬约束 | 载重/时限/SOC/资源不重叠/通信连续 | 唯一分组、每组非空、同架次同组、资源不跨组 |

## 2. 原样保留的框架机制

1. **主循环骨架**：deadline 时间预算、克隆-变异-评价-接受-记账结构。
2. **轮盘赌 + 分段自适应权重**：每 100 迭代 `weight = 0.2 + rewards/uses`，奖励 改进+4 / 接受+1 / 新 best+8；失败导向算子门控（V2 中 relay_shift 仅在有 offending 诊断时可入选；Q4 中 shortage_targeted_removal 仅在 incumbent 缺口 >0 时可入选，权重更新时置 0）。
3. **SA 接受准则**：词典序改进直收；同 hard 层按固定标量化的相对 Δ 做退火（T0=0.12，×0.99985/迭代，侧向阈值 0.08）；hard 层变多强拒。标量化改为 Q4 量纲：`sa = 100·shortage + 10·relay_extra + balance + 0.01·redundancy`（仅定温度尺度，不参与选解，见 assumptions.md A4）。
4. **外部非支配档案**：支配判定、容量 32；Q4 档案向量 = (shortage_total, balance_time, redundancy_total, relay_extra)，并按 AGENTS.md §3.5 为每条记录附冻结不变量 (WT, makespan, 架次数, 能耗)。**截断规则在 Q4 修复为冠军保护式多样性淘汰**（保护词典序最优条目，详见 assumptions.md A6——V2 式"按单目标最差弹出"在 Q4 会系统性驱逐缺口最小的冠军解）；主循环另维护独立的 best-ever 候选，最终选解不受档案容量影响。
5. **档案重锚定反馈**：V2 每第 3 次解码后按 λ 轮换标量化选档案亲本重置搜索；Q4 无解码事件，改为每 250 迭代触发一次，λ 轮换表不变 (0.5, 1.0, 0.0, 0.25, 0.75)。
6. **种子与时间预算**：`random.Random(seed)` 贯穿，确定性可复现。
7. **落盘结构**：optimization_trace.csv / operator_statistics.csv / pareto_archive.json / solution.json / run.json。**补齐了 V2 缺失的算子使用次数与接受次数列**（V2 只有终权重）。

## 3. 删除的 Q3 专属机制

decode_joint（MILP 联合解码）、build_blind_info / blind_signature / blind_overload / substantive_change / hybrid 触发器、relay_shift、能源组件贪心分配与 SOC 复检。原因：Q4 评价是精确闭式核算，不存在"运输层可行但通信层不可行"的解码间隙需要弥合；V2 为这些机制服务的缓存与签名结构在 Q4 没有意义。

## 4. 新增的 Q4 专属机制

1. **组件级解表示**：同架次耦合并查集压缩（15 服务区 → 6 组件），搜索空间 S(6,2)+S(6,3)=121。
2. **destroy 算子**（4）：random_removal（1–4 个组件）、related_removal（同中继覆盖簇）、shortage_targeted_removal（缺口 witness 时刻活跃组件；V2 relay_shift 失败导向思想的对应物）、balance_targeted_removal（最大工作量组）。
3. **repair 算子**（4）：greedy_insertion、regret2_insertion、optimal_insertion（≤4 移除精确枚举联合重指派，调试中证实序贯贪心无法到达 K=3 最优盆地）、random_insertion（均保证 K 组非空）。
4. **多初始解**：中继簇对齐 / 地理 k-means / 随机三候选评优起步；刻意不用枚举最优暖启动（V2 消融已证盆地锁定风险）。
5. **评价不变量断言**：WT/makespan/架次/能耗与 π 无关，作为 §3.5 主链在 Q4 的退化层级处理并留痕。

## 5. 正确性验证策略

V2 用严格口径 gate（whli_q3_gate）复核；Q4 用**全枚举基线**复核：历史 WHLi 冻结输入压缩为 6 组件、121 个无标号分区，枚举给出严格最优，ALNS 在该输入下 5 种子复现 gap=0。ALNS-Q4 在本规模不声称优于枚举，定位为可扩展启发式框架 + §3.5 非支配档案产出。

## 6. 2026-09-25 更新：自产 Q3 方案严格过门后重新冻结

- 新冻结源：`members/weiliu/Q3/results_alns_q3_v2_hardcap_600s/seed_20260924/` 的运输排程，经过 `gate_whli_strict_1s/` 的严格 DEM 候选、两架中继 MILP 重装配、1 s 密检 0 断链及关键区 0.05 s 数值闭包。**冻结的中继和通信关系来自门控重新装配结果**，不是 ALNS 内部每航段 5 点粗口径的 relay_plan。用 `members/weiliu/Q3/code/export_frozen_for_q4.py` 导出为 `results/frozen_alns_q3_v2_seed_20260924/`。
- 该冻结输入有 24 运输架次、3 中继架次、9 个不可拆服务组件，WT=0、严格联合完工 6,606.55 s、能耗 74.0224 kWh；因此本实例 K=2/3 的全枚举规模改为 255/3,025，不能再沿用旧实例的 121。
- `python members/weiliu/Q4/code/run_strict_frozen.py --frozen-dir members/weiliu/Q4/results/frozen_alns_q3_v2_seed_20260924 --results-dir members/weiliu/Q4/results/strict_q3_v2_seed_20260924 --budget 60`：K=2/3 各 5 种子、全枚举核验。结果：K=2 缺口 4，选 `{S012}|{其余14区}`；K=3 缺口 6，选 `{S012}|{S015}|{其余13区}`；两者各 5 次审计均通过，词典序分数与各自全枚举最优一致（gap=0）。前沿分别 18/29 个分区（按代码支配判定）。
- 旧 `results/` 根目录的 3/5 缺口和 121 个分区是 **WHLi 历史冻结输入** 对照，不得与新冻结输入的 4/6 缺口混用；本次新输入结果存放在独立的 `results/strict_q3_v2_seed_20260924/` 目录。
