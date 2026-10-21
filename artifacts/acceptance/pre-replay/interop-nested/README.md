# D3：nested 能力缺口对照（struct / array / map）

> 本目录是**能力缺口对照**，不是互操作通过证明。
> **外部引擎成功**：delta-rs 与 DuckDB 都能读取本目录生成的 nested Delta 表；
> **本项目当前拒绝**：`moondelta-core 0.1.0` 在 schema 校验阶段返回结构化 `Unsupported`，没有读出任何一行嵌套数据。
> 任何把本目录的负向用例描述成“nested 互操作通过”的写法都是错误的。

## 0. 结论摘要

- 主 fixture `engine-written/`：schema 为 `id: long, profile: struct<name:string, age:int>, tags: array<string>, attrs: map<string,string>`，共 5 行（含 `profile=NULL`、struct 子字段 null、`tags=[]`、`tags` 含 null 元素、`attrs={}`、`tags/attrs=NULL`）。
- protocol：`reader v1 / writer v2`，与文档第 4 节案例 B 的目标配置一致；delta-rs 未启用 reader/writer features。
- **外部引擎（成功）**：delta-rs 1.6.6 读回 5 行；DuckDB 1.5.6 + delta 扩展 6059958 成功；两种引擎逐字段编码一致：`True`（见 `comparison.json` 的 `oracle_cross_check`）。
- **本项目（拒绝）**：`delta inspect` 能列出 `profile: struct` / `tags: array` / `attrs: map`；`delta read` 返回 `unsupported: nested column 'profile' (type=struct) is not supported by the parquet reader; only flat schemas are readable`，且在打开 Parquet 之前发生。
- **错误策略问题**：`delta read` 失败时 CLI 退出码仍为 `0`（应为非 0），错误写 stdout 而非 stderr。
- **七项门槛统计**：已完成 1 项 / 部分完成 0 项 / 未完成 6 项。

## 1. 方法与判定口径

1. 用 delta-rs `write_deltalake` 按 reader v1 / writer v2、JSON log + Parquet、本地文件系统写出 nested fixture；
2. 主 oracle 用 delta-rs `to_pyarrow_table()` 读回，导出完整 schema（含递归类型树）与逐字段值；
   第二 oracle 用 DuckDB `INSTALL/LOAD delta` + `delta_scan` 读同一张表；
3. 用 `delta.exe inspect/history/read` 对照本项目，保存 stdout/stderr/退出码原文；
4. 额外构造“Delta schema 是 flat、Parquet 物理层是 LIST”的负向表，以及 checkpoint / checkpoint-only 变体；
5. 逐条核对文档第 3 节七项门槛与第 4 节案例 B 的预期。

判定口径：

- **外部引擎成功** 只证明 fixture 与 oracle 成立，不证明本项目兼容；
- **本项目拒绝** 只要不是“返回 0 行/部分错误数据/静默忽略”，就属于“安全失败”；
  但按文档要求，只做“识别后报错”**不算完成**第 3 节门槛 7，也不得写成互操作通过；
- CSV 只用于展示，嵌套值用 `{}`/`[]`/`NULL` 书写，不能替代 JSON 里的逐字段断言。

## 2. 环境与运行

```text
OS            : Windows-11-10.0.28000-SP0
Python        : 3.12.14
deltalake     : 1.6.6
pyarrow       : 26.0.0
duckdb        : 1.5.6 (delta extension: 6059958)
pandas        : 3.0.6
moondelta CLI : _build/native/debug/build/cmd/delta/delta.exe (moondelta-core 0.1.0)
```

一键复现（在仓库根目录执行）：

```powershell
.tools\pyenv\Scripts\python.exe artifacts\interop-nested\run_nested.py
```

脚本会重建本目录所有 fixture、oracle、`raw/` 输出，并重新生成本 README、`comparison.md`、`fixture-manifest.json`。单独运行某条 CLI 对照的原始命令见 `raw/*.cmd.txt`，例如：

```powershell
_build\native\debug\build\cmd\delta\delta.exe inspect artifacts\interop-nested\engine-written
_build\native\debug\build\cmd\delta\delta.exe history artifacts\interop-nested\engine-written
_build\native\debug\build\cmd\delta\delta.exe read    artifacts\interop-nested\engine-written
_build\native\debug\build\cmd\delta\delta.exe scan    artifacts\interop-nested\engine-written --where id>=99
_build\native\debug\build\cmd\delta\delta.exe scan    artifacts\interop-nested\engine-written --where profile>=1
```

