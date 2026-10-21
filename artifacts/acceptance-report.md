# D5 独立证据验收报告（moondelta-core 0.1.1 / Delta flat 互操作与协议边界）

> 验收人：`evidence-verifier`（独立于 D1–D4 作者）
> 验收日期：2026-10-20（Asia/Hong_Kong）
> 仓库：`E:\Project\Moonbit Hackson\P1 RE`，当前 HEAD `3376045 docs: add roadmap/changelog and release 0.1.1`
> 验收依据：[docs/协议兼容性与互操作范围.md §6（D1–D4 验收）与 §7（证据/发布门槛）](../docs/协议兼容性与互操作范围.md)
> 本报告只做复核与取证，**未修复任何交付物**；唯一新增写入为 `artifacts/acceptance-report.md` 与 `artifacts/acceptance/`。

---

## 0. 结论总览

| 交付物 | 结论 | 核心理由（详见对应章节） |
|---|---|---|
| D1 `artifacts/protocol-audit.md` | **通过**（3 项低风险文档性瑕疵，不影响结论） | 122 项矩阵统计逐项复算一致（27/20/4/24/47）；抽查 17 条结论（含全部“严重”项）的 CLI/代码/原始输出均与报告一致；闭集外输入有 62 个 fixture 的实测输出；未发现“支持所有 Delta”类无证据结论 |
| D2 `artifacts/interop-flat/` | **通过** | 独立重放 `run_interop.py`：exit 0、`PASS=49 FAIL=0 DIFF=2 INFO=5 BLOCKED=0`；用 delta-rs 1.6.6 + DuckDB 1.5.6 独立双向读两表，5 行逐值一致；`hashes.txt` 79 项全部可重算 |
| D3 `artifacts/interop-nested/` | **通过** | 8 个 fixture 实测存在；struct / 3-level LIST / MAP 物理结构成立（`tags.list.item`、`attrs.entries.key/value`）；独立 delta-rs + DuckDB 重读 5 行与 `oracle-delta-rs.json` 逐字段一致；moondelta 错误文本/退出码与 README 完全一致；七门槛统计 1/7（仅证据门槛 6），负向用例未被写成互操作通过 |
| D4 `artifacts/claim-alignment.patch` + `.md` | **通过（v3 已修正 D5 遗留项）** | 终版 v3 patch SHA-256 `DBDE5AB7…`（22,063 B、172 行、9 hunk）与 v3 快照哈希一致；`git apply -p1 --check` exit 0、临时副本真实 apply exit 0；9 个 hunk 逐条映射到 D1/D2/D3/权威文档；D4-I1（moon package / GitHub Actions / mooncakes 未验证声明）、D4-I2（warning 43=39+4、duplicate alias 第 44 条不计入）、D4-I3（121 份带退出码输出 vs commands.txt 131 条）均已闭环（见附录）；Markdown 围栏/表格/链接完好；nested 仍为 1/7，未新增未证实声明 |
| 工作区卫生 | **通过**（1 条入库建议） | `git status` 仅 ` M .gitignore`、`?? artifacts/`、`?? docs/协议兼容性与互操作范围.md`、`?? src/examples/interop_flat_writer/`；tracked 的 docs/README/申报书/ROADMAP/src 核心/fixtures/moon.mod 均未被修改 |
| 阻塞项 | **无** | 4 份交付物均达到“有可复现证据”的验收线；遗留项见 §8 |

---

## 1. 被验收文件版本（快照）

| 文件 | 大小 | mtime | SHA-256 | 备注 |
|---|---:|---|---|---|
| `artifacts/protocol-audit.md` | 83,960 B | 2026-10-20 20:26:29 | `3C5D515A8ED0463AA997A66D12E003D33B46A9D2B7E83C35B77D02C796F4F811` | D1，514 行 |
| `artifacts/interop-flat/README.md` | 25,837 B | 2026-10-20 20:22:02 | `9B6F99DC3DC09C1FA1850F1620E48DB4EBE5DAC19C1836E3BA65BD4C6FECB0BB` | D2；重放不改 README |
| `artifacts/interop-nested/README.md` | 30,964 B | 2026-10-20 20:24:42 | `71870EF1934ADCDACE28277017FDE450F4283FCAF55A0171174184BA73C7ED5F` | D3 |
| `artifacts/claim-alignment.patch` | 22,063 B | 2026-10-20 20:38:22 | `DBDE5AB7E0578C43828ACD5C601A29EB360A20795DB0C9101F14AE108FDEA3EA` | **D4 终版 v3（本报告所验版本）**，172 行、9 hunk |
| `artifacts/claim-alignment.md` | 43,161 B | 2026-10-20 20:41:02 | `0B4BB22025156F9CE3CCB52376055EEE2385D1E05DF3C3F505D142FE988291A7` | D4 v3 说明；版本戳 v3 / 20:40 |
| `_build/native/debug/build/cmd/delta/delta.exe` | 837,632 B | 2026-10-20 20:27:50 | `00CEB7E621CBE106871BA39D540ACB68FCFF38111E29D1111AFDB04D52B92718` | 本报告独立重建后的 CLI |

> 版本一致性：D4 终版 v3 patch/`claim-alignment.md` 与 20:42 快照 `artifacts/acceptance/pre-replay/claim-alignment.*.v3` 哈希逐一相等，说明增量复核期间文件未再变化，所验即当前磁盘终版。D1–D3 三份交付物哈希自 D5 首轮以来未变化（`protocol-audit.md` = `3C5D51…`、`interop-flat/README.md` = `9B6F99…`、`interop-nested/README.md` = `71870E…`），本次仅对 D4 做增量复核，未重跑/未改动其结论。

---

## 2. 工具链与 CLI 重建（含复核结论是否变化）

环境：`moon 0.1.20261012 (748b63c 2026-10-12)`；`.tools\pyenv\Scripts\python.exe` = Python 3.12.14，duckdb 1.5.6 / deltalake 1.6.6 / pyarrow 26.0.0 / pandas 3.0.6（未安装/升级任何包）。

| 项 | 结果 | 原始输出 |
|---|---|---|
| `moon check --target native` | **0 errors，43 warnings，exit 0** | `artifacts/acceptance/moon-check.out`、`moon-check.exit` |
| `moon test --target native` | **Total tests: 14, passed: 14, failed: 0，exit 0** | `artifacts/acceptance/moon-test.out`、`moon-test.exit` |
| 重建 CLI `moon build --target native src/cmd/delta` | 重编 9 个 task，42 warnings，0 errors，exit 0；二进制 837,632 B，时间戳 20:27:50 | `artifacts/acceptance/rebuild-cli.out` |

关键说明（对应任务要求“若重建后结论变化必须指出”）：

1. 复核开始时 `_build` 里的 CLI 已是 D1 于 20:17:06 重建的产物（837,632 B），D1 报告的“预编译二进制曾落后于源码、已重建”发生在本次验收之前，**无法从当前磁盘还原 D1 之前的旧二进制来独立复测“落后”这一历史事实**；但本次又独立重建了一次并核对了行为（见下）。
2. 本次重建时 moon 实际重编 9 个 task（D1 的 `build-cli.out` 记录为 3 个 task），二进制字节数不变，SHA-256 为 `00CEB7…`。重建后我用它重跑了 D1 的 24 个 CLI 用例、10 个补充用例，以及 D2/D3 的全部关键行为；**所有结果与 D1/D2/D3 报告一致，没有结论变化**（删除向量仍读 3 行、截断日志仍报 v9/90 文件、reader v2/v3 仍被接受、错误退出码仍为 0、nested 仍结构化拒绝、双向互操作仍成立）。
3. 因此 D1 §2.1 关于“CLI 证据来自重建后二进制”的声明可接受；本报告所有 CLI 结论均基于上表 20:27:50 重建产物。

---

## 3. D1（协议审计）验收：**通过**

### 3.1 报告与实测一致性的抽查（17 项，重点覆盖 §5 严重项与闭集外行为）

> “一致”指：报告中的结论、错误文本/数值、代码行号、引用 `out/` 文件均被独立复现；CLI 均使用 §1 重建二进制。

