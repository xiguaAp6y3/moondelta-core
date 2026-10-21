# fixtures —— Delta / Parquet 测试数据

本目录存放本项目测试用的**真实** Delta 表与 Parquet 文件，全部以二进制/日志原样入库，供 `moon test` 与 `cmd/` 示例读取。

> **本次仅复制测试数据，未复制任何实现代码。** 目录内不存在 `.rs` / `.mbt` / `.py` / `.scala` 等源文件，也没有上游仓库的 `src/`、`tests/` 代码；只取数据文件（Delta 事务日志、checkpoint、Parquet 数据文件）。

---

## 1. 目录布局

```
fixtures/
├─ README.md
├─ delta/
│  ├─ table_a/
│  │  └─ _delta_log/
│  │     ├─ 00000000000000000000.json … 00000000000000000012.json   # 13 个版本日志（715 / 3,111 / 3,131 ×9 / 3,152 ×2 B）
│  │     ├─ 00000000000000000010.checkpoint.parquet                 # v10 checkpoint，21,615 B
│  │     └─ _last_checkpoint                                        # 4,799 B
│  └─ covid/
│     ├─ log0.json                                                  # 5,624 B
│     └─ part-00000-a496f40c-…-c000.snappy.parquet                  # 690,424 B
└─ parquet/
   └─ list_columns.parquet                                          # 2,526 B
```

合计 **18 个数据文件**（13 个版本 JSON + 1 个 checkpoint + `_last_checkpoint` + covid 的 `log0.json` 与 1 个数据文件 + `list_columns.parquet`）；连本 `README.md` 共 **19 个文件**。

---

## 2. 来源、许可证与用途