DuckDB delta 扩展首次运行可能需要网络执行 `INSTALL delta`；本机已安装（版本见上），脚本优先 `LOAD delta`，失败才回退 `INSTALL`。若扩展不可用，脚本会记录原始错误并用 `read_parquet` 兜底，同时明确标注“非 Delta 级”，不会伪装成 `delta_scan` 成功。

> 可重放性说明：fixture 是现场写出的，`add.path` 含 UUID、commitInfo 含墙钟时间，因此每次重跑后数据文件/日志的 SHA-256 会变化；表中的哈希是**本次运行产物**的哈希，重跑后 manifest 与 README 自动更新。schema、protocol、行值、错误文本是稳定可复现的。

## 3. Fixture 清单与协议配置

| fixture | 写入方 | protocol | version | 数据文件数 |
|---|---|---|---|---|
| `engine-written/` | delta-rs (Python deltalake.write_deltalake) | reader=1 writer=2 | 0 | 1 |
| `kind-struct-only/` | delta-rs (Python deltalake.write_deltalake) | reader=1 writer=2 | 0 | 1 |
| `kind-array-only/` | delta-rs (Python deltalake.write_deltalake) | reader=1 writer=2 | 0 | 1 |
| `kind-map-only/` | delta-rs (Python deltalake.write_deltalake) | reader=1 writer=2 | 0 | 1 |
| `checkpoint-nested/` | delta-rs write_deltalake + delta-rs create_checkpoint() | reader=1 writer=2 | 0 | 1 |
| `checkpoint-only-nested/` | checkpoint-nested 的副本，删除了全部 JSON 日志（checkpoint-only 变体） | reader=- writer=- | - | 1 |
| `negative-physical-nested/` | 手写 JSON 日志 + 复制 fixtures/parquet/list_columns.parquet | reader=1 writer=2 | 0 | 0 |
| `write-attempt-nested/` | moondelta-core `delta create`（nested schemaString，无数据） | reader=1 writer=2 | 0 | 0 |

### 3.1 主 fixture `engine-written/`

`metaData.schemaString` 原文（来自 `_delta_log/00000000000000000000.json`）：

```json
{"type":"struct","fields":[{"name":"id","type":"long","nullable":true,"metadata":{}},{"name":"profile","type":{"type":"struct","fields":[{"name":"name","type":"string","nullable":true,"metadata":{}},{"name":"age","type":"integer","nullable":true,"metadata":{}}]},"nullable":true,"metadata":{}},{"name":"tags","type":{"type":"array","elementType":"string","containsNull":true},"nullable":true,"metadata":{}},{"name":"attrs","type":{"type":"map","keyType":"string","valueType":"string","valueContainsNull":true},"nullable":true,"metadata":{}}]}
```

protocol（同一日志）：

```json
{"minReaderVersion": 1, "minWriterVersion": 2}
```

- 数据文件：`part-00000-b9863671-bdd7-45b5-83b1-ebe4eba783dc-c000.snappy.parquet`（2279 B）
- `add.stats` 原文：`{"numRecords":5,"minValues":{"profile":{"age":30,"name":"alice"},"id":1},"maxValues":{"id":5,"profile":{"name":"bob","age":42}},"nullCount":{"profile":{"name":3,"age":3},"id":0}}`

行值（来自 delta-rs oracle；CSV 同内容，仅展示）：

| id | profile | tags | attrs |
|---|---|---|---|
| 1 | {name: alice, age: 30} | [red, green] | {k1: v1, k2: v2} |
| 2 | NULL | [] | {} |
| 3 | {name: NULL, age: NULL} | NULL | {k: v} |
| 4 | {name: bob, age: NULL} | [blue, NULL] | {} |
| 5 | {name: NULL, age: 42} | [] | NULL |

边界覆盖（由 oracle 逐字段编码判定，脚本启动时断言，不满足会直接失败）：

| 边界 | 行 id |
|---|---|
| `profile = NULL` | 2 |
| `profile` 非 null、name/age 均 null | 3 |
| `profile` 非 null、name 有值 age null | 4 |
| `tags = []` | 2, 5 |
| `tags = NULL` | 3 |
| `tags` 含 null 元素 | 4 |
| `attrs = {}` | 2, 4 |
| `attrs = NULL` | 5 |

### 3.2 单 kind fixture 与 checkpoint / 负向 fixture

