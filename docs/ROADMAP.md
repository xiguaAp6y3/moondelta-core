# moondelta-core 路线图（2026-10-22 修订版：完全兼容冲刺）

> 修订原因：2026-10-22 审核意见指出，申报材料承诺"与标准 Delta 表互通"，但项目将嵌套 schema 完整处理排除在终点之外，且未确立完整兼容的协议版本，两者不一致。
> 本版回应：目标从"受限 flat 子集"上调为 **Delta Transaction Log Protocol reader v1 / writer v2 的完全兼容**，并给出 2026-10-31 前的排期、必需行为闭集与验收标准。
> 兼容性口径的正式定义见 [docs/协议兼容性与互操作范围.md](协议兼容性与互操作范围.md)——其第 3 节的七项门槛与第 4 节案例 A/B 即"完全兼容"的验收口径，本路线图把该升级路径落到日期；申报措辞在门槛完成前仍按其第 3 节使用"flat 子集兼容、nested 明确拒绝"。逐项现状取证见 [artifacts/acceptance-2026-10-22-full-compat.md](../artifacts/acceptance-2026-10-22-full-compat.md)。

## 1. 目标协议与"完全兼容"的定义

**目标协议**：Delta Lake Transaction Log Protocol —— `minReaderVersion = 1, minWriterVersion = 2` 的**完整**实现。

**关键修正**：嵌套 schema（`struct` / `array` / `map`）不是协议可选特性，而是 `metaData.schemaString` 与 Parquet 物理 schema 的一部分，**不受协议版本门控**——一张 reader v1/writer v2 的标准 Delta 表可以（且经常）包含嵌套列。因此"完全实现 reader v1/writer v2"必然包含嵌套 schema 的读取与写入，不得排除在终点之外。

**"完全兼容"判定标准**（全部成立才可使用该表述）：

1. 第 2 节必需行为闭集内的每一项都实现且有正向 + 负向测试；
2. 闭集内无任何静默行为：不静默忽略、不静默降级、不静默返回错误数据；闭集外输入一律结构化拒绝并说明原因；
3. **同一协议配置（reader v1 / writer v2、JSON log + SNAPPY Parquet）下**，flat 表与 nested 表分别完成"外部引擎 → 本项目读取"和"本项目写入 → 外部引擎读取"的双向对拍，外部引擎至少两种（当前采用 delta-rs 1.6.6 与 DuckDB 1.5.6，Spark 可选加分），逐行逐字段一致；
4. 高于支持范围的输入（reader ≥ 2、未知必需 feature、未申报的类型/编码）**按协议规范拒绝并说明原因**——这是 reader v1 读取端的规范行为，不算缺口，但拒绝行为本身必须有测试；
5. 所有显式例外（申报为"不支持"的项）在申报材料中逐条列出，未列出的一律按"必须支持"验收。

## 2. 必需行为闭集（完全兼容验收范围）

