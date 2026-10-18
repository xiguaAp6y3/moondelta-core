# THIRD_PARTY_NOTICES

本项目包含的第三方代码与测试数据如下。所有第三方材料均保留其原始许可证。

## 1. Vendored 代码：mizchi/parquet

- 上游：https://github.com/mizchi/parquet
- 版本：0.2.2（发布于 2026-09-24）
- 许可证：Apache-2.0（全文见 `src/vendor/parquet/LICENSE`）
- 位置：`src/vendor/parquet/`
- 修改：删除上游 `src/cmd/duckdb_e2e` 与 `src/playground_bridge` 两个 DuckDB 演示子包（它们依赖的 `mizchi/duckdb@0.6.4` 仍使用旧版 `moon.mod.json`，当前 MoonBit 工具链会拒绝解析）；reader / writer / encoding / metadata 实现未做任何修改。
- 详细说明见 `src/vendor/parquet/NOTICE.md`。

## 2. 测试数据：delta-rs

- 上游：https://github.com/delta-io/delta-rs
- 许可证：Apache-2.0
- 使用位置：
  - `fixtures/delta/table_a/`（13 个事务日志版本 + checkpoint + `_last_checkpoint`）
  - `fixtures/delta/covid/`（Databricks 写出的 COVID-19_NYT 表：1 个日志 + 1 个 snappy 数据文件）
- 用途：日志回放、时间旅行、扫描剪枝、真实数据读取的互操作验证。本项目仅复制测试数据，未复制任何实现代码。

## 3. 测试数据：apache/parquet-testing

- 上游：https://github.com/apache/parquet-testing
- 许可证：Apache-2.0
- 使用位置：`fixtures/parquet/list_columns.parquet`
- 用途：嵌套类型列（list）负向用例——验证读取路径明确拒绝，而不是 panic 或静默返回 0 行。

## 4. 运行时依赖

- `moonbitlang/x@0.5.5`：Apache-2.0
- MoonBit core（`moonbitlang/core`）：Apache-2.0

## 5. 协议规范（仅参考，未复制代码）

- Delta Lake 协议规范：https://github.com/delta-io/delta （Apache-2.0）
- 本项目为原创实现，按协议规范自行实现日志模型、回放语义与扫描规划，未复制 delta-io/delta 或 delta-rs 的源代码。
