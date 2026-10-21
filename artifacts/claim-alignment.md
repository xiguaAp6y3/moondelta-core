# D4 申报文本一致性修订（claim-alignment）· 第三版

> 版本：**v3（D4c）**，覆盖 v1/v2；生成时间：**2026-10-20 20:40 +08:00**。
> 本版修正 D5 独立验收（[artifacts/acceptance-report.md](../artifacts/acceptance-report.md) §6.5）发现的 D4-I1（中）、D4-I2（低）、D4-I3（低），并保留 v2 已并入的 D1 协议审计（[artifacts/protocol-audit.md](../artifacts/protocol-audit.md)）、D2 flat 对拍（[artifacts/interop-flat/README.md](../artifacts/interop-flat/README.md)）与 D3 nested 结论（[artifacts/interop-nested/README.md](../artifacts/interop-nested/README.md)）。

## 0. 交付摘要

| 项 | 内容 |
|---|---|
| 补丁 | [artifacts/claim-alignment.patch](claim-alignment.patch)，标准 unified diff（`a/` `b/` 前缀，LF、无 BOM），共 **9 个 hunk**，22,063 B / 172 行，SHA-256 `DBDE5AB7E0578C43828ACD5C601A29EB360A20795DB0C9101F14AE108FDEA3EA` |
| 范围 | `申报书.md` 2 hunk、`README.md` 5 hunk、`docs/ROADMAP.md` 2 hunk；`CHANGELOG.md` 无 hunk（理由见 §5） |
| 校验 | `git apply -p1 --check artifacts/claim-alignment.patch` → exit 0；临时副本实际 apply → exit 0；四个目标文件工作区未改动 |
| 本版核心（v2→v3） | ① **D4-I1**：把 `moon package` / GitHub Actions / mooncakes 安装三项未验证断言改为“未在本机复核、发布前需提供输出 / CI 链接 / 安装日志”，并同步 README 顶部“已发布”、快速开始安装注释、开发章节 CI 描述、ROADMAP 0.1.x 表；② **D4-I2**：warning 统计精确为“43 条带文件定位 = 39 条项目 src/ + 4 条 vendored；duplicate alias 是第 44 条无文件定位的工程配置 warning，不在这 43 条内”；③ **D4-I3**：md 中“121 次运行全部 exit 0”改为“121 份带退出码的 CLI/校验输出全部 exit 0”，并注明 `commands.txt` 131 条记录（含重复 probe 块与首轮 `probe-library` exit 1）的口径；④ 顺手为“约 2,575 行”标注“按文件全行统计、含空行/注释、含 D2 示例 69 行” |

> 入库提醒：`docs/协议兼容性与互操作范围.md`、`artifacts/protocol-audit.md`、`artifacts/acceptance-report.md`、`artifacts/interop-flat/`、`artifacts/interop-nested/` 均为未跟踪文件/目录；patch 中的引用依赖它们，须与 patch 同 commit 入库（或先确认这些证据目录的提交策略），否则 README/申报书的链接与证据索引会失效。
>
> 行数统计依赖：D2 新增的 `src/examples/interop_flat_writer/`（69 行）目前未跟踪；若最终不入库，申报书中的“约 2,575 行”应改为“约 2,506 行”（核心库 1,508 + CLI 383 + 测试 570 + 示例 45，按文件全行统计）。

## 1. 证据基线

### 1.1 D1 协议审计的关键结论（v2 已并入，v3 仅修正统计口径）

| 审计结论 | 与本 patch 的关系 |
|---|---|
| 122 项矩阵：已实现且有测试 27 / 已实现无测试 20 / 只识别 4 / 明确拒绝 24 / **未覆盖 47（多为静默接受/忽略/0 行）** | 支撑把 README 的“遇到即报错、不静默降级”改为“对已识别边界返回结构化错误；闭集外尚未全部拒绝” |
| F1（严重）：`add.deletionVector` 被静默忽略，3 行 fixture `read` 返回 3 行（正确应为 2 行）；`deletionVector` 在 `src/` 0 命中 | 不得写“删除向量明确拒绝”，改“未检测/已知缺口” |
| F2（严重）：JSON 0..9 + checkpoint v10 + `_last_checkpoint=10` 的表被静默当旧版本：`inspect` 报 `current version: 9`、`files: 90`，无错误 | 删除“日志被截断的表明确拒绝”，改为“当前未检测，会读旧快照” |
| F3（严重）：reader=2/writer=5、reader=3/writer=7（含 `readerFeatures: ["unknownRequiredFeature"]`）均被接受，`read` 成功；`readerFeatures`/`writerFeatures` 在 `src/` 0 命中 | 写“高于 reader v1 的 protocol 与 feature 当前不校验、不拒绝”，不写“拒绝” |
| F4（严重）：**121 份带退出码的 CLI/校验输出（112 个编号 CLI case + 9 个探针/Parquet 复核脚本）全部 `[exit code: 0]`**（含 not found / parse error / unsupported）。口径说明：`commands.txt` 共 **131 条记录**，含重复 probe 块与首轮 `probe-library` 的 `exit 1`（随后同块重跑 `exit 0`）；“121”以 `out/` 中带退出码的输出为准（D1-I2、D5 D4-I3） | 申报书/README 增“CLI 错误退出码恒 0”缺口（不写具体次数，避免再次口径争议） |
| F5（严重）：`append_rows` 不校验列名/类型，long 列写 string 后 `read` 输出 `id (long) = not-a-long`；NOT NULL 列可写 NULL | 改写写入边界：只校验列数/行长，不校验列名/类型/NOT NULL |
| F6–F12：类型错配静默转换、`stats` 非 string 时 `rows: 0`、未知 action 静默忽略、column mapping 误报“列缺失”、`create_table` 不校验/覆盖 v0、CLI 过滤降级、错误上下文缺失 | 作为“闭集外静默/未检测项”证据；不新增超出证据的表述 |
| §5 共 15 条“声明与实现不一致” | 本 patch 逐条落实（映射见 §3） |
| §5 #14：申报书 L47 “已实测”三项均复核属实；`moon test` 14/14、`moon check` 0 errors 属实 | 保留 L47 事实，并补 D2/D3/审计引用 |
| §5 #15：`moon package`、GitHub Actions、“mooncakes 0.1.1 空工程安装”均**未验证** | v3 已按 D4-I1 改为“未在本机复核、发布前需提供输出/链接/日志”（B11、B13、R0、R5b、H7、H8） |