| # | 层次 | 完全兼容必需行为 |
|---|---|---|
| A1 | 协议协商 | `minReaderVersion > 1` 拒绝并说明原因；`readerFeatures`/`writerFeatures` 解析，含未知必需 feature 的表拒绝 |
| A2 | 协议协商 | writer 版本高于本项目写入能力时：读允许（reader=1 表）、写拒绝并说明；writer 版本门控的特性物化在数据/日志中时结构化拒绝 |
| B1 | 日志与状态 | version 0 起连续 JSON 回放、版本发现、时间旅行、`history()`（现状已有） |
| B2 | 日志与状态 | **checkpoint 恢复**：解析 `_last_checkpoint` 与 checkpoint Parquet，重建快照；日志截断/缺口经 checkpoint 恢复，无法恢复时明确拒绝（不得静默读旧版本） |
| B3 | 日志与状态 | 日志文件名严格按 20 位数字校验；版本完整性校验（缺中间版本 → 错误） |
| C1 | actions | `protocol`/`metaData`/`add`/`remove`/`commitInfo` 完整语义；`txn` 识别 |
| C2 | actions | 未知 action 不静默忽略：结构化错误（快照无关 action 的显式忽略策略须在文档申报并输出提示） |
| D1 | schema（基础格式） | 全部 primitive：boolean、byte、short、integer、long、float、double、string、binary、date、timestamp（timestamp_ntz 随 M2 评估）读取；分区列按 `partitionValues` 补齐 |
| D2 | schema（基础格式） | **嵌套读取**：`struct`、标准 3-level `list`、`map`，覆盖 null、empty、重复元素、嵌套组合 |
| D3 | schema（基础格式） | **嵌套写入**：与 D2 对等的写入能力，产出可被外部引擎读回的 Parquet |
| D4 | schema | 显式例外（允许拒绝但必须申报）：decimal、uuid 等需保留额外逻辑语义的类型——拒绝时错误含类型名与字段路径 |
| E1 | Parquet 数据层 | definition/repetition level 解码；SNAPPY/UNCOMPRESSED；其余编码结构化拒绝（显式例外） |
| E2 | Parquet 数据层 | `add.path` percent-decoding；数据文件缺失结构化错误（含文件名） |
| F1 | 统计 | flat min/max 剪枝、`numRecords`（现状已有）；nested 字段 stats 不误用为 flat，无法安全剪枝时放弃剪枝继续正确读取 |
| F2 | 统计 | stats 解析失败显式告警或拒绝，不得静默返 0 |
| G1 | 写路径 | `create_table` 拒绝已存在的表；`append_rows` 校验列名与类型同 `metaData.schema` 一致；commit 原子性（目标版本存在即失败，put-if-absent） |
| G2 | 写路径 | 写出表 protocol 字段恒为 `minReaderVersion=1, minWriterVersion=2`（现状已满足） |
| H1 | 对拍 | 案例 A（flat，scope 文档 §4）：双引擎双向，逐行逐字段一致（现状已通过） |
| H2 | 对拍 | 案例 B（nested，同协议同口径）：双引擎双向，含 `profile=NULL`、`tags=[]`、`attrs={}` 等边界（现状失败，本冲刺关闭） |
| H3 | 工程行为 | CLI 退出码：成功 0、错误非 0；错误输出到 stderr；CLI 参数校验（未知 flag 报错） |

显式例外清单（申报材料同步列出，拒绝必须带原因）：decimal、uuid、ZSTD/GZIP/BROTLI/LZ4 压缩、对象存储、reader ≥ 2 的全部特性（column mapping、deletion vectors、v2 checkpoint）、分区表 append、MERGE/UPDATE/DELETE、并发写仲裁。

## 3. 对照案例（审核答复口径）

同一 Delta protocol 配置：`minReaderVersion=1, minWriterVersion=2`、按版本编号 JSON log、SNAPPY Parquet 数据文件；仅改变 schema，以分离"协议日志兼容"与"基础格式兼容"两个维度。

**案例 A（flat）**：`id: long NOT NULL, name: string, active: boolean`，含 null、多文件。delta-rs 1.6.6 与 DuckDB 1.5.6（`delta_scan`）写出/读出作为 oracle，本项目双向读取与写入。**当前状态：通过**（`artifacts/interop-flat/`，56 项检查 49 PASS/0 FAIL/2 DIFF，哈希 79 项可重算，2026-10-22 复验 17/17）。

**案例 B（nested）**：`id: long, profile: struct<name:string, age:int>, tags: array<string>, attrs: map<string,string>`，5 行含 `profile=NULL`、struct 子字段 null、`tags=[]`、`tags=NULL`、数组含 null、`attrs={}`、`attrs=NULL`。外部双 oracle 均读回 5 行逐字段一致；本项目当前在 Parquet 层结构化拒绝（错误含字段路径与 kind）。**当前状态：不通过**（外部引擎通过、本项目拒绝 = 能力缺口）；完成第 2 节 D2/D3/E1 后，同一 fixture 转为正向断言（`artifacts/interop-nested/`，38 项复验可重放）。

## 4. 冲刺排期（2026-10-22 → 2026-10-31）