- `kind-struct-only/`：delta-rs (Python deltalake.write_deltalake)；schemaString=`{"type":"struct","fields":[{"name":"id","type":"long","nullable":true,"metadata":{}},{"name":"profile","type":{"type":"struct","fields":[{"name":"name","type":"string","nullable":true,"metadata":{}},{"name":"age","type":"integer","nullable":true,"metadata":{}}]},"nullable":true,"metadata":{}}]}`；1 个 add。
- `kind-array-only/`：delta-rs (Python deltalake.write_deltalake)；schemaString=`{"type":"struct","fields":[{"name":"id","type":"long","nullable":true,"metadata":{}},{"name":"tags","type":{"type":"array","elementType":"string","containsNull":true},"nullable":true,"metadata":{}}]}`；1 个 add。
- `kind-map-only/`：delta-rs (Python deltalake.write_deltalake)；schemaString=`{"type":"struct","fields":[{"name":"id","type":"long","nullable":true,"metadata":{}},{"name":"attrs","type":{"type":"map","keyType":"string","valueType":"string","valueContainsNull":true},"nullable":true,"metadata":{}}]}`；1 个 add。
- `checkpoint-nested/`：`engine-written/` 的副本 + delta-rs `create_checkpoint()`；checkpoint 文件 `00000000000000000000.checkpoint.parquet`，`_last_checkpoint`={'version': 0, 'size': 3, 'sizeInBytes': 14072, 'numOfAddFiles': 1}。
- `checkpoint-only-nested/`：只保留 checkpoint 与 `_last_checkpoint`、删除全部 JSON 日志，用于验证 checkpoint-only 路径。
- `negative-physical-nested/`：手写 flat schema 的 JSON 日志，指向 `fixtures/parquet/list_columns.parquet`（SHA-256 `5988ab91b6cb7efa7bf6a77f789b40929212280519be6c9daad56e01d5ceb218`，Apache-2.0，来自 `apache/parquet-testing`）。
- `write-attempt-nested/`：用本项目 `delta create` 写入 nested schemaString 的空表（补充观察，见门槛 7）。

## 4. Oracle：外部引擎结果

### 4.1 主 oracle：delta-rs

- 引擎：delta-rs (Python `deltalake`) 1.6.6，`DeltaTable.to_pyarrow_table()`；
- 表：`artifacts/interop-nested/engine-written`，version=0，行数=5；
- protocol：reader=1 / writer=2，readerFeatures=None，writerFeatures=None；
- `delta_rs` 导出的 schema JSON 与日志 `metaData.schemaString` 等价：`True`；
- 全量 schema 树、逐行逐字段编码（null 单独编码为 `{"kind":"null"}`，空 list 为 `{"kind":"list","items":[]}`，空 map 为 `{"kind":"map","entries":[]}`）见 `oracle-delta-rs.json`；展示版 CSV 见 `oracle-delta-rs.csv`。

### 4.2 第二 oracle：DuckDB

- DuckDB 1.5.6，delta 扩展 6059958，`delta_scan` 状态：**成功**；
- 行数=5；全量 schema 树与逐字段值见 `oracle-duckdb.json`；
- 与 delta-rs 对拍：row_count_equal=`True`，id_sets_equal=`True`，rows_equal=`True`，differing_ids=[]。

## 5. moondelta 对照（原始输出与退出码）

每个命令的 stdout / stderr / 退出码分别保存在独立文件（`raw/<table>__<subcmd>.stdout.txt` 等）。命令本身也写进 `raw/*.cmd.txt`。

### 5.1 主 fixture

`inspect`（exit=0）：

```text
table            : artifacts/interop-nested/engine-written
current version  : 0  (log versions: 1)
files            : 1  rows: 5  bytes: 2279
protocol         : reader=1 writer=2
format           : parquet
partition columns: (none)
schema:
  id: long
  profile: struct
  tags: array
  attrs: map
```

`read`（exit=0，stdout；stderr 为空：True）：

```text
error: unsupported: nested column 'profile' (type=struct) is not supported by the parquet reader; only flat schemas are readable
```

- 结构化错误原文：`unsupported: nested column 'profile' (type=struct) is not supported by the parquet reader; only flat schemas are readable`
- 错误包含的字段路径：`profile`；nested kind：`struct`；是否含深层路径：`False`
- 是否返回假成功/0 行：fake_success=`False`，returns_zero_rows=`False`，打印数据行数=`0`
- CLI 失败退出码非 0：`False`（实际 0）

`history`（exit=0）：

```text
version | timestamp     | operation
--------+---------------+----------
0 | 1792499081132 | WRITE
```