### 1.2 D2 / D3 最终结论（v2 已并入）

| 报告 | 关键结论 |
|---|---|
| D2 [interop-flat](../artifacts/interop-flat/README.md) | delta-rs 1.6.6 + DuckDB 1.5.6；**49 PASS / 0 FAIL / 2 已记录 DIFF / 5 INFO / 0 BLOCKED（共 56 项）**；A（delta-rs 写→本项目读）与 C（本项目写→外部引擎读）逐列值一致；R-1 写出列恒为 `optional`（Delta 声明 NOT NULL）、R-2 stats 缺 `nullCount` 与非数值列 min/max、R-3 `reader=2/writer=5` 表被接受；**Spark / Databricks 未运行**（§12） |
| D3 [interop-nested](../artifacts/interop-nested/README.md) | 外部引擎（delta-rs、DuckDB）读 nested fixture 成功且逐字段一致；本项目 `read` 在 schema 阶段结构化拒绝，无假成功；第 3 节七项门槛 **已完成 1 / 部分 0 / 未完成 6**，唯一完成的**门槛 6 是“fixture/边界/外部引擎对照”证据门槛，不代表本项目可读 nested**；`[comparison.md](../artifacts/interop-nested/comparison.md)` 明确“不要引用为 nested 兼容证据” |

### 1.3 D5 独立验收结论（本版依据）

| 项 | D5 结论 | v3 处理 |
|---|---|---|
| D4-I1（中，发布前须处理） | patch 保留 `moon package` 通过 / GitHub Actions 已通过 / mooncakes 安装验证三项声明，而 D1 §5 #15 与 D5 均未验证 | 申报书 B11/B13 重写为“未在本机复核、发布前需提供 `moon package` 输出 / CI 链接 / 安装日志”；README 顶部发布声明、快速开始安装注释、开发章节 CI 描述、ROADMAP 0.1.x 表同步 |
| D4-I2（低） | “43 条 warning 中 39 条在 src/：…另有重复 alias 与示例 env 未 import”把 duplicate alias 混入 43 条 | 改为“43 条带文件定位 = 39（38 derive + 1 示例 env）+ 4 vendored；duplicate alias 是第 44 条无文件定位的工程配置 warning，不在这 43 条内” |
| D4-I3（低） | `claim-alignment.md` 转录“121 次带退出码运行全部 exit 0”，继承 `commands.txt` 131 条记录/1 条 exit 1 的口径瑕疵 | 本文件 §1.1 F4、§3 改为“121 份带退出码的 CLI/校验输出全部 exit 0”，并写明 `commands.txt` 131 条记录口径 |
| 行数口径（建议） | 建议标注统计口径 | B7/B9 注明“按文件全行统计、含空行/注释、含 D2 示例 69 行” |

### 1.4 本次实测（仍成立）

- `moon check --target native`：0 errors，43 条带文件定位 warning（39 条项目 `src/`、4 条 `src/vendor/parquet`；另有 1 条无文件定位的 duplicate alias 不在此 43 条内）。
- `moon test --target native`：`Total tests: 14, passed: 14, failed: 0`。
- CLI `inspect fixtures/delta/table_a`：version 12、13 版本、120 文件 / 120 行 / 54232 B、`last checkpoint: version=10`；`scan --where "id>=60"` 120→60、`skipped stats: 60`；`read fixtures/delta/covid --limit 157865` → 1 文件 157865 行 6 列（D1 §4.13 复核一致）。
- 代码事实：`src/actions.mbt` `parse_protocol` 只读两个版本号；`src/snapshot.mbt` `replay_until` 只检查 `versions[0]==0`，不比较 checkpoint 版本；`src/write.mbt` `append_rows` 只校验列数/行长、列恒 `@parquet.Optional`；`src/cmd/delta/main.mbt` `fail()` 只打印、不设退出码。

## 2. 逐 hunk 说明（v3，共 9 hunk）

行号为**原文**行号；改句保留原 Markdown 行内代码、链接与全角标点。

### H1 — `申报书.md` `@@ -16,14 +16,14 @@`（L19、L20、L21、L25、L26）

**B1｜L19（核心超范围承诺）**

- 原句：`- 格式与生态一致：读写标准 Delta 目录，Spark / Databricks / delta-rs / DuckDB 可直接互通，已有表可以平移使用；`
- 改句：`- 格式与生态一致：读写 Delta protocol reader v1 / writer v2、version 0 起连续 JSON 日志、flat schema、UNCOMPRESSED/SNAPPY 压缩与本地文件系统范围内的目录；flat 表已完成与 delta-rs 1.6.6、DuckDB 1.5.6 的双向目录级对拍（D2：49 PASS / 0 FAIL / 2 项已记录差异，见 `artifacts/interop-flat/README.md`），Spark / Databricks 未在本环境验证；闭集之外当前并非“明确拒绝”：删除向量被静默忽略并读全量、截断日志 + checkpoint 被当作旧版本读、reader v2/v3 与未知必需 feature 不被拒绝（见 `artifacts/protocol-audit.md` §5）；nested 读写的第 3 节七项门槛仅完成 1/7（仅证据门槛 6，不构成 nested 兼容，见 `artifacts/interop-nested/README.md`），需完成 nested 读写、checkpoint 恢复与至少两种外部引擎对拍等门槛后再扩大承诺；`
- 理由：原句把“遵循 Delta JSON 日志格式”写成“任意标准 Delta 表互通”。D1 证明 reader≥2/DV/column mapping 等标准表既不拒绝也可能读错，D3 证明 nested 仅证据门槛完成，D2 仅覆盖闭集内 flat 表。按 §5 建议措辞与 §3 门槛，写“限定范围内已对拍 + 范围外未拒绝的已知缺口 + 未来门槛”，不写“已互通”。
- 映射：§2 全表、§3 第 33/35–45 行、§5、§7；D1 §5 #1、§4.1/§4.2/§4.3；D2 §0；D3 §0/§7/§10。

**B2｜L20（存储边界）**