| 本仓库路径 | 上游来源仓库 | 上游路径 | 许可证 | 本项目用途 |
|---|---|---|---|---|
| `fixtures/delta/table_a/_delta_log/`（13 个 `000…json` + `000…10.checkpoint.parquet` + `_last_checkpoint`） | [`delta-io/delta-rs`](https://github.com/delta-io/delta-rs) 测试数据（`table_a` 表） | 上游测试数据目录下的 `table_a`（本仓库未 pin 上游 commit，见 §5） | Apache-2.0 | **多版本回放**：日志解析、快照构建、时间旅行、扫描规划的统计剪枝 |
| `fixtures/delta/covid/log0.json` + `fixtures/delta/covid/part-00000-…-c000.snappy.parquet` | [`delta-io/delta-rs`](https://github.com/delta-io/delta-rs) 测试数据（`COVID-19_NYT` 表，由 Databricks 写出） | 上游测试数据目录下的 `covid/`（同上，未 pin commit） | Apache-2.0 | **真实 Spark/Databricks 数据读取**：验证把 Delta `add` 指向的真实 snappy Parquet 文件读成行/列 |
| `fixtures/parquet/list_columns.parquet` | [`apache/parquet-testing`](https://github.com/apache/parquet-testing) | `data/list_columns.parquet` | Apache-2.0 | **负向用例**：嵌套 `LIST` 列在依赖库不支持时的报错行为（必须"明确报错"，不得 panic 或静默错读） |
| `fixtures/delta/byte-short/`（1 个 `000…json` + 1 个 snappy parquet） | 本项目用 `deltalake`（delta-rs）Python 包生成（2026-10-22，M1） | 本地生成（`write_deltalake`，无上游） | Apache-2.0（随本项目） | **byte/short 类型读取**（M1-D4）：`b: byte`（含 null）、`s: short`（含 null）、`id: long`，3 行；验证 schema 映射与 Parquet 物理读取 |

两份上游仓库均为 **Apache License 2.0**，与本项目 `LICENSE` 一致；数据文件随原仓库以 Apache-2.0 分发，本目录仅做原样复制（字节级一致，校验见 §4）。

复制来源（本机研究快照，2026-10-18）：

- `%TEMP%\dsh-moon-research\a1probe\proj2\data\_delta_log\` → `fixtures/delta/table_a/_delta_log/`
- `%TEMP%\dsh-moon-research\a1probe\proj2\data\covid\` → `fixtures/delta/covid/`
- `%TEMP%\dsh-moon-research\a1probe\proj2\data\types\list_columns.parquet` → `fixtures/parquet/list_columns.parquet`

> 许可证提醒：`LICENSE` 只覆盖本项目代码与本目录数据在复制/再分发时的 Apache-2.0 条款；上游数据的著作权与商标归属见各自仓库。

---

## 3. 各 fixture 说明

### 3.1 `delta/table_a` —— 多版本回放（**只有日志，没有数据文件**）

- 表结构：单列 `id: integer`（nullable），`partitionColumns = []`，无分区。
- 版本：0 → 12 共 **13 个版本**；`commitInfo` 显示由 `Apache-Spark/3.5.4 Delta-Lake/3.3.0` 写出，`protocol = (minReaderVersion=1, minWriterVersion=1)`。
- 动作分布：
  - `v0`：`protocol` + `metaData` + `commitInfo`（CREATE TABLE）；
  - `v1` … `v12`：每版 10 个 `add`（+ 1 个 `commitInfo`），共 **120 个 add，无 remove**。
- 回放预期：`versions = 13`，最终文件清单 **120 个文件**，每文件 `numRecords = 1`，`id` 覆盖 0 … 119，合计 120 行。
- `add.stats` 完整（`numRecords` / `minValues` / `maxValues` / `nullCount`），`partitionValues` 恒为 `{}`：
  - 用途①：**统计剪枝**——如 `id >= 60` 应剪到 60 个文件，`id = 7` 应只留 1 个；
  - 用途②：**分区剪枝**不适用——本表没有分区列，分区剪枝用例需另建（手写日志 + `year=2026/` 目录布局）。
- `_last_checkpoint`：`{"version":10,"size":102,"numOfAddFiles":100,...}`，并带完整 `checkpointSchema`（`txn` / `add` / `remove` / `metaData` / `protocol` / `domainMetadata` 结构）。
- `00000000000000000010.checkpoint.parquet`：21,615 B，100 个 add 文件（v1…v10）的行式快照。
- **注意**：`add.path` 指向的 `part-…snappy.parquet` 数据文件**不在本目录内**（上游只提供日志）。因此 `table_a` 只能用于日志层（解析 / 回放 / 清单 / 剪枝规划 / 时间旅行），不能用于读取真实数据；读数据请用 `delta/covid` 或 `parquet/list_columns.parquet`。

### 3.2 `delta/covid` —— 真实 Spark/Databricks 数据读取

- `log0.json`：11 行动作（`commitInfo` + `protocol` + `metaData` + **8 个 `add`**），`engineInfo` 为 Databricks，`commitInfo.timestamp = 1619121487931`（2021-04-22），`operationMetrics = {numFiles:8, numOutputBytes:6190485, numOutputRows:1111930}`。
- 表结构：`date: string, county: string, state: string, fips: integer, cases: integer, deaths: integer`（6 列，无分区，`minReaderVersion=1 / minWriterVersion=2`）。
- **只复制了第 1 个数据文件**：`part-00000-a496f40c-e091-413a-85f9-b1b69d4b3b4e-c000.snappy.parquet`（690,424 B，snappy），`stats.numRecords = 157865`，即该文件 **157,865 行 6 列**；其余 7 个 `part-00001…00007` 未复制。
- 因此：「文件级读取」用例可用本文件（行数、首行值、`min/max` 与 `stats` 对账）；「整表扫描」用例会因缺 7 个文件而失败——测试若走表级 API，必须显式声明"数据文件缺失"路径，或改为只断言文件清单而不读数据。
- 附带用途：`stats` 的 `minValues/maxValues/nullCount`（如 `date` 2020-01-21 ~ 2020-05-19、`cases` 0 ~ 198114、`deaths` nullCount 1065）可作为统计剪枝的真实样本。

### 3.3 `parquet/list_columns.parquet` —— 嵌套类型负向用例

- 2,526 B，**3 行 2 列**：`int64_list: list<int64>`、`utf8_list: list<utf8>`（均由 `PAR1` magic 结尾，写入方为 `pyarrow 0.15.1` / `parquet-cpp 1.5.1-SNAPSHOT`，pandas metadata 记录 `pandas_version 0.25.3`）。
- LIST 在 Parquet 物理层是 repeated group，当前依赖 `mizchi/parquet` 对 repeated 列报 `Unsupported(Repeated columns are not supported yet)`，与本项目实测一致。
- 断言口径：读该文件应返回**结构化错误**（含列名/原因），不得 panic、不得返回 0 行假装成功。

---

## 4. 完整性校验（SHA-256，复制后实测）

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `delta/table_a/_delta_log/_last_checkpoint` | 4,799 | `b6e9a62fc690b1012b4f30dc46697d1038dfab5c1b6f455f01e022bac7e6cf56` |
| `delta/table_a/_delta_log/00000000000000000000.json` | 715 | `9e06c54ff2303dde9675d0b7324f92068a04a4f4c91a3bb812b3f4fa159c101e` |
| `delta/table_a/_delta_log/00000000000000000012.json` | 3,152 | `2e92dccfdde1b16fd3baa7f88087ed617ed999c805d52cf825860fe5bdd69f94` |
| `delta/table_a/_delta_log/00000000000000000010.checkpoint.parquet` | 21,615 | `4c217257e9ed15793d8584c77d1d75dc842cd6f966a047f7b86394d0264397ed` |
| `delta/covid/log0.json` | 5,624 | `6e93948d6ff8bf09207c2a7eb788d7a58fcf0764223e1b2d3dcf8449eb14f356` |
| `delta/covid/part-00000-…-c000.snappy.parquet` | 690,424 | `ac5c9a4ae246dde1f054bfd2a9f67f120294b1632f7d89bb03a2fe4c5e7fea55` |
| `parquet/list_columns.parquet` | 2,526 | `5988ab91b6cb7efa7bf6a77f789b40929212280519be6c9daad56e01d5ceb218` |

其余版本日志可自行校验：`Get-FileHash fixtures\delta\table_a\_delta_log\00000000000000000007.json -Algorithm SHA256`。

---

## 5. 复现方式与已知限制

- **复现**：从 `delta-io/delta-rs` 的测试数据（`table_a`、`covid/`）与 `apache/parquet-testing` 的 `data/list_columns.parquet` 取同名文件，字节数/哈希应与 §4 一致。本仓库不提供下载脚本，也不在 CI 里联网取数据。
- **未 pin 上游 commit**：上游测试数据会随仓库演进（如 delta-rs 新增 `deletionVector` 等字段）；本目录是 2026-10-18 的快照。若哈希不符，以 §4 为准，不要"顺手更新"上游新版本——`_last_checkpoint` 的 `checkpointSchema` 会随之变化，破坏快照/回放断言。
- **不含实现代码**：本目录没有任何上游源文件，测试与解析逻辑全部在本项目 `src/` 内独立实现。
- **不做数据文件补全**：`table_a` 的数据文件与 `covid` 的另外 7 个数据文件都不会补入仓库（体积与许可证都不需要）；相关限制见 §3.1 / §3.2。