| # | 报告条目 | 报告结论（摘要） | 独立复核方式与结果 | 证据 | 一致性 |
|---:|---|---|---|---|---|
| 1 | F1 / T37 删除向量 | `deletionVector` 静默忽略，3 行 fixture 读出 3 行（正确应 2 行） | fixture 中 `deletionVector.cardinality=1`；`delta.exe read …/deletion-vector-add` → `read 1 file(s), 3 row(s)`，exit 0；`src/` 检索 `deletionVector` **0 命中** | `acceptance/d1/05-deletion-vector-read.out`；`protocol-audit/out/36-read-deletion-vector.out` | ✅ 一致 |
| 2 | F2 / C5 截断日志 + checkpoint | 静默当旧版本：`current version: 9`、`files: 90`，无 error；仅打印 last checkpoint v10 | `delta.exe inspect …/checkpoint-truncated` → `current version: 9 (log versions: 10)`、`files: 90 rows: 90`、`last checkpoint: version=10`，exit 0 | `acceptance/d1/06-…out`；`out/51-inspect-checkpoint-truncated.out` | ✅ 一致 |
| 3 | F3 / P2 / P3 / P6 reader v2/v3/feature | reader=2/writer=5、reader=3/writer=7（含 `unknownRequiredFeature`）均被接受并读出数据 | 两 fixture `inspect`/`read` 成功：`reader=2 writer=5`、`reader=3 writer=7`；`src/` 检索 `readerFeatures`/`writerFeatures` 均 0 命中；`parse_protocol` 只读两个 Int（`src/actions.mbt:15-24`） | `acceptance/d1/02-reader-v2-inspect.out`、`03-reader-v2-read.out`、`04-reader-v3-read.out`；`out/21`、`out/23`；`acceptance/d1/code-evidence.out` | ✅ 一致 |
| 4 | F4 / E4 CLI 错误退出码 | 121 次带退出码运行全部 exit 0（含 not found/parse/unsupported） | 本次 34 个 CLI 用例（含错误场景）**全部 `EXIT=0`**；错误文本以 `error:` 开头 | `acceptance/d1/*.out`（`01`–`34`）；`protocol-audit/commands.txt` | ✅ 一致（详见 §8 D1-I2 的计数口径瑕疵） |
| 5 | F5 / T28 / T29 append 不校验 | 把 long 列写成 string 后读出 `id (long) = not-a-long`；NOT NULL 列可写 NULL | `read …/probe-append-mismatch` → 行值 `not-a-long`（声明为 long 的列）与 `42`（声明为 string 的列）；`read …/probe-append-notnull` → 第 2 行为 `NULL`；`append_rows` 仅校验列数/行长（`src/write.mbt:202-217`），直接使用调用方 name/type（`:224-237`） | `acceptance/d1/25…26…out`；`out/probe-cli-append-mismatch.out`、`out/probe-cli-notnull.out` | ✅ 一致 |
| 6 | F6 / Q12 类型错配 | schema `id: long`、Parquet 为 string 时静默输出 `abc/def/ghi` | `delta.exe read …/type-mismatch` → 表头 `id (long)`，值 `abc/def/ghi`，exit 0 | `acceptance/d1/12-type-mismatch-read.out`；`out/87` | ✅ 一致 |
| 7 | F7 / L19 stats object | `stats` 为 object 时被丢弃：`inspect rows: 0`、`scan` 不剪枝 | `inspect …/stats-object` → `rows: 0`；`scan --where id>=100` → `matched rows: 0` | `acceptance/d1/13/14-…out`；`out/100`、`out/101` | ✅ 一致 |
| 8 | F9 / T38 column mapping | 未识别，报误导性“列缺失” | `read …/column-mapping` → `error: unsupported: column 'id' is missing from parquet file 'data.parquet'`；`src/` 检索 `columnMapping` 0 命中 | `acceptance/d1/30-…out`；`out/38` | ✅ 一致 |
| 9 | F10 / T24 / T25 create 不校验/可覆盖 | 非法 schema 创建成功；覆盖已有表 v0 后静默丢列 | 复现：`create …/created-bad '{not a schema'` → `created … (version 0)`，随后 `inspect` 才报 `parse error: invalid schema JSON`；对 2 文件表执行 create（缩减 schema）→ `inspect` 只剩 `id`，`read` 静默只返回 `id` 列 | `acceptance/d1/audit-claims-verify.out`；`out/113`、`out/126` | ✅ 一致 |
| 10 | F11 / T13 `--where` 降级 | 解析失败后继续无过滤扫描，退出 0 | `scan fixtures/delta/covid --where date>=2020-05-01` → 先 `error: invalid number`，随后 `selected files: 8`、`matched rows: 1111930`，exit 0 | `acceptance/d1/21-…out`；`out/14` | ✅ 一致 |
| 11 | T14 伪分区 | `--partition id=7` 静默返回 0 文件 | → `selected files: 0`、`skipped partition: 120`，exit 0 | `acceptance/d1/15-…out`；`out/07` | ✅ 一致 |
| 12 | S14–S16 nested 拒绝 | struct/list/map schema 阶段结构化拒绝，含字段名+kind | `read nested-struct/list/map` → `unsupported: nested column 'profile' (type=struct)…` 等三条文本完全一致；实现 `src/schema.mbt:113-131` | `acceptance/d1/08-10…out`；`out/68/70/72` | ✅ 一致 |
| 13 | Q3–Q6 codec | ZSTD/GZIP/BROTLI/LZ4 结构化拒绝并带文件名 | `read codec-zstd` → `error: unsupported: cannot read 'data.parquet': Unsupported parquet codec: 6` | `acceptance/d1/11-…out`；`out/84-read-codec-zstd.out` | ✅ 一致 |
| 14 | C4 checkpoint-only | 结构化 NotFound（消息 “has no log entries”） | → `error: not found: table '…' has no log entries`，exit 0 | `acceptance/d1/07-…out`；`out/50` | ✅ 一致 |
| 15 | 正向基线 | table_a 13 版本→120 文件/120 行/54232 B；`id>=60` 120→60；covid 157865 行 6 列 | CLI 复现：`version 12 (log versions: 13)`、120/120/54232；`selected 60 / skipped stats 60`；pyarrow 独立读 covid 文件元数据 `rows=157865 cols=6` | `acceptance/d1/22/23/24-…out`；`out/02`、`out/05`、`out/11` | ✅ 一致 |
| 16 | T26/T30/T31 append 写出 | 2 文件 5 行；stats 仅 numRecords+数值 min/max；写出列恒 Optional | 独立运行 `interop_flat_writer.exe` 到临时目录：`version=2 files=2 rows=5`，两条 stats `{numRecords:3,minValues:{id:1},maxValues:{id:3}}`、`{2,4,5}`；CLI 自读 5 行一致；pyarrow 复核写出列 `id nullable=True`（R-1） | `acceptance/d1/audit-claims-verify.out`；`acceptance/d2-parquet-schema.out` | ✅ 一致 |
| 17 | 测试覆盖声明 | 14 个具名测试；名 `checkpoint-only` 的用例实际是非 0 起点；nested 测试未真正打开嵌套 Parquet | `src/delta_wbtest.mbt` 实有 14 个 `test "…"`；`snapshot: checkpoint-only…` 只写 `000…03.json`（L230-247）；nested 测试用 array schema 在 schema 阶段被拒（L250-296） | `acceptance/d1/audit-claims-verify.out`；`src/delta_wbtest.mbt:230-247,250-296` | ✅ 一致 |

### 3.2 矩阵统计与闭集外实测

- **矩阵逐项复算**：§3 共 **122 行**，结论分布 **已实现且有测试 27 / 已实现（无测试）20 / 只识别 4 / 明确拒绝 24 / 未覆盖 47**，与 §1 摘要完全一致；分类行数 P12/L19/C7/S22/Q16/T40/E6。
- **闭集外输入均有实测**：`artifacts/protocol-audit/fixtures/` 62 个表（47 个 `gen_fixtures.py` 生成 + `appended-flat/cli-*` 6 个 + `probe-*` 9 个 runner 生成），`out/` 123 个原始输出文件覆盖 `reader v2/v3、DV、column mapping、CDF、截断 checkpoint、未知 action、非 0 起点/缺版本、4 种 codec、decimal/uuid/nested、类型错配、stats object、伪分区、退出码` 等；本次又用重建 CLI 复核 34 条（`acceptance/d1/*.out`）。
- **无“支持所有 Delta”类无证据结论**：对 `支持所有 Delta / 所有 Delta / 任意标准 Delta / 完整实现 Delta / 已完整支持 / 完全兼容` 全文检索均 **0 命中**；报告明确写“闭集之外必须结构化拒绝这一条没有落实”。D1 的 **401 个相对链接全部可解析**、代码围栏成对（`acceptance/links-hashes.out`）。