| 里程碑 | 截止 | 交付 | 退出标准（全部满足才进入下一里程碑） |
|---|---|---|---|
| **M0 口径落地** | 10-22 | 应用 `artifacts/claim-alignment.patch`（现状如实表述为"受限 flat"，补丁已验证可应用）；`artifacts/`、范围文档、示例一并提交入库 | 三份文档无超范围表述；`git status` 干净；推送成功 |
| **M1 协议与工程基线** | 10-23 | A1/A2/C1/C2/B3/H3/D4（byte/short 补齐）+ 写路径 G1 + F2/E2 | 每项至少一个负向 fixture 测试；`deletion-vector-add`、`protocol-reader-v3-features`、`unknown-actions`、`nonstandard-log-name` 由"静默接受"变为"非 0 退出 + 明确原因" |
| **M2 Parquet 嵌套读取层**（✅ 2026-10-22 完成，提前） | 10-25 | E1（def/rep level，vendored reader 已有 `decode_levels_v1` 骨架）+ D2 + D1 timestamp_ntz 评估 | 案例 B fixture 本项目读回 5 行，与双 oracle 逐字段一致；D3 七项门槛中读取侧 3 项转绿 |
| **M3 checkpoint 恢复**（✅ 2026-10-22 完成，提前） | 10-27 | B2（checkpoint Parquet 解析依赖 M2 的 repeated group 读取） | `checkpoint-truncated` fixture 经 checkpoint 重建快照并与 JSON 回放逐字段一致；`checkpoint-only` 由拒绝变为可读 |
| **M4 嵌套写入 + 统计**（✅ 2026-10-22 完成，提前） | 10-29 | D3（nested Parquet 编码）+ F1（nested stats 放弃剪枝策略） | 本项目写出的 nested 表被 delta-rs 与 DuckDB 读回逐字段一致（门槛 7）；外部引擎写出的 nested 表本项目可读（双向闭环） |
| **M5 全量复验与重提** | 10-31 | 按第 2 节闭集全量复验收，出正式报告；更新申报表格、README、CHANGELOG | 闭集 100% PASS（或按第 5 节降级表述）；案例 A/B 双向通过；申报材料与实现零差距 |

排序依据：M1 无依赖且直接关闭审核指出的"必须拒绝而未拒绝"类行为；M2 是 M3/M4 的公共依赖（checkpoint 与 nested 写入都需要 repeated group 编解码）；M5 留整日复验。

## 5. 风险与降级承诺

- **最大风险**：M4（nested 写入）——需要扩展 vendored writer 支持 repeated group 编码，工作量最大。若 10-29 未达标：申报措辞降级为"读取覆盖 reader v1 协议全部基础格式（含嵌套）；写入覆盖 flat 全类型"，显式列出写入侧例外，**不得**保留"完全兼容/直接互通/可平移"表述；读侧承诺不受影响。
- M3 若 checkpoint Parquet 解析延期：降级为"日志完整或缺口可检测的表可读；checkpoint-only 表明确拒绝"，但 B3 的完整性校验（不静默读旧版本）为**硬门槛**，不可降级——静默读错数据比明确拒绝更不可接受。
- 任何降级都必须同步更新申报表格与 README，并在本路线图记录降级原因与时间。

## 6. 10-31 之后（原 0.3 / 1.0 保留）

1. 压缩编解码扩展：ZSTD/GZIP/BROTLI/LZ4（集成 `bikallem/compress`，vendored `compression.mbt` 加分发点）；
2. 分区表 append；对象存储抽象（S3/R2）；Change Data Feed 读取；
3. reader v2/v3 特性：column mapping、deletion vectors、v2 checkpoint（在实现前按协议拒绝）；
4. 列裁剪、谓词下推、row-group 级流式读取；MERGE/UPDATE/DELETE；乐观并发仲裁；性能基准。

## 7. 验收对齐

| 里程碑 | 对应验收条目（acceptance-2026-10-22-full-compat.md） |
|---|---|
| M0 | §3 第 0 项（口径落地） |
| M1 | 矩阵 A1/A2/C1/C2/B3/D1(byte/short)/E2/F2/G1/H3 全部转 PASS |
| M2 | 矩阵 D2、案例 B 读取侧转 PASS |
| M3 | 矩阵 B2 转 PASS |
| M4 | 矩阵 D3/F1、案例 B 写入侧转 PASS |
| M5 | 全矩阵复核，出具结论 |