`scan` 补充观察（只做文件级规划，不读数据）：

| 命令 | selected files | skipped stats | matched rows | 结论 |
|---|---|---|---|---|
| `scan`（无过滤） | 1 | 0 | 5 | 计划层可用，不代表数据可读 |
| `scan --where id>=99` | 0 | 1 | 0 | flat 列 min/max 剪枝生效（全部剪掉） |
| `scan --where profile>=1` | 1 | 0 | 5 | 嵌套 stats 是对象，`json_as_double` 返回 None → 不剪枝（安全，但没有任何嵌套剪枝能力） |

注意：`scan` 只输出文件级计划，不读取任何行；不能把 `scan` 成功当作 nested 读取成功。

### 5.2 单 kind fixture：逐类错误

- `kind-struct-only`（struct）：`unsupported: nested column 'profile' (type=struct) is not supported by the parquet reader; only flat schemas are readable`（exit=0）
- `kind-array-only`（array）：`unsupported: nested column 'tags' (type=array) is not supported by the parquet reader; only flat schemas are readable`（exit=0）
- `kind-map-only`（map）：`unsupported: nested column 'attrs' (type=map) is not supported by the parquet reader; only flat schemas are readable`（exit=0）

这证明三类 nested kind 都被识别并拒绝；因为主 fixture 的拒绝是 fail-fast（第一个嵌套列即返回），单 kind fixture 用于分别取得 struct / array / map 的原始错误文本。

### 5.3 checkpoint 变体

- `checkpoint-nested/`：`inspect` exit=0，能识别 checkpoint（`last checkpoint`/note），但 `read` 仍因 nested schema 拒绝（`unsupported: nested column 'profile' (type=struct) is not supported by the parquet reader; only flat schemas are readable`）。delta-rs 对该表读回 5 行（status=ok）。
- `checkpoint-only-nested/`：**外部引擎可从 checkpoint 重建快照**：delta-rs status=ok、读回 5 行；DuckDB delta_scan status=ok。
  - delta-rs 从 checkpoint-only 变体读回的行与主表逐字段一致：rows_equal=`True`（见 `comparison.json` 的 `checkpoint_only_cross_check`）。
  - 本项目却无法读取：`inspect` exit=0，原始输出 `error: not found: table 'artifacts/interop-nested/checkpoint-only-nested' has no log entries`；根因是不解析 checkpoint Parquet，只把 checkpoint 当提示。

### 5.4 负向用例：Delta schema 是 flat、Parquet 物理层是 LIST

- `negative-physical-nested/` 的 `delta inspect` 把列报告为 `int64_list: long`、`utf8_list: string`（schema 是 flat）；
- `delta read` 原始错误：`unsupported: cannot read 'data/list_columns.parquet': Repeated columns are not supported yet`（exit=0）；
- 错误来自 Parquet 读取层（`data/list_columns.parquet` → `Repeated columns are not supported yet`），不是 schema 阶段；
- 外部引擎 delta-rs 对该负向表的读取同样失败：`ArrowNotImplementedError: Unsupported cast from list<item: int64> to int64 using function cast_int64`；**该表是伪造/不自洽的表，不用于互操作结论**。
- DuckDB 对该负向表的 `delta_scan` 状态：`failed`（首行错误：`ConversionException: Conversion Error: Error while reading file "E:/Project/Moonbit Hackson/P1 RE/artifacts/interop-nested/negative-physical-nested/data/list_columns.parquet": failed to cast column "int64_list" from type BIGINT[] to BIGINT: : Unimplemented type for cast (BIGINT[] -> BIGINT)`；完整错误见 `negative-physical-nested.json`）；同样不作为互操作基线。

## 6. 文档第 4 节案例 B 预期核对