### 3.3 D1 发现的问题（均为低风险文档性瑕疵，不改变结论）

| 编号 | 问题 | 复现/证据 | 建议 |
|---|---|---|---|
| D1-I1 | §8.2 写“`out/` 输出索引（122 个文件）”，实际 **123** 个；漏列 `out/check-warnings-summary.out`（该文件正是 §5 #5 warning 统计的证据） | `(Get-ChildItem artifacts/protocol-audit/out -File).Count` → 123 | 把索引改为 123 并补列该文件 |
| D1-I2 | §4.4 与 `commands.txt` 末尾称“121 次带退出码的运行全部 exit 0”；但 `commands.txt` 实际记录 **131** 条命令，其中 `probe-library : moon run … => exit 1`（首轮失败，随后同块重跑 `exit 0`，出现重复记录）。`out/` 的 121 份带退出码输出确实全为 0，**E4“delta CLI 错误退出码恒 0”结论不受影响** | `Select-String -Path artifacts/protocol-audit/commands.txt -Pattern '=> exit '`（131 条，1 条 exit 1）；`out/probe-library.out` 头部 `[exit code: 0]` | 把“全部 exit 0”限定为“112 个 delta CLI case + 9 个探针脚本的 out 输出”；删除重复的 probe 记录块或注明首轮失败原因 |
| D1-I3 | §8.1 标题“自造 fixture 清单（47 个表）”与 `fixtures/` 实际 62 个目录易混；正文 §8.4 已说明 cli/probe 由 runner 生成，但标题/计数口径应写明“47 个 gen 表 + 15 个 runner 表 = 62” | `(Get-ChildItem artifacts/protocol-audit/fixtures -Directory).Count` → 62 | 标题与 §2.3 统一口径 |

---

## 4. D2（flat 双向互操作）验收：**通过**

### 4.1 独立重放

- 重放前把原证据整体备份到 `artifacts/acceptance/pre-replay/interop-flat/`（`run_interop.py` 会清空重建 `engine-written/`、`moondelta-written/`、`probe/`、`raw/`、`results/`，且 delta-rs 文件 UUID 会变化）。
- 命令：`.tools\pyenv\Scripts\python.exe artifacts\interop-flat\run_interop.py`（使用 §1 重建 CLI，未加 `--build`）。
- 结果：**exit 0**，控制台汇总 `PASS=49 FAIL=0 DIFF=2 INFO=5 BLOCKED=0`；`results.json` 的 `verdict` 为 `{"pass":49,"fail":0,"diff":2,"info":5,"blocked":0}`，`checks` 56 项（49 PASS / 2 DIFF / 5 INFO），`results/checks.csv` 56 行且状态计数一致；README 写的 `49 PASS / 0 FAIL / 2 DIFF / 5 INFO` 与原始结果一致。
- 原始证据：`artifacts/acceptance/d2-replay-console.log`、`artifacts/interop-flat/results.json`、`artifacts/interop-flat/results/checks.csv`。

### 4.2 双向互操作的独立验证（不依赖 D2 脚本的断言）

| 方向 | 独立操作 | 结果 |
|---|---|---|
| 外部引擎写 → 本项目读 | `delta.exe inspect/read artifacts/interop-flat/engine-written` | version 1、2 文件、5 行、`protocol reader=1 writer=2`、`id (long) not null`，5 行值与 README §1 数据表逐值一致 |
| 本项目写 → 外部引擎读（delta-rs） | `DeltaTable('…/moondelta-written').to_pyarrow_table()` | version=2（3 个 commit）、2 文件、5 行；按 id 排序后 `(1,alice,true)…(5,NULL,NULL)` 与期望一致；schema `id int64 not null / name string / active bool` |
| 本项目写 → 外部引擎读（DuckDB） | `LOAD delta; delta_scan('…/moondelta-written')`（扩展 6059958） | 5 行逐值一致；`engine-written` 同样 5 行一致 |
| 物理格式 | pyarrow `ParquetFile` 元数据 | `engine-written` 2 文件 SNAPPY（3+2 行）；`moondelta-written` 2 文件 UNCOMPRESSED（3+2 行），与 README C-DIFF-2 一致 |
| D2 已记录差异 R-1（物理 nullability） | pyarrow `read_schema` | `engine-written.id` nullable=**False**；`moondelta-written.id` nullable=**True**，与 README R-1 完全一致 |
| D2 风险 R-3（reader=2 被接受） | `inspect/read …/probe/protocol-reader2` | `protocol reader=2 writer=5`、`read 1 row`、exit 0，与 README R-3 一致 |
| stats 差异 R-2 | 直读 `moondelta-written/_delta_log/*.json` | stats 仅 `{numRecords,minValues:{id},maxValues:{id}}`，无 `nullCount`、无 string/bool min/max，与 README E-DIFF/R-2 一致 |

### 4.3 哈希重算

- `artifacts/interop-flat/hashes.txt` 共 **79** 条，**79/79 全部重算一致**（前 10 条抽样已逐一打印：engine-written 的 2 个日志 + 2 个 parquet、moondelta-written 的 3 个日志 + 2 个 parquet、probe/default-protocol 日志）。
- 复核脚本与输出：`artifacts/acceptance/d2_verify.py`、`artifacts/acceptance/d2-verify.out`（17/17 PASS）。

### 4.4 D2 问题

未发现影响“互操作成立”的问题。D2 已如实记录 R-1/R-2/R-3/R-5/R-7 等差异与风险，本次独立复核与之一致；重放会改变 `engine-written` 的 UUID 与哈希（README §3 已声明），属预期行为。

---

## 5. D3（nested 能力缺口对照）验收：**通过**

### 5.1 fixture 与 schema 事实

- 8 个 fixture 全部存在：`engine-written`、`kind-struct-only`、`kind-array-only`、`kind-map-only`、`checkpoint-nested`、`checkpoint-only-nested`、`negative-physical-nested`、`write-attempt-nested`。
- 主 fixture `engine-written`：`protocol = {minReaderVersion:1, minWriterVersion:2}`；`metaData.schemaString` 同时含 `profile: struct<name:string, age:int>`、`tags: array<string>`（`containsNull=true`）、`attrs: map<string,string>`（`valueContainsNull=true`）。
- 物理 Parquet 独立核对：列路径为 `profile.name`、`profile.age`、`tags.list.item`（**标准 3-level LIST**）、`attrs.entries.key/value`（**标准 MAP**）——README 的“struct / 3-level list / map”声明成立。
- 边界值独立读回（delta-rs 与 DuckDB 两个 oracle）：`profile=NULL`（id=2）、`tags=[]`（2,5）、`tags=NULL`（3）、`tags` 含 null 元素（4）、`attrs={}`（2,4）、`attrs=NULL`（5），与 README §3.1 表格逐格一致。

### 5.2 oracle 独立复算

- delta-rs 1.6.6 重读 `engine-written`：5 行；把 `oracle-delta-rs.json` 的 kind 编码解码后与独立读回结果**逐行逐字段一致**（含嵌套 struct/list/map 与 null/empty）；delta-rs `schema().to_json()` 与日志 `metaData.schemaString` **等价**。
- 第二 oracle DuckDB 1.5.6（delta 扩展 6059958）独立 `delta_scan`：5 行，schema 为 `STRUCT("name" VARCHAR, age INTEGER)`、`VARCHAR[]`、`MAP(VARCHAR, VARCHAR)`，值与 delta-rs 一致（`acceptance/d3-duckdb.out`）。

### 5.3 moondelta 原始错误/退出码与 README 引用一致

