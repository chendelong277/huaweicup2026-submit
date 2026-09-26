# ALNS-Q2 迁移说明

> **三分钟摘要**：这个文档说明本目录的 Q2 求解代码是怎么拼出来的——把 chendelong E007 的 ALNS 算子框架接到 WHLi 的资源解码器上，并列出三个关键算子分别对应成了什么。它只讲代码来源和对应关系，以及一条运行命令，不包含实验结果。术语看不懂可查仓库根目录 `shared/glossary.md`。

本目录中的 `code/alns_q2.py`、`code/run_alns_q2.py` 将 chendelong E007 的
ALNS 算子框架接入 WHLi 的资源解码器。任务状态仍由“货箱集合—访问顺序—优先机型”
表示，候选解经载荷/体积、DEM 飞行、SOC、电池充电、实体无人机时间线和配送时限
解码后才评分。评分采用硬约束、加权延误、完工时间、架次、能耗的字典序。

E007 的关键算子 `critical_chain`、`deadline_split`、`related_rebuild` 已映射为
资源链顺序扰动、时限优先重组和跨服务区大邻域重建；WHLi 原有的六个破坏/修复算子
保留并共同进行自适应权重更新。

运行：

```text
python code/run_alns_q2.py --data-root <problem/数据> --output-dir <results> --time-limit 100
```
