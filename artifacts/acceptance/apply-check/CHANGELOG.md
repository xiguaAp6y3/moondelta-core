# Changelog

本项目遵循语义化版本；格式参考 Keep a Changelog。

## [0.1.1] - 2026-10-19

### Added

- **写入**：`BOOLEAN` / `DOUBLE` 列支持（扩展 vendored parquet writer，PLAIN 编码：
  布尔按位打包、双精度按 IEEE-754 小端写入）
- **读取**：分区表按 `partitionValues` 补齐分区列（数据文件里不存分区列）
- **测试**：分区表读取与分区过滤、统计剪枝边界（无 stats、min==max）、
  缺失数据文件报错、布尔/双精度写读往返；测试数 10 → 14

### Changed

- `src/vendor/parquet/NOTICE.md` 记录 writer 扩展（reader 未改动）
- README / 申报书更新能力矩阵、测试数与代码规模
- `THIRD_PARTY_NOTICES.md` 同步 vendored 修改说明

## [0.1.0] - 2026-10-18

### Added

- 首个版本：`_delta_log` 解析与回放（protocol / metaData / add / remove / commitInfo）、
  快照与时间旅行、分区 + min/max 统计剪枝、flat + UNCOMPRESSED/SNAPPY 读取、
  `create_table` / `append_rows`（Int32/Int64/String/Binary）、CLI 六个子命令、
  示例、CI 与 mooncakes.io 发布
