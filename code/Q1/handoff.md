# Q1 交接说明

> **三分钟摘要**：本文档告诉做后续问题（Q2/Q3/Q4）的人：问题一产出了哪些 CSV 文件、每个文件有哪些字段、什么可以直接拿去用（航段几何和能耗算法）、什么不能当死输入（具体组批方案，后问可以重新分配货箱）。所有接口文件已生成并通过审计，数据版本可查 results/runtime_manifest.json。术语看不懂可查仓库根目录 `shared/glossary.md`。

## 状态

可集成。默认 20% 返航余量主方案与全部审计文件已生成。

## 正式接口

### results/safe_payload.csv

必需字段：service_node、vehicle_type、max_safe_payload_kg、energy_outbound_kwh、energy_return_empty_kwh、energy_total_kwh、energy_margin_kwh、limiting_constraint。

说明：最大安全载荷为连续质量上限。limiting_constraint 为 rated_payload、energy 或 zero_payload_energy_infeasible。

### results/batching_baseline.csv

必需字段：batch_id、service_node、vehicle_type、box_id、box_mass_kg、box_volume_m3、batch_total_mass_kg、batch_total_volume_m3、batch_energy_kwh。

新增向后兼容字段：batch_operation_time_s、energy_margin_kwh、soc_end。

### results/segment_library.csv

包含 O01 与 15 个服务区之间的 30 条有向航段、3 个机型，共 90 行。除强制字段外，增加最高 DEM 高程、相交像元数和几何模型版本。

### results/node_registry.csv

x、y 为以 O01 为原点的局部平面米制坐标；额外保留经纬度，便于 Q2/Q3 复核空间变换。

## 扩展结果

- batch_summary.csv：一批一行，便于调度模块直接生成候选任务包。
- service_summary.csv：服务区级需求与主方案指标。
- safe_payload_sensitivity.csv、sensitivity_summary.csv：返航余量敏感性。
- tradeoff_summary.csv：三个优先序的目标对比。
- constraint_audit.csv、global_audit.json：可行性审计。
- runtime_manifest.json：输入和代码校验和、配置、运行环境与耗时。

## 后续模块可使用范围

- Q2/Q3 必须复用本模块的航段几何、作业高度、等效航程和能耗函数，除非决策日志冻结了新的官方口径。
- Q1 组批只作为单点基线和候选任务包，不应冻结为 Q2/Q3 的硬输入。
- Q2 多点配送时须按每次投送后的剩余载荷逐段重算能耗，不能直接套用单点往返能耗。
- 40% 余量下 S008 空载往返不可行；若后续做高余量情景，应将其作为系统不可行而非组批失败处理。

## 数据版本

原始输入文件 SHA-256 和代码 SHA-256 见 results/runtime_manifest.json。所有 CSV 为 UTF-8 with BOM，字段名为英文 snake_case，数值字段不混入单位文本。