- `read engine-written`：重建 CLI 输出 `error: unsupported: nested column 'profile' (type=struct) is not supported by the parquet reader; only flat schemas are readable`，与 `raw/engine-written__read.stdout.txt` 和 README §5.1 引文**逐字一致**；**exit=0**、stderr 为空——README 已把这三点作为“错误策略问题”如实记录，未掩盖。
- 单 kind 用例：`kind-struct-only`/`kind-array-only`/`kind-map-only` 分别给出字段名+kind 的结构化 `Unsupported`，与 README §5.2 一致。
- `checkpoint-only-nested`：delta-rs 可读回 5 行（证明 checkpoint 完整），本项目 `inspect` 报 `error: not found: table '…' has no log entries`，与 README §5.3 一致。
- `negative-physical-nested`：本项目报 `Repeated columns are not supported yet`；delta-rs/DuckDB 对该伪造表同样失败，README 已标注“不作为互操作基线”。

### 5.4 七门槛统计与“负向不等于通过”

- `comparison.json` 的 `gate_counts = {done:1, partial:0, missing:6}`；`gates` 数组 7 项状态为 `未完成×6 + 已完成(门槛6)×1`；README/`comparison.md` 写“**已完成 1 项 / 部分完成 0 项 / 未完成 6 项**”。
- 门槛 6 是“fixture/边界/外部引擎对照”的证据门槛；门槛 7（双向读写）明确“未完成”。README、`comparison.md`、`comparison.json` 均明确“**能力缺口对照，不是互操作通过 / 不要引用为 nested 兼容证据**”，未把负向测试写成互操作通过。
- D3 的 SHA-256 表抽样 9 项（含文件大小）全部一致。

### 5.5 D3 问题

未发现影响“缺口对照成立”的问题。README 已主动记录 CLI 失败退出码 0、错误写 stdout、`inspect` 丢子字段、`create` 不校验 nested 可写性等缺陷，本次复核确认这些记录属实。

---

## 6. D4（申报/README 口径补丁）验收：**通过**

> 注：本节 6.1–6.5 记录的是 **v2（SHA `034C70…`）** 的复核过程，作为历史留档；此后 D4 又交付 **v3 终版**（SHA `DBDE5AB7…`），对 D5 提出的 D4-I1/I2/I3 三项遗留问题做了修正。v3 的增量复核见文末 **附录：D4 v3 增量复核**；§0/§1/§8 已按 v3 更新，D1–D3 结论未改动。

### 6.1 版本与可应用性

| 项 | 结果 |
|---|---|
| 所验终版 | `artifacts/claim-alignment.patch`，SHA-256 `034C70EADD9FB54C3D0A5E9FDE9E553B4FE6720D5D41D9D6CF4D0D138FA307C0`，20,169 B，147 行，**8 个 hunk**（申报书 2 / README 4 / ROADMAP 2）；与 20:31 v2 快照哈希相同 |
| `git apply -p1 --check` | **exit 0**（在仓库根与临时副本各执行一次） |
| 临时副本真实 apply | `artifacts/acceptance/apply-check/` 下复制 `申报书.md`、`README.md`、`docs/ROADMAP.md` 后 `git apply -p1` → **exit 0**；三个结果文件 **无 BOM、纯 LF**（LF-only 52/144/68） |
| 工作区目标文件 | `git diff --stat` 仅 `.gitignore`（新增 `.tools/` 忽略），`申报书.md`、`README.md`、`docs/ROADMAP.md`、`CHANGELOG.md` 均未被直接修改（patch 只作为文件交付，未落地） |
| 变更规模 | 申报书 12 行删/14 行增；README 13 行删/21 行增；ROADMAP 2 行删/2 行增——与 8 个 hunk 的语义一致，无越界改动 |

### 6.2 Markdown 结构 / 链接（apply 后校验）

- 代码围栏成对（申报书 0、README 10、ROADMAP 0）；表格列数一致（README 1 块、ROADMAP 2 块）；`~~~` 围栏成对。
- apply 后 README 的 9 个相对链接、`claim-alignment.md` 的 6 个相对链接全部可解析；`protocol-audit.md` 的 401 个链接全部可解析（`acceptance/links-hashes.out`、`acceptance/d4-structure.out`）。
- 版本戳：`claim-alignment.md` 末行“v2（D4b，覆盖 v1） 生成时间 2026-10-20 20:30”与交付说明一致。

### 6.3 逐 hunk 核对（是否属实、是否扩大/缩小范围）

| hunk | 位置 | 主要改动 | 独立核对结果 |
|---|---|---|---|
| H1 | `申报书.md @@ -16,14 +16,14 @@` | B1 删除“标准 Delta 可直接互通/已有表平移”，改为 reader v1/writer v2 + flat 范围 + D2 49/0/2 + D1 已知缺口 + nested 1/7；B2 存储仅本地；B3 删除“类型层/不静默降级”绝对表述；B4 Spark 直读改为“delta-rs/DuckDB 已回读、Spark 未验证”；B5 covid fixture 范围 | ✅ 全部有 D1 §5/D2/D3 证据支撑；未把 reader≥2/DV/截断 checkpoint 说成“已拒绝”，而是“未检测/会被旧版本读”；nested 写 1/7 且注明“不构成 nested 兼容”；未新增“Spark 已验证”类声明 |
| H2 | `申报书.md @@ -32,19 +32,21 @@` | B6 stats 范围（numRecords+数值 min/max，无 nullCount）；B7/B9 行数 2,575（1,508/383/570/114，含 D2 示例 69 行）；B8 边界与简化逐条改为已知缺口；B10 保留已实测并新增 D2 互操作与 D1 缺口；B11 warning 来源；B12 后续计划补修复项 | ✅ 行数独立复算：核心库 1,508 + CLI 383 + 测试 570 + 示例 114 = **2,575**（含 `interop_flat_writer` 69 行；若该示例不入库则 2,506）；checkpoint-only 用例实际构造、DV 3 行/应为 2 行、截断 checkpoint v9/90、reader=3/writer=7 成功、stats rows=0、append 不校验、CLI exit 0 全部复核属实；“43 条 warning 中 39 条在 src/、4 条 vendor”独立统计一致 |
| H3 | `README.md @@ -12,17 +12,20 @@` | 版本 0.1.0/0.1.1 待统一说明；新增协议范围 bullet（含截断 checkpoint 未检测、reader v2/v3 不校验）；append 不校验/恒 Optional；测试名 caveat；真实数据限定仓库 fixture；新增 D2/D3/审计证据链接段 | ✅ CLI `version` 实测 `moondelta-core 0.1.0`，`moon.mod` 为 0.1.1，待统一属实；table_a fixture 协议确为 `reader=1 writer=1`（H4 注释正确）；D2/D3 结论与本次复核一致 |
| H4 | `README.md @@ -58,6 +61,8 @@` | 新增“样例 reader=1 writer=1 是 fixture 自身协议，本项目写 1/2”注释 | ✅ `fixtures/delta/table_a` 日志确为 `{1,1}`，本项目写出为 `{1,2}`（D2 日志验证） |
| H5 | `README.md @@ -87,19 +92,22 @@` | 能力边界表：标题从“遇到即报错，不静默降级”改为“对已识别边界返回结构化错误；闭集外尚未全部拒绝”；新增 protocol/日志行、把删除向量/column mapping/CDF 移入“闭集外 feature 未检测”等 | ✅ 与 D1 §5 #7 一致；没有把“删除向量/截断日志/reader≥2”写成“明确拒绝”，明确写“未检测/已知缺口”；未缩小已支持类型范围 |
| H6 | `README.md @@ -117,7 +125,7 @@` | ROADMAP 摘要：checkpoint parquet 解析注明“仅 checkpoint 所需最小嵌套 shape，不构成 nested schema 兼容” | ✅ 与权威文档 §3 第 45 行一致 |
| H7 | `docs/ROADMAP.md @@ -13,7 +13,7 @@` | 负向边界行：把“✅ 明确报错”改为“行为：结构化错误；真实 checkpoint-only/DV/reader≥2 拒绝等测试缺口见 protocol-audit §6” | ✅ 与 D1 实测一致（行为 vs 测试覆盖分开表述），未删减已有功能 |
| H8 | `docs/ROADMAP.md @@ -48,7 +48,7 @@` | “不做通用嵌套”行补“该中间实现不构成 nested schema 兼容，需满足七项门槛” | ✅ 与权威文档 §3 第 45 行一致 |

