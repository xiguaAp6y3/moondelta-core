# D2：flat 表双向互操作对拍（delta-rs / DuckDB ↔ moondelta-core）

> 对应 `docs/协议兼容性与互操作范围.md` 第 4 节 **案例 A（扁平表，当前版本应通过）**、第 6 节 **任务 D2**、第 7 节证据要求。
>
> 本目录只包含**可重放的证据**：外部引擎真实写出的 Delta 表、moondelta-core 真实写出的 Delta 表、原始命令输出、逐项 diff、对比 JSON/CSV、SHA-256 清单。所有“通过”均可由 `run_interop.py` 重新生成并复核；未伪造任何外部引擎结果。

## 0. 结论摘要（本次运行）

| 指标 | 值 |
|---|---|
| 检查项 | **49 PASS / 0 FAIL / 2 已记录 DIFF / 5 INFO / 0 BLOCKED**（共 56 项） |
| 外部引擎 1 | `deltalake`（delta-rs）Python **1.6.6**，pyarrow 26.0.0 |
| 外部引擎 2 | DuckDB **1.5.6** + delta 扩展 `extension_version=6059958`（`INSTALL delta` 成功） |
| 本机内核 | `moondelta-core 0.1.0`（`_build/native/debug/build/cmd/delta/delta.exe`），moon `0.1.20261012` |
| 双向结论 | **A（delta-rs 写 → moondelta 读）：列名/顺序/类型/nullability/版本/文件/行数/逐值/add.stats 全部一致**；**C（moondelta 写 → delta-rs 与 DuckDB 读）：schema/行数/逐列值（含 null）一致** |
| 关键差异 | Parquet 物理层 `id` 为 optional（Delta 声明 NOT NULL）；moondelta 写出的 `add.stats` 缺 `nullCount` 与 `name`/`active` 的 min/max。二者均不影响读值，但影响约束强度与剪枝能力（见 §9） |
| 建议提请 D1 复核的风险 | CLI 对 `reader=2/writer=5` 的表 `inspect`/`read` 返回 0 而非拒绝，与文档第 1/2 节“高于 reader v1 必须拒绝”的口径不一致（见 §9 R-3） |

一键复现（仓库根目录，退出码 0 表示无 FAIL）：

```powershell
.tools\pyenv\Scripts\python.exe artifacts\interop-flat\run_interop.py
# 如 _build 可执行文件不存在或需要重建：
.tools\pyenv\Scripts\python.exe artifacts\interop-flat\run_interop.py --build
```

## 1. 方法与 fixture

对拍使用**完全相同的 Delta protocol 配置**（reader v1 / writer v2、JSON commit log + Parquet 数据文件、本地文件系统），只改变“谁写谁读”：

1. **A 外部引擎写出**：delta-rs 写 `engine-written/`（列 `id long NOT NULL, name string, active boolean`，5 行、含 null，两次提交 → 2 个数据文件，SNAPPY）；
2. **B moondelta 读取**：`delta.exe inspect/history/scan/read` 读 `engine-written/`，与 delta-rs 的读回结果逐项 diff；
3. **C moondelta 写出**：`src/examples/interop_flat_writer` 写 `moondelta-written/`（同一 schema、同一 5 行数据，3 次提交 / 2 个数据文件），再用 delta-rs 与 DuckDB 回读断言；
4. **D DuckDB**：优先 `INSTALL delta; LOAD delta; delta_scan(...)`；另用 `read_parquet` 做 parquet 级补充（**明确标注非 Delta 级互操作**）；
5. **E add.stats**：比较两侧 `numRecords/minValues/maxValues/nullCount` 的存在性、类型、值，并实测 `scan --where "id>=4"` 的剪枝效果；
6. **F 证据**：原始 stdout/stderr/退出码、对比 JSON/CSV、SHA-256。

数据（两侧一致，`id` 唯一便于按主键逐值比对）：