- 原句：`- 不绑定存储与运行时：库只处理目录与文件，不依赖 JVM/Rust 运行时，native 目标可用，后续可扩 wasm/js；`
- 改句：`- 不绑定运行时：库只处理目录与文件，不依赖 JVM/Rust 运行时，native 目标可用，后续可扩 wasm/js；当前存储只实现本地文件系统，对象存储未实现；`
- 理由：避免“不绑定存储”被读成已抽象 S3/R2；§2 Parquet 行只承诺本地文件系统。
- 映射：§2 Parquet 行；审计 §3.6 T40。

**B3｜L21（“类型层/明确拒绝/不静默降级”与实际不符）**

- 原句：`- 边界显式：不支持的 schema、压缩、写入类型在类型层与错误信息里明确拒绝，不静默降级——数据基础设施宁可拒绝，不可返回错数据。`
- 改句：`- 边界显式：对已识别的闭集边界（嵌套 schema、ZSTD/GZIP/BROTLI/LZ4、不在支持列表的写入类型）返回结构化错误；但闭集之外尚未全部拒绝——删除向量与未知 feature 未检测、append 不校验列名/类型/NOT NULL、stats 非 string 时行数显示 0、CLI 错误退出码恒 0（`artifacts/protocol-audit.md` §5 列出 15 条声明不一致），这些是待修复的静默/未检测项，不能视为“不静默降级”——数据基础设施宁可拒绝，不可返回错数据。`
- 理由：审计 §5 #2 实测 13 类静默行为，其中 DV 与类型错配返回错数据；原句“类型层”“不静默降级”均不成立。
- 映射：§2 第 29 行、§3 第 33 行；审计 §5 #2、§4.5–§4.8、§3.7 E4/E6。

**B4｜L25（“Spark 直接读”计划写成现状）**

- 原句：`1. **MoonBit ETL 写出、Spark 直接读**：…输出标准 Delta 目录；Spark / Databricks / delta-rs 用 `spark.read.format("delta")` 直接读，MoonBit 侧不需要装 Spark。`
- 改句：`1. **MoonBit ETL 写出、外部引擎读取**：…输出 Delta protocol reader v1 / writer v2 目录；D2 已用 delta-rs 1.6.6 与 DuckDB 1.5.6 回读本项目写出的 flat 表（…见 `artifacts/interop-flat/README.md`），Spark / Databricks 的 `spark.read.format("delta")` 读取尚未验证；本场景仅覆盖闭集内 flat 表，闭集外表的已知缺口见 `artifacts/protocol-audit.md` §5；MoonBit 侧不需要装 Spark。`
- 理由：保留预期场景，只写已实际验证的 delta-rs/DuckDB，明确 Spark/Databricks 未验证、场景仅限闭集内 flat 表。
- 映射：§5、§6 D2、§7；D2 §6/§10、§12；审计 §5 #14。

**B5｜L26（fixture 范围与“两个版本”不符）**

- 原句：`2. **外部表校验与迁移**：输入 delta-rs 或 Databricks 产出的表（例如 COVID-19_NYT，157,865 行 6 列），…对比两个版本的文件清单、行数与值…`
- 改句：`2. **外部表校验与迁移**：输入 delta-rs 或 Databricks 产出的 flat 表（例如 COVID-19_NYT；本仓库 fixture 只含该表 1 个 157,865 行 6 列的数据文件与 version 0 日志，对比两个版本需另备日志与数据文件齐全的表），…；注意当前对 reader≥2、含删除向量或 column mapping 的表未做检测（见 `artifacts/protocol-audit.md` §5）。`
- 理由：fixture 只有 `log0.json` 与 1/8 个数据文件，无法“对比两个版本”；157,865 是单文件 `stats.numRecords`（D1 用 pyarrow 复核）。
- 映射：§2 表操作行、§7 第 129 行；fixtures/README §3.2；审计 §4.1/§4.9/§5 #1。

### H2 — `申报书.md` `@@ -32,19 +32,21 @@`（L35、L41、L43、L46、L47、L48、L49、L50）

**B6｜L35（统计信息范围）**

- 原句：`- **写入**：`create_table` + `append_rows`，写 Parquet 数据文件、提交新的 JSON 版本，统计信息（numRecords/min/max）随日志写入；`
- 改句：`- **写入**：`create_table` + `append_rows`，写 Parquet 数据文件、提交新的 JSON 版本，统计信息（numRecords 与数值列 min/max；无 nullCount、无字符串/布尔 min/max）随日志写入；`
- 理由：审计 T31 与 D2 R-2 实测 stats 只含 `numRecords` + 数值列 min/max。
- 映射：§2 表操作行；审计 §3.6 T31；D2 §8/§9 R-2。

**B7｜L41（代码行数与实际不符；v3 标注口径）**

- 原句：`- **实现方式**：从零手写约 2,390 行 MoonBit（核心库 1,420、CLI 373、测试 552、示例 45）；…`
- 改句：`- **实现方式**：从零手写约 2,575 行 MoonBit（按文件全行统计、含空行/注释：核心库 1,508、CLI 383、测试 570、示例 114，其中含 D2 新增的 69 行互操作写入示例）；…`
- 理由：审计 §5 #13 实测全行统计为 1,508/383/570/114（D2 示例前合计 2,506，之后 2,575）；v3 按 D5 建议补齐“含空行/注释”口径说明。
- 映射：§7 第 127 行；审计 §5 #13、`out/code-evidence.out`。

**B8｜L43（边界与简化：checkpoint/feature/append 的事实修正）**