| 文档预期 | 结果 | 证据 |
|---|---|---|
| 能读取并报告 Delta schema 中的 nested 类型 | 通过（但不完整） | `delta inspect` 输出 `profile: struct`、`tags: array`、`attrs: map`；未显示 struct 子字段 name/age。 |
| 在进入 Parquet 数据读取前返回 Unsupported，错误包含字段路径和 nested kind | 通过（字段路径仅为顶层列名） | `unsupported: nested column 'profile' (type=struct) is not supported by the parquet reader; only flat schemas are readable`（文件 raw/engine-written__read.stdout.txt）；错误发生在打开 Parquet 之前（ensure_readable_schema），但路径只有 `profile`，没有 `profile.age` 这类深层路径。 |
| 不得返回 0 行 | 通过 | read 未打印任何 `read N file(s), M row(s)` 成功行，也没有数据行；只输出 error。 |
| 不得把 array 当 string | 通过（拒绝） | schema 阶段直接拒绝，未进入列值解码；`kind-array-only` 用例给出 `nested column 'tags' (type=array)`。 |
| 不得丢弃 struct 子字段 | 数据层通过（拒绝），识别层已丢失 | 数据读取被拒绝；但 `src/schema.mbt` 的 `NestedType(kind~)` 只保留 kind，`DeltaField` 不保存 struct 子字段，`inspect` 无法打印 name/age。 |
| 不得静默忽略 map | 通过（拒绝） | `kind-map-only` 用例给出 `nested column 'attrs' (type=map)`。 |
| CLI 失败时退出码非 0（任务补充检查） | 不通过 | read 失败的退出码为 0；`fail()` 只打印 `error: ...` 后正常返回，`main` 无失败退出路径。 |
| 结构化错误写到 stderr（任务补充检查） | 不通过 | read 的 stderr 为空（raw/engine-written__read.stderr.txt），错误在 stdout。 |

结论：本项目**没有**返回 0 行、没有把 array 当 string、没有静默忽略 map、也没有读出部分错误数据；拒绝是安全的。但“识别后报错”按文档第 3 节门槛 7 明确**不算完成**，且退出码/错误通道存在问题。

## 7. 第 3 节七项门槛：缺口清单

统计：**已完成 1 项 / 部分完成 0 项 / 未完成 6 项**。

- 已完成（证据/对照门槛）：门槛 6；注意门槛 6 是“fixture/边界/外部引擎对照”证据门槛，完成它**不代表**本项目能读 nested。
- 部分完成：无；
- 未完成（实现门槛）：门槛 1, 2, 3, 4, 5, 7。

### 门槛 1：Parquet definition/repetition level，正确处理 struct、list（标准 3-level LIST）和 map 的 null、empty、重复元素与嵌套组合

**当前状态**：未完成

**证据**：
  - `raw/engine-written__read.stdout.txt`：本项目在 schema 校验阶段就拒绝，根本没有进入 Parquet 解码；
  - `raw/negative-physical-nested__read.stdout.txt`：当 Delta schema 伪装成 flat、物理层是 repeated LIST 时，底层 `mizchi/parquet` 报 `Repeated columns are not supported yet`，说明连 list 的 repetition level 都不支持；
  - `src/vendor/parquet/` 为移植的 flat reader，无 Dremel definition/repetition level 解码。

**完成所需工作**：替换/扩展 Parquet 读取层，实现 definition/repetition level、3-level LIST / MAP 的 null-empty-空集合语义，并用 `engine-written`、`kind-*`、`negative-physical-nested` 做正负回归。

### 门槛 2：Delta schema JSON 与 Parquet schema 双向映射，保留 nullable、字段顺序、字段名和嵌套路径

**当前状态**：未完成

**证据**：
  - `src/schema.mbt:36-73`：`parse_type_node` 对 struct/array/map 只返回 `NestedType(kind)`，丢弃 `fields`/`elementType`/`keyType`/`valueType`；`DeltaField`（`src/types.mbt:23-27`）没有子字段；
  - `raw/engine-written__inspect.stdout.txt` 仅打印 `profile: struct`、`tags: array`、`attrs: map`，无子字段/嵌套路径；
  - fixture `engine-written/_delta_log/00000000000000000000.json` 的 `metaData.schemaString` 保留完整嵌套 JSON，但解析后信息丢失；写端 `is_writable_type`（`src/schema.mbt:135-145`）排除 nested，无 Parquet schema 生成映射。

**完成所需工作**：把 Delta 类型建模为可递归结构（struct fields / array element / map key-value），并实现 Delta↔Arrow/Parquet schema 双向转换（nullable、字段顺序、嵌套路径、map valueContainsNull、list containsNull）。

### 门槛 3：add.stats 对嵌套字段的缺失、null 和 min/max 不能被当作 flat 值误用；无法安全剪枝时必须放弃剪枝

**当前状态**：未完成

**证据**：
  - `fixture-manifest.json` 的 `engine-written.adds[0].stats_json` 含嵌套 `minValues.profile`、`nullCount.profile.age/name`，delta-rs 对 tags/attrs 完全省略统计；
  - `src/snapshot.mbt:59-70` 只读取 `numRecords`；`src/scan.mbt:54-94` 的 min/max 只接受标量（`json_as_double`），遇到嵌套对象返回 None → 不剪枝；
  - `raw/engine-written__scan_where_profile_1.stdout.txt` 显示 `skipped stats: 0`、选中 1 个文件（未误用为浮点）；但也没有任何嵌套字段的语义化剪枝能力；`nullCount` 被整体忽略。