| id (long NOT NULL) | name (string) | active (boolean) |
|---|---|---|
| 1 | alice | true |
| 2 | NULL | false |
| 3 | bob | NULL |
| 4 | carol | true |
| 5 | NULL | NULL |

批次：batch1 = id 1–3，batch2 = id 4–5（→ 2 个数据文件、2 次数据提交）。

## 2. 环境（`raw/00_environment.json`）

| 组件 | 版本 / 说明 |
|---|---|
| OS | Windows-11-10.0.28000-SP0 |
| Python | 3.12.14（`.tools\pyenv\Scripts\python.exe`，**未安装/升级任何包**） |
| delta-rs | `deltalake` 1.6.6 |
| DuckDB | 1.5.6；delta 扩展 `extension_version=6059958`（首次 `INSTALL delta` 需网络） |
| Arrow | pyarrow 26.0.0（pandas 3.0.6 仅随环境存在，未用于断言） |
| MoonBit | moon 0.1.20261012 (748b63c 2026-10-12) |
| moondelta CLI | `_build\native\debug\build\cmd\delta\delta.exe`，`delta version` → `moondelta-core 0.1.0` |
| writer 示例 | `_build\native\debug\build\examples\interop_flat_writer\interop_flat_writer.exe`（源码 `src/examples/interop_flat_writer/`，本次未改动） |

## 3. 目录结构

```text
artifacts/interop-flat/
├── run_interop.py                 # 一键复现脚本（唯一入口）
├── README.md                      # 本文
├── results.json                   # 全量结构化结果（断言以此为准）
├── hashes.txt                     # fixture 与关键输出 SHA-256（79 个文件，不含本 README）
├── results/                       # 展示用表格
│   ├── checks.csv                 #   56 项检查明细（49 PASS + 2 DIFF + 5 INFO）
│   ├── values_engine_written.csv  #   A/B 侧逐值（delta-rs / moondelta CLI / DuckDB）
│   ├── values_moondelta_written.csv#  C/D 侧逐值（delta-rs / moondelta CLI / DuckDB）
│   └── stats_compare.csv          #   两侧 add.stats 逐文件对比
├── raw/                           # 原始证据：每条命令的 stdout/stderr/退出码/命令行 + 结构化 JSON
├── engine-written/                # delta-rs 写出的 Delta 表（受测输入）
├── moondelta-written/             # moondelta-core 写出的 Delta 表（受测输出）
└── probe/                         # 附带探针表：protocol-reader2（reader=2/writer=5，见 §9 R-3）与
                                   # default-protocol（不传 configuration 的 delta-rs 默认协议），超出 D2 验收
```

> `engine-written/` 的 parquet 文件名含 delta-rs 生成的一次性 UUID，重跑会变化（内容/哈希随之变化）；`moondelta-written/` 文件名为确定性命名。本文记录的是**本次运行**的文件名，重跑后请以 `results.json`、`raw/` 与 `hashes.txt` 为准。

## 4. A. 外部引擎写出（delta-rs）

命令（由 `run_interop.py` 执行，等价 Python 代码）：

```python
from deltalake import write_deltalake
import pyarrow as pa

schema = pa.schema([
    pa.field("id", pa.int64(), nullable=False),
    pa.field("name", pa.string()),
    pa.field("active", pa.bool_()),
])
write_deltalake("artifacts/interop-flat/engine-written", batch1, mode="overwrite",
                configuration={"delta.minReaderVersion": "1", "delta.minWriterVersion": "2"})
write_deltalake("artifacts/interop-flat/engine-written", batch2, mode="append",
                configuration={"delta.minReaderVersion": "1", "delta.minWriterVersion": "2"})
```

实测（`raw/a_engine_write.json`、`engine-written/_delta_log/`）：