### 6.4 未新增未证实声明 / 口径确认

- **nested 门槛**：patch 两处写 **1/7（仅证据门槛 6）**，与 D3 最终证据一致；没有写成“nested 已支持/互操作通过”。
- **D2 数值**：`49 PASS / 0 FAIL / 2 项已记录差异` 与重放结果一致；Spark/Databricks 明确写“未验证/未在本环境验证”。
- **版本**：`moon.mod` 0.1.1 vs README/CLI 0.1.0 的差异已在 patch 中如实说明“待统一”。
- **未把“未检测/已知缺口”写成“明确拒绝”**：对 patched 三个文件逐行检索“删除向量 / reader v2/v3 / 截断 / stats / 退出码”相关句，均使用“未检测 / 不校验 / 不拒绝 / 已知缺口 / 待修复 / 不能视为”等措辞，唯一使用“明确拒绝”的对象是 nested/decimal/4 种 codec 等 D1 已实测拒绝项。

### 6.5 D4 发现的问题

| 编号 | 严重度 | 问题 | 复现/证据 | 建议 |
|---|---|---|---|---|
| D4-I1 | **中（发布前须处理）** | patch 在改写 `申报书.md` 当前交付状态行（H2/B10-邻近）时，**保留**了“`moon package` 通过”“GitHub Actions 流水线已通过（check/build/test 全绿）”“已发布 mooncakes.io … 空工程安装并编译验证通过”等表述；D1 §5 #15 明确这些**未被 D1 验证**，D5 也未验证（无法本地复现 CI/mooncakes 安装）。patch 未按 D1 建议标注“待复核”或附 CI 链接/安装输出 | `Acceptance/d4-structure.out` §5；patch 第 48-49 行上下文；D1 §5 #15、§7 “未覆盖/未验证项” | 发布/申报前补证据（CI 运行链接、`moon package` 输出、空工程安装日志），或把这三句改为“待复核/仓库内有 CI 配置”。**不影响其它互操作口径修订的成立** |
| D4-I2 | 低 | `申报书.md` warning 句称“43 条 warning 中 39 条定位在项目 src/：…另有重复 alias 与示例 env 未 import”；独立统计：43 条带编号 warning 中 39 条在 src/（38 条 derive + 1 条示例 env），4 条 vendor；**duplicate alias 是第 44 条无文件定位的 warning**（moon 汇总仍计 43 条），把它归入“43 条之内”的写法不精确 | `acceptance/d1/audit-claims-verify2.out`；`moon-check.out` 首行 | 改为“43 条带定位 warning 中 39 条在 src/；另有 1 条 duplicate alias 无文件定位（moon test 输出或 check 首部）”。不改变“warning 并非全部来自 vendored parquet”的结论 |
| D4-I3 | 低 | `claim-alignment.md` §1.1 转录 D1 的“121 次带退出码运行全部 exit 0”，继承了 D1-I2 的计数口径问题（`commands.txt` 含 131 条记录与 1 条 exit 1 的重复探针块）；`out/` 的 121 份输出确为 exit 0 | 同 D1-I2 | 与 D1 一并修正措辞；不影响 patch 正文结论 |

---

## 7. 工作区卫生

### 7.1 实测状态

```
$ git status --porcelain
 M .gitignore
?? artifacts/
?? "docs/协议兼容性与互操作范围.md"
?? src/examples/interop_flat_writer/

$ git diff --stat
 .gitignore | 4 ++++
```

- `.gitignore` 的唯一改动是新增本地 Python 环境忽略：`# 本地互操作测试环境（Python venv）` + `.tools/`（`git check-ignore -v .tools` → `.gitignore:16:.tools/`，生效）。
- **未发现** docs/README/申报书/ROADMAP/src 核心/fixtures/moon.mod 的意外修改（tracked 文件除 `.gitignore` 外无 diff）。
- `docs/协议兼容性与互操作范围.md` 是本次工作新增的权威口径文件（untracked），D4 的引用依赖它；**必须与 artifacts 同批入库**，否则 README/申报书中的链接会失效（D4 `claim-alignment.md` 已提示，本次复核对链接可解析性无影响，但入库顺序需注意）。
- `artifacts/` 下未发现 Python venv、`__pycache__`、仓库根 `_build` 被误纳入的迹象；`artifacts/protocol-audit/probe-module/_build`（5.41 MB）与 `.mooncakes`（0.94 MB）已被根 `.gitignore` 的 `_build/`、`.mooncakes/` 规则忽略（`git check-ignore` 实测命中），不会随交付物入库。

### 7.2 对 Lead 三个卫生问题的明确回答

1. **`artifacts/protocol-audit/probe-module/` 是否入库？**
   - **建议：只入库源码 + `moon.mod`（约 0.33 MB、37 个文件），不入库 `_build/`（5.41 MB）与 `.mooncakes/`（0.94 MB）。**
   - 当前根 `.gitignore` 的全局 `_build/`、`.mooncakes/`、`*.exe` 规则**已经覆盖**这两个目录，**无需新增规则**；若希望语义更显式，可选加 `artifacts/protocol-audit/probe-module/_build/` 与 `.../.mooncakes/`（冗余但不错误）。
   - 保留理由：`probe-module/src/probe/main.mbt` 是 T28/T29/T33/append 边界等库级探针的唯一可复现源码；其 `src/` 与仓库 `src/` 逐文件哈希一致（仅多出 `probe` 包），删除会削弱 D1 证据可重放性。
2. **`artifacts/` 约 8.4 MB 是否可接受入库？**
   - **可接受**：当前总计 1067 个文件 / 8.45 MB，但排除已被忽略的 `_build`、`.mooncakes` 后，`git` 实际会跟踪 **829 个文件 / 约 2.09 MB**（含 fixture 二进制 0.14+0.17+0.31 MB 与全部原始输出）。
   - 若需要进一步瘦身：可删除 `artifacts/acceptance/pre-replay/` 的整目录备份（约 0.48 MB，仅为重放前快照，属可选证据）与 `acceptance/apply-check/` 的三个已应用副本（约 0.1 MB）；但**不建议**删除 `protocol-audit/fixtures`、`out/`、`interop-*/raw/` 与 `oracle-*`，它们是核心证据。
3. **D5 临时产物是否保留？**
   - **已清理**：`artifacts/acceptance/tmp/`（T24/T25/T26 复现时创建的临时表 `created-bad`、`appended-check`）已在复核结束前删除，`artifacts/` 下已无临时表。
   - **保留的证据**：`acceptance/d1/*.out`（34 个 CLI 复核用例原始输出 + 代码证据 + 复现脚本）、`moon-check/test` 原始输出、`d2/d3/d4_verify.py` 与 `.out`、`d2-replay-console.log`、`apply-check/`（真实 apply 结果）、`pre-replay/`（重放前快照与 D4 v1/v2 快照）、`d3-duckdb.out`、`links-hashes.out` 等；均为可复核证据，建议随报告一并保留。

---

## 8. 问题清单汇总与阻塞项

| 编号 | 交付物 | 严重度 | 问题摘要 | 阻塞？ |
|---|---|---|---|---|
| D1-I1 | D1 | 低 | out/ 索引写 122，实际 123（漏列 `check-warnings-summary.out`） | 否 |
| D1-I2 | D1 | 低 | `commands.txt` 含重复 probe 块与 1 条 `exit 1`，与“121 次全部 exit 0”的措辞不一致；CLI E4 结论本身成立 | 否 |
| D1-I3 | D1 | 低 | “47 个表”与 fixtures 实际 62 目录的口径需合并说明（47 gen + 15 runner） | 否 |
| D4-I1 | D4 | 中 → **v3 已闭环** | v2 保留 `moon package`/CI/mooncakes 安装验证的既有声明且未标注；**v3 已改为“未在本机复核/待复核，发布前需提供输出、CI 链接、安装日志”**（附录 A.4） | 否（发布前证据仍需用户/Lead 提供，属发布门槛而非 patch 缺陷） |
| D4-I2 | D4 | 低 → **v3 已闭环** | warning 统计把 duplicate alias 归入 43 条之内；**v3 已改为“43 条带文件定位 = 39 src + 4 vendored；duplicate alias 为第 44 条无定位 warning、不计入”**（附录 A.4） | 否 |
| D4-I3 | D4 | 低 → **v3 已闭环** | 转录“121 次全部 exit 0”的口径瑕疵；**v3 已改为“121 份带退出码的 CLI/校验输出全部 exit 0”，并注明 `commands.txt` 131 条记录（含重复 probe 块与首轮 `probe-library` exit 1）**（附录 A.4） | 否 |
| WS-I1 | 工作区 | 信息 | `docs/协议兼容性与互操作范围.md` 与 `artifacts/`、`src/examples/interop_flat_writer/` 均 untracked，D4 引用依赖它们，须同批入库 | 否（入库注意事项） |
| WS-I2 | 工作区 | 信息 | `probe-module/_build`（5.41 MB）已由根 `.gitignore` 忽略；`artifacts/` 实际入库约 2.09 MB | 否 |