**完成所需工作**：定义嵌套 stats 的读取模型（按字段路径解析 min/max/nullCount），实现“无法安全剪枝就放弃”的显式判定，并补 stats 缺失/部分缺失/嵌套 null 的测试；不能把 struct 对象当标量比较。

### 门槛 4：checkpoint 中的嵌套 action schema 可解析，能从 checkpoint 加载快照，并与 JSON 回放快照逐字段一致

**当前状态**：未完成

**证据**：
  - `checkpoint-nested.json`：delta-rs 生成的 `00000000000000000000.checkpoint.parquet` 中，`metaData.schemaString` 内含完整 nested schema，`add.stats` 字符串内含嵌套 `minValues`/`nullCount`，checkpoint 本身是 struct<add, remove, metaData, protocol, txn, ...> 的嵌套 action 行式快照；
  - `src/log.mbt:99-129`：只解析 `_last_checkpoint` 的 version/size/parts 并检测 `.checkpoint.parquet` 是否存在，不解析 checkpoint Parquet；
  - `oracle-delta-rs.json` / `comparison.json` 的 `checkpoint_only_cross_check`：delta-rs 能从 `checkpoint-only-nested/`（已删除全部 JSON 日志）读回与主表逐字段一致的 5 行，证明 checkpoint 本身完整可用；而本项目同一张表直接 `not found`（`raw/checkpoint-only-nested__inspect.stdout.txt`）。

**完成所需工作**：实现 checkpoint Parquet 读取（nested action struct schema）、按版本合并 checkpoint + 后续 JSON，并对 nested 表做 checkpoint 快照与 JSON 回放逐字段比对。

### 门槛 5：外部引擎写入和读取对拍：Spark/Databricks、delta-rs 或 DuckDB 至少两种，分别覆盖 flat 与 nested 表

**当前状态**：未完成

**证据**：
  - nested 侧外部引擎已完成两种：delta-rs 1.6.6（写 + 读回）与 DuckDB 1.5.6 + delta 扩展（版本见 `oracle-duckdb.json`），两种引擎对 `engine-written` 的 5 行逐字段编码一致（见 `oracle-delta-rs.json` / `oracle-duckdb.json` 与 `comparison.json` 的 `oracle_cross_check`）；
  - 但“对拍”的本项目一侧缺失：本项目读不出任何一行嵌套数据（只返回 Unsupported）；
  - flat 对拍属于任务 D2（`artifacts/interop-flat/`），不在本目录结论内。

**完成所需工作**：在本项目实现 nested 读取后，加入 delta-rs + DuckDB 双向对拍（写后读回、读后逐字段 diff）；flat 部分沿用 D2。

### 门槛 6：对 nested struct、array/list、map 各提供至少一个正向 fixture、一个 null/empty 边界用例、一个外部引擎结果对照

**当前状态**：已完成

**证据**：
  - `engine-written/` 同时包含 struct/array/map，另有 `kind-struct-only/`、`kind-array-only/`、`kind-map-only/` 三个单一 kind 的合法 nested fixture（均为 delta-rs 写出的真实嵌套 Delta 表）；
  - `fixture-manifest.json` 的 `boundary_coverage` 证明覆盖 profile=NULL、struct 子字段全 null/age null、tags=[]/NULL/含 null 元素、attrs={}/NULL；
  - 外部引擎对照：delta-rs 与 DuckDB 均成功读回并给出逐字段编码（oracle 文件），且两引擎结果一致；
  - 注意：本门槛是“fixture/证据”门槛，已完成仅表示证据已交付；它不表示本项目可读 nested——本项目读取仍被门槛 1/2/7 阻塞（见负向对照）。

**完成所需工作**：fixture/边界/外部引擎对照已交付，本条无剩余证据工作；待本项目实现 nested 读取后，把本目录从“拒绝对照”升级为“项目↔外部引擎正向断言”（属于门槛 5/7 的验收扩展）。

### 门槛 7：写入端生成的 nested Parquet 必须能被外部引擎读回；外部引擎生成的 nested Delta 必须能被本项目读回（只做“识别后报错”不算完成）

**当前状态**：未完成