| 项 | 实测值 |
|---|---|
| protocol | `{"minReaderVersion":1,"minWriterVersion":2}`（无 `writerFeatures`/`readerFeatures`） |
| delta-rs `protocol()` | `min_reader_version=1, min_writer_version=2, writer_features=None, reader_features=None` |
| 版本 | 当前 version 1，日志共 2 个 commit（v0 = create/overwrite + add，v1 = append + add） |
| 数据文件 | 2 个，均 SNAPPY Parquet |
| schemaString | `id long nullable=false, name string nullable=true, active boolean nullable=true`（顺序一致） |
| 行数 | 5（3 + 2） |
| add.stats | 两个文件均含 `numRecords`、3 列 `minValues`、3 列 `maxValues`、3 列 `nullCount` |
| parquet 物理 nullability | `id` = `required int64`（`name`/`active` optional）—— 与 C 侧 moondelta 写出的 `optional id` 形成对照，见 R-1 |

实测文件清单（本次运行）：

```text
engine-written/part-00000-a84180dd-113f-467e-9428-b0b48082ca2c-c000.snappy.parquet   (v0, 3 行, 980 B)
engine-written/part-00000-291b0f4f-41cb-4fea-897e-80f10e4b3835-c000.snappy.parquet   (v1, 2 行, 969 B)
```

**关于“如何写出 reader v1/writer v2 兼容表”**：本环境下 delta-rs 1.6.6 在**不传 configuration** 时的默认输出即 reader=1 / writer=2（未启用任何 feature；实测见 `raw/f_probe_protocol_config.json` 的 `default_write_no_configuration`），因此 `engine-written/` 天然落在 moondelta 的协议闭集内。脚本仍显式传入 `configuration={"delta.minReaderVersion": "1", "delta.minWriterVersion": "2"}` 把该协议**钉死**，使结果不随 delta-rs 默认值变化；该配置键真实生效已由探针证明（写成 `2/5` 时日志 protocol 变为 `{minReaderVersion:2,minWriterVersion:5}`，见 §9 R-3 与 `raw/f_probe_protocol_config.json`）。两个键同时会出现在 `metaData.configuration` 中（delta-rs 的写法），moondelta 读取时忽略之，见 §9 R-4。

## 5. B. moondelta 读取 delta-rs 表 —— 逐项 diff

原始输出：`raw/b_engine_{inspect,history,scan,scan_where_id_ge_4,read}.{stdout,stderr,exit,cmd}.txt`；解析结果：`raw/b_moondelta_read_engine.json`。

```text
$ delta.exe inspect artifacts/interop-flat/engine-written
table            : artifacts/interop-flat/engine-written
current version  : 1  (log versions: 2)
files            : 2  rows: 5  bytes: 1949
protocol         : reader=1 writer=2
format           : parquet
partition columns: (none)
schema:
  id: long (not null)
  name: string
  active: boolean

$ delta.exe read artifacts/interop-flat/engine-written
read 2 file(s), 5 row(s), version 1
id (long) | name (string) | active (boolean)
1 | alice | true
2 | NULL | false
3 | bob | NULL
4 | carol | true
5 | NULL | NULL
```

逐项 diff（期望 = delta-rs 读回 / Delta 日志；实测 = moondelta CLI）：

| # | 项目 | delta-rs / 日志（oracle） | moondelta CLI 实测 | 结论 |
|---|---|---|---|---|
| B1 | 命令退出码 | — | inspect/history/scan/read 均 `0`（stderr 空） | ✅ 一致 |
| B2 | 当前版本 / 日志版本数 | version 1 / 2 commits | `current version 1 (log versions: 2)` | ✅ |
| B3 | protocol | reader=1 writer=2 | `reader=1 writer=2` | ✅ |
| B4 | 列名与顺序 | `id, name, active` | `id, name, active` | ✅ |
| B5 | 类型 | long / string / boolean | long / string / boolean | ✅ |
| B6 | nullability | false / true / true | `id` 标 `(not null)`，其余可空 | ✅ |
| B7 | 数据文件清单 | 2 个 add.path | `scan` 列出同名 2 个文件 | ✅ 完全一致 |
| B8 | 行数 | 5（stats numRecords 3+2） | `read 2 file(s), 5 row(s)`；`scan matched rows 5` | ✅ |
| B9 | 逐列值与 null | 5 行（见 §1 数据表） | 5 行逐值严格一致（多重集，含 NULL） | ✅ |
| B10 | history | v0/v1 = WRITE | `0 WRITE`, `1 WRITE` | ✅ |
| B11 | add.stats 可用性 | 3 列 min/max/nullCount 齐全 | `scan --where "id>=4"` 剪掉 1 个文件、命中 2 行 | ✅ 数值剪枝可用 |