- 原句：`- **边界与简化**：不实现嵌套 schema（struct/list/map）与 ZSTD/GZIP/BROTLI/LZ4；不解析 checkpoint parquet（checkpoint-only 表明确拒绝）；写入支持 Int32/Int64/String/Binary/Boolean/Double（Required/Optional），不写分区表；不做对象存储、删除向量、列映射、MERGE/UPDATE/DELETE、并发写冲突仲裁、SQL/DataFrame 层。`
- 改句：`- **边界与简化**：读取只承诺 Delta protocol reader v1 的表、写出 writer v2 的表，且日志必须从 version 0 起连续；高于 reader v1 的 protocol（v2/v3）、`readerFeatures`/`writerFeatures` 与未知必需 feature 当前不校验、不拒绝；不实现嵌套 schema（struct/list/map）与 ZSTD/GZIP/BROTLI/LZ4，仅支持 UNCOMPRESSED/SNAPPY 压缩与本地文件系统；不解析 checkpoint parquet：checkpoint-only 表返回结构化 NotFound（消息为“has no log entries”），而日志被截断但存在 checkpoint 的表当前会当作旧版本读取（未检测，已知缺口）；删除向量（`deletionVector`）、column mapping、CDF 当前未检测（可能读全量或报误导错误）；写入支持 Int32/Int64/String/Binary/Boolean/Double，append 只校验列数/行长、不校验列名/类型/NOT NULL，写出列恒为 Parquet Optional；不写分区表；不做对象存储、MERGE/UPDATE/DELETE、并发写冲突仲裁、SQL/DataFrame 层。`
- 理由：逐条对应审计 §5 #2/#3/#6 与 §3.1 P2/P3、§3.3 C4/C5、§3.6 T28/T29/T30、T37/T38/T36。
- 映射：§1 第 9–14 行、§2 日志/protocol/checkpoint/schema/表操作行、§2 第 29 行；审计 §4.1/§4.2/§4.3/§4.9/§5 #2/#3/#6。

**B9｜L46（测试清单：用例名与真实构造不符 + 行数；v3 标注口径）**

- 原句：`- 手写 MoonBit 代码 2,390 行（核心库 1,420、CLI 373、测试 552、示例 45）；14 个具名测试全部通过，含负向用例（嵌套列拒绝、checkpoint-only 表拒绝、缺失数据文件、统计边界）与分区表读取/过滤；`
- 改句：`- 手写 MoonBit 代码约 2,575 行（按文件全行统计、含空行/注释与 D2 新增的 69 行互操作写入示例；核心库 1,508、CLI 383、测试 570、示例 114）；14 个具名测试全部通过，含负向用例（嵌套列拒绝、日志非 0 起点/缺版本拒绝、缺失数据文件、统计边界）与分区表读取/过滤；但名为 checkpoint-only 的用例实际构造的是非 0 起点日志，真实 checkpoint-only、删除向量、reader≥2 拒绝均无测试；`
- 理由：审计 §5 #4 核对 `src/delta_wbtest.mbt:230-247` 该测试只写 `000...03.json` 并断言错误含 `checkpoint-only`，并非真正 checkpoint-only；C4/C5 场景无测试。
- 映射：§7 第 127 行；审计 §5 #4、§3.3 C4、§6.1 P0。

**B10｜L47（已实测保留 + D2 互操作 + D1 已知缺口）**

- 原句：`- 已实测：delta-rs `table_a` 13 个版本回放 → 120 个文件；Databricks 写出的 COVID-19_NYT 数据文件读取 157,865 行 6 列；`id>=60` 统计剪枝 120 → 60 个文件；create + append + 重新读回通过；`
- 改句（三个 bullet）：
  1. `- 已实测（仓库 fixture 上的运行结果，D1 已复核）：…（仓库只含日志、不含数据文件）；…（该表共 8 个数据文件，仅 1 个入库）；…；`
  2. `- 互操作验证（D2）：与 delta-rs 1.6.6、DuckDB 1.5.6 完成 flat 表双向对拍（…49 PASS / 0 FAIL / 2 项已记录差异…）；Spark / Databricks 未在本环境验证；nested 表只做到“识别嵌套类型并明确拒绝”（D3：七项门槛 1/7，仅证据门槛 6……），不构成嵌套互操作；`
  3. `- 已知缺口（D1 审计，见 artifacts/protocol-audit.md §5）：删除向量被静默忽略并读全量（3 行 fixture 返回 3 行、正确应为 2 行）、截断日志 + checkpoint 被当作旧版本读（报 v9/90 文件而非 checkpoint v10）、reader v2/v3 与未知必需 feature 不被拒绝（reader=3/writer=7 的 read 成功）、append 不校验列名/类型/NOT NULL、stats 非 string 时 rows 显示 0、CLI 错误退出码恒 0；这些是待修复项，当前不能描述为“明确拒绝/不静默降级”；`
- 理由：审计 §5 #14 复核 L47 三项事实成立；D2/D3 只写实际范围；D3 由“0 项完成”改为 **1/7（仅证据门槛 6）**。
- 映射：§3 第 33/75 行、§5、§7 第 128/133 行；审计 §5 #14；D2 §0/§9/§10；D3 §0/§7/§10。

**B11｜L48（CI/package/warning 三处事实修正 —— D4-I1、D4-I2）**

- 原句：`- `moon check` / `moon test`（native）通过，`moon package` 通过，CLI 六子命令与示例可运行（warning 全部来自 vendored parquet 上游代码）；GitHub Actions 流水线已通过（check / build / test 全绿）；`
- 改句：`- `moon check` / `moon test`（native）通过（0 errors、14/14；当前工具链 43 条带文件定位的 warning 中 39 条在项目 `src/`（38 条 derive 触发 `implicit_impl_as_method` + 1 条示例 env 未 import）、4 条在 vendored parquet；另有 1 条无文件定位的 duplicate alias 工程配置 warning 不在这 43 条内，均非 error）；CLI 六子命令与示例可运行；`moon package` 与 GitHub Actions 流水线（check / build / test）未在本机复核，发布/申报前需提供 `moon package` 输出与 CI 运行链接；`
- 理由：**D4-I1（中）**：原句把未验证的 `moon package`/CI 写成已通过，D1 §5 #15 与 D5 §6.5 均确认未验证。改为“未在本机复核 + 发布前需提供证据”，保留已实测的 `moon check/test` 与 warning 构成。**D4-I2**：43 条 = 39 条带定位 `src/`（38 derive + 1 示例 env）+ 4 条 vendored；duplicate alias 是第 44 条无文件定位的工程配置 warning，不混入 43。
- 映射：§7 第 127 行；D1 §2.2、§5 #5、§5 #15、§7；D5 §6.5 D4-I1/D4-I2；`protocol-audit/check.out`、`moon test` 输出。

**B12｜L50（未来计划补充修复项）**

- 原句：`- 后续计划：ZSTD 解压与 checkpoint parquet 解析、分区表 append、Float/Timestamp 写入（季度扩展）。`
- 改句：`- 后续计划：ZSTD 解压与 checkpoint parquet 解析（仅 checkpoint 所需的最小嵌套 shape，不等于 nested schema 兼容）、分区表 append、Float/Timestamp 写入（季度扩展）；同时修复 `artifacts/protocol-audit.md` §5 列出的闭集外静默/未检测项（删除向量、截断 checkpoint、protocol version/feature 校验、append 校验、CLI 退出码）；`
- 理由：§3 第 45 行要求 checkpoint 最小 shape 不得写成 nested 兼容；审计 §6.3 给出修复路径。
- 映射：§3 第 35–45 行；审计 §6.3。

