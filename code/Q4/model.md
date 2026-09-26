# Q4 模型（ALNS-Q4）

> **三分钟摘要**：这是问题四的数学模型文档，写清了决策变量（每个组件分到哪一组）、每组资源需求怎么精确核算（按占用时间区间的同一时刻最大并发数）、按什么优先级逐级比较分区方案（先看资源缺口，再依次看中继复制数、组间均衡、资源冗余），以及 ALNS 算法的完整伪代码和算子清单。读问题四的代码或结果前建议先读这份。术语看不懂可查仓库根目录 `shared/glossary.md`。

## 集合与参数

- 服务区集合 S = {S001..S015}；组件集合 C = {C1..C6}（同架次耦合的并查集压缩结果，见 analysis.md）。
- 任务组集合 G = {0..K-1}，K ∈ {2, 3}。
- 冻结运输架次 T（22 个）：每个架次 t 有路线 route(t)、机型 m(t)、起飞 start(t)、返航 ret(t)、能耗 e(t)、落地 SOC soc(t)。
- 冻结中继架次 R（2 个）：每个 r 有起飞 launch(r)、返航 ret(r)、就绪 ready(r)（含 300 s 周转）、SOC soc(r)、覆盖架次集合 covers(r)。
- 资源类型 RT = {transport_A/B/C, battery_A/B/C, relay, energy_component}；库存 I = (4,2,2,6,4,4,2,6)。
- 电池等效完全充电时间 T_full = (1800, 2400, 3000) s；能源组件 1800 s；两段式充电模型 charge(soc)（与 Q1-Q3 口径一致）。

## 决策变量

指派向量 π: C → G（K 组无标号指派）。搜索空间大小 S(6,K)（K=2 为 31，K=3 为 90）。

## 资源核算（解码器，精确）

对给定 π，组 g 的占用区间（半开）：

- 运输无人机机型 m：∪_{t:π(c(t))=g} [start(t), ret(t)]
- 电池机型 m：∪ [start(t), ret(t) + charge(soc(t), T_full_m)]
- 中继无人机：∪_{r 覆盖组 g 架次} [launch(r), ready(r)]（跨组中继按组复制，复制数 = 涉及组数）
- 能源组件：∪ [launch(r), ret(r) + charge(soc(r), 1800)]

组 g 对资源 rt 的最小需求 n(g,rt) = 区间族峰值并发（区间图为完美图，峰值即精确最小配置，扫描线求得，同时返回峰值时刻 witness）。

总量 N(rt) = Σ_g n(g,rt)；缺口 short(rt) = max(0, N(rt) − I(rt))；冗余 red(rt) = N(rt) − N_glob(rt)（N_glob 为全局共享峰值）。

## 目标（词典序，AGENTS.md §3.5 的 Q4 特化）

§3.5 主链"硬违约→WT→makespan→架次→能耗"中，WT、makespan、架次数、能耗在冻结方案下与 π 无关（代码中以不变量断言并在档案中留痕），因此 Q4 的判别层级按题面输出维度扩展为：

```
score(π) = ( hard_violations,      # 空组数 + 未分配组件数（构造保证为 0）
             shortage_total,       # Σ_rt short(rt)
             relay_extra,          # 跨组中继复制数 − 原架次数
             balance_time,         # max_g W_g / mean_g W_g，W_g = 组内架次时长和
             redundancy_total )    # Σ_rt red(rt)
```

逐级比较，前级优者胜出。

## 硬约束

1. 每个服务区恰属一组（π 为全函数，组件划分天然满足）；
2. 每组至少一个服务区（空组计入 hard_violations）；
3. 同架次多服务区同组（组件构造保证）；
4. 资源不跨组调配（按组独立核算峰值）；
5. Q3 冻结的路线/组批/顺序/通信关系不变（算法不触碰，仅读入）。

## 外部档案（§3.5 要求）

搜索中所有 hard_violations=0 的解按目标向量 (shortage_total, balance_time, redundancy_total, relay_extra) 判定 Pareto 支配：不被支配者入档、被新解支配者出档，上限 32（超出时弹出均衡比最差者）。每条档案记录附带冻结方案不变量 (WT, makespan, 架次数, 能耗)。算法双输出：①词典序最优解 min_archive(score)；②档案全集（pareto_archive.json）。