**阻塞项：无。** D1–D4 的证据链均可由本报告给出的命令重放；D5 首轮提出的 D4-I1/I2/I3 已由 v3 全部闭环（附录 A.4）。剩余仅“发布前证据提供”事项（`moon package` 输出、GitHub Actions 运行链接、mooncakes.io 安装日志/页面链接），已在 v3 文本中如实标注为待复核，需用户/Lead 在发布/申报前补齐，不属于补丁正确性缺陷。

---

## 9. 未复核项及原因

| 项 | 状态 | 原因 |
|---|---|---|
| Spark / Databricks 目录级互操作 | 未复核 | 本机无 Spark 环境；D2/D4 均明确写“未验证”，与事实一致 |
| `moon package`、GitHub Actions 流水线、mooncakes.io 0.1.1 空工程安装 | 未复核 | 需 CI 运行/联网安装，D1 §5 #15 已声明未验证；D4 v3 已把相关表述改为“待复核/需提供输出、CI 链接、安装日志”，但证据本身仍需用户/Lead 在发布前提供（见附录 A.7） |
| D1 之前“预编译 CLI 落后源码”的历史状态 | 无法还原 | 复核开始时 CLI 已是 D1 重建产物；本次只能独立再次重建并确认行为不变（见 §2） |
| `run_interop.py --build` 分支 | 未单独复核 | 本次使用已有 writer/CLI 产物重放（README 的主路径），未触发其内部 `moon build` 重试逻辑 |
| D2 `run_interop.py` 的 DuckDB `INSTALL delta` 失败分支 | 未复核 | 本机扩展已安装，脚本走 `LOAD delta` 成功分支；失败分支仅代码审阅 |
| D3 checkpoint Parquet 内部内容解析 | 未复核（超出 D3 声明） | D3 只声明“checkpoint 内部 action schema 探针 + 外部引擎可读”，未声明本项目解析；本次只验证了 oracle 从 checkpoint-only 读回 5 行 |
| fuzz/畸形输入全覆盖 | 未做 | D1 已声明未做；本次仅重放既有 112 个 CLI case + 探针 |
| D4 patch 落地到工作区 | 按设计未落地 | 交付物是 patch；本次仅在 `acceptance/apply-check/` 临时副本真实 apply 验证 |

---

## 10. 可复现复核命令清单

在仓库根 `E:\Project\Moonbit Hackson\P1 RE` 执行（PowerShell）：

```powershell
# 0) 工具链
moon check --target native
moon test  --target native
moon build --target native src/cmd/delta
Get-FileHash _build\native\debug\build\cmd\delta\delta.exe -Algorithm SHA256

# 1) D1：重建后的 CLI 抽查（输出见 artifacts/acceptance/d1/*.out）
pwsh -NoProfile -File artifacts\acceptance\d1\run-d1-checks.ps1
_build\native\debug\build\cmd\delta\delta.exe read artifacts\protocol-audit\fixtures\deletion-vector-add
_build\native\debug\build\cmd\delta\delta.exe inspect artifacts\protocol-audit\fixtures\checkpoint-truncated
_build\native\debug\build\cmd\delta\delta.exe read artifacts\protocol-audit\fixtures\protocol-reader-v3-features
_build\native\debug\build\cmd\delta\delta.exe inspect artifacts\acceptance\d1\does-not-exist   # 观察 exit code
Select-String -Path artifacts\protocol-audit\out\*.out -Pattern '\[exit code: \d+\]'
# 矩阵/§5 计数与措辞检索：见 artifacts/acceptance/d1/audit-claims-verify2.out 的生成命令

# 2) D2：重放（会重建 fixture；建议先备份）
Copy-Item -Recurse -Force artifacts\interop-flat artifacts\acceptance\pre-replay\interop-flat
.tools\pyenv\Scripts\python.exe artifacts\interop-flat\run_interop.py
.tools\pyenv\Scripts\python.exe artifacts\acceptance\d2_verify.py
.tools\pyenv\Scripts\python.exe artifacts\acceptance\d2_parquet_schema_check.py

# 3) D3：独立 oracle / 错误文本 / DuckDB 第二 oracle
.tools\pyenv\Scripts\python.exe artifacts\acceptance\d3_verify.py
.tools\pyenv\Scripts\python.exe artifacts\acceptance\d3_duckdb_check.py

# 4) D4：apply 校验 + Markdown/链接检查
git apply -p1 --check artifacts\claim-alignment.patch
New-Item -ItemType Directory -Force artifacts\acceptance\apply-check\docs | Out-Null
Copy-Item 申报书.md artifacts\acceptance\apply-check\申报书.md
Copy-Item README.md artifacts\acceptance\apply-check\README.md
Copy-Item docs\ROADMAP.md artifacts\acceptance\apply-check\docs\ROADMAP.md
Push-Location artifacts\acceptance\apply-check; git apply -p1 ..\..\..\claim-alignment.patch; Pop-Location
.tools\pyenv\Scripts\python.exe artifacts\acceptance\d4_structure.py
.tools\pyenv\Scripts\python.exe artifacts\acceptance\links_hashes.py
Get-FileHash artifacts\claim-alignment.patch,artifacts\claim-alignment.md -Algorithm SHA256

# 5) 工作区与体积
git status --porcelain
git diff --stat
git check-ignore -v artifacts\protocol-audit\probe-module\_build artifacts\protocol-audit\probe-module\.mooncakes
```

> 注：`d2_verify.py`、`d3_verify.py` 等脚本中的路径均以仓库根为基准；Windows 控制台若出现中文乱码，以 UTF-8 的 `.out` 文件为准（与 D2 README 的说明一致）。

---

## 11. 验收证据目录索引（`artifacts/acceptance/`）

```
artifacts/acceptance/
├── moon-check.out / moon-check.exit      # moon check 原始输出（0 errors / 43 warnings / exit 0）
├── moon-test.out  / moon-test.exit       # moon test 原始输出（14/14 / exit 0）
├── rebuild-cli.out                       # CLI 独立重建原始输出（含 42 warnings、9 tasks）
├── d1/
│   ├── 01-…34-*.out                      # 34 个 CLI 复核用例（每文件含命令行、EXIT、stdout/stderr）
│   ├── run-d1-checks.ps1                 # 上述用例的复现脚本
│   ├── code-evidence.out                 # 源码关键字命中/0 命中与关键代码行
│   ├── audit-claims-verify.out           # T24/T25/T26、D2 交叉证据等复现记录
│   └── audit-claims-verify2.out          # D1 矩阵计数、§5 行数、warning 来源、commands 退出码统计
├── d2-replay-console.log                 # run_interop.py 重放控制台汇总（EXIT=0 / 49 PASS…）
├── d2_verify.py / d2-verify.out          # D2 独立复核脚本与结果（17/17 PASS）
├── d2_parquet_schema_check.py / d2-parquet-schema.out  # R-1 物理 nullability 复核
├── d3_verify.py / d3-verify.out          # D3 独立复核脚本与结果（38/38 PASS）
├── d3_duckdb_check.py / d3-duckdb.out    # DuckDB 第二 oracle 独立读 nested
├── d4_structure.py / d4-structure.out    # v2 patch 应用后 Markdown 结构/措辞/未证实声明检查（27/27 PASS）
├── d4_v3_check.py / d4-v3-structure.out  # D4 v3 增量复核：结构/行数/三项闭环/9 hunk/哈希（65/65 PASS）
├── d4-v3-apply.log                       # v3 apply --check / 真实 apply / 行数 / 哈希原始输出
├── d4-v3-claims-grep.out                 # v3 未验证断言残留与三项修正的声明检索
├── d4-v3-apply-check/                    # v3 真实 apply 后的三个目标文件（申报书/README/ROADMAP）
├── links_hashes.py / links-hashes.out    # 交付物链接完整性、D3 哈希抽样、D2 checks.csv（15/15 PASS）
├── apply-check/                          # v2 patch 真实 apply 后的三个目标文件（历史留档）
└── pre-replay/                           # 重放前快照：interop-flat/、interop-nested/、
                                          # D4 patch/md v1、v2-snapshot、v3、protocol-audit.md v1
```