**证据**：
  - 读方向：外部引擎生成的 `engine-written` 被本项目 `read` 拒绝（`raw/engine-written__read.stdout.txt`）；
  - 写方向：`src/schema.mbt:135-145` 的 `is_writable_type` 排除 nested；`src/write.mbt:45-58` 的 `parquet_type_of` 只映射 int/long/string/binary/boolean/double，append 无法写 nested；
  - `write-attempt-nested.json`（补充观察）：`delta create` 甚至不校验 schema 可写性，会生成一张metadata 声称 nested、实际无法 append 且自身无法 read 的空表——这也不等于“写入支持”。

**完成所需工作**：实现 nested 写入（Parquet writer 的 struct/list/map 编码、Delta schema 生成、add.stats），写出后交给 delta-rs/DuckDB 读回逐字段对拍；同时让 `create_table` 对不可写 schema 明确拒绝。

## 8. 补充发现（不属于新增门槛，但影响错误策略/验收口径）

1. **CLI 失败退出码为 0**：`src/cmd/delta/main.mbt:44-46` 的 `fail()` 只 `println("error: ...")` 后返回，`main` 没有失败退出路径；脚本对所有 `read` 失败的 exit code 都有原文记录。
2. **错误写 stdout**：`fail()` 用 `println`，stderr 为空；脚本/CI 不能只靠 stderr 判失败。
3. **`delta create` 接受 nested schema**：`src/write.mbt:165-185` 不校验 schema 可写性，`write-attempt-nested/` 就是这样一张“metadata 声称 nested、实际无法 append、自己也读不了”的表；建议 create 阶段就按 `is_writable_type` 拒绝。
4. **`scan` 在 nested 表上会成功**：它是计划层 API（只读 add/metadata），不读数据、不校验 schema；这本身不是错误，但验收时不能拿 `scan` 成功冒充读取成功。
5. **inspect 丢弃子字段**：`inspect` 只打印 `profile: struct`，无法显示 `name/age`，根因是 `NestedType(kind)` 不保存子结构（门槛 2）。

## 9. 证据索引与完整性

### 9.1 文件索引

- `run_nested.py`：一键复现脚本（建 fixture → oracle → moondelta → 生成结论）；
- `oracle-delta-rs.json`：主 oracle，完整 schema 树 + 逐行逐字段编码；
- `oracle-delta-rs.csv`：主 oracle 展示版（仅展示，不替代 JSON 断言）；
- `oracle-duckdb.json`：DuckDB 第二 oracle（含扩展版本、失败原文/兜底标注）；
- `checkpoint-nested.json`：checkpoint 内部 action schema 与嵌套 stats 探针；
- `negative-physical-nested.json`：负向 fixture 说明与外部引擎尝试记录；
- `write-attempt-nested.json`：本项目 `delta create` 写入 nested schema 的补充观察；
- `fixture-manifest.json`：fixture 描述、边界覆盖、全部文件 SHA-256；
- `comparison.json` / `comparison.md`：机器可读/人读对比结论；
- `raw/`：114 个原始文件（每条命令的 stdout、stderr、exit、cmd）；
- README.md：本报告。

### 9.2 SHA-256（本次运行产物）

