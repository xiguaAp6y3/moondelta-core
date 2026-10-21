# moondelta-core

[![CI](https://github.com/xiguaAp6y3/moondelta-core/actions/workflows/ci.yml/badge.svg)](https://github.com/xiguaAp6y3/moondelta-core/actions/workflows/ci.yml)

MoonBit 原生的 **Delta Lake 表格式内核**：回放 `_delta_log` 事务日志、构建快照、时间旅行、分区/统计剪枝的扫描规划，读取数据文件，并在受支持的列类型上 append 提交新版本。

> 生态里已经有 Parquet 编解码、DataFrame、对 Parquet 跑 SQL 的查询引擎、Iceberg 只读——缺的是"带事务日志的表"这一层。本项目补这一层。
>
> 参赛申报书：[申报书.md](申报书.md) ｜ 第三方声明：[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
>
> 已发布：[mooncakes.io/docs/xiguaAp6y3/moondelta-core](https://mooncakes.io/docs/xiguaAp6y3/moondelta-core)（`moon add xiguaAp6y3/moondelta-core`）

## 状态

MVP（README/CLI 仍标 0.1.0，`moon.mod` 已为 0.1.1，版本文案待统一，见 `artifacts/protocol-audit.md` §5 第 9 条）：

-  日志解析与回放：`protocol` / `metaData` / `add` / `remove` / `commitInfo`，`_last_checkpoint` 识别
-  协议范围：读取 Delta protocol reader v1 的表、写出 `minReaderVersion = 1, minWriterVersion = 2` 的表；日志从非 0 起点/缺版本明确拒绝；checkpoint-only 返回结构化 NotFound；日志被截断但存在 checkpoint 的表当前未检测（会读旧快照，已知缺口）；reader v2/v3 与未知必需 feature 当前不校验
-  快照、版本历史、时间旅行（`snapshot_at`）
-  扫描规划：分区等值过滤 + 文件级 min/max 统计剪枝
-  读取：flat schema + UNCOMPRESSED/SNAPPY 的 Delta 数据文件
-  写入：`create_table` + `append_rows`（Int32/Int64/String/Binary/Boolean/Double）；append 只校验列数/行长，不校验列名/类型/NOT NULL，写出列恒为 Parquet Optional（已知缺口，见 `artifacts/protocol-audit.md` §5）；分区列读取时按 `partitionValues` 补齐
-  CLI：`inspect / history / scan / read / create / version`
-  14 个单元测试（含分区读/过滤、统计剪枝边界、缺失数据文件、嵌套列拒绝等负向用例；其中名为 checkpoint-only 的用例实际构造的是非 0 起点日志，真实 checkpoint-only / 删除向量 / reader≥2 拒绝无测试）+ 真实 Delta 表 fixture（delta-rs、apache/parquet-testing）

已用真实数据验证（均为仓库 fixture 读取，D1 已复核）：delta-rs `table_a`（13 个版本回放 → 120 个文件，仓库只含日志、不含数据文件）、Databricks 写出的 COVID-19_NYT 表的 1 个数据文件（157,865 行 6 列，snappy；该表共 8 个数据文件，其余 7 个未入库）。

外部引擎互操作（D2）：已与 delta-rs 1.6.6、DuckDB 1.5.6 完成 flat 表双向对拍（49 PASS / 0 FAIL / 2 项已记录差异，见 [artifacts/interop-flat/README.md](artifacts/interop-flat/README.md)）；Spark / Databricks 未验证。nested schema 当前明确拒绝，七项门槛 1/7（仅证据门槛 6，[artifacts/interop-nested/README.md](artifacts/interop-nested/README.md)），不构成 nested 互操作。闭集外的静默/未检测项与测试缺口见 [artifacts/protocol-audit.md](artifacts/protocol-audit.md) §5；口径见 [docs/协议兼容性与互操作范围.md](docs/协议兼容性与互操作范围.md)。

## 快速开始

要求：MoonBit 工具链（`moon` / `moonc`，建议 2026-10 之后的版本）。

```bash
# 从 mooncakes.io 安装（v0.1.0 已发布）
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
note             : checkpoint files exist; checkpoint-only tables are not readable by this kernel
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

## 能力边界（对已识别边界返回结构化错误；闭集外尚未全部拒绝）

| 维度 | 支持 | 不支持 |
|---|---|---|
| protocol | reader v1 读取；writer v2 写出 | 高于 reader v1 的 protocol、`readerFeatures` / `writerFeatures`、未知必需 feature 当前不校验、不拒绝（已知缺口） |
| 日志 | version 0 起的连续 JSON commit；非 0 起点/缺版本拒绝 | checkpoint-only 返回结构化 NotFound；日志被截断但存在 checkpoint 的表未检测（会读旧快照，已知缺口） |
| schema | flat 原始类型 | 嵌套 struct / list / map（明确拒绝）；decimal/uuid（明确拒绝） |
| 压缩 | UNCOMPRESSED、SNAPPY | ZSTD、GZIP、BROTLI、LZ4（明确拒绝） |
| 逻辑类型 | string、int、long、float、double、boolean、date、timestamp(INT96) | decimal、uuid 等（会丢语义） |
| 表 | 日志完整、可从头回放的本地表 | checkpoint-only / 截断日志（后者未检测，见上） |
| 写入类型 | Int32 / Int64 / String / Binary / Boolean / Double | Float / Timestamp / Repeated |
| 写入表 | 非分区表 | 分区表 append（MVP 明确拒绝）；append 不校验列名/类型/NOT NULL，列恒为 Parquet Optional |
| 存储 | 本地文件系统 | 对象存储（S3/R2） |
| 闭集外 feature | — | 删除向量、column mapping、CDF 当前未检测；删除向量被静默忽略并读全量（已知缺口） |

其中嵌套列、缺失数据文件、非 0 起点/缺版本日志、四种不支持压缩、decimal/未知类型已实测为结构化错误（部分有测试）；但**闭集外并非全部拒绝**：删除向量被静默忽略并读全量、截断日志 + checkpoint 读旧快照、reader v2/v3 接受、stats 非 string 时 `rows: 0`、append 不校验列名/类型/NOT NULL、CLI 错误退出码恒 0（见 `artifacts/protocol-audit.md` §5）。这些是已知缺口/待修复项，不能当成“不静默降级”的证据。

## 依赖说明：vendored parquet

`mizchi/parquet@0.2.2` 声明了 `mizchi/duckdb@0.6.4`，后者仍使用旧版 `moon.mod.json`，当前 MoonBit 工具链会拒绝解析。该依赖只被上游的 DuckDB 演示子包使用，因此本项目把 parquet 的 `src/` 以 Apache-2.0 许可证 **vendored 到 `src/vendor/parquet`**，删除了 `cmd/duckdb_e2e` 与 `playground_bridge` 两个子包；reader 未改动，writer 扩展了 BOOLEAN / DOUBLE 写入（见 NOTICE）。详见 `src/vendor/parquet/NOTICE.md`。

## Fixtures 与数据来源

`fixtures/` 中仅包含**测试数据**（不含上游实现代码），来源与许可证见 `fixtures/README.md`：

- `fixtures/delta/table_a`：delta-rs 测试表（13 个日志版本 + checkpoint），Apache-2.0
- `fixtures/delta/covid`：Databricks 写出的 COVID-19_NYT 表数据文件，Apache-2.0
- `fixtures/parquet/list_columns.parquet`：apache/parquet-testing 的嵌套类型样例（负向用例），Apache-2.0

## 路线图

完整路线图见 [docs/ROADMAP.md](docs/ROADMAP.md)，近期重点：

- 0.2：ZSTD/GZIP/BROTLI 解压、checkpoint parquet 解析（仅 checkpoint 所需的最小嵌套 shape，不构成 nested schema 兼容）、分区表 append
- 0.3：对象存储（S3/R2）抽象、变更数据（CDF）读取、Float/Timestamp 写入
- 1.0：流式读取与 row-group 级剪枝、Arrow IPC / DataFrame / moonice 互操作

## 开发

```bash
moon check --target native
moon test --target native
moon fmt
```

CI：`.github/workflows/ci.yml`（安装最新 MoonBit 工具链 → `moon update` → check/test/build）。

## 许可证

Apache-2.0。Delta Lake 协议参考 <https://github.com/delta-io/delta>（Apache-2.0）；本项目为原创实现，未复制上游代码。