原始第三方证据仍位于各交付物目录（`artifacts/protocol-audit/{out,fixtures}`、`artifacts/interop-flat/{raw,results,hashes.txt,results.json}`、`artifacts/interop-nested/{raw,oracle-*.json,comparison.json,fixture-manifest.json}`），本报告未改动其内容。

---

# 附录：D4 v3 增量复核（D5b，2026-10-20 20:41–20:46）

> 触发：D4 在 v2 之后又交付 **v3（D4c）**，修正 D5 首轮提出的 D4-I1（中）、D4-I2（低）、D4-I3（低）。按任务 `task-8` 只对 D4 v3 做增量复核，**未重跑 D1–D3**；其交付物哈希与首轮一致（见 A.1），原有结论不删改。

## A.1 复核对象与版本（最终哈希）

| 文件 | 大小 | mtime | SHA-256 | 与 Lead 声称对比 |
|---|---:|---|---|---|
| `artifacts/claim-alignment.patch` | 22,063 B | 2026-10-20 20:38:22 | `DBDE5AB7E0578C43828ACD5C601A29EB360A20795DB0C9101F14AE108FDEA3EA` | ✅ 一致（172 行、9 hunk） |
| `artifacts/claim-alignment.md` | 43,161 B | 2026-10-20 20:41:02 | `0B4BB22025156F9CE3CCB52376055EEE2385D1E05DF3C3F505D142FE988291A7` | ✅ 一致（v3 / 20:40） |

- 上述哈希与 `artifacts/acceptance/pre-replay/claim-alignment.patch.v3`、`claim-alignment.md.v3` 快照逐一相等，说明增量复核期间文件未再变化（`d4-v3-apply.log` §1）。
- **D1–D3 交付物未变化**（仅复核不受影响）：`protocol-audit.md` = `3C5D515A…F811`、`interop-flat/README.md` = `9B6F99DC…B0BB`、`interop-nested/README.md` = `71870EF1…7ED5F`，与 §1 表一致。
- 说明：v3 `claim-alignment.md` 自述“md 的 SHA-256 在本文写定后由交付消息给出（本文件无法自引用自身哈希）”，因此 md 内部不含自身哈希属正常；本报告以磁盘实测哈希为准。

## A.2 复核命令与原始输出

```powershell
# 哈希 / 版本
Get-FileHash artifacts\claim-alignment.patch,artifacts\claim-alignment.md -Algorithm SHA256
# 仓库根 apply 校验 + 变更统计
git apply -p1 --check artifacts\claim-alignment.patch
git apply -p1 --stat  artifacts\claim-alignment.patch
# 临时副本真实 apply（不落地工作区）
#   复制 申报书.md / README.md / docs/ROADMAP.md 到 artifacts/acceptance/d4-v3-apply-check/
git -C artifacts/acceptance/d4-v3-apply-check apply -p1 --check ../../claim-alignment.patch
git -C artifacts/acceptance/d4-v3-apply-check apply -p1        ../../claim-alignment.patch
# 结构与三项闭环检查
.tools\pyenv\Scripts\python.exe artifacts\acceptance\d4_v3_check.py
.tools\pyenv\Scripts\python.exe artifacts\acceptance\links_hashes.py
```

原始输出：`artifacts/acceptance/d4-v3-apply.log`（哈希/apply/行数/工作区）、`artifacts/acceptance/d4-v3-structure.out`（65/65 PASS）、`artifacts/acceptance/d4-v3-claims-grep.out`（未验证断言残留检索）、`artifacts/acceptance/d4-v3-apply-check/`（apply 后文件）、`artifacts/acceptance/links-hashes.out`（链接/哈希 15/15 PASS）。

## A.3 apply 与结构/行数校验（全部通过）

| 校验项 | 结果 |
|---|---|
| 仓库根 `git apply -p1 --check` | **exit 0** |
| `git apply --stat` | 3 files changed, **42 insertions(+), 32 deletions(-)**（申报书 28 行改动、README 40、ROADMAP 6） |
| 临时副本 `--check` / 真实 apply | **exit 0 / exit 0**，无冲突、无 fuzz |
| apply 后行数 | 申报书 **52** 行、README **144** 行、ROADMAP **68** 行（与 Lead 声称的 52 / 144 / 68 一致） |
| README 结构 | 代码围栏 **10**（成对）、能力边界表 **12 行**（表头+分隔+10 数据行，与声称一致） |
| 编码 | 三文件均**无 BOM、纯 LF**（CRLF=0） |
| Markdown 结构 | 围栏成对、表格列数一致、相对链接可解析（README 9 个链接、申报书/ROADMAP 0 个）；v3 `claim-alignment.md` 7 个链接全部可解析、围栏成对（`links-hashes.out`） |
| 综合检查 | `d4-v3-structure.out` **65/65 PASS** |

## A.4 D4-I1 / I2 / I3 闭环逐项核验（均已闭环）

| 编号 | D5 首轮问题 | v3 修正内容 | 独立核验（证据） | 结论 |
|---|---|---|---|---|
| **D4-I1**（中） | v2 在申报书当前交付状态行保留“`moon package` 通过”“GitHub Actions 流水线已通过”“已发布 mooncakes…安装并编译验证通过”，而 D1/D5 均未验证 | ① 申报书：`moon package` 与 GitHub Actions 改为“**未在本机复核，发布/申报前需提供 `moon package` 输出与 CI 运行链接**”；mooncakes 改为“**发布状态待复核**…发布前需提供安装日志与页面链接”；② README 顶部“已发布”改为“**发布记录待复核**…本机未做安装验证”；③ 快速开始安装注释改为“**发布记录待复核**”；④ 开发章节 CI 改为“**远端运行结果未在本机复核（D1/D5）**，发布前需提供 CI 运行链接”；⑤ ROADMAP 0.1.x 表行改为“CI 与 mooncakes.io **发布记录待复核**” | apply 后文件检索：`moon package` 通过 / 流水线已通过 / 安装并编译验证通过 / `已发布：` / “warning 全部来自 vendored” **5 类肯定句均 0 命中**；5 处“待复核/未在本机复核/需提供”标注全部存在；`d4-v3-structure.out` §3、`d4-v3-claims-grep.out` §1–2 | ✅ **已闭环** |
| **D4-I2**（低） | “43 条 warning 中 39 条在 src/：…另有重复 alias 与示例 env 未 import”把 duplicate alias 混入 43 条 | 申报书改为“**43 条带文件定位的 warning 中 39 条在项目 `src/`（38 条 derive 触发 `implicit_impl_as_method` + 1 条示例 env 未 import）、4 条在 vendored parquet；另有 1 条无文件定位的 duplicate alias 工程配置 warning 不在这 43 条内，均非 error**” | 与 D5 首轮独立统计一致（43 条带编号 warning = 39 src + 4 vendor；duplicate alias 为第 44 条无定位 warning，moon 汇总仍计 43）；句中 4 个必要口径串全部命中；`d4-v3-structure.out` §4 | ✅ **已闭环** |
| **D4-I3**（低） | `claim-alignment.md` 转录 D1“121 次带退出码运行全部 exit 0”，继承 `commands.txt` 131 条记录/1 条 `exit 1` 的口径瑕疵 | md §1.1 F4 改为“**121 份带退出码的 CLI/校验输出（112 个编号 CLI case + 9 个探针/Parquet 复核脚本）全部 `[exit code: 0]`**”，并注明“**`commands.txt` 共 131 条记录**，含重复 probe 块与首轮 `probe-library` 的 `exit 1`（随后同块重跑 exit 0，计数以 out/ 带退出码输出为准）”；§3/映射表同步 | md 实测含“121 份带退出码的 CLI/校验输出”“131 条记录”及完整口径说明；与 D1-I2/D5 统计一致；`d4-v3-structure.out` §5 | ✅ **已闭环** |