| 文件 | 字节 | SHA-256 |
|---|---|---|
| `artifacts/interop-nested/engine-written/_delta_log/00000000000000000000.json` | 1718 | `92cd385fab287a9b4e2f89a5e8dbbcac62db8ff2e30a1bb41db81d4475e74b3c` |
| `artifacts/interop-nested/engine-written/part-00000-b9863671-bdd7-45b5-83b1-ebe4eba783dc-c000.snappy.parquet` | 2279 | `fc13bcbeaeda2a0c15916fe932f4769954e96719a487e35252f9bcfda43471eb` |
| `artifacts/interop-nested/kind-struct-only/_delta_log/00000000000000000000.json` | 1429 | `7da6aff497854ed1490e824f9d0ecd578d0ab2873760136d9d2feeba89ec22e5` |
| `artifacts/interop-nested/kind-struct-only/part-00000-c84eeadc-6a6c-4dbb-8600-70a2a2f409cb-c000.snappy.parquet` | 1139 | `07c5199eeda7d54ce1fba341794eb50629ec5d7d470a28dcc01ac700ffd959ab` |
| `artifacts/interop-nested/kind-array-only/_delta_log/00000000000000000000.json` | 1189 | `e129190bdbd3b2156f418f5fda84771226999f147a3e3e5b91244c9c06da09e4` |
| `artifacts/interop-nested/kind-array-only/part-00000-25e1b114-9d7d-452e-b9cf-fd6797f9e5d4-c000.snappy.parquet` | 887 | `e2973d53112024d8ff5ccc0069d3c7e3e05776f45e897272220ee990b8c9cfae` |
| `artifacts/interop-nested/kind-map-only/_delta_log/00000000000000000000.json` | 1215 | `212943c5279bf63f1e1950c619c067406f13b4f20819dd5cd6f352521aa41a12` |
| `artifacts/interop-nested/kind-map-only/part-00000-85a8feb6-1b52-4a8e-aa5c-0b2cd50acef9-c000.snappy.parquet` | 1244 | `6037683051b88d0da55b32f95a1a766357b2bf21898fd46fbe3dc208981779e2` |
| `artifacts/interop-nested/checkpoint-nested/_delta_log/00000000000000000000.checkpoint.parquet` | 14072 | `0afc46760af210f48a4b37a332846161df6f417d5e756ecd3664b311aa6f1100` |
| `artifacts/interop-nested/checkpoint-nested/_delta_log/00000000000000000000.json` | 1718 | `92cd385fab287a9b4e2f89a5e8dbbcac62db8ff2e30a1bb41db81d4475e74b3c` |
| `artifacts/interop-nested/checkpoint-nested/_delta_log/_last_checkpoint` | 61 | `49e8d8cc29507cc9c178ea9e898e4b3338922eb5cd7ff0cec2dd3998756dd8c4` |
| `artifacts/interop-nested/checkpoint-nested/part-00000-b9863671-bdd7-45b5-83b1-ebe4eba783dc-c000.snappy.parquet` | 2279 | `fc13bcbeaeda2a0c15916fe932f4769954e96719a487e35252f9bcfda43471eb` |
| `artifacts/interop-nested/checkpoint-only-nested/_delta_log/00000000000000000000.checkpoint.parquet` | 14072 | `0afc46760af210f48a4b37a332846161df6f417d5e756ecd3664b311aa6f1100` |
| `artifacts/interop-nested/checkpoint-only-nested/_delta_log/_last_checkpoint` | 61 | `49e8d8cc29507cc9c178ea9e898e4b3338922eb5cd7ff0cec2dd3998756dd8c4` |
| `artifacts/interop-nested/checkpoint-only-nested/part-00000-b9863671-bdd7-45b5-83b1-ebe4eba783dc-c000.snappy.parquet` | 2279 | `fc13bcbeaeda2a0c15916fe932f4769954e96719a487e35252f9bcfda43471eb` |
| `artifacts/interop-nested/negative-physical-nested/_delta_log/00000000000000000000.json` | 708 | `c88247ce400eec7ded2a46c1f6e38d7b9ec259c2af2a6372bec117685687e88e` |
| `artifacts/interop-nested/negative-physical-nested/data/list_columns.parquet` | 2526 | `5988ab91b6cb7efa7bf6a77f789b40929212280519be6c9daad56e01d5ceb218` |
| `artifacts/interop-nested/write-attempt-nested/_delta_log/00000000000000000000.json` | 774 | `1298c7bbf0e31ba6d2aba860fb461928a5d08859b47ad39f9a3b21860625f559` |
| `fixtures/parquet/list_columns.parquet` | 2526 | `5988ab91b6cb7efa7bf6a77f789b40929212280519be6c9daad56e01d5ceb218` |

## 10. 结论

1. **fixture 成立**：同一 reader v1 / writer v2 配置下，delta-rs 写出了包含 struct/array/map 的 nested Delta 表，覆盖文档案例 B 要求的 null/empty 边界；两种外部引擎都能读取，且逐字段一致。
2. **外部引擎成功 ≠ 本项目兼容**：`moondelta-core 0.1.0` 对同一张表 `read` 返回结构化 `Unsupported`，没有读出任何嵌套数据；按文档第 3 节门槛 7，这**不是**互操作通过。
3. **拒绝是安全的，但不完整**：没有 0 行假成功、没有类型混淆、没有静默忽略；但 inspect 丢子字段、错误路径只有顶层列名、CLI 失败退出码为 0、错误写 stdout。
4. **门槛状态**：第 3 节七项门槛当前已完成 1 项（6）、部分完成 0 项（无）、未完成 6 项（1, 2, 3, 4, 5, 7）；其中已完成的门槛 6 只是 fixture/证据门槛，**不改变**“本项目拒绝 nested”的结论；在完成嵌套读写与 checkpoint 恢复前，只能使用“flat 子集兼容、nested 明确拒绝”的表述。

