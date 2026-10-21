# 完全兼容标准验收（严格口径）—— 2026-10-22

> 验收人：Team Lead　｜　验收时点：2026-10-22
> **标准**：[docs/ROADMAP.md](../docs/ROADMAP.md) 第 1/2 节定义的"完全兼容"——Delta Transaction Log Protocol `minReaderVersion=1, minWriterVersion=2` 完整实现，含嵌套 schema 读写与 checkpoint 恢复，双外部引擎（delta-rs 1.6.6 + DuckDB 1.5.6）同协议双向对拍。
> **背景**：2026-10-22 审核意见指出申报承诺与范围不一致。本报告按升级后的标准对当前工作区（HEAD `3376045`，源码未变更，见 [acceptance-2026-10-22.md](acceptance-2026-10-22.md) §0）严格取证，作为路线图 M1–M5 的差距基线。只取证，不修复。
> 注意：此前验收（[acceptance-2026-10-21.md](acceptance-2026-10-21.md)）中案例 B 的"负向通过"（本项目结构化拒绝嵌套表）在新标准下**反转为不通过**——同一 fixture 必须正向读出并与外部引擎逐字段一致。

## 1. 结论总览

| 验收对象 | 结论 |
|---|---|
| **完全兼容标准（整体）** | **不通过（15 项 FAIL / 9 项 PASS / 2 项部分）** |
| 案例 A（flat，同协议双引擎双向） | **通过** |
| 案例 B（nested，同协议双引擎双向） | **不通过**（外部双 oracle 通过；本项目读取、写入双侧均拒绝） |
| 差距与排期映射 | FAIL 项全部映射到 ROADMAP M1–M4（§4） |

## 2. 必需行为闭集逐项判定

判定符号：✅ PASS（实现且有测试/对拍证据）；❌ FAIL；🔸 显式例外（当前拒绝但按标准允许，前提是申报中逐条列出）；当前行为证据格式：`代码位置` + 运行时复验。

### A. 协议协商

| # | 要求 | 判定 | 证据 |
|---|---|---|---|
| A1 | reader>1 拒绝；reader/writerFeatures 解析，未知必需 feature 拒绝 | ❌ | `parse_protocol` 只取两个整数字段、不解析 features（actions.mbt:15-25）、回放无任何版本校验（snapshot.mbt:129）；复验：`protocol-reader-v3-features`（reader=3/writer=7 + 未知 feature）被接受并读 3 行，exit 0 |
| A2 | writer 版本门控特性物化时结构化拒绝 | ❌ | 删除向量 fixture（writer=7 特性）被静默忽略：`deletion-vector-add` 读出 3 行（含已删除行），无错误，exit 0 |

### B. 日志与状态

| # | 要求 | 判定 | 证据 |
|---|---|---|---|
| B1 | v0 起连续回放、时间旅行、history | ✅ | `table_a` 13 版本回放 120 文件、`snapshot_at(0)`、history 13 条（delta_wbtest.mbt:138-171）；D2 引擎对拍佐证 |
| B2 | checkpoint 恢复（checkpoint Parquet → 快照；截断/缺口经 checkpoint 恢复） | ❌ | 无 checkpoint Parquet 解析代码；`checkpoint-truncated` 静默按 v9 读取（不查 `_last_checkpoint` v10）——**静默读旧版本，属最不可接受类别** |
| B3 | 日志文件名 20 位校验、版本完整性 | ❌ | `version_from_name` 对任意数字前缀返回值（log.mbt:22-33）；复验：`nonstandard-log-name`（`0.json`）伪造 version 0 绕过 checkpoint-only 防护后报误导错误；`log-gap` 结构化拒绝（但 exit 0） |

### C. Actions

| # | 要求 | 判定 | 证据 |
|---|---|---|---|
| C1 | protocol/metaData/add/remove/commitInfo 完整语义；txn 识别 | 🔸→❌ | 五类 action 语义实现且有测试；`txn`/`cdc`/`domainMetadata` 走 `UnknownAction` 被静默忽略（actions.mbt:148、snapshot.mbt:148）——按 C2 标准判 FAIL |
| C2 | 未知 action 不静默忽略，结构化错误 | ❌ | 复验：`unknown-actions` fixture 静默读 3 行，exit 0 |

### D. Schema（基础格式）