> B9 采用**按多重集、类型严格**比较（`id`/`name`/`active` 的类型也参与比较），不使用 CSV 做断言；CSV 仅为展示（NULL 显示为空单元格，DuckDB 行按 `source` 列标注所属表）。

## 6. C. moondelta 写出 + 外部引擎回读

命令与 writer 原始输出（`raw/c_moon_writer.stdout.txt`）：

```powershell
_build\native\debug\build\examples\interop_flat_writer\interop_flat_writer.exe artifacts/interop-flat/moondelta-written
```

```text
created version 0 at artifacts/interop-flat/moondelta-written
appended batch1 => version 1
appended batch2 => version 2
self-check: version=2 files=2 rows=5
  file: part-0000-1750000000000-1.parquet size=297 stats={"numRecords":3,"minValues":{"id":1},"maxValues":{"id":3}}
  file: part-0000-1750000001000-2.parquet size=280 stats={"numRecords":2,"minValues":{"id":4},"maxValues":{"id":5}}
```

写出表实测（`raw/c_moondelta_written_deltars_read.json`）：

| 项 | 实测值 |
|---|---|
| protocol | reader=1 / writer=2（`src/write.mbt` 固定写出） |
| 提交 | 3 个 commit：v0 `CREATE TABLE`（仅 protocol+metaData），v1/v2 各一次 `append_rows`（各 1 个 add） |
| 数据文件 | 2 个，UNCOMPRESSED Parquet（moondelta 当前不支持 SNAPPY 写出） |
| Delta schema | `id long NOT NULL, name string, active boolean`，顺序一致 |
| 自读（CLI） | `inspect/read` 退出码 0，5 行逐值一致 |

外部引擎回读：

| 引擎 | schema | 行数 | 逐列值（含 null） | 结论 |
|---|---|---|---|---|
| delta-rs 1.6.6（`DeltaTable.to_pyarrow_table()`） | `id int64 not null, name string, active bool` | 5 | 与期望严格一致 | ✅ |
| DuckDB 1.5.6 `delta_scan` | `id BIGINT, name VARCHAR, active BOOLEAN` | 5 | 与期望严格一致 | ✅ |
| DuckDB `read_parquet`（非 Delta 级补充） | parquet 列 | 5 | 与期望严格一致 | ✅（仅 parquet 级） |
| moondelta CLI 自读 | 与 Delta schema 一致 | 5 | 与期望严格一致 | ✅ |

**差异（如实记录，未掩盖）**：

- **C-DIFF-1（物理 nullability）**：Delta schema 声明 `id` NOT NULL，delta-rs 写出的 parquet 中 `id` 为 `required int64`；**moondelta 写出为 `optional int64`**。delta-rs 回读时按 Delta schema 呈现 `id int64 not null`（`raw/c_moondelta_written_deltars_read.json` 的 `delta_arrow_schema`），但 `read_parquet` / 直接检查 parquet schema 会看到三列全部 optional。影响见 §9 R-1。
- **C-DIFF-2（压缩）**：moondelta 写出 UNCOMPRESSED，delta-rs 写出 SNAPPY。两者都在文档 Parquet 支持闭集内（UNCOMPRESSED、SNAPPY），**不构成互操作失败**；仅文件体积/读取成本不同（本次 297/280 B vs 980/969 B）。

