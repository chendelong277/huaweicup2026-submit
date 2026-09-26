# Q1 单点往返运输能力与货箱组批

> **三分钟摘要**：这是问题一（无人机从调度中心到单个服务区往返送货、并把货箱分组装箱）模块的总览文档，说明代码在哪、怎么跑、结果文件有哪些。目前状态是"可集成"：主方案已在真实题目数据上跑通，80 个货箱分 18 架次全部送完，所有自动检查通过；读者照着"复现命令"一节即可重跑全部结果。

## 当前状态

可集成。代码已对原始附件运行，默认返航安全余量 20% 的主方案完成 80 个货箱的唯一交付，共 18 架次，总运输能耗 59.263908 kWh，累计作业时间 32804.873419 s。独立结果校验通过，未发现漏箱、重复配送、跨服务区组批、超质量、超体积或返航能量不足。

## 目录

- code/q1_solver.py：数据读取、严格 DEM 像元穿越、能耗与时间计算、精确组批、敏感性、图表和结果工作簿生成。
- code/validate_results.py：从已生成 CSV 反向重建批次并重新审计。
- code/config.json：默认余量、敏感性场景和多目标优先序。
- results/：强制接口 CSV、扩展结果、审计、图件、Excel 汇总和复现清单。

## 运行环境

Python 3.8 及以上；依赖见 code/requirements.txt。当前实际运行环境为 Anaconda Python，具体版本和输入文件 SHA-256 记录在 results/runtime_manifest.json。

## 复现命令

从仓库根目录运行，并将 `<题目数据目录>` 替换为未纳入 Git 的原始附件目录：

    python "code/Q1/code/q1_solver.py" \
      --data-root "<题目数据目录>" \
      --output-dir "code/Q1/results"

再次审计：

    python "code/Q1/code/validate_results.py" \
      --data-root "<题目数据目录>" \
      --results-dir "code/Q1/results"

## 主要输出

- results/safe_payload.csv：三机型对 15 个服务区的最大安全载荷。
- results/batching_baseline.csv：逐箱组批与机型选择，符合项目强制接口。
- results/segment_library.csv、results/node_registry.csv：供 Q2/Q3 复用的公共航段与节点数据。
- results/sensitivity_summary.csv、results/safe_payload_sensitivity.csv：返航余量 10% 至 40% 的敏感性。
- results/constraint_audit.csv、results/global_audit.json：批次和全局硬约束审计。
- results/Q1_results.xlsx：便于人工查看的 Excel 汇总；仅含结果值，无公式和外部链接。

## 已知限制

题面明确给出等效航程、总能耗构成与返航余量约束，但当前题目文档没有展开水平能耗和爬升附加能耗的独立公式。本实现采用“可用电量 × 航段距离 / 等效航程”及重力势能除以爬升效率的解释（见 model.md 第 1、2 节）；若官方补充说明给出不同展开式，需统一重算 Q1 至 Q3。
