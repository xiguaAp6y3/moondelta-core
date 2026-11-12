# moondelta-core

[![CI](https://github.com/xiguaAp6y3/moondelta-core/actions/workflows/ci.yml/badge.svg)](https://github.com/xiguaAp6y3/moondelta-core/actions/workflows/ci.yml)

MoonBit 原生的 **Delta Lake 表格式内核**：回放 `_delta_log` 事务日志、构建快照、时间旅行、分区/统计剪枝的扫描规划，读取数据文件，并在受支持的列类型上 append 提交新版本。

> 生态里已经有 Parquet 编解码、DataFrame、对 Parquet 跑 SQL 的查询引擎、Iceberg 只读——缺的是"带事务日志的表"这一层。本项目补这一层。
>
> 参赛申报书：[申报书.md](申报书.md) ｜ 第三方声明：[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
>
> 已有发布记录：[mooncakes.io/docs/xiguaAp6y3/moondelta-core](https://mooncakes.io/docs/xiguaAp6y3/moondelta-core)（0.3.1；空工程安装日志见 `artifacts/acceptance/mooncakes-0.3.1/`）。当前本地工作区为 0.4.0（S1–S3 事务加固与新增测试），尚未重新发布，不能认为 registry 包已包含这些改动。

## 状态

0.4.0（M1 协议基线 → M2 嵌套读取 → M3 checkpoint 恢复 → M4 嵌套写入 + 类型/安全检查 → S1–S3 事务加固；能力口径为**已验证子集**，不是任意标准 Delta 表的完整兼容）：

-  日志解析与回放：`protocol` / `metaData` / `add` / `remove` / `commitInfo`，`txn` 识别，`cdc` / `domainMetadata` 显式忽略并在输出中提示，未知 action 结构化拒绝，`_last_checkpoint` 识别
-  协议范围（0.2.0 起强制）：只读 `minReaderVersion = 1` 的表——reader v2/v3 与 `readerFeatures` 拒绝并说明原因（column mapping / 删除向量 / v2 checkpoint 为高版本特性）；JSON 与 checkpoint 的 `add.deletionVector` 均拒绝；日志文件名严格按 20 位校验；`add.path` 与 `remove.path` 统一 percent-decoding；写出到 `minReaderVersion = 1, minWriterVersion = 2`（writer > 2 或非空 `writerFeatures` 的表拒绝 append）；日志从非 0 起点/缺版本明确拒绝；**M3 起日志被截断 / checkpoint-only 的表从最新单文件 checkpoint 恢复基底并叠加其后 JSON commit（不再静默读旧快照；多部分 checkpoint 显式拒绝）**
-  快照、版本历史、时间旅行（`snapshot_at`）
-  扫描规划：分区等值过滤 + 文件级 min/max 统计剪枝（stats 损坏显式报错，不再静默返 0）
-  读取：11 种 primitive + UNCOMPRESSED/SNAPPY；**M2 起支持嵌套读取**、**M4 起支持嵌套写入**——`struct` / 标准 3-level `list` / `map`（含 NULL、空集合、null 元素/子字段），读写均与 delta-rs/DuckDB 逐值对拍一致（`artifacts/acceptance/m2|m4/`）
-  事务身份与 intent（S2）：每次 `create_table` / `append_rows` 用操作系统随机源生成 32 位十六进制 txn ID；在创建任何被管理文件之前，把不可变 intent（格式版本、txn ID、目标版本、临时/正式相对路径、预期长度、数据摘要、JSON commit 摘要）原子持久发布并同步目录；数据与日志临时名由 intent 预先固定、排他创建；数据文件名包含 txn ID；Delta 日志仍只使用标准 action，txn ID 只出现在 `commitInfo` 的附加字段；`add.path` 采用最小 percent-encoding（`A-Z a-z 0-9 - _ . ~` 保持字面），使其它引擎写的 `remove` 能与本项目的 `add` 匹配
-  提交顺序（S1/S2）：intent 文件 `fsync` → intent 目录 `fsync` → 数据临时文件完整写入与 `fsync` → 不覆盖 `link` → 数据父目录 `fsync` → JSON 临时文件完整写入与 `fsync` → 不覆盖 `link` → `_delta_log` `fsync`。正式 JSON `link` 是 reader 可见的线性化点，目录同步成功才是 writer 可返回成功的持久边界
-  结果语义（S2）：`PublishOutcomeUnknown(txn, version, path, phase, code)`（数据已可见但目录同步失败，不发布 JSON）与 `CommitOutcomeUnknown(txn, version, path, phase, code)`（commit 已可见但持久性未确认，禁止盲目重放）；`query_transaction(root, txn_id)` 只读返回 committed / committed-unconfirmed / conflict / not-committed / unresolved：比较 intent 摘要与正式 commit 内容，匹配后重新同步文件/目录才升级为确认持久；Windows 没有目录项同步原语，匹配后保持 `committed-unconfirmed`，不升级为确认持久
-  维护锁（S2）：POSIX 固定 `.moondelta.lock` + `flock`（writer 共享、create 独占、恢复器非阻塞独占、`O_CLOEXEC`、拒绝 symlink）；Windows 固定锁文件句柄 + `LockFileEx`（句柄不继承、进程退出由操作系统释放）。同进程多句柄、多线程/多进程竞争与“活跃 writer 时恢复器 busy”均有测试；锁文件永不 unlink 或替换
-  安全清理与恢复（S3）：`recover_table(root, dry_run=true)` 与 CLI `delta recover <root> [--apply]`。默认 dry-run；只有 `delta manage <root>` 显式标记的受管表才允许自动 apply，普通/混合 writer 表永远只预览。持独占锁后校验全部 intent，保护集合来自全部可保留 JSON 历史与受支持 checkpoint；只删除 intent 精确列出的临时名和被证明未引用、长度/摘要吻合的正式 Parquet；旧 `part-*` / `.moondelta-tmp-*` / `mdt*.tmp`、未知文件、软链接、越界路径一律保留并报告；多部分 checkpoint、reader v2+ 或任何超出已支持 reader v1 子集的状态都会关闭删除（只报告）；删除可重入、`ENOENT` 幂等，提交成功不因清理失败改报失败
-  写入限制：进程被强制终止时仍可能留下未引用数据文件或临时文件，它们不进入快照；下一次受管恢复会按 intent 清理。仍不提供分区表 append、历史 VACUUM、跨请求 exactly-once operation ID；Windows 只有进程崩溃一致性，没有 NTFS 断电持久性保证
-  CLI：`inspect / history / scan / read / create / recover / query / manage / version`；错误输出到 stderr、非 0 退出码、未知参数/缺值报错
-  56 个单元测试（含 SHA-256 FIPS 向量、txn ID 唯一性、intent/查询/恢复/锁语义，以及原有 primitive、嵌套、协议门控、checkpoint 与原子发布用例）
-  Linux 事务集成：100 轮 JSON 发布前确定性竞争、连续 reader、冲突重试；23 项故障注入（20 个 syscall/intent + 3 个建表目录/version 0 同步）；SIGKILL 残留恢复、dry-run/受管 apply、历史保护、日志缺口、锁忙、未知文件保留、txn ID 并发唯一性、writer 暂停在数据与 JSON 发布之间时恢复器只得到 busy、清理 unlink 失败后保留 intent 并可重试收敛、真实 `remove` 动作与 checkpoint 引用保护、checkpoint-only 与多部分 checkpoint/不支持协议只报告不删除、软链接保留、真实 commit-unknown 结果查询为 committed。证据见 [事务复验](artifacts/acceptance/transaction-2026-11-11/README.md) 与 `results.json`
-  Windows 事务验收：独立脚本覆盖 intent/提交/清理、查询状态、恢复策略、未知文件保留、跨进程 `LockFileEx` 互斥与并发 txn ID 唯一性（`artifacts/acceptance/test_transactions_windows.py`）。远端 CI 尚未运行。

已用真实数据验证（均为仓库 fixture 读取，D1 已复核）：delta-rs `table_a`（13 个版本回放 → 120 个文件，仓库只含日志、不含数据文件）、Databricks 写出的 COVID-19_NYT 表的 1 个数据文件（157,865 行 6 列，snappy；该表共 8 个数据文件，其余 7 个未入库）。

外部引擎互操作：与 delta-rs 1.6.6、DuckDB 1.5.6 在**指定 flat/nested fixture** 上完成双向对拍（flat 49 PASS / 0 FAIL / 2 项已记录差异，见 [artifacts/interop-flat/README.md](artifacts/interop-flat/README.md)；nested 见 [artifacts/acceptance/m2/](artifacts/acceptance/m2/README.md) 与 [m4/](artifacts/acceptance/m4/README.md)）。11 种 primitive 的写出另经 delta-rs 与 DuckDB 验证（`artifacts/interop-flat/all-primitives-written/`、`artifacts/acceptance/all_types_verify.py`；DuckDB 环境缺 pytz，timestamp 列由 delta-rs/pyarrow 验证）。这些结果不外推到任意 reader v1 表；Spark / Databricks 未验证。完整口径见 [docs/协议兼容性与互操作范围.md](docs/协议兼容性与互操作范围.md)。

## 快速开始

要求：MoonBit 工具链（`moon` / `moonc`，建议 2026-10 之后的版本）。

```bash
# 从 mooncakes.io 安装当前发布版本 0.3.1（本地工作区为 0.4.0，源码构建见下）
moon add xiguaAp6y3/moondelta-core

# 或从源码构建
moon check --target native
moon test --target native

# CLI（源码根目录为 src/，因此包路径带 src/ 前缀）
moon run --target native src/cmd/delta -- inspect fixtures/delta/table_a
moon run --target native src/cmd/delta -- history fixtures/delta/table_a
moon run --target native src/cmd/delta -- scan fixtures/delta/table_a --where "id>=60"
moon run --target native src/cmd/delta -- read fixtures/delta/covid --limit 5
moon run --target native src/cmd/delta -- recover <root>            # 默认 dry-run
moon run --target native src/cmd/delta -- query <root> <txn-id>     # 只读事务查询
moon run --target native src/cmd/delta -- manage <root>             # 显式启用受管清理
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
let (version, txn_id) = @delta.append_rows_with_txn(root, columns, 1750000000000L)
```

事务结果查询与保守恢复（只读查询不会删除任何文件）：

```moonbit
let status = @delta.query_transaction(root, txn_id)
println(@delta.txn_state_name(status.state)) // committed / conflict / unresolved ...
let preview = @delta.recover_table(root, true) // 默认 dry-run
for entry in preview.entries {
  println(entry.action + " " + entry.path + " (" + entry.reason + ")")
}
```

## 能力边界（已验证子集）

| 维度 | 支持 | 不支持 / 显式拒绝 |
|---|---|---|
| protocol | reader v1 读取；writer v2 写出 | reader > 1、`readerFeatures`、非空 `writerFeatures`；未声称任意 reader v1 表全部兼容 |
| 日志 | version 0 起连续 JSON commit；20 位文件名；截断 / checkpoint-only 从单文件 checkpoint 恢复 | 多部分 checkpoint；checkpoint 缺失 protocol/metaData；checkpoint 中真实出现的未知 action |
| schema | 11 种 primitive 与 struct / 3-level list / map 读写；flat 与 nested 叶均校验 Delta 类型和 Parquet 物理类型 | decimal、uuid、`timestamp_ntz`、timestamp millis/nanos |
| 压缩 | UNCOMPRESSED、SNAPPY | ZSTD、GZIP、BROTLI、LZ4 |
| 写入类型 | `boolean` / `byte` / `short` / `int` / `long` / `float` / `double` / `string` / `binary` / `date` / `timestamp`，包含嵌套叶 | decimal、uuid、`timestamp_ntz`、timestamp millis/nanos |
| 写入表 | 非分区 append；校验列名/类型/NOT NULL；put-if-absent | 分区 append；列在 Parquet 中恒为 Optional |
| 删除向量 | JSON 与 checkpoint 的 `add.deletionVector` 均拒绝 | column mapping、v2 checkpoint、CDF、MERGE/UPDATE/DELETE |
| 存储 | 本地文件系统 | 对象存储（S3/R2） |
| 事务与恢复 | OS 随机 txn ID、持久 intent、`query_transaction`、dry-run / 显式受管 apply 清理、POSIX `flock` 与 Windows `LockFileEx` 维护锁 | 跨请求 exactly-once operation ID、历史 VACUUM / 日志过期、分区表 append |
| 断电持久性 | 已在指定 CI 配置下验证（100 轮有效切断，见 `artifacts/acceptance/powerloss/ci-2026-11-12/`）；结论仅覆盖该记录的 host 镜像/内核/ext4/设备模型 | 不宣称 Windows NTFS、WSL `/mnt/*`、NFS/SMB/FUSE 的强持久性 |

分区值回填覆盖 11 种 primitive；date 同时接受 `YYYY-MM-DD` 与 epoch 天数，timestamp 分区值使用 epoch 微秒。`add.stats` 必须是 JSON 编码字符串；非字符串或损坏内容直接报错。CLI 成功退出 0，错误走 stderr 并以非 0 退出。

## 依赖说明：vendored parquet

`mizchi/parquet@0.2.2` 声明了 `mizchi/duckdb@0.6.4`，后者仍使用旧版 `moon.mod.json`，当前 MoonBit 工具链会拒绝解析。该依赖只被上游的 DuckDB 演示子包使用，因此本项目把 parquet 的 `src/` 以 Apache-2.0 许可证 **vendored 到 `src/vendor/parquet`**，删除了 `cmd/duckdb_e2e` 与 `playground_bridge` 两个子包；新增 shredded 嵌套读写入口并修复 REPEATED definition level 规则，writer 另扩展 BOOLEAN / FLOAT / DOUBLE 与 DATE / TIMESTAMP_MICROS logical annotation（见 NOTICE）。详见 `src/vendor/parquet/NOTICE.md`。

## Fixtures 与数据来源

`fixtures/` 中仅包含**测试数据**（不含上游实现代码），来源与许可证见 `fixtures/README.md`：

- `fixtures/delta/table_a`：delta-rs 测试表（13 个日志版本 + checkpoint），Apache-2.0
- `fixtures/delta/covid`：Databricks 写出的 COVID-19_NYT 表数据文件，Apache-2.0
- `fixtures/parquet/list_columns.parquet`：apache/parquet-testing 的嵌套 LIST 正读样例，Apache-2.0
- `fixtures/checkpoint-negative/`：checkpoint 删除向量/未知 action/缺失状态负向用例，由 `fixtures/generate_checkpoint_negative.py` 生成

## 路线图

完整路线图见 [docs/ROADMAP.md](docs/ROADMAP.md)，事务阶段验收见 [docs/S1-S5事务验收路线图.md](docs/S1-S5事务验收路线图.md)。0.4.0 已完成 M1–M4、S1–S3 的代码与本机验收，当前剩余重点：

- S5 掉电持久性：已在手动 CI（`.github/workflows/s5-powerloss.yml`，run 43157333594）的 100 轮有效切断矩阵中通过，证据与精确配置见 `artifacts/acceptance/powerloss/ci-2026-11-12/`；结论只覆盖该记录的组合，Windows NTFS 仍未验证；
- 远端 CI 实际运行一次并保存 artifacts（本地 Linux/Windows 矩阵已通过，但不能替代）；
- 发布包含本轮事务加固的新版本并复核 registry 安装；
- 补带时区/精度语义的 `timestamp_ntz`、timestamp millis/nanos；
- ZSTD/GZIP/BROTLI 解压、分区表 append、对象存储（S3/R2）、CDF、Float/Timestamp 写入；
- 流式读取与 row-group 级剪枝、Arrow IPC / DataFrame / moonice 互操作。

## 开发

```bash
moon check --target native
moon test --target native
moon fmt
```

CI：`.github/workflows/ci.yml`（安装最新 MoonBit 工具链 → `moon update` → check/test/build → Linux 事务矩阵；Windows runner 单独运行 Windows 矩阵）。本机 Linux/Windows 均 56/56 通过（证据见 `artifacts/acceptance/transaction-2026-11-11/`）；0.4.0 工作区的远端 Actions 尚未推送运行。0.3.1 提交 `cc461d2` 的历史 Actions 已通过：<https://github.com/xiguaAp6y3/moondelta-core/actions/runs/41164588241>。

## 许可证

Apache-2.0。Delta Lake 协议参考 <https://github.com/delta-io/delta>（Apache-2.0）；本项目为原创实现，未复制上游代码。