**B13｜L49（mooncakes 发布/安装声明 —— D4-I1）**

- 原句：`- 已发布 mooncakes.io：`moon add xiguaAp6y3/moondelta-core@0.1.1`（空工程安装并编译验证通过），页面 https://mooncakes.io/docs/xiguaAp6y3/moondelta-core ；`
- 改句：`- mooncakes.io 发布状态待复核：`moon add xiguaAp6y3/moondelta-core@0.1.1` 的空工程安装与编译未在本机复核，发布前需提供安装日志与页面链接；页面 https://mooncakes.io/docs/xiguaAp6y3/moondelta-core ；`
- 理由：**D4-I1**：D1 §5 #15 与 D5 §6.5 明确未做安装验证（需联网空工程安装）；原句“空工程安装并编译验证通过”属未验证断言，改为待复核并给出所需证据类型。
- 映射：§7 第 133 行；D1 §5 #15、§7；D5 §6.5 D4-I1。

### H3 — `README.md` `@@ -8,28 +8,31 @@`（顶部发布声明、L15、L17、L21、L23、L25、L32）

**R0｜L11 顶部“已发布”（D4-I1 同源断言）**

- 原句：`> 已发布：[mooncakes.io/docs/xiguaAp6y3/moondelta-core](https://mooncakes.io/docs/xiguaAp6y3/moondelta-core)（`moon add xiguaAp6y3/moondelta-core`）`
- 改句：`> 发布记录待复核：[…](…)（`moon add xiguaAp6y3/moondelta-core`；本机未做安装验证，发布前需复核并留存安装日志）`
- 理由：与 B13 同源；README 首页声明与申报书口径必须一致，避免 README 仍写“已发布”而申报书写“待复核”。
- 映射：D1 §5 #15、D5 D4-I1。

**R1｜L15（版本 0.1.0 vs 0.1.1）**

- 原句：`MVP（0.1.0）：`
- 改句：`MVP（README/CLI 仍标 0.1.0，`moon.mod` 已为 0.1.1，版本文案待统一，见 `artifacts/protocol-audit.md` §5 第 9 条）：`
- 理由：把版本不一致作为待修说明保留（D1 §5 #9）。
- 映射：审计 §5 #9；`moon.mod:3`、`out/01-version.out`。

**R2｜L17 后新增协议 bullet**

- 新增：`-  协议范围：读取 Delta protocol reader v1 的表、写出 `minReaderVersion = 1, minWriterVersion = 2` 的表；日志从非 0 起点/缺版本明确拒绝；checkpoint-only 返回结构化 NotFound；日志被截断但存在 checkpoint 的表当前未检测（会读旧快照，已知缺口）；reader v2/v3 与未知必需 feature 当前不校验`
- 理由：把 §1/§2 口径写进正文，区分“拒绝”与“未检测”；v2 曾写“日志被截断的表明确拒绝”，与 F2 不符，已改。
- 映射：§1、§2 日志/protocol/checkpoint 行；审计 §3.1 P2/P3、§3.3 C4/C5。

**R3｜L21（写入口径 + append 缺口）**

- 原句：`-  写入：`create_table` + `append_rows`（Int32/Int64/String/Binary/Boolean/Double，Required/Optional）；分区列读取时按 `partitionValues` 补齐`
- 改句：`-  写入：`create_table` + `append_rows`（Int32/Int64/String/Binary/Boolean/Double）；append 只校验列数/行长，不校验列名/类型/NOT NULL，写出列恒为 Parquet Optional（已知缺口，见 `artifacts/protocol-audit.md` §5）；分区列读取时按 `partitionValues` 补齐`
- 理由：审计 §5 #6 + F5/T28–T30。
- 映射：§2 schema/表操作行；审计 §3.6 T28–T30、§4.5、§5 #6；D2 R-1。

**R4｜L23（测试描述修正）**

- 原句：`-  14 个单元测试（含分区读/过滤、统计剪枝边界、缺失数据文件、嵌套列拒绝等负向用例）+ 真实 Delta 表 fixture（delta-rs、apache/parquet-testing）`
- 改句：`-  14 个单元测试（…；其中名为 checkpoint-only 的用例实际构造的是非 0 起点日志，真实 checkpoint-only / 删除向量 / reader≥2 拒绝无测试）+ 真实 Delta 表 fixture（delta-rs、apache/parquet-testing）`
- 理由：审计 §5 #4。
- 映射：§7 第 127 行；审计 §5 #4。

**R5｜L25 改写 + 新增互操作/审计状态段**

- 原句：`已用真实数据验证：delta-rs `table_a`（13 个版本回放 → 120 个文件）、Databricks 写出的 COVID-19_NYT 表（157,865 行 6 列，snappy）。`
- 改句：`已用真实数据验证（均为仓库 fixture 读取，D1 已复核）：…（仓库只含日志、不含数据文件）、…表的 1 个数据文件（157,865 行 6 列，snappy；该表共 8 个数据文件，其余 7 个未入库）。`，并新增：`外部引擎互操作（D2）：已与 delta-rs 1.6.6、DuckDB 1.5.6 完成 flat 表双向对拍（49 PASS / 0 FAIL / 2 项已记录差异，见 [artifacts/interop-flat/README.md](artifacts/interop-flat/README.md)）；Spark / Databricks 未验证。nested schema 当前明确拒绝，七项门槛 1/7（仅证据门槛 6，[artifacts/interop-nested/README.md](artifacts/interop-nested/README.md)），不构成 nested 互操作。闭集外的静默/未检测项与测试缺口见 [artifacts/protocol-audit.md](artifacts/protocol-audit.md) §5；口径见 [docs/协议兼容性与互操作范围.md](docs/协议兼容性与互操作范围.md)。`
- 理由：修正“表（157,865 行）”为单文件；新增 D2/D3/审计证据路径；nested 改为 1/7。
- 映射：§5、§7 第 128/133 行；D2 §0；D3 §7；审计 §5 #7。

