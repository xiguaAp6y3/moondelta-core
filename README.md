# moondelta-core

[![CI](https://github.com/xiguaAp6y3/moondelta-core/actions/workflows/ci.yml/badge.svg)](https://github.com/xiguaAp6y3/moondelta-core/actions/workflows/ci.yml)

MoonBit 原生的 **Delta Lake 表格式内核**：回放 `_delta_log` 事务日志、构建快照、时间旅行、分区/统计剪枝的扫描规划，读取数据文件，并在受支持的列类型上 append 提交新版本。

> 生态里已经有 Parquet 编解码、DataFrame、对 Parquet 跑 SQL 的查询引擎、Iceberg 只读——缺的是"带事务日志的表"这一层。本项目补这一层。
>
> 参赛申报书：[申报书.md](申报书.md) ｜ 第三方声明：[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
>
> 已发布：[mooncakes.io/docs/xiguaAp6y3/moondelta-core](https://mooncakes.io/docs/xiguaAp6y3/moondelta-core)（`moon add xiguaAp6y3/moondelta-core`）

## 状态

MVP（0.1.0）：

-  日志解析与回放：`protocol` / `metaData` / `add` / `remove` / `commitInfo`，`_last_checkpoint` 识别
-  快照、版本历史、时间旅行（`snapshot_at`）
-  扫描规划：分区等值过滤 + 文件级 min/max 统计剪枝
-  读取：flat schema + UNCOMPRESSED/SNAPPY 的 Delta 数据文件
-  写入：`create_table` + `append_rows`（Int32/Int64/String/Binary，Required/Optional）
-  CLI：`inspect / history / scan / read / create / version`
-  10 个单元测试 + 真实 Delta 表 fixture（delta-rs、apache/parquet-testing）

已用真实数据验证：delta-rs `table_a`（13 个版本回放 → 120 个文件）、Databricks 写出的 COVID-19_NYT 表（157,865 行 6 列，snappy）。

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

## 能力边界（遇到即报错，不静默降级）

| 维度 | 支持 | 不支持 |
|---|---|---|
| schema | flat 原始类型 | 嵌套 struct / list / map |
| 压缩 | UNCOMPRESSED、SNAPPY | ZSTD、GZIP、BROTLI、LZ4 |
| 逻辑类型 | string、int、long、float、double、boolean、date、timestamp(INT96) | decimal、uuid 等（会丢语义） |
| 表 | 日志完整、可从头回放的本地表 | checkpoint-only / 日志被截断的表 |
| 写入类型 | Int32 / Int64 / String / Binary（Required/Optional） | Boolean / Float / Double / Timestamp / Repeated |
| 写入表 | 非分区表 | 分区表 append（MVP 明确拒绝） |
| 存储 | 本地文件系统 | 对象存储（S3/R2）、删除向量、列映射 |

这些边界来自底层 Parquet 实现的真实能力（见 `src/vendor/parquet/NOTICE.md`），已在测试里固化为**明确的错误信息**（不是 panic，也不是静默 0 行）。

## 依赖说明：vendored parquet

`mizchi/parquet@0.2.2` 声明了 `mizchi/duckdb@0.6.4`，后者仍使用旧版 `moon.mod.json`，当前 MoonBit 工具链会拒绝解析。该依赖只被上游的 DuckDB 演示子包使用，因此本项目把 parquet 的 `src/` 以 Apache-2.0 许可证 **vendored 到 `src/vendor/parquet`**，删除了 `cmd/duckdb_e2e` 与 `playground_bridge` 两个子包，reader/writer 实现未改动。详见 `src/vendor/parquet/NOTICE.md`。

## Fixtures 与数据来源

`fixtures/` 中仅包含**测试数据**（不含上游实现代码），来源与许可证见 `fixtures/README.md`：

- `fixtures/delta/table_a`：delta-rs 测试表（13 个日志版本 + checkpoint），Apache-2.0
- `fixtures/delta/covid`：Databricks 写出的 COVID-19_NYT 表数据文件，Apache-2.0
- `fixtures/parquet/list_columns.parquet`：apache/parquet-testing 的嵌套类型样例（负向用例），Apache-2.0

## 路线图

- 写入扩展：Double / Boolean / Timestamp 列
- 读取扩展：ZSTD/GZIP/BROTLI 解压、嵌套 schema、checkpoint parquet 解析
- 对象存储（S3/R2）抽象
- 与 Arrow IPC / DataFrame / moonice（Iceberg）的互操作示例
- 增量扫描与变更数据（CDF）读取

## 开发

```bash
moon check --target native
moon test --target native
moon fmt
```

CI：`.github/workflows/ci.yml`（安装最新 MoonBit 工具链 → `moon update` → check/test/build）。

## 许可证

Apache-2.0。Delta Lake 协议参考 <https://github.com/delta-io/delta>（Apache-2.0）；本项目为原创实现，未复制上游代码。