> 说明：D4-I1 的闭环是“**文本已如实标注未验证**”，不是“证据已补齐”。三项发布证据本身（`moon package` 输出、CI 运行链接、mooncakes 安装日志/页面）仍未提供，属 A.7 的发布门槛事项。

## A.5 9 个 hunk 逐条映射（v3）

| # | 文件与 hunk | 内容 | 映射/闭环 |
|---:|---|---|---|
| 1 | `申报书.md @@ -16,14 +16,14 @@` | H1/B1–B5：删除“标准 Delta 可直接互通”等超范围承诺，写 reader v1/writer v2 闭集 + D2 49/0/2 + D1 已知缺口 + nested 1/7；存储仅本地；删除“类型层/不静默降级”；Spark 改“未验证”；covid fixture 范围 | 权威文档 §1/§2/§3/§5；D1 §5 #1/#2/#3；D2 §0/§12；D3 §0/§7 —— ✅ 与首轮一致，未回退 |
| 2 | `申报书.md @@ -32,19 +32,21 @@` | H2/B6–B12：stats 范围、2,575 行（全行口径）、边界与简化逐条改已知缺口、测试名 caveat、D2/审计引用、**warning 口径（I2）**、**moon package/CI/mooncakes 待复核（I1）**、后续计划补修复项 | D1 §5 #2/#3/#4/#5/#13/#14/#15；D2 R-1/R-2；D3 §7 —— ✅ 含两项闭环 |
| 3 | `README.md @@ -8,28 +8,31 @@` | H3（v3 合并）：顶部**“已发布”→发布记录待复核（I1）**、版本 0.1.0/0.1.1 待统一、协议范围 bullet、append 不校验、测试名 caveat、真实数据范围、D2/D3/审计链接段、**快速开始安装注释待复核（I1）** | D1 §5 #6/#7/#9；D2/D3 —— ✅ 含 I1 闭环 |
| 4 | `README.md @@ -57,6 +60,8 @@` | H4：新增样例 `reader=1 writer=1` 是 fixture 协议、本项目写 1/2 的注释 | fixture 日志与 D2 写出协议实测 —— ✅ |
| 5 | `README.md @@ -87,19 +92,22 @@` | H5：能力边界表与措辞改为“对已识别边界返回结构化错误；闭集外尚未全部拒绝”，新增 protocol/日志行、删除向量/column mapping/CDF 未检测 | D1 §5 #7、§4.1/§4.9/T36–T38 —— ✅ 未把未检测写成拒绝 |
| 6 | `README.md @@ -117,7 +125,7 @@` | H6：ROADMAP 摘要注明 checkpoint 最小嵌套 shape 不构成 nested 兼容 | 权威文档 §3 第 45 行 —— ✅ |
| 7 | `README.md @@ -129,7 +137,7 @@` | H7（v3 新增）：开发章节 CI 描述补“远端运行结果未在本机复核（D1/D5），发布前需提供 CI 运行链接” | D1 §5 #15；D5 §9 —— ✅ 含 I1 闭环 |
| 8 | `docs/ROADMAP.md @@ -12,8 +12,8 @@` | H8（v3 并入）：0.1.x 表“CLI、示例、CI、mooncakes.io 发布”行拆为“CLI、示例 ✅；**CI 与 mooncakes.io 发布记录待复核**”；负向边界行改为“行为：结构化错误；测试缺口见审计 §6” | D1 §5 #10/#15；D2/D5 —— ✅ 含 I1 闭环 |
| 9 | `docs/ROADMAP.md @@ -48,7 +48,7 @@` | H9：“不做通用嵌套”行补“该中间实现不构成 nested schema 兼容，需满足七项门槛” | 权威文档 §3 第 45 行 —— ✅ |

结论：9 个 hunk 均能映射到 D1 §5 / D2 / D3 / 权威文档；新增的两个 hunk（README 开发章节 CI、ROADMAP 0.1.x 表行）与合并后的 README 顶部 hunk 只做“未验证声明 → 待复核”的如实修正，**未新增未证实声明、未扩大/缩小实现范围**；nested 仍为 **1/7（仅证据门槛 6）**；删除向量/截断日志/reader≥2 仍表述为“未检测/已知缺口”，未见“明确拒绝”误写。

## A.6 未验证断言残留扫描

对 apply 后三份文件扫描 5 类肯定句，**全部 0 命中**：

- `moon package` 通过 / `moon package`…通过 → 0
- 流水线已通过 → 0
- 安装并编译验证通过（或“安装验证通过”）→ 0
- `已发布：`（肯定句）→ 0
- warning 全部来自 vendored → 0

新增行中保留的仅是带限定的如实表述（4 处“待复核”、3 处“未在本机复核”、3 处“需提供”），如“本机未做安装验证，发布前需复核并留存安装日志”“远端运行结果未在本机复核（D1/D5），发布前需提供 CI 运行链接”。`d4-v3-claims-grep.out` 为完整检索输出。

## A.7 仍未闭环的发布证据事项（不影响 D4 v3 通过，但发布/申报前必须补齐）

| 事项 | 当前状态 | 需要谁提供 |
|---|---|---|
| `moon package` 实际运行与输出 | **未运行、无输出**；v3 已改为“未在本机复核，发布前需提供输出” | 用户/Lead（或 CI 任务） |
| GitHub Actions 流水线远端运行结果 | **无运行链接/日志**；v3 已改为“远端运行结果未在本机复核，发布前需提供 CI 链接” | 用户/Lead |
| mooncakes.io 0.1.1 空工程安装与编译验证 | **未验证**；v3 已改为“发布状态待复核，发布前需提供安装日志与页面链接” | 用户/Lead（需联网安装） |

说明：D5 复核环境不具备 CI 运行与联网空工程安装条件，**无法替 Lead 生成这三项证据**；v3 的职责边界是“不得把未验证写成已验证”，该点已完成。另：申报书“拟实现的核心功能”中“mooncakes.io 发布”作为计划性功能条目保留，未含“已验证”断言，不视为残留未验证声明。

## A.8 D5b 新增证据文件

- `artifacts/acceptance/d4_v3_check.py`：v3 增量复核脚本（哈希、行数、结构、三项闭环、9 hunk）。
- `artifacts/acceptance/d4-v3-structure.out`：**65/65 PASS** 的逐项结果。
- `artifacts/acceptance/d4-v3-claims-grep.out`：未验证断言残留 + 三项修正的声明检索。
- `artifacts/acceptance/d4-v3-apply.log`：哈希/`git apply --check`/真实 apply/apply 后行数/D1–D3 哈希/工作区原始输出。
- `artifacts/acceptance/d4-v3-apply-check/`：v3 真实 apply 后的 `申报书.md`、`README.md`、`docs/ROADMAP.md`。
- `artifacts/acceptance/pre-replay/claim-alignment.patch.v3`、`claim-alignment.md.v3`：v3 终版快照（与磁盘哈希相同）。

## A.9 D5b 结论

- **D4 v3：通过**。两个文件 SHA-256 与 Lead 声称完全一致；`git apply --check`/真实 apply 均 exit 0；apply 后行数 52 / 144 / 68、README 围栏 10/表格 12 行，与声称一致；Markdown 结构完好（65/65 + 15/15 检查通过）。
- **D4-I1 / I2 / I3 全部闭环**（文本层面）；发布证据（package/CI/mooncakes 安装）仍待用户/Lead 在发布前补齐，已在 v3 中如实标注，属发布门槛而非补丁缺陷。
- **9 个 hunk 未新增未证实声明、未改变实现范围、nested 仍 1/7**；D1–D3 交付物哈希与首轮一致，**原有 D1–D3 结论不受影响**。
- 本报告 §0、§1、§8、§9 与 §11 索引已按 v3 更新；§6.1–6.5 保留为 v2 复核历史。

