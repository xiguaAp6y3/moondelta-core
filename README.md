# moondelta-core

[![CI](https://github.com/xiguaAp6y3/moondelta-core/actions/workflows/ci.yml/badge.svg)](https://github.com/xiguaAp6y3/moondelta-core/actions/workflows/ci.yml)

MoonBit 原生的 **Delta Lake 表格式内核**：回放 `_delta_log` 事务日志、构建快照、时间旅行、分区/统计剪枝的扫描规划，读取数据文件，并在受支持的列类型上 append 提交新版本。

> 生态里已经有 Parquet 编解码、DataFrame、对 Parquet 跑 SQL 的查询引擎、Iceberg 只读——缺的是"带事务日志的表"这一层。本项目补这一层。
>
> 参赛申报书：[申报书.md](申报书.md) ｜ 第三方声明：[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
>
> 已发布：[mooncakes.io/docs/xiguaAp6y3/moondelta-core](https://mooncakes.io/docs/xiguaAp6y3/moondelta-core)（0.3.0；空工程安装日志见 `artifacts/acceptance/mooncakes-0.3.0/`）。当前源码另含未发布的 checkpoint/路径/写入安全加固，安装 registry 版时不包含这些修复。

## 状态

0.3.0 及后续未发布加固（M1 协议基线 → M2 嵌套读取 → M3 checkpoint 恢复 → M4 嵌套写入；能力口径为**已验证子集**，不是任意标准 Delta 表的完整兼容）：

-  日志解析与回放：`protocol` / `metaData` / `add` / `remove` / `commitInfo`，`txn` 识别，`cdc` / `domainMetadata` 显式忽略并在输出中提示，未知 action 结构化拒绝，`_last_checkpoint` 识别
-  协议范围（0.2.0 起强制）：只读 `minReaderVersion = 1` 的表——reader v2/v3 与 `readerFeatures` 拒绝并说明原因（column mapping / 删除向量 / v2 checkpoint 为高版本特性）；JSON 与 checkpoint 的 `add.deletionVector` 均拒绝；日志文件名严格按 20 位校验；`add.path` 与 `remove.path` 统一 percent-decoding；写出到 `minReaderVersion = 1, minWriterVersion = 2`（writer > 2 或非空 `writerFeatures` 的表拒绝 append）；日志从非 0 起点/缺版本明确拒绝；**M3 起日志被截断 / checkpoint-only 的表从最新单文件 checkpoint 恢复基底并叠加其后 JSON commit（不再静默读旧快照；多部分 checkpoint 显式拒绝）**
-  快照、版本历史、时间旅行（`snapshot_at`）
-  扫描规划：分区等值过滤 + 文件级 min/max 统计剪枝（stats 损坏显式报错，不再静默返 0）
-  读取：flat schema（含 byte/short）+ UNCOMPRESSED/SNAPPY；**M2 起支持嵌套读取**、**M4 起支持嵌套写入**——`struct` / 标准 3-level `list` / `map`（含 NULL、空集合、null 元素/子字段），读写均与 delta-rs/DuckDB 逐值对拍一致（`artifacts/acceptance/m2|m4/`）
-  写入：`create_table`（已存在的表拒绝）+ `append_rows`（Int32/Int64/String/Binary/Boolean/Double；校验列名/类型/NOT NULL；数据文件与 commit 均为 put-if-absent）；分区列读取时按 `partitionValues` 补齐
-  CLI：`inspect / history / scan / read / create / version`；错误输出到 stderr、非 0 退出码、未知参数/缺值报错
-  44 个单元测试（含分区读/过滤、统计剪枝边界、缺失数据文件、嵌套读写、flat 物理类型一致性、协议版本/feature 拒绝、JSON 与 checkpoint 删除向量拒绝、未知 action 拒绝、日志名校验、add/remove 路径解码、checkpoint 必需状态校验、写防护等负向用例）+ 真实与自造 Delta fixture

已用真实数据验证（均为仓库 fixture 读取，D1 已复核）：delta-rs `table_a`（13 个版本回放 → 120 个文件，仓库只含日志、不含数据文件）、Databricks 写出的 COVID-19_NYT 表的 1 个数据文件（157,865 行 6 列，snappy；该表共 8 个数据文件，其余 7 个未入库）。

外部引擎互操作：与 delta-rs 1.6.6、DuckDB 1.5.6 在**指定 flat/nested fixture** 上完成双向对拍（flat 49 PASS / 0 FAIL / 2 项已记录差异，见 [artifacts/interop-flat/README.md](artifacts/interop-flat/README.md)；nested 见 [artifacts/acceptance/m2/](artifacts/acceptance/m2/README.md) 与 [m4/](artifacts/acceptance/m4/README.md)）。这些结果不外推到任意 reader v1 表；Spark / Databricks 未验证。完整口径见 [docs/协议兼容性与互操作范围.md](docs/协议兼容性与互操作范围.md)。

## 快速开始

要求：MoonBit 工具链（`moon` / `moonc`，建议 2026-10 之后的版本）。

```bash
# 从 mooncakes.io 安装已发布版本（0.3.0；不含当前源码中的未发布加固）
moon add xiguaAp6y3/moondelta-core

# 或从源码构建
moon check --target native
moon test --target native

# CLI（源码根目录为 src/，因此包路径带 src/ 前缀）
moon run --target native src/cmd/delta -- inspect fixtures/delta/table_a
moon run --target native src/cmd/delta -- history fixtures/delta/table_a
moon run --target native src/cmd/delta -- scan fixtures/delta/table_a --where "id>=60"
moon run --target native src/cmd/delta -- read fixtures/delta/covid --limit 5
```

`inspect fixtures/delta/table_a` 输出示例：

```text
table            : fixtures/delta/table_a
current version  : 12  (log versions: 13)
files            : 120  rows: 120  bytes: 54232
protocol         : reader=1 writer=1
format           : parquet
partition columns: (none)
schema:
  id: int
last checkpoint  : version=10
note             : checkpoint present; truncated / checkpoint-only tables are recovered from the latest single-file checkpoint
```

> 注：样例中的 `reader=1 writer=1` 是 delta-rs fixture 自身的协议字段；本项目写出的表为 `minReaderVersion = 1, minWriterVersion = 2`。

`scan ... --where "id>=60"` 会利用日志里的 min/max 统计把 120 个文件剪到 60 个（`skipped stats: 60`）。

## 作为库使用

```moonbit
let table = @delta.DeltaTable::open("path/to/table")
let snapshot = table.snapshot()              // 最新版本
let old = table.snapshot_at(1)               // 时间旅行
let report = @delta.scan(snapshot, {
  partitions: [("dt", "2026-10-18")],
  predicate: Some({ column: "id", op: GeV, value: 60.0 }),
})
let rows = @delta.read_snapshot("path/to/table", snapshot, Some(100))
for c in rows.columns {
  println(c.name + " : " + @delta.delta_type_name(c.type_))
}
```

写入：

```moonbit
@delta.create_table(root, schema_json, [])
let columns : Array[@delta.ReadColumn] = [
  { name: "id", type_: Int64Type, cells: [CellI64(1L), CellI64(2L)] },
  { name: "name", type_: StringType, cells: [CellStr("a"), CellStr("b")] },
]
@delta.append_rows(root, columns, 1750000000000L) // 写 parquet + 提交新版本
```

## 能力边界（已验证子集）

| 维度 | 支持 | 不支持 / 显式拒绝 |
|---|---|---|
| protocol | reader v1 读取；writer v2 写出 | reader > 1、`readerFeatures`、非空 `writerFeatures`；未声称任意 reader v1 表全部兼容 |
| 日志 | version 0 起连续 JSON commit；20 位文件名；截断 / checkpoint-only 从单文件 checkpoint 恢复 | 多部分 checkpoint；checkpoint 缺失 protocol/metaData；checkpoint 中真实出现的未知 action |
| schema | flat primitive 读取（校验 Delta 类型与 Parquet 物理类型）；struct / 3-level list / map 读写（指定 fixture 对拍） | decimal、uuid、`timestamp_ntz`；nested 叶类型尚不做同等一致性校验 |
| 压缩 | UNCOMPRESSED、SNAPPY | ZSTD、GZIP、BROTLI、LZ4 |
| 写入类型 | Int32 / Int64 / String / Binary / Boolean / Double，以及已验证的嵌套 Cell | Float / Date / Timestamp / 其他 primitive 写入 |
| 写入表 | 非分区 append；校验列名/类型/NOT NULL；put-if-absent | 分区 append；列在 Parquet 中恒为 Optional |
| 删除向量 | JSON 与 checkpoint 的 `add.deletionVector` 均拒绝 | column mapping、v2 checkpoint、CDF、MERGE/UPDATE/DELETE |
| 存储 | 本地文件系统 | 对象存储（S3/R2） |

分区值回填只覆盖 int/long/double/boolean/string/date；date 需十进制天数，ISO 日期字符串尚不支持。`add.stats` 必须是 JSON 编码字符串；非字符串或损坏内容直接报错。CLI 成功退出 0，错误走 stderr 并以非 0 退出。

## 依赖说明：vendored parquet

`mizchi/parquet@0.2.2` 声明了 `mizchi/duckdb@0.6.4`，后者仍使用旧版 `moon.mod.json`，当前 MoonBit 工具链会拒绝解析。该依赖只被上游的 DuckDB 演示子包使用，因此本项目把 parquet 的 `src/` 以 Apache-2.0 许可证 **vendored 到 `src/vendor/parquet`**，删除了 `cmd/duckdb_e2e` 与 `playground_bridge` 两个子包；新增 shredded 嵌套读写入口并修复 REPEATED definition level 规则，writer 另扩展 BOOLEAN / DOUBLE（见 NOTICE）。详见 `src/vendor/parquet/NOTICE.md`。

## Fixtures 与数据来源

`fixtures/` 中仅包含**测试数据**（不含上游实现代码），来源与许可证见 `fixtures/README.md`：

- `fixtures/delta/table_a`：delta-rs 测试表（13 个日志版本 + checkpoint），Apache-2.0
- `fixtures/delta/covid`：Databricks 写出的 COVID-19_NYT 表数据文件，Apache-2.0
- `fixtures/parquet/list_columns.parquet`：apache/parquet-testing 的嵌套 LIST 正读样例，Apache-2.0
- `fixtures/checkpoint-negative/`：checkpoint 删除向量/未知 action/缺失状态负向用例，由 `fixtures/generate_checkpoint_negative.py` 生成

## 路线图

完整路线图见 [docs/ROADMAP.md](docs/ROADMAP.md)。0.3.0 已完成 M1–M4，当前剩余重点：

- 发布包含本轮安全加固的新版本并复核 registry 安装；
- 补 nested 叶类型一致性校验，以及带单位/时区语义的 INT64 timestamp/timestamp_ntz；
- ZSTD/GZIP/BROTLI 解压、分区表 append、对象存储（S3/R2）、CDF、Float/Timestamp 写入；
- 流式读取与 row-group 级剪枝、Arrow IPC / DataFrame / moonice 互操作。

## 开发

```bash
moon check --target native
moon test --target native
moon fmt
```

CI：`.github/workflows/ci.yml`（安装最新 MoonBit 工具链 → `moon update` → check/test/build）。本地已通过 `moon check --target native` 与 44/44 测试；远端 Actions 运行链接仍需在可联网环境核对。

## 许可证

Apache-2.0。Delta Lake 协议参考 <https://github.com/delta-io/delta>（Apache-2.0）；本项目为原创实现，未复制上游代码。
