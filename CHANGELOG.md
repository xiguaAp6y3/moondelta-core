# Changelog

本项目遵循语义化版本；格式参考 Keep a Changelog。

## [Unreleased]

### Fixed

- `remove.path` 与 `add.path` 统一 percent-decode；JSON 与 checkpoint 回放不再因编码路径漏删文件
- checkpoint 恢复检测并拒绝 `add.deletionVector`，修复其绕过 JSON 路径删除向量防护的问题
- checkpoint 仅拒绝当前记录中真实出现的未知 action，不再误伤预声明为空列的合法 schema
- checkpoint 缺失 protocol/metaData 时按损坏拒绝，不再回退到默认 reader v1 或空 schema
- 非字符串 `add.stats` 结构化报错，不再静默按 0 行处理
- append 拒绝非空 `writerFeatures`，不生成数据文件或新 commit
- `timestamp_ntz` 在保留时区语义前显式拒绝，不再与 UTC timestamp 混淆
- flat 与 nested 叶读取校验 Delta 逻辑类型与 Parquet 物理类型，类型错配不再读出错误值
- 写入补齐 11 种基础类型；`float`、`date`、`timestamp` 增加正确的 Parquet 物理编码与 logical annotation
- 分区值回填补齐 11 种基础类型，`date` 支持 `YYYY-MM-DD` 与 epoch 天数
- reader v1 / writer v2 门控收紧：读取端同样拒绝 writer>2 与非空 `writerFeatures`

### Changed

- 单元测试保持 37 个；新增场景合并进既有用例，不增加测试计数
- README、申报书、使用/测试说明及第三方声明同步为“已验证子集”口径

## [0.3.0] - 2026-10-22

（0.3.0 = 完全兼容冲刺发布：M1 已单独发 0.2.0；本版并入 M2/M3/M4 + M5 收尾）

路线图 M4（嵌套写入 + 统计，2026-10-22 完成，较排期提前）：测试数 36 → 37；
七项门槛 7/7 全部达成（嵌套读、嵌写、双外部引擎双向对拍、checkpoint 嵌套解析）。

### Added

- **嵌套写入**：`append_rows` 支持 `struct` / 标准 3-level `list` / `map`
  （LIST/MAP 注解；NULL、空集合、null 元素/值边界），写出表经 delta-rs 1.6.6
  与 DuckDB 回读逐行逐字段一致（`artifacts/acceptance/m4/`）；新增示例
  `src/examples/write_nested`
- vendored parquet 新增 shredded 写入口（叶级槽位流 + schema 树 → parquet）
- 嵌套字段不写 min/max 统计（放弃剪枝、保证正确读取）

### Fixed

- MAP 注解 converted_type 应为 1（误用 4=ENUM 导致 pyarrow 拒读）；
  map 的 repeated 中间组名用 `entries`（pyarrow/delta-rs 兼容的实际形态）

路线图 M3（checkpoint 恢复，2026-10-22 完成，较排期提前）：测试数 32 → 36。

### Added

- **checkpoint 恢复**：单文件经典 checkpoint parquet 解析（按 shredded 叶列的
  def/rep levels 逐记录解析，兼容 Spark 经典与 delta-rs 两种可空性标注）；
  日志不完整（顶端截断 / checkpoint-only）时从最新 checkpoint 恢复基底并叠加
  其后 JSON commit——不再静默读旧版本；`current_version` = max(JSON 最新,
  checkpoint 最新)；多部分 checkpoint 显式拒绝
- 完整 JSON 日志仍走从头回放（历史/时间旅行语义与 0.1.x 一致）

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