| # | 要求 | 判定 | 证据 |
|---|---|---|---|
| D1 | 全部 primitive 读取（boolean/byte/short/int/long/float/double/string/binary/date/timestamp） | ❌ | `byte`/`short` 未映射 → `UnknownType` 拒绝（schema.mbt:39-61）；其余 primitive 有测试与真实表读取（COVID 157,865 行） |
| D2 | 嵌套读取：struct/3-level list/map + null/empty/重复/组合 | ❌ | `ensure_readable_schema` 对 `NestedType` 直接拒绝（schema.mbt:116-119）；D3：外部双 oracle 读回 5 行逐字段一致，本项目打开 Parquet 前结构化拒绝——外部引擎可读、本项目不可读 = 能力缺口 |
| D3 | 嵌套写入（外部引擎可读回） | ❌ | vendored writer 仅 flat 列（write.mbt:45-58 明确拒绝非 flat）；vendored reader 无 def/rep level 数据路径（`decode_levels_v1` 为未使用死代码，reader.mbt:345） |
| D4 | 例外类型拒绝带类型名与字段路径 | ✅ | decimal/uuid 拒绝错误含两者（schema.mbt:120-127）；列为显式例外，申报需同步 |

### E. Parquet 数据层

| # | 要求 | 判定 | 证据 |
|---|---|---|---|
| E1 | def/rep level 解码；SNAPPY/UNCOMPRESSED；其余编码拒绝 | ❌/🔸 | def/rep 数据路径缺失（同 D3）；SNAPPY/UNCOMPRESSED 通过（D2 双引擎文件）；ZSTD 等 4 种 codec fixture 结构化拒绝（D1 审计矩阵），列显式例外 |
| E2 | `add.path` percent-decoding；缺文件错误含文件名 | ❌ | 全仓无解码代码；复验：`path="my%20file.parquet"`（磁盘为 `my file.parquet`）→ `io error: No such file or directory`，无文件名（log.mbt:37-49 fs 错误无上下文） |

### F. 统计与剪枝

| # | 要求 | 判定 | 证据 |
|---|---|---|---|
| F1 | flat 剪枝；nested stats 不误用、无法剪枝则放弃 | ❌（随 D2） | flat 剪枝通过（`table_a` 120→60 文件、min==max 边界）；nested stats 处理随 M2/M4 |
| F2 | stats 解析失败不静默返 0 | ❌ | `stats_num_records` catch 后静默返 0（snapshot.mbt:62、69）；行数统计可静默失真 |

### G. 写路径

| # | 要求 | 判定 | 证据 |
|---|---|---|---|
| G1 | create 防重；append 校验列名/类型；commit 原子（put-if-absent） | ❌ | 三项均缺：复验双次 create 覆盖 version 0（schema long→string 被替换，exit 0）；append 只查列数/行数（write.mbt:203-217）不查名字与类型；commit 直接 `write_text` 覆盖目标版本（write.mbt:269） |
| G2 | 写出 protocol 恒为 reader=1/writer=2 | ✅ | write.mbt:174 固定写出；D2 外部引擎可读本项目写出的表 |

### H. 对拍与工程行为

| # | 要求 | 判定 | 证据 |
|---|---|---|---|
| H1 | 案例 A（flat）双引擎双向一致 | ✅ | D2：delta-rs 1.6.6 + DuckDB 1.5.6，56 项检查 49 PASS/0 FAIL/2 DIFF（差异已如实记录），哈希 79 项可重算；2026-10-22 复验 `d2_verify.py` 17/17 |
| H2 | 案例 B（nested）双引擎双向一致 | ❌ | D3：外部双 oracle 各 5 行逐字段一致；本项目读/写双侧拒绝；2026-10-22 复验 `d3_verify.py` 38/38（复验的是"缺口对照如实"而非互操作通过） |
| H3 | CLI 退出码（成功 0/错误非 0）、stderr、参数校验 | ❌ | `main` 无 exit 调用、`fail()` 用 println 到 stdout（main.mbt:44-46）；复验所有错误路径 exit=0；未知 flag 静默跳过（main.mbt:207-209、262-264） |

### 汇总

- **PASS（9）**：B1、D4、E1(codec 部分)、F1(flat 部分)、G2、H1 + D2 外部 oracle 侧、快照/时间旅行/剪枝等既有能力（详见上表）。
- **FAIL（15）**：A1、A2、B2、B3、C1/C2、D1(byte/short)、D2、D3、E1(levels)、E2、F2、G1、H2、H3。
- **显式例外（须申报同步）**：decimal、uuid、ZSTD/GZIP/BROTLI/LZ4、对象存储、reader≥2 全部特性、分区 append、MERGE/UPDATE/DELETE、并发仲裁。

## 3. 对照案例判定（同一协议 reader v1 / writer v2）

### 案例 A：flat 表（`id long NOT NULL, name string, active boolean`，含 null，2 SNAPPY 文件）