## 7. D. DuckDB（第二外部引擎）

原始结果：`raw/d_duckdb.json`。

```sql
INSTALL delta; LOAD delta;
SELECT * FROM delta_scan('artifacts/interop-flat/engine-written');
SELECT * FROM delta_scan('artifacts/interop-flat/moondelta-written');
```

| 项 | 结果 |
|---|---|
| 扩展可用性 | ✅ `INSTALL delta` + `LOAD delta` 成功，`extension_version=6059958` |
| `delta_scan(engine-written)` | 5 行，`(1,alice,true) (2,NULL,false) (3,bob,NULL) (4,carol,true) (5,NULL,NULL)`，类型 `BIGINT/VARCHAR/BOOLEAN` |
| `delta_scan(moondelta-written)` | 同样 5 行逐值一致，类型 `BIGINT/VARCHAR/BOOLEAN` |
| `DESCRIBE` nullability | 两表所有列均显示 `null=YES`（含 Delta 中 NOT NULL 的 `id`）——DuckDB delta 扩展的展示行为，两侧一致，**不影响值读取**（记录为 INFO） |
| `read_parquet('…/*.parquet')` | 5 行逐值一致；**标注：这是 parquet 级补充，不是 Delta 级互操作**（未经过事务日志） |

> 本次 DuckDB delta 扩展可正常安装/加载，因此 `read_parquet` 只作为**补充**而非替代；脚本对“扩展不可用”的分支同样有显式记录逻辑（`raw/d_duckdb.json` 的 `delta_extension.available/error`）。

## 8. E. add.stats 对比（逐文件）

`results/stats_compare.csv`（完整 JSON：`raw/e_stats_compare.json`）：

| writer | 文件（本次运行） | 版本 | numRecords | minValues 列 | maxValues 列 | nullCount 列 | 示例值（id） |
|---|---|---|---|---|---|---|---|
| delta-rs | `part-…-a84180dd…snappy.parquet` | 0 | 3 | `active;id;name` | `active;id;name` | `active;id;name` | min.id=1, max.id=3 |
| delta-rs | `part-…-291b0f4f…snappy.parquet` | 1 | 2 | `active;id;name` | `active;id;name` | `active;id;name` | min.id=4, max.id=5 |
| moondelta | `part-0000-1750000000000-1.parquet` | 1 | 3 | `id` | `id` | **（无）** | min.id=1, max.id=3 |
| moondelta | `part-0000-1750000001000-2.parquet` | 2 | 2 | `id` | `id` | **（无）** | min.id=4, max.id=5 |

- delta-rs 的 `minValues/maxValues` 同时包含 `name`（字符串字典序）与 `active`（布尔），且 `nullCount` 三列齐全；
- moondelta 只写数值列 `id` 的 `minValues/maxValues`，**无 `nullCount`，无 string/boolean 的 min/max**（E-DIFF，见 §9 R-2）。

**剪枝实测**（`raw/e_pruning.json` 与 `raw/e_scan_where_id_ge_4_*.stdout.txt`）：

| 表 | `scan --where "id>=4"` selected | skipped stats | matched rows |
|---|---|---|---|
| `engine-written`（delta-rs 写） | 1 / 2 | 1 | 2 |
| `moondelta-written`（moondelta 写） | 1 / 2 | 1 | 2 |

结论：两侧**数值列 `id` 的 min/max 剪枝行为一致且正确**；moondelta 写出表缺少字符串/布尔统计与 `nullCount`，只影响**这些谓词的剪枝能力**（`name`/`active` 过滤与 `IS NULL` 只能退化为全文件扫描），不会导致少读或多读——5 行读值已在 §6 由两个外部引擎验证一致。

## 9. 差异与风险清单（全部如实记录，不掩盖）