**R5b｜L32 快速开始安装注释（D4-I1 同源）**

- 原句：`# 从 mooncakes.io 安装（v0.1.0 已发布）`
- 改句：`# 从 mooncakes.io 安装（发布记录待复核，见上方说明；README/CLI 版本文案待统一为 0.1.1）`
- 理由：与 R0/B13 一致，避免 README 内自相矛盾（“已发布” vs “待复核”）。
- 映射：D1 §5 #9、§5 #15；D5 D4-I1。

### H4 — `README.md` `@@ -57,6 +60,8 @@`（inspect 样例注）

- 新增：`> 注：样例中的 `reader=1 writer=1` 是 delta-rs fixture 自身的协议字段；本项目写出的表为 `minReaderVersion = 1, minWriterVersion = 2`。`
- 理由：样例输出确为 `writer=1`（fixture 自身），易被误读为本项目 writer；不改样例输出以保持可复现。
- 映射：§1 第 9–10 行。

### H5 — `README.md` `@@ -87,19 +92,22 @@`（能力边界整段）

- 标题改为“对已识别边界返回结构化错误；闭集外尚未全部拒绝”；表格新增 protocol/日志/闭集外 feature 行，并修正“表 / 写入类型 / 存储”行；结尾段落删除“已在测试里固化/不是静默 0 行”，改为如实列出静默/未检测项（DV 读全量、截断 checkpoint 读旧快照、reader v2/v3 接受、stats object `rows: 0`、append 校验缺口、CLI exit 0）。
- 理由：审计 §5 #7/#8 + F1/F2/F3/F7/E4/E6；D2 R-1/R-2；D3。
- 映射：§2 第 29 行、§3；审计 §3.1 P2/P3、§3.3 C4/C5、§3.6 T28–T30/T37、§3.7 E4/E6。

### H6 — `README.md` `@@ -117,7 +125,7 @@`（路线图行）

- 原句：`- 0.2：ZSTD/GZIP/BROTLI 解压、checkpoint parquet 解析（嵌套）、分区表 append`
- 改句：`- 0.2：ZSTD/GZIP/BROTLI 解压、checkpoint parquet 解析（仅 checkpoint 所需的最小嵌套 shape，不构成 nested schema 兼容）、分区表 append`
- 理由：§3 第 45 行。
- 映射：§3 第 35–45 行。

### H7 — `README.md` `@@ -129,7 +137,7 @@`（开发章节 CI 描述；D4-I1 同源）

- 原句：`CI：`.github/workflows/ci.yml`（安装最新 MoonBit 工具链 → `moon update` → check/test/build）。`
- 改句：`CI：`.github/workflows/ci.yml`（…）；远端运行结果未在本机复核（D1/D5），发布前需提供 CI 运行链接。`
- 理由：README 只描述工作流，但结合徽章易被读成“CI 已绿”；补“未复核”提示，与申报书 B11 一致。
- 映射：D1 §5 #15、D5 D4-I1。

### H8 — `docs/ROADMAP.md` `@@ -12,8 +12,8 @@`（L15、L16）

- **L15**：原句 `| CLI、示例、CI、mooncakes.io 发布 | ✅ 0.1.0 |` → `| CLI、示例 | ✅ 0.1.0；CI 与 mooncakes.io 发布记录待复核（本机未跑 CI、未做安装验证） |`。理由：D4-I1 同源，CI/mooncakes 未验证。
- **L16**：原句 `| 负向边界：嵌套 schema、checkpoint-only、缺失文件、未知类型 | ✅ 明确报错 |` → `| 负向边界：嵌套 schema、非 0 起点/缺版本日志、缺失文件、未知类型 | ✅ 行为：结构化错误；真实 checkpoint-only、删除向量、reader≥2 拒绝等测试缺口见 `artifacts/protocol-audit.md` §6 |`。理由：审计 §5 #10（checkpoint-only 测试实际是非 0 起点、未知类型无具名测试），拆开“行为已实现”和“测试缺口”。
- 映射：§2、§3；审计 §3.3 C4、§3.4 S21、§5 #10、§5 #15。

### H9 — `docs/ROADMAP.md` `@@ -48,7 +48,7 @@`（明确不做）

- 原句：`- 不做通用嵌套类型（struct/list/map）的完整读写（只做 checkpoint 所需的最小 shape）；`
- 改句：补 `该中间实现不构成 nested schema 兼容，完整 nested 需满足 `docs/协议兼容性与互操作范围.md` 第 3 节七项门槛`。
- 理由：§3 第 45 行；D3 结论 1/7 为其证据背景。
- 映射：§3 七项门槛。

## 3. 声明 → 证据映射矩阵（v3）

| 修订后声明 | 证据 |
|---|---|
| reader v1 / writer v2；version 0 起连续日志 | `src/write.mbt` L174；`replay_until` L111–115；测试 `snapshot: replay 13 versions…`；审计 §3.1 P1/P11、§3.2 L12/L13 |
| 高于 reader v1 / feature 当前不校验、不拒绝 | 审计 §3.1 P2/P3/P4/P6（out/20–23、28–29）、§4.3、§5 #2；D2 R-3 |
| checkpoint-only 返回结构化 NotFound；截断日志+checkpoint 未检测 | 审计 §3.3 C4（out/50）、C5（out/51/52）、§4.2、§5 #3；`replay_until` 只查 `versions[0]==0` |
| flat + UNCOMPRESSED/SNAPPY + 本地 FS | 测试 `read: real Databricks-written covid…`；审计 §3.5 Q1–Q6、§3.6 T40 |
| nested 明确拒绝、门槛 1/7 | 测试 `read: nested parquet column is rejected…`；D3 §5–§7、comparison.md；门槛 6 仅证据门槛 |
| append 只校验列数/行长，不校验列名/类型/NOT NULL，列恒 Optional | 审计 §3.6 T27–T30、§4.5、§5 #6；`src/write.mbt` L202–237；`out/probe-cli-append-mismatch`、`out/probe-cli-notnull`、`out/probe-written-parquet` |
| stats 只含 numRecords + 数值列 min/max | 审计 §3.6 T31；D2 R-2；`src/write.mbt` L107–136 |
| 删除向量静默读全量 | 审计 §3.6 T37（out/35/36）、§4.1、§5 #2；`deletionVector` 在 `src/` 0 命中 |
| CLI 错误退出码恒 0 | 审计 §3.7 E4、§4.4；**121 份带退出码的 CLI/校验输出（112 编号 case + 9 探针）全部 exit 0**；`commands.txt` 共 131 条记录（含重复 probe 块与首轮 `probe-library` exit 1，重跑 exit 0，计数以 out/ 带退出码输出为准）；`src/cmd/delta/main.mbt` L44–46 |
| warning 构成 | 43 条带文件定位 = 39 条项目 `src/`（38 derive + 1 示例 env）+ 4 条 vendored；duplicate alias 是第 44 条无文件定位的工程配置 warning；审计 §5 #5、D5 D4-I2 |
| CI / `moon package` / mooncakes 安装 | **D1 §5 #15 与 D5 §6.5 均未验证**；patch 已改为“未在本机复核、发布前需提供输出/链接/日志”，不再写“已通过/已发布并验证” |
| 代码行数约 2,575（全行统计） | 审计 §5 #13；`out/code-evidence.out`；本次复核 HEAD 全行 1,508+383+570+45=2,506，加 D2 示例 69=2,575；口径：按文件全行统计、含空行/注释 |
| 14 个测试通过 | `moon test --target native` → 14/14；审计 §2.2 |
| flat 双向对拍 49 PASS / 0 FAIL / 2 DIFF | D2 §0/§10、results.json verdict `{pass:49, fail:0, diff:2, info:5, blocked:0}` |
| Spark / Databricks 未验证 | D2 §12 |
| 测试缺口（真实 checkpoint-only、DV、reader≥2） | 审计 §5 #4、§6.1 P0；D3 §8 |

