# Changelog

本项目遵循语义化版本；格式参考 Keep a Changelog。

## [Unreleased]

路线图 M2（Parquet 嵌套读取层，2026-10-22 完成，较排期提前）：测试数 31 → 32。

### Added

- **嵌套读取**：`struct` / 标准 3-level `list` / `map` 的完整读取（NULL、空集合、
  null 元素/子字段、Delta schema 外多余列忽略）；`Cell` 新增
  `CellStruct` / `CellList` / `CellMap`，CLI 以 JSON 风格渲染嵌套值
- vendored parquet 新增 shredded 读取入口（每叶列输出值 + def/rep level 槽位流），
  `NestedType` schema 解析保留子树

### Fixed

- vendored `build_leaf_columns` 的 definition level 递增规则：Parquet 规范要求
  OPTIONAL 与 REPEATED 都递增，原实现只对 OPTIONAL 递增（扁平数据不受影响，
  嵌套列 def 全错）

## [0.2.0] - 2026-10-22

路线图 M1（协议与工程基线）：协议越界从"静默接受"转为"拒绝并说明原因"，全部有负向测试；测试数 14 → 31。

### Added

- **协议校验**：`minReaderVersion != 1` 拒绝并说明（v2/v3 门控 column mapping、
  删除向量、v2 checkpoint）；`add.deletionVector` 存在即拒绝；`readerFeatures`
  在 reader v1 上拒绝；`writerFeatures` 解析入库
- **日志完整性**：`_delta_log` 文件名严格按 20 位零填充版本号校验（`0.json`
  之类的名字此前会伪造 version 0 绕过 checkpoint-only 防护）
- **Actions 闭集**：`txn` 识别；`cdc` / `domainMetadata` 显式忽略并在 `inspect`
  输出与 `Snapshot.ignored_actions` 中提示；未知 action 结构化拒绝（不再静默跳过）
- **读取**：`byte` / `short` 类型映射与读取（delta-rs 写出的真实表回读，
  fixture `fixtures/delta/byte-short/`）；`add.path` percent-decoding（规范要求，
  此前含空格/非 ASCII 文件名的表不可读）；parquet I/O 错误带上文件路径
- **写入防护**：`create_table` 拒绝已存在的表；`append_rows` 校验列名/类型/
  NOT NULL 与 `metaData.schema` 一致；writer protocol > 2 拒绝 append；
  数据文件与 commit 文件均为 put-if-absent
- **CLI**：错误输出到 stderr、失败以退出码 1 结束；未知参数 / 缺值 / 非法
  `--where` 显式报错

### Changed

- `add.stats` 解析失败从"静默按 0 行计"改为结构化 `ParseError`
- 截断日志的报错信息不再误导为 "checkpoint-only or truncated table?"
- 测试数 14 → 31；README / 申报书能力矩阵同步到 0.2.0 现状
  （唯一残留静默缺口：截断日志 + checkpoint 读旧快照，路线图 M3）

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