## ALNS-Q4 伪代码

```
输入: ctx(冻结方案), K, budget, seed
初始: 候选初始指派 ∈ {中继簇对齐, 地理 k-means, 随机}，取 score 最优者为 current
archive ← ∅; best ← current; T ← T0
while 未超 budget:
    按轮盘赌选 destroy 算子 d（shortage>0 时 shortage_targeted 才可入选）和 repair 算子 r
    removed ← d(current)          # 移除 1~4 个组件（或相关/失败导向集合）
    cand ← r(current \ removed)   # 贪心 / regret-2 / 随机插入，修复至 K 组非空
    ev ← evaluate(cand)           # 精确峰值核算
    if ev.score < current.score: accept            # 词典序改进
    elif 同 hard 层 且 Δsa ≤ 0.08 且 rand < exp(-max(0,Δsa)/T): accept   # SA 侧向游走
    if ev.hard > current.hard: reject              # 硬违约变多强拒
    accept ⇒ current ← cand, 奖励 (+4 改进 / +1 接受；成为新 best 再 +8)
    archive_insert(ev)                              # 非支配档案维护
    每 250 迭代: 按轮换权重 λ 从档案选亲本, current ← 亲本   # 重锚定反馈
    每 100 迭代: weight ← 0.2 + rewards/uses, 清零计数      # 分段自适应权重
    T ← T × 0.99985
输出: min_archive(score)（词典序最优）与 archive（非支配解集）
```

## 算子

Destroy：random_removal（1–3 组件）；related_removal（同中继覆盖簇耦合组件）；shortage_targeted_removal（缺口资源 witness 时刻活跃组件，失败导向、门控）；balance_targeted_removal（最大工作量组 1–2 组件）。
Repair：greedy_insertion（重组件优先、试全部组取词典序最优）；regret2_insertion（最大后悔值优先）；optimal_insertion（移除数 ≤4 时枚举全部 k^|removed| 联合重指派取最优，否则退化为贪心——跳出序贯贪心无法到达的盆地）；random_insertion（随机 + 空组修复）。

## 外部档案截断

档案容量 32，采用冠军保护式多样性淘汰：永不淘汰词典序最优条目，其余弹出均衡比最差者；主循环另维护独立 best-ever，最终选解 = min(best-ever ∪ archive)。

**输入版本说明**：上文 `C1..C6`、22 运输架次、历史搜索空间 S(6,2/3) 描述的是原 `results/` 的 WHLi 冻结输入，仅作对照。2026-09-25 的 `results/frozen_alns_q3_v2_seed_20260924/`（weiliu ALNS 自产运输 + WHLi 严格门控中继重装配）有 24 运输架次、3 中继架次、9 个不可拆服务组件，实际搜索空间变为 S(9,2)=255、S(9,3)=3,025；解码器及目标定义不变，组分与资源需求由冻结数据动态读取，不能沿用历史结果。

## 四种偏好场景的选择规则

独立程序 `code/run_scenarios.py` 对所有非空无标号分区完整枚举；本规模下该候选集而非启发式搜索是选择结果的权威依据。每个分区仍按原 `evaluate_partition` 精确核算硬约束、占用并发、库存缺口与工作量，不改变冻结运输或通信任务。

1. `shortage_first`：按 `(硬违约, 总缺口, 中继复制数, 均衡比, 冗余)` 升序比较。
2. `balance_first`：按 `(硬违约, 最大组工作量, 均衡比, 总缺口, 中继复制数, 冗余)` 升序比较，明确将最大工作量设为均衡首要指标。
3. `balanced_shortage_first`：仅保留 K=2 均衡比不超过 1.5、K=3 不超过 2.0 的分区，再按 `(硬违约, 总缺口, 中继复制数, 均衡比, 冗余)` 比较。阈值无候选时输出无候选，不放宽阈值。
4. `pareto`：只纳入硬约束可行候选，按 `(总缺口, 均衡比, 中继复制数, 冗余)` 求非支配解；另从该前沿选出缺口优先、均衡优先和满足同一均衡阈值下缺口优先的代表点。

该场景脚本与 ALNS 搜索彼此独立。可选的 `--run-alns` 只读取并重评估已有 ALNS `solution.json`，作为保存候选与枚举权威结果的对照，不声称是新一轮 ALNS 优化。