## 4. 保留不改的可疑表述及理由

| 位置 | 表述 | 不修改的理由 |
|---|---|---|
| README L3 CI 徽章 | 徽章指向 GitHub Actions（图片内容随远端状态变化） | 徽章本身是动态引用而非文字断言；配套的“CI 已通过”文字断言已在 B11/R0/R5b/H7/H8 改为待复核。保留徽章不影响如实性，发布前以仓库实际 CI 状态为准 |
| README L96 / 申报书 L24 `date、timestamp(INT96)` 读取 | 审计 S8/S9：行为已实测（输出原始天数/微秒），但**无具名测试** | 闭集允许（§2 schema 行含 date/timestamp）；保留“支持”，审计 §6.1 P2 列为补测项 |
| 申报书 L47 “create + append + 重新读回通过” | 审计 §5 #14 复核属实（2 文件、5 行） | 保留（B10 仅补限定语与证据） |
| 申报书 L36 `14 个单元测试（含负向）` | 14/14 属实 | 保留；具体缺口已在 L46/R4 说明 |
| README L5 `Delta Lake 表格式内核` 泛称 | 易被读成完整格式实现 | 已由能力边界表与协议 bullet 限定；标题类泛称保留 |
| CHANGELOG 0.1.0 `分区 + min/max 统计剪枝` | 与 0.1.1 “分区表读取与分区过滤”看似重复 | 经 git 历史核对在 0.1.0 前已合入（`e3232f8`），0.1.1 补分区列补齐与测试；历史属实（见 §5） |
| `docs/开发计划.md` L6 `reader/writer 未改` | 与 NOTICE/CHANGELOG “writer 扩展”矛盾 | 不在 D4 指定文件范围；建议 lead 文档收口时单独修（§6） |
| `fixtures/README.md` §1 `covid/log0.json` 路径、§3.3 list_columns 说明 | 审计 §5 #11/#12 指出与实际路径/测试行为不符 | 不在 D4 指定文件范围；建议单独修正（§6） |
| ROADMAP 0.2 `能读 delta-rs 测试集中带 _last_checkpoint 且旧日志已清理的表` | 未来计划，不是现状 | 已在“明确不做”处加门槛说明（H9）；计划本身保留 |

## 5. 为什么 CHANGELOG.md 没有 hunk

- 0.1.1 的“BOOLEAN/DOUBLE 写入、partitionValues 补齐、10→14 测试”与代码、`moon test` 14/14 一致；
- 0.1.0 的“分区 + min/max 统计剪枝”经 git 历史确认在 0.1.0 前落地（`e3232f8`）；
- CHANGELOG 无“任意标准 Delta 表”“完整 nested”“checkpoint 恢复”等超出 §2 闭集的现状声明；D1 §5 的 15 条不一致没有指向 CHANGELOG。

因此按最小 diff 原则不改 CHANGELOG；若后续修复 DV/checkpoint/protocol 校验等，应在对应版本 Added/Changed 中记录（属实现任务）。

## 6. 需用户/lead 决策与待追加 hunk（不阻塞本版交付）

1. **发布证据（D4-I1 的彻底闭合）**：本版已把 `moon package` / GitHub Actions / mooncakes 安装改为“未在本机复核、发布前需提供证据”。**申报/发布前仍需补齐**：`moon package` 输出、CI 运行链接、空工程 `moon add` 安装日志；若无法提供，建议删除相应句子而不是保留。**这一项需要用户/lead 决策并补证据**。
2. **代码修复后升级措辞**：审计 §6.3 的 8 项修复（protocol/feature 校验、checkpoint 一致性、DV/column mapping/CDF 检测、schema↔parquet 类型断言、append name/type/nullable 校验、create 校验、CLI exit code、stats object）完成后，把“未检测/已知缺口/恒 0”升级为“结构化拒绝”，并补测试；修复前不得改回“明确拒绝”。
3. **Spark / Databricks 对拍**：若后续补齐，把 B1/B4/B10/R5 中“未验证”改为已完成并给证据路径；只交付 delta-rs + DuckDB 时保持现状。
4. **nested 门槛变化**：D3 当前 1/7（仅门槛 6）。若完成门槛，按 §3 逐项更新；“仅证据门槛 6”完成**永远不能**单独推出 nested 兼容。
5. **版本统一**：确认 0.1.1 发布后，把 README L15/L32 与 CLI `version` 输出统一为 0.1.1（CLI 属代码改动，不在 D4 范围）；本 patch 已把 README 两处标为“待复核/待统一”。
6. **证据入库**：`docs/协议兼容性与互操作范围.md`、`artifacts/protocol-audit.md`、`artifacts/acceptance-report.md`、`artifacts/interop-flat/`、`artifacts/interop-nested/` 均未跟踪；须与 patch 同 commit 入库，否则引用失效。
7. **D2 示例入库与否**：申报书行数 2,575 含 `src/examples/interop_flat_writer/` 69 行；若该示例不入库，改为 2,506。**需 lead 确认**。
8. **`fixtures/README.md` 两处小错**（审计 §5 #11/#12）与 `docs/开发计划.md` L6：不在 D4 文件范围，建议另开文档修正任务；若允许扩范围可追加两个最小 hunk（当前未加，避免越界）。
9. **D1/D5 未覆盖项**：`moon package`、CI、mooncakes、fuzz、多段 checkpoint、`SetTransaction` 语义等（审计 §7、D5 §11）；不影响当前闭集结论，但发布前需按决策 1 处理。