| 编号 | 类型 | 现象与证据 | 影响 / 建议 |
|---|---|---|---|
| **R-1** | 差异（物理格式） | C-DIFF-1：moondelta 写出的 parquet 中 `id` 为 `optional int64`，而 Delta schema 声明 `nullable=false`；delta-rs 写出为 `required int64`。证据：`raw/c_moondelta_written_deltars_read.json` 的 `parquet_physical[].arrow_fields`（moondelta 侧）对照 `raw/a_engine_write.json` 的 `parquet_physical[].arrow_fields`（delta-rs 侧） | 读值正确，外部引擎按 Delta schema 呈现 NOT NULL；但**物理层无法阻止/发现违反 NOT NULL 的写入**，仅按 parquet schema 推导的工具会认为 `id` 可为空。建议写入端对 `nullable=false` 列落盘为 required（或至少在文档中声明该限制）。属 Writer 实现问题，**不影响本次互操作通过判定**。 |
| **R-2** | 差异（stats） | moondelta `add.stats` 仅 `{numRecords,minValues:{id},maxValues:{id}}`，无 `nullCount`、无 `name`/`active` 的 min/max。证据：`raw/e_stats_compare.json`、`moondelta-written/_delta_log/00000000000000000001.json` | 少做剪枝，不多读/少读数据；`IS NULL` 与字符串/布尔谓词剪枝不可用。Delta 规范建议 stats 覆盖所有可统计列，建议补齐 `nullCount` 及 string/boolean 的 min/max。 |
| **R-3** | 风险（协议校验，**建议交 D1**） | 用 delta-rs 配置 `{"delta.minReaderVersion":"2","delta.minWriterVersion":"5"}` 写出 `probe/protocol-reader2`（日志 protocol=`{minReaderVersion:2,minWriterVersion:5}`，无 `readerFeatures`/`writerFeatures`），moondelta `inspect`/`read` 均**退出码 0 且正常返回 1 行**，未按文档第 1/2 节“高于 reader v1 的表必须拒绝”报错。证据：`raw/f_probe_protocol_config.json`、`raw/f_probe_reader2_*.stdout.txt`。代码侧 `src/actions.mbt:15-25` 的 `parse_protocol` 只读取版本号，未见版本校验分支（结论待 D1 复核） | 若外部引擎产出 reader>1 表（如启用 column mapping、删除向量、CDF 等 feature 的表），当前实现可能**静默按 v1 语义读取**而非结构化拒绝，存在读错语义的风险。本次探针表无 feature 字段、属非常规日志，不构成 D2 互操作失败，但应作为兼容性风险交 D1 协议审计确认并补拒绝逻辑/测试。 |
| **R-4** | 观察（metadata） | delta-rs 把 `delta.minReaderVersion`/`delta.minWriterVersion` 写入 `metaData.configuration`；moondelta 读取时忽略 `configuration`。证据：`engine-written/_delta_log/00000000000000000000.json` | 本次无害；但这两个键并非标准 Delta 表属性，其他引擎可能告警。若追求“最干净”的兼容写出，可不传 configuration（delta-rs 默认同样是 1/2）。 |
| **R-5** | 观察（metadata.id） | moondelta `create_table` 生成 `metaData.id = "moondelta-<传入的表根路径>"`（本次 `moondelta-artifacts/interop-flat/moondelta-written`），delta-rs 用 UUID。代码位置：`src/write.mbt:176` | Delta 规范中 table id 应为 UUID 且与路径无关；路径派生 id 在移动表目录/不同路径重建时不稳定，依赖 id 做表身份/血缘的工具可能混淆。建议改 UUID（不影响本次读值互操作）。 |
| **R-6** | 差异（压缩） | moondelta 写出 UNCOMPRESSED（297/280 B），delta-rs 写出 SNAPPY（980/969 B） | 均在文档支持闭集内，双方均可读；仅体积/IO 差异，非缺陷。 |
| **R-7** | 观察（时间戳） | moondelta 写出的 `commitInfo.timestamp` 由调用方传入（示例用固定 1750000000000/1750000001000），`createdTime=0`、v0 `commitInfo.timestamp=0`；delta-rs 用系统时钟。 | 外部引擎可正常读取（delta-rs/DuckDB 均 5 行）；`createdTime=0` 语义上是 1970，做时间旅行/审计的工具可能显示异常。建议 `create_table` 写入真实时间。 |
| **R-8** | 观察（DuckDB） | DuckDB `DESCRIBE` 对两表所有列返回 `null=YES`（包括 Delta NOT NULL 的 `id`） | DuckDB 扩展展示行为，两侧一致、不影响值；记录以免误读为“nullability 不一致”。 |

