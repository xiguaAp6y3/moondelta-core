# moondelta-core 路线图

> 目标：让 MoonBit 程序能读写"表"（Delta Lake 表格式），而不是只能读写"文件"。
> 当前版本：**0.1.1**（2026-10-19）。路线图按能力分层，越往后依赖越重、周期越长。

## 0.1.x — 当前交付（已完成 / 收尾中）

| 能力 | 状态 |
|---|---|
| `_delta_log` action 解析与回放、版本发现、`_last_checkpoint` 识别 | ✅ 0.1.0 |
| 快照、时间旅行、`history()` | ✅ 0.1.0 |
| 分区等值过滤 + 文件 min/max 统计剪枝 | ✅ 0.1.0（分区/边界用例 0.1.1 补齐） |
| flat schema + UNCOMPRESSED/SNAPPY 读取；分区列按 `partitionValues` 补齐 | ✅ 0.1.0 / 0.1.1 |
| 写入：Int32/Int64/String/Binary/Boolean/Double；create + append | ✅ 0.1.0 / 0.1.1 |
| CLI、示例、CI、mooncakes.io 发布 | ✅ 0.1.0 |
| 负向边界：嵌套 schema、checkpoint-only、缺失文件、未知类型 | ✅ 明确报错 |

## 0.2 — 读取能力扩展（近期，1–2 周）

1. **压缩**：ZSTD / GZIP / BROTLI 解压
   - 方案：集成 `bikallem/compress`（Apache-2.0，含多算法）作为 codec 后端，
     在 vendored parquet 的 `compression.mbt`（116 行）加分发点；
   - 验收：能读 polars / DuckDB 默认写出的 zstd parquet。
2. **checkpoint parquet 解析**
   - 需要嵌套 schema（repeated group）解码；先做 checkpoint 的固定 shape
     （add / remove / metaData / protocol），而不是通用嵌套；
   - 验收：能读 delta-rs 测试集中带 `_last_checkpoint` 且旧日志已清理的表。
3. **分区表 append**
   - 按分区列分组、写 `dt=.../part-*.parquet`、日志写 `partitionValues`；
   - 验收：写入后 Spark / delta-rs 能读出相同分区。

## 0.3 — 写入与互操作（中期，1–2 月）

1. `FLOAT` / `TIMESTAMP`（微秒）写入：扩展 vendored writer 与 schema 转换；
2. 对象存储抽象（S3 / R2 / 本地 fs），复用 `moonbitlang/async` 的 HTTP 栈；
3. 变更数据（Change Data Feed）读取：`_change_data/` 与 `cdc` action；
4. 与 Arrow IPC / DataFrame（MoonFrame）/ moonice（Iceberg）的互操作示例；
5. 扫描规划增强：多列谓词、分区范围、统计直方图（若日志提供）。

## 1.0 — 生产级（长期）

1. 流式读取与 row-group 级裁剪（不整文件进内存）；
2. 列裁剪、谓词下推；与 parquet 页索引 / 布隆过滤集成；
3. 删除向量（deletion vectors）与列映射（column mapping）；
4. MERGE / UPDATE / DELETE（重写文件 + 新日志版本）；
5. 并发写冲突仲裁（乐观并发 + 重试）；
6. 性能基准（读吞吐、日志回放开销）与 CI 性能门槛。

## 明确不做

- 不做通用嵌套类型（struct/list/map）的完整读写（只做 checkpoint 所需的最小 shape）；
- 不做 SQL 引擎、DataFrame、元数据服务（catalog）——这些由生态内其他项目承担；
- 不复刻 mooncakes.io 审计工具、moon-fmt 等官方工具。

## 里程碑与验收对齐

| 里程碑 | 验收标准 |
|---|---|
| 0.1.x | `moon check/test` 全绿；14 个测试；mooncakes.io 可安装；CLI 可演示 |
| 0.2 | zstd parquet 可读；checkpoint 表可读；分区表可 append（各有测试） |
| 0.3 | 对象存储读路径可用；CDF 示例；互操作示例可运行 |
| 1.0 | 千兆级 parquet 流式读；基准数据入库；并发写测试 |

## 如何贡献

- 每个能力点对应一个独立 commit + 测试 + README/CHANGELOG 更新；
- 外部依赖（vendored parquet 等）的修改必须在 `NOTICE.md` 记录；
- 新增能力先在 `fixtures/` 放最小可复现数据，再写断言。