## 7. 校验命令与结果

在仓库根 `E:\Project\Moonbit Hackson\P1 RE` 执行：

```powershell
# 1) 补丁可应用性（不落盘）
git apply -p1 --check artifacts/claim-alignment.patch
# 结果：exit 0，无输出

# 2) 改动规模（9 hunk）
git apply -p1 --numstat artifacts/claim-alignment.patch
# 15  13  "申报书.md"
# 24  16  README.md
# 3   3   docs/ROADMAP.md
(Select-String -Path artifacts/claim-alignment.patch -Pattern '^@@').Count   # 9

# 3) 临时副本真实 apply + Markdown 结构检查
$tmp = Join-Path $env:TEMP "claim-v3-check"
# 复制 申报书.md / README.md / CHANGELOG.md / docs/ROADMAP.md 到 $tmp 后：
git apply -p1 artifacts/claim-alignment.patch   # exit 0
(Select-String -Path README.md -Pattern '^```').Count      # 10（围栏成对）
(Select-String -Path README.md -Pattern '^\|').Count       # 12（表格连续）
(Get-Content 申报书.md).Count                              # 52（原 50 行 + 2 条新增 bullet）
(Get-Content docs/ROADMAP.md).Count                        # 68（仅改 3 行，行数不变）

# 4) 工作区原文件未被修改
git status --porcelain -- 申报书.md README.md CHANGELOG.md docs/ROADMAP.md
# 结果：无输出

# 5) 交付物哈希
Get-FileHash artifacts/claim-alignment.patch,artifacts/claim-alignment.md -Algorithm SHA256
```

补充说明：

- patch 由 `difflib.unified_diff` 生成，逐行精确替换，未重排段落、未改动全角标点、未触碰既有行内代码与链接；
- 临时副本位于 `%TEMP%`，验证后清理，不在仓库内留下中间文件；
- 只覆盖写入 `artifacts/claim-alignment.patch` 与 `artifacts/claim-alignment.md`，未直接修改 docs/README/申报书/ROADMAP、未 `git add/commit`、未触碰 `.tools/` 或其他 agent 的 `artifacts/` 子目录。

## 8. v2 → v3 变更清单

| # | 位置 | v2 | v3 | 依据 |
|---|---|---|---|---|
| 1 | 申报书 L48 | `moon package 通过`；`GitHub Actions 已通过（全绿）`；warning 句含 duplicate alias | 两项改为“未在本机复核、发布前需提供 `moon package` 输出与 CI 运行链接”；warning 精确为“43 条带文件定位 = 39 条项目 src/（38 derive + 1 示例 env）+ 4 条 vendored；duplicate alias 是第 44 条无文件定位，不在这 43 条内” | D5 D4-I1（中）、D4-I2（低）；D1 §5 #15、§5 #5 |
| 2 | 申报书 L49 | `已发布 mooncakes.io…（空工程安装并编译验证通过）` | `mooncakes.io 发布状态待复核…未在本机复核，发布前需提供安装日志与页面链接` | D5 D4-I1；D1 §5 #15 |
| 3 | README L11 | `已发布：[mooncakes.io…]` | `发布记录待复核：[…]（本机未做安装验证，发布前需复核并留存安装日志）` | D5 D4-I1（同源） |
| 4 | README L32 | `# 从 mooncakes.io 安装（v0.1.0 已发布）` | `# 从 mooncakes.io 安装（发布记录待复核，见上方说明；README/CLI 版本文案待统一为 0.1.1）` | D5 D4-I1、D1 §5 #9 |
| 5 | README L132 | CI 工作流描述无复核说明 | 补 `远端运行结果未在本机复核（D1/D5），发布前需提供 CI 运行链接` | D5 D4-I1（同源） |
| 6 | ROADMAP L15 | `CLI、示例、CI、mooncakes.io 发布 \| ✅ 0.1.0` | `CLI、示例 \| ✅ 0.1.0；CI 与 mooncakes.io 发布记录待复核（本机未跑 CI、未做安装验证）` | D5 D4-I1（同源） |
| 7 | 申报书 L41/L46 | `按文件全行统计` | `按文件全行统计、含空行/注释`（并保留“含 D2 示例 69 行”） | D5 §6.5 建议、审计 §5 #13 |
| 8 | 本 md §1.1/§3 | `121 次运行全部 exit 0` | `121 份带退出码的 CLI/校验输出（112 编号 case + 9 探针）全部 exit 0`；注明 `commands.txt` 131 条记录（含重复 probe 块与首轮 `probe-library` exit 1，重跑 exit 0） | D5 D4-I3；D1-I2 |
| 9 | 整包 | 8 hunk | **9 hunk**：新增 README 开发章节 CI 说明 hunk；README 顶部 hunk 并入首页发布声明与安装注释；ROADMAP 首个 hunk 并入 0.1.x 表 CI/mooncakes 行 | 上述 1–6 |

**最终哈希（v3）**：patch = `DBDE5AB7E0578C43828ACD5C601A29EB360A20795DB0C9101F14AE108FDEA3EA`（22,063 B、172 行、9 hunk）；`claim-alignment.md` 的 SHA-256 在本文写定后由交付消息给出（本文件无法自引用自身哈希）。

---

**版本**：v3（D4c，覆盖 v1/v2）　**生成时间**：2026-10-20 20:40 +08:00　**依据**：`artifacts/protocol-audit.md`、`artifacts/acceptance-report.md`（D5）、`artifacts/interop-flat/README.md`、`artifacts/interop-nested/README.md`、`docs/协议兼容性与互操作范围.md`。