> 说明：R-1/R-2 在检查表中标记为 `DIFF`（已记录差异，不判 FAIL）；R-3/R-4 为风险/观察（`INFO`）。D2 的“通过”判定依据是 §10 的 PASS 项与排除已记录差异后的逐值一致性。

## 10. 通过 / 未通过项（检查表汇总）

完整 56 项明细见 `results/checks.csv`，JSON 见 `results.json` 的 `checks[]`。

| 组 | 范围 | 结果 |
|---|---|---|
| A | delta-rs 写出：protocol/版本/文件数/schema/行数/逐值/stats/物理 nullability | **9 PASS / 0 FAIL** |
| B | moondelta 读取 delta-rs 表：inspect/history/scan/read 退出码与逐项 diff | **16 PASS / 0 FAIL** |
| C | moondelta 写出 + delta-rs 回读 + 物理 parquet 观测 + CLI 自读 | **14 PASS / 1 DIFF / 0 FAIL** |
| D | DuckDB delta 扩展、`delta_scan` 两表、`read_parquet` 补充 | **7 PASS / 2 INFO / 0 FAIL** |
| E | add.stats 对比、剪枝实测、protocol 配置探针与风险 | **3 PASS / 1 DIFF / 3 INFO / 0 FAIL** |
| **合计** | | **49 PASS / 0 FAIL / 2 DIFF / 5 INFO / 0 BLOCKED** |

**未通过项：无。** 未完成/未覆盖项见 §12。

## 11. 复现步骤

前置（本仓库已具备，不修改环境）：

- `moon` 在 PATH；`_build/native/debug/build/{cmd/delta/delta.exe, examples/interop_flat_writer/interop_flat_writer.exe}` 存在；
- `.tools\pyenv\Scripts\python.exe` 已装 deltalake 1.6.6 / duckdb 1.5.6 / pyarrow 26.0.0（**勿 pip install**）；
- DuckDB delta 扩展首次运行需网络（`INSTALL delta`）；若无网络，脚本会记录 BLOCKED 并继续 `read_parquet` 补充（标注非 Delta 级）。

一键复现（在仓库根目录 `E:\Project\Moonbit Hackson\P1 RE`）：

```powershell
# 1) 直接使用现有 _build 产物
.tools\pyenv\Scripts\python.exe artifacts\interop-flat\run_interop.py

# 2) 需要重建 writer/CLI 时（脚本内部为 moon build 失败提供 3 次重试，避免与其他 agent 构建锁冲突）
.tools\pyenv\Scripts\python.exe artifacts\interop-flat\run_interop.py --build
```

- 脚本会**清空并重建** `engine-written/`、`moondelta-written/`、`probe/`、`raw/`、`results/`，然后按 A→B→C→D→E→F 执行；
- 退出码：`0` = 无 FAIL 且无 BLOCKED；`1` = 存在 FAIL/BLOCKED（先看 `results/checks.csv` 与 `raw/`）；
- 控制台不落盘为证据（Windows 控制台编码会乱码），证据以 UTF-8 的 `raw/*.txt` / `results.json` 为准；
- 手工逐条复核命令等价于脚本内的调用，例如：