| 步骤 | 引擎 | 结果 |
|---|---|---|
| 外部写出 → 本项目读 | delta-rs 1.6.6 写出，moondelta 读 | ✅ 逐列一致（含 null） |
| 外部写出 → 本项目读 | DuckDB 1.5.6 `delta_scan` 写出，moondelta 读 | ✅ 逐列一致 |
| 本项目写 → 外部读 | moondelta `create+append` → delta-rs / DuckDB 读 | ✅ schema/行数/值/统计一致 |
| 统计对拍 | `add.stats`、文件哈希 | ✅ 79 项哈希可重算；`nullCount` 缺失等差异已如实记录 |

**判定：通过。**（证据：`artifacts/interop-flat/`，2026-10-22 复验通过）

### 案例 B：nested 表（`profile: struct<name,age>, tags: array<string>, attrs: map<string,string>`，5 行含 NULL/empty 边界）

| 步骤 | 引擎 | 结果 |
|---|---|---|
| 外部写出 → 外部 oracle 读 | delta-rs、DuckDB | ✅ 双 oracle 各 5 行逐字段一致（含全部边界值） |
| 外部写出 → 本项目读 | moondelta | ❌ 打开 Parquet 前 `unsupported: nested column 'profile' (type=struct)` 结构化拒绝，exit 0 |
| 本项目写 → 外部读 | moondelta | ❌ 嵌套写入未实现 |

**判定：不通过。** 旧口径下"结构化拒绝"满足 §4 负向要求；完全兼容标准下该 fixture 必须正向读出——10-31 前由 M2（读取）/M4（写入）关闭。（证据：`artifacts/interop-nested/`，38 项复验可重放）

## 4. 差距 → 里程碑映射

| FAIL 项 | 里程碑（ROADMAP §4） | 退出标准 |
|---|---|---|
| A1/A2/C1/C2/B3/H3/D1(byte/short)/E2/F2/G1 | M1（→10-23） | 负向 fixture 测试；静默行为全部变为非 0 退出 + 明确原因 |
| D2/E1(levels)/H2 读取侧 | M2（→10-25） | 案例 B 本项目读回 5 行与双 oracle 一致 |
| B2 | M3（→10-27） | `checkpoint-truncated` 经 checkpoint 重建快照，与 JSON 回放一致 |
| D3/F1(nested)/H2 写入侧 | M4（→10-29） | 本项目 nested 写出被双引擎读回一致 |
| 全部 | M5（→10-31） | 本报告全矩阵复核后出具正式结论 |

## 5. 复验命令

```powershell
moon check --target native; moon test --target native
.tools\pyenv\Scripts\python.exe artifacts\acceptance\d2_verify.py     # 17/17（2026-10-22）
.tools\pyenv\Scripts\python.exe artifacts\acceptance\d3_verify.py     # 38/38（2026-10-22）
_build\native\debug\build\cmd\delta\delta.exe inspect artifacts\protocol-audit\fixtures\protocol-reader-v3-features   # A1 复现：exit 0
_build\native\debug\build\cmd\delta\delta.exe read    artifacts\protocol-audit\fixtures\deletion-vector-add          # A2 复现：读 3 行
_build\native\debug\build\cmd\delta\delta.exe inspect artifacts\protocol-audit\fixtures\checkpoint-truncated          # B2 复现：静默 v9
_build\native\debug\build\cmd\delta\delta.exe inspect artifacts\protocol-audit\fixtures\nonstandard-log-name          # B3 复现：伪造 v0
_build\native\debug\build\cmd\delta\delta.exe inspect artifacts\protocol-audit\fixtures\unknown-actions               # C2 复现：exit 0
```

## 6. 审核答复要点（可直接用于报名表格修订）

1. **目标协议**：Delta Transaction Log Protocol `minReaderVersion=1, minWriterVersion=2` 完整实现；对 reader≥2（column mapping、deletion vectors、v2 checkpoint）按协议拒绝并说明原因——拒绝行为本身纳入验收。
2. **完全兼容承诺范围**：ROADMAP §2 必需行为闭集（协议协商、日志与 checkpoint 恢复、actions、全 primitive + 嵌套 schema 读写、Parquet 数据层、统计、写路径防护、CLI 工程行为）+ 显式例外清单。
3. **对照案例**：同一协议下案例 A（flat，已通过）与案例 B（nested，本冲刺关闭），外部引擎 delta-rs 1.6.6 + DuckDB 1.5.6 双向对拍，可重放证据在 `artifacts/interop-flat/` 与 `artifacts/interop-nested/`。
4. **兑现时间**：2026-10-31 前完成 M1–M5（ROADMAP §4），届时以本报告全矩阵复核结论支撑重新提交；若 nested 写入延期，按 ROADMAP §5 降级表述，不虚报。