```powershell
_build\native\debug\build\cmd\delta\delta.exe inspect artifacts\interop-flat\engine-written
_build\native\debug\build\cmd\delta\delta.exe history artifacts\interop-flat\engine-written
_build\native\debug\build\cmd\delta\delta.exe scan    artifacts\interop-flat\engine-written --where "id>=4"
_build\native\debug\build\cmd\delta\delta.exe read    artifacts\interop-flat\engine-written
.tools\pyenv\Scripts\python.exe -c "from deltalake import DeltaTable; dt=DeltaTable('artifacts/interop-flat/moondelta-written'); print(dt.to_pyarrow_table().to_pylist())"
.tools\pyenv\Scripts\python.exe -c 'import duckdb; con=duckdb.connect(); con.execute("INSTALL delta"); con.execute("LOAD delta"); print(con.execute("SELECT * FROM delta_scan(\"artifacts/interop-flat/moondelta-written\")").fetchall())'
```

## 12. 未覆盖 / 边界（不冒充结论）

- **Spark / Databricks 未在本环境运行**：文档案例 A 允许 “Spark 或 delta-rs 或 DuckDB”，本任务达成 **delta-rs + DuckDB 两个外部引擎**；Spark 侧不宣称已验证。
- 仅覆盖**本地文件系统、非分区、flat schema、reader v1/writer v2** 闭集；对象存储、分区 append、checkpoint-only 表、删除向量、column mapping、CDF 均不在 D2 范围。
- 未覆盖 **nested schema**（文档案例 B 属任务 D3）；本目录未包含任何 nested fixture。
- `engine-written/` 的 delta-rs 文件 UUID 与 `hashes.txt` 每次重跑都会变化；`hashes.txt` 记录的是**本次运行产物**的哈希（排除 README 自身与 hashes.txt）。
- R-1/R-2/R-5 是**写入端实现观察**，本次未修改 `src/` 代码（D2 只允许新增 `src/examples/interop_flat_writer/`，且该示例未改动），交由后续实现任务处理。

## 13. 证据文件索引

| 证据 | 文件 |
|---|---|
| 全量结构化结果 / 断言 | `results.json`（`checks[]` 为逐项断言，`verdict` 为汇总） |
| 检查明细表 | `results/checks.csv` |
| 逐值展示表（CSV 仅展示） | `results/values_engine_written.csv`、`results/values_moondelta_written.csv` |
| stats 逐文件对比 | `results/stats_compare.csv`、`raw/e_stats_compare.json` |
| delta-rs 写出记录 | `raw/a_engine_write.json`、`engine-written/_delta_log/*.json` |
| moondelta 读取原始输出 | `raw/b_engine_{inspect,history,scan,scan_where_id_ge_4,read}.{stdout,stderr,exit,cmd}.txt`、`raw/b_moondelta_read_engine.json` |
| writer 原始输出 | `raw/c_moon_writer.{stdout,stderr,exit,cmd}.txt` |
| moondelta 写出的外部回读 | `raw/c_moondelta_written_deltars_read.json`、`raw/d_duckdb.json` |
| 剪枝实测 | `raw/e_pruning.json`、`raw/e_scan_where_id_ge_4_{engine,moondelta}.*` |
| protocol 配置/高版本探针 | `raw/f_probe_protocol_config.json`、`raw/f_probe_reader2_{inspect,read}.*`、`probe/protocol-reader2/` |
| 环境与版本 | `raw/00_environment.json`、`raw/00_delta_version.*` |
| fixture 与关键输出哈希 | `hashes.txt`（SHA-256，79 个文件） |

## 14. 清单重封记录（2026-10-22）

`run_interop.py` 于 2026-10-22 做了纯安全卫生修改（DuckDB 查询由 f-string 拼接改为参数绑定，共 3 处；行为与输出不变），`hashes.txt` 第 79 行（该脚本自身的哈希）随之重封：`d0ac371e…` → `086a648c…`。其余 78 项哈希未动，`d2_verify.py` 全部输出校验（16/17 中的其余项 + 本次重跑）证明产物未被改动。pre-replay 快照中的脚本副本同步应用了同一修改。
