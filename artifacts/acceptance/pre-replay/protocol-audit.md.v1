# D1 协议审计：moondelta-core 0.1.1

> 任务来源：[docs/协议兼容性与互操作范围.md 第 6 节任务 D1](../docs/协议兼容性与互操作范围.md#L91-L97)。本报告以该文档第 1/2/3/6 节为口径权威，只写**实测或代码证据**；README / 申报书 / ROADMAP 的声明只作为“待核对对象”，不作为证据。
>
> 审计对象：`E:\Project\Moonbit Hackson\P1 RE`（模块 `xiguaAp6y3/moondelta-core@0.1.1`，`moon.mod` 声明 0.1.1）。
> 审计日期：2026-10-20。审计期间仓库由其他任务并行修改（D2 新增 `src/examples/interop_flat_writer/`），本审计**未修改** `docs/`、`README.md`、`申报书.md`、`src/`、`fixtures/`、`moon.mod`。

---

## 1. 结论摘要

**总判定：协议日志层（reader v1 / version 0 起连续 JSON）与 flat 读取主路径的真实能力与文档闭集基本一致；但“闭集之外必须结构化拒绝”这一条没有落实，存在 27 项标记为“静默”的输入处理（其中 5 项会造成数据正确性损害：读到已删除行 T37、静默读旧快照 C5、读到错类型 Q12、append 写出错类型 T28、stats 丢失导致行数统计为 0 L19），另有 1 项 CLI 退出码缺陷。**

结论分布（122 项，逐项见 §3，计数已按矩阵实际行数核对）：

| 结论 | 数量 | 含义 |
|---|---:|---|
| 已实现且有测试 | 27 | 有实现，且 `src/delta_wbtest.mbt` 中存在具名测试覆盖（运行结果见 §2） |
| 已实现（无测试） | 20 | 有实现，但 14 个测试中没有对应断言，或只有间接覆盖 |
| 只识别 | 4 | 能解析/展示（如 nested schema 的 kind、checkpoint 元信息），但不执行对应语义 |
| 明确拒绝 | 24 | 返回闭集内 `DeltaError`（`IoError/ParseError/Unsupported/NotFound`），不返回数据 |
| 未覆盖 | 47 | 无实现；其中多数表现为**静默接受 / 静默忽略 / 静默 0 行**（逐项在“行为特征”列标注） |

**必须让 D4 处理的最高优先级口径冲突**：README/申报书声称“遇到即报错，不静默降级”“不是静默 0 行”，但本审计实测到 reader v2/v3 被接受、删除向量被忽略后读全量、未知 action 被忽略、类型错配静默读成错值、stats 为 object 时 `rows: 0`、CLI 所有错误退出码为 0。明细见 §5。

### 1.1 关键发现速览（完整版见 §4）

| 编号 | 严重度 | 发现 | 一句话证据 |
|---|---|---|---|
| F1 | 严重 | `add.deletionVector` 被静默忽略，读到已删除行 | 3 行 fixture 声明 `cardinality: 1`，`read` 返回 3 行（[out/36](protocol-audit/out/36-read-deletion-vector.out)） |
| F2 | 严重 | 日志被截断但有 checkpoint 的表被静默当作旧版本读取 | JSON 0..9 + checkpoint v10 + `_last_checkpoint=10`，`inspect` 报 `current version: 9, files: 90`，无错误（[out/51](protocol-audit/out/51-inspect-checkpoint-truncated.out)） |
| F3 | 严重 | reader v2/v3、`readerFeatures`（含未知必需 feature）不校验、直接读 | reader=3/writer=7 表 `read` 成功（[out/23](protocol-audit/out/23-read-reader-v3-features.out)）；delta-rs 1.6.6 写出的 reader=2 表同样被接受（D2 交叉证据，见 §4.3） |
| F4 | 严重 | CLI 任何错误都退出码 0 | 121 次带退出码的运行（112 个编号 CLI case + 9 个探针/Parquet 复核脚本）全部 `exit 0`，包括 `not found`/`parse error`/`unsupported`（[out/17](protocol-audit/out/17-inspect-nonexistent.out)；统计见 §3.7） |
| F5 | 严重 | `append_rows` 不校验列名/类型与表 schema 是否一致，写出“与表定义矛盾”的数据 | 把 long 列写成 string 后 `read` 输出 `id (long) = not-a-long`（[out/probe-cli-append-mismatch](protocol-audit/out/probe-cli-append-mismatch.out)） |
| F6 | 高 | Delta schema 与 Parquet 物理类型不一致时静默转换 | schema `id: long`，parquet 为 string，`read` 输出 `abc/def/ghi`（[out/87](protocol-audit/out/87-read-type-mismatch.out)） |
| F7 | 高 | `add.stats` 为 JSON object（非 string）时被静默丢弃 | 实际 3 行，`inspect` 显示 `rows: 0`，`scan` 不剪枝（[out/100](protocol-audit/out/100-inspect-stats-object.out)） |
| F8 | 高 | 未知 action（`cdc`/`txn`/`domainMetadata`/`sidecar`）静默忽略 | replay 继续，version=1 正常返回（[out/32](protocol-audit/out/32-inspect-unknown-actions.out)） |
| F9 | 中 | `column mapping` 表未识别，报错原因误导为“列缺失” | `error: unsupported: column 'id' is missing from parquet file`（[out/38](protocol-audit/out/38-read-column-mapping.out)） |
| F10 | 中 | `create_table` 不校验 schema JSON，且会覆盖已有表的 v0 | 非法 schema 创建成功（[out/113](protocol-audit/out/113-create-invalid-schema.out)）；覆盖后旧列静默消失（[out/126](protocol-audit/out/126-read-overwritten.out)） |
| F11 | 中 | CLI `scan --where` 解析失败后继续执行“无过滤扫描”（且退出 0） | `date>=2020-05-01` 打印 invalid number 后仍返回全部 8 文件（[out/14](protocol-audit/out/14-scan-covid-date-where.out)） |
| F12 | 低 | 缺失数据文件、分区 null 值等错误信息缺少关键上下文 | `io error: No such file or directory` 不含路径（[out/86](protocol-audit/out/86-read-missing-data-file.out)） |

**值得肯定的实现质量**（用于 D4 保留 / 加固措辞，不夸大）：nested schema 的拒绝信息同时给出列名与 kind，并且发生在打开 Parquet **之前**（用“数据文件不存在”的 nested 表证明，[out/73](protocol-audit/out/73-read-nested-missing-file.out)）；ZSTD/GZIP/BROTLI/LZ4 给出带文件名的 `Unsupported`（[out/84](protocol-audit/out/84-read-codec-zstd.out)）；日志缺版本/非 0 起点/损坏 Parquet 均为结构化错误；重复 add/remove 收敛正确（[out/47](protocol-audit/out/47-inspect-dup-add-remove.out)）；min/max 剪枝含 `min==max` 边界正确（[out/98](protocol-audit/out/98-scan-min-max-eq-gt.out)、[out/99](protocol-audit/out/99-scan-min-max-eq-ge.out)）。

---

## 2. 审计方法、环境与可重放命令

### 2.1 工具链与运行环境（实测）

| 项 | 值 | 证据 |
|---|---|---|
| 工作目录 / 仓库根 | `E:\Project\Moonbit Hackson\P1 RE` | — |
| `moon` | `0.1.20261012 (748b63c 2026-10-12)` | §2.3 命令 `moon --version` |
| `moonc` | `v0.10.15+943464bfa (2026-10-12)` | 同上 |
| CLI | 审计开始时仓库内预编译 `_build\native\debug\build\cmd\delta\delta.exe` 时间戳为 `2026-10-19 00:33`，而 `src/schema.mbt`、`src/write.mbt` 修改时间为 `2026-10-19 09:51` —— **预编译二进制落后于源码**。审计前已用 `moon build --target native src/cmd/delta` 重建（[build-cli.out](protocol-audit/build-cli.out)），本报告所有 CLI 结果均来自重建后的二进制（`2026-10-20 20:17`）。 | §2.3 |
| Python 引擎 | `.tools\pyenv\Scripts\python.exe`：pyarrow 26.0.0 / duckdb 1.5.6 / deltalake 1.6.6 / pandas 3.0.6（仅用于生成 fixture 与读回校验，不改环境） | §2.3 |
| 仓库状态 | `git status --short`：` M .gitignore`、`?? artifacts/`、`?? docs/协议兼容性与互操作范围.md`、`?? src/examples/interop_flat_writer/`。审计未修改任何 tracked 文件；`src/examples/interop_flat_writer/` 是 D2 并行新增的示例，本报告使用它生成 append 证据（§3.6 T26），并在 §6 注明该文件不属于 0.1.x 申报声明。 | §2.3 |

### 2.2 `moon check` / `moon test`（原始输出已落盘）

- `moon check --target native`：**0 errors，43 warnings**，退出码 0。输出：[check.out](protocol-audit/check.out)（首轮）、[check-final.out](protocol-audit/check-final.out)（收尾复核，同样 0 errors）。43 条 warning 的构成见 §5 第 5 条（并非全部来自 vendored parquet）。
- `moon test --target native`：**`Total tests: 14, passed: 14, failed: 0.`**，退出码 0。输出：[test.out](protocol-audit/test.out)、[test-final.out](protocol-audit/test-final.out)。
- 14 个具名测试的清单（`Select-String '^test "'`）见 [code-evidence.out §12](protocol-audit/out/code-evidence.out)。

### 2.3 可重放命令（全部原始输出在 `out/`，命令清单在 `commands.txt`）

```powershell
# 1) 工具链
moon check --target native          # -> check.out / check-final.out
moon test  --target native          # -> test.out / test-final.out
moon build --target native src/cmd/delta   # -> build-cli.out

# 2) 自造 fixture（手写 JSON log + pyarrow 写数据文件）
.tools\pyenv\Scripts\python.exe artifacts\protocol-audit\gen_fixtures.py   # -> gen_fixtures.out

# 3) CLI 矩阵（112 个编号 case，逐条保存 stdout/stderr/exit code）
pwsh -NoProfile -File artifacts\protocol-audit\run_cli.ps1                  # -> commands.txt + out/*.out

# 4) 库级探针（append_rows / snapshot_at / create_table 等 CLI 触发不到的行为）
pwsh -NoProfile -File artifacts\protocol-audit\run_probe.ps1                # -> out/probe-*.out
```

- fixture 生成脚本：[gen_fixtures.py](protocol-audit/gen_fixtures.py)，重建 47 个表；文件级 SHA-256 清单见 [fixtures.sha256](protocol-audit/fixtures.sha256)。
- 命令清单（含每条命令的退出码）：[commands.txt](protocol-audit/commands.txt)。
- 全部 112 个编号 CLI case（另有库级探针/Parquet 复核 9 个）的原始输出：`artifacts/protocol-audit/out/`，本报告用 `out/NN-....out` 形式引用（见 §8.2）。
- 库级探针为什么需要隔离副本：仓库 `moon.mod` 的 `source = "src"` 只索引 `src/` 下的包（实测 `moon run artifacts/...` 报 “Cannot find package to build based on input path”），而 D1 不允许改 `src/`；因此把模块复制到 [probe-module/](protocol-audit/probe-module) 并在其 `src/probe` 下运行探针。探针源码：[probe-module/src/probe/main.mbt](protocol-audit/probe-module/src/probe/main.mbt)。

### 2.4 结论判定规则（避免“README 即证据”）

1. 只把 `src/delta_wbtest.mbt` 中 `test "..."` 具名用例计为“有测试”；`moon test` 必须实际通过（14/14）。
2. 代码证据使用 `文件:行号`；命令证据使用 `out/*.out` 的原文；两者都给出时，以命令实测为准。
3. “明确拒绝”要求返回 `DeltaError` 且命令输出以 `error: io error/parse error/unsupported/not found` 开头；退出码为 0 不影响“拒绝”判定，但单独列为 E4 缺陷。
4. “未覆盖”包含“静默接受/静默忽略”：这是本报告最重要的结论类别——实现没有识别该输入，也没有报错。

---

## 3. 完整矩阵

列说明：**结论** 取 §1 的 5 个标签之一；**行为特征** 描述实际可观察行为（尤其是否静默）；**证据** 同时给代码行号、测试名或 `out/` 文件。`out/` 链接相对本报告所在的 `artifacts/` 目录。

### 3.1 protocol（reader/writer version 与 features）

| ID | 能力项 | 结论 | 行为特征 | 证据 | 缺口与风险 | 建议补测命令 |
|---|---|---|---|---|---|---|
| P1 | 读取 reader v1 表 | 已实现且有测试 | 正常 | 测试 `snapshot: replay 13 versions to 120 files and time travel`；[out/02](protocol-audit/out/02-inspect-table-a.out)；[src/actions.mbt:15-25](../src/actions.mbt#L15-L25) | 只验证 v1 | `delta.exe inspect fixtures/delta/table_a` |
| P2 | 读取 reader v2 表 | 未覆盖 | **静默接受** | [out/20](protocol-audit/out/20-inspect-reader-v2.out)、[out/21](protocol-audit/out/21-read-reader-v2.out)（protocol: reader=2 writer=5，read 3 行成功） | 违反文档 §1/§2“高于 reader v1 必须拒绝”；D2 的 delta-rs 写入 reader=2 表同样被接受（[D2 证据](interop-flat/raw/f_probe_reader2_inspect.stdout.txt)） | `delta.exe inspect artifacts/protocol-audit/fixtures/protocol-reader-v2` |
| P3 | 读取 reader v3 + `readerFeatures`（含未知必需 feature） | 未覆盖 | **静默接受** | [out/22](protocol-audit/out/22-inspect-reader-v3-features.out)、[out/23](protocol-audit/out/23-read-reader-v3-features.out)；`readerFeatures` 在 `src/` 中 0 命中（[code-evidence.out §10](protocol-audit/out/code-evidence.out)） | 未知必需 feature 会被忽略，可能读到不支持语义的数据（如 DV） | 同上，fixtures/protocol-reader-v3-features |
| P4 | 任意 `minWriterVersion`（如 5）+ `writerFeatures` | 未覆盖 | **静默接受** | [out/28](protocol-audit/out/28-inspect-writer-v5.out)、[out/29](protocol-audit/out/29-read-writer-v5.out)；空快照 append 到 writer=5 表成功（[probe-library](protocol-audit/out/probe-library.out) `[append-writer-v5]`） | 写入端同样不校验 writer protocol/feature | `delta.exe inspect artifacts/protocol-audit/fixtures/writer-v5-features` |
| P5 | `readerFeatures` / `writerFeatures` 字段解析 | 未覆盖 | **静默忽略**（字段被丢弃） | [src/actions.mbt:15-25](../src/actions.mbt#L15-L25) 只读两个版本号；grep 0 命中 | 无法判断表能力，也无法给出拒绝原因 | 在 `parse_protocol` 增加字段并断言 |
| P6 | 未知必需 feature 的拒绝 | 未覆盖 | **静默接受** | 同 P3 | 直接违反文档 §1 最后一段 | 新增 reader v3 + `readerFeatures:["unknownRequiredFeature"]` 负向测试 |
| P7 | 没有 protocol action 的表 | 未覆盖 | **静默默认** reader=1/writer=1 | [out/24](protocol-audit/out/24-inspect-no-protocol.out)、[out/25](protocol-audit/out/25-read-no-protocol.out)；[src/snapshot.mbt:2-4](../src/snapshot.mbt#L2-L4)、[:129](../src/snapshot.mbt#L129) `default_protocol()` | Delta 规范要求每个快照有 protocol；缺失时应报错而不是替用户补 1 | `delta.exe inspect artifacts/protocol-audit/fixtures/no-protocol` |
| P8 | `protocol.minReaderVersion` 缺失 | 未覆盖 | **静默默认 1** | [out/26](protocol-audit/out/26-inspect-protocol-missing-reader.out)；[src/actions.mbt:16-19](../src/actions.mbt#L16-L19) `None => 1` | 坏日志被掩盖 | 同上 fixtures/protocol-missing-reader |
| P9 | protocol 版本字段类型错误（字符串 `"2"`） | 未覆盖 | **静默默认 1** | [out/27](protocol-audit/out/27-inspect-protocol-string-version.out)（两种版本都被默认成 1） | 类型容错掩盖坏日志 | 同上 fixtures/protocol-string-version |
| P10 | 空日志文件（0 字节 v0） | 未覆盖 | **静默返回空快照** | [out/30](protocol-audit/out/30-inspect-empty-log.out)、[out/31](protocol-audit/out/31-read-empty-log.out)（0 文件 0 行，无 error） | 没有 protocol/metaData 也不报错，`read` 输出空表头 | `delta.exe inspect artifacts/protocol-audit/fixtures/empty-log` |
| P11 | 写入端 protocol 固定 `reader=1, writer=2` | 已实现（无测试） | 正常 | [src/write.mbt:174](../src/write.mbt#L174)；[out/111](protocol-audit/out/111-inspect-created-empty.out) | 无测试断言 v0 的 protocol 字段 | 新增测试断言 create 后 `snapshot.protocol == {1,2}` |
| P12 | reader/writer version 与 features 的一致性校验（reader>=2 必须列 feature） | 未覆盖 | 无（不校验） | 无对应代码 | 是 F3 的根因之一 | 见 §6 补测建议 |

### 3.2 JSON log 与 actions

| ID | 能力项 | 结论 | 行为特征 | 证据 | 缺口与风险 | 建议补测命令 |
|---|---|---|---|---|---|---|
| L1 | `commitInfo` 解析 | 已实现且有测试 | 正常 | 测试 `actions: parse protocol and metadata from real log line`；[out/03](protocol-audit/out/03-history-table-a.out)、[out/09](protocol-audit/out/09-history-covid.out) | — | `delta.exe history fixtures/delta/table_a` |
| L2 | `protocol` action 解析 | 已实现且有测试 | 正常 | 测试同上；[src/actions.mbt:15-25](../src/actions.mbt#L15-L25) | 解析后不校验（P2/P3） | 同上 |
| L3 | `metaData` 解析（`schemaString` 必需，分区列/format/configuration） | 已实现且有测试 | 正常 | 测试同上；[out/02](protocol-audit/out/02-inspect-table-a.out)；[src/actions.mbt:44-69](../src/actions.mbt#L44-L69) | `id` 缺失时静默为 `""`；`format.provider` 只展示不校验 | 同上 |
| L4 | `metaData.schemaString` 缺失 | 明确拒绝 | 正常（ParseError） | [src/actions.mbt:50-53](../src/actions.mbt#L50-L53) | 无 fixture/测试覆盖该分支 | 新增 fixture：metaData 无 schemaString |
| L5 | `add.path` 必需 | 已实现（无测试） | 正常（ParseError） | [out/44](protocol-audit/out/44-inspect-add-missing-path.out)；[src/actions.mbt:72-92](../src/actions.mbt#L72-L92) | 无具名测试 | `delta.exe inspect artifacts/protocol-audit/fixtures/add-missing-path` |
| L6 | `add.size/modificationTime/dataChange` 缺失 | 未覆盖 | **静默默认 0/0/true** | [src/actions.mbt:78-89](../src/actions.mbt#L78-L89) | 审计/统计字段可能是假值 | 新增 fixture：add 只给 path |
| L7 | `remove` 解析 | 已实现（无测试） | 正常 | [src/actions.mbt:95-103](../src/actions.mbt#L95-L103)；[out/49](protocol-audit/out/49-read-dup-add-remove.out) | 无具名测试 | 见 L8/L9 |
| L8 | 重复 `add` 同路径收敛（后写覆盖） | 已实现（无测试） | 正常 | [src/snapshot.mbt:24-42](../src/snapshot.mbt#L24-L42)；[out/47](protocol-audit/out/47-inspect-dup-add-remove.out) | 无具名测试 | 新增测试：同路径 add 两次后 files=1 |
| L9 | `remove` 收敛（含删除不存在路径） | 已实现（无测试） | 正常 | [src/snapshot.mbt:45-56](../src/snapshot.mbt#L45-L56)；[out/47](protocol-audit/out/47-inspect-dup-add-remove.out)（v2 remove → 0 文件，v3 add 回来 → 1 文件，v4 remove 不存在路径无影响） | 无具名测试 | 同上 |
| L10 | 未知 action（`cdc`/`txn`/`domainMetadata`/`sidecar`） | 未覆盖 | **静默忽略** | [src/actions.mbt:148](../src/actions.mbt#L148) `UnknownAction("unrecognized action")`；[src/snapshot.mbt:148](../src/snapshot.mbt#L148) `UnknownAction(_) => ()`；[out/32](protocol-audit/out/32-inspect-unknown-actions.out)~[out/34](protocol-audit/out/34-read-unknown-actions.out)；[probe-library](protocol-audit/out/probe-library.out) `[unknown-action-api]` | 违反文档 §1“未知必需 feature 必须拒绝”；`cdc` 数据完全不可见、`txn` 幂等语义丢失 | `delta.exe inspect artifacts/protocol-audit/fixtures/unknown-actions` |
| L11 | 非法 JSON 行 | 明确拒绝 | 正常（ParseError） | [src/actions.mbt:122-127](../src/actions.mbt#L122-L127)；[out/43](protocol-audit/out/43-inspect-malformed-json.out) | 错误信息不含行号/片段，定位困难 | 同上 fixtures/malformed-json |
| L12 | version 0 起连续回放 | 已实现且有测试 | 正常 | 测试 `snapshot: replay 13 versions...`；[out/02](protocol-audit/out/02-inspect-table-a.out)（13 版本 → 120 文件） | — | `delta.exe scan fixtures/delta/table_a` |
| L13 | 日志缺中间版本（v0、v2，缺 v1） | 明确拒绝 | 正常（NotFound） | [src/log.mbt:80-85](../src/log.mbt#L80-L85)；[out/40](protocol-audit/out/40-inspect-log-gap.out) | 错误信息“log version 1 is missing (checkpoint-only or truncated table?)”会误导排查方向 | `delta.exe inspect artifacts/protocol-audit/fixtures/log-gap` |
| L14 | 日志从非 0 起点（只有 v3） | 明确拒绝 | 正常（Unsupported） | [src/snapshot.mbt:111-115](../src/snapshot.mbt#L111-L115)；[out/41](protocol-audit/out/41-inspect-log-start-v1.out) | 现有测试名 `checkpoint-only table is rejected` 实际构造的是本场景（`v3` 起点），不是真正的 checkpoint-only（见 §5 第 4 条） | `delta.exe inspect artifacts/protocol-audit/fixtures/log-start-v1` |
| L15 | 20 位零填充日志文件名（写入） | 已实现且有测试 | 正常 | 测试 `log: table_a exposes 13 versions with 20-digit names`；[src/log.mbt:9-18](../src/log.mbt#L9-L18) | — | `moon test --target native` |
| L16 | 非 20 位日志文件名的读取 | 只识别 | **宽松发现 + 固定路径读取 → 误导性 NotFound** | [src/log.mbt:22-33](../src/log.mbt#L22-L33) 从任意文件名前导数字取版本；[:80](../src/log.mbt#L80) 又拼 20 位路径；[out/42](protocol-audit/out/42-inspect-nonstandard-name.out)；[probe-library](protocol-audit/out/probe-library.out) `[nonstandard-log-name]`（发现 1 个版本，随后报 v0 缺失） | 版本发现与读取路径不一致；Delta 规范要求 20 位，但实现既不拒绝也不支持短名 | 新增测试：`0.json` 单文件表 → 期望明确“非法日志文件名”错误 |
| L17 | 非数字前缀的 `.json` 文件（如 `notes.json`） | 未覆盖 | 可能被当作 version 0 | [src/log.mbt:63-67](../src/log.mbt#L63-L67) `version_from_name` 无数字时返回 0 | 若 v0 缺失则表现为“v0 缺失”，若 v0 存在则去重掩盖，行为不确定 | 新增测试：`_delta_log/notes.json` + 正常 v0 |
| L18 | 单个 commit 文件内多 action 的原子性 | 未覆盖 | 中途报错时已应用的 action 不向前传播（`raise`） | [src/snapshot.mbt:134-152](../src/snapshot.mbt#L134-L152) | 由于 snapshot 不返回，风险低；但 `read_actions` 先解析全部再回放，行为实际是“全或无” | 新增测试：v1 中 add 后跟非法 JSON 行 |
| L19 | `add.stats` 为 JSON object（非 string） | 未覆盖 | **静默丢弃**：`inspect` 行数变 0，`scan` 不剪枝 | [src/actions.mbt:90](../src/actions.mbt#L90)；[out/100](protocol-audit/out/100-inspect-stats-object.out)、[out/101](protocol-audit/out/101-scan-stats-object-where.out) | 实际 3 行的表被显示为 0 行，统计不可信（F7） | `delta.exe inspect artifacts/protocol-audit/fixtures/stats-object` |

### 3.3 checkpoint

| ID | 能力项 | 结论 | 行为特征 | 证据 | 缺口与风险 | 建议补测命令 |
|---|---|---|---|---|---|---|
| C1 | `_last_checkpoint` 识别（version/size/parts） | 已实现且有测试 | 正常 | 测试 `log: table_a exposes 13 versions with 20-digit names`；[src/log.mbt:99-113](../src/log.mbt#L99-L113)；[out/02](protocol-audit/out/02-inspect-table-a.out)（last checkpoint: version=10） | 只读元信息，不解析 checkpoint 内容 | `delta.exe inspect fixtures/delta/table_a` |
| C2 | `_last_checkpoint` 内容非法 JSON | 未覆盖 | **静默忽略**（返回 None） | [src/log.mbt:104-105](../src/log.mbt#L104-L105) `catch { _ => return None }`；[out/53](protocol-audit/out/53-inspect-bad-last-checkpoint.out)（无 last checkpoint 行、无 error） | 用户不会知道 checkpoint 元信息坏了 | `delta.exe inspect artifacts/protocol-audit/fixtures/bad-last-checkpoint` |
| C3 | checkpoint 文件存在提示 | 已实现且有测试 | 正常（仅提示） | [src/log.mbt:117-129](../src/log.mbt#L117-L129)；测试 `log: table_a...`；[out/02](protocol-audit/out/02-inspect-table-a.out) | 只按后缀识别 | 同上 |
| C4 | checkpoint-only 表（只有 checkpoint + `_last_checkpoint`，无 JSON） | 明确拒绝 | 结构化 NotFound，但消息为“has no log entries” | [src/snapshot.mbt:107-110](../src/snapshot.mbt#L107-L110)；[out/50](protocol-audit/out/50-inspect-checkpoint-only.out) | 消息未说明 checkpoint-only；现有测试未覆盖该真实场景（见 §5 第 4 条） | `delta.exe inspect artifacts/protocol-audit/fixtures/checkpoint-only` |
| C5 | 日志被截断但 checkpoint 存在（JSON 0..9 + checkpoint v10 + `_last_checkpoint=10`） | 未覆盖 | **静默返回旧快照**（v9、90 文件），无 error | [out/51](protocol-audit/out/51-inspect-checkpoint-truncated.out)、[out/52](protocol-audit/out/52-history-checkpoint-truncated.out) | **严重**：违反文档 §1/§2“日志被截断或只能依靠 checkpoint 的表必须明确返回不支持错误”；下游会以为表只有 90 个文件/90 行 | `delta.exe inspect artifacts/protocol-audit/fixtures/checkpoint-truncated` |
| C6 | 解析 checkpoint Parquet 重建快照 | 未覆盖 | 无实现 | [docs/ROADMAP.md:24-27](../docs/ROADMAP.md#L24-L27) 列为 0.2；`src/` 无 checkpoint parquet 读取路径 | 文档已明确不承诺，风险可接受 | — |
| C7 | 多段 checkpoint（`*.checkpoint.0000000001.0000000002.parquet`） | 未覆盖 | 不识别（后缀匹配严格） | [src/log.mbt:124](../src/log.mbt#L124) 仅匹配 `.checkpoint.parquet` | 多段 checkpoint 表会漏提示 | 新增 fixture |

### 3.4 schema

| ID | 能力项 | 结论 | 行为特征 | 证据 | 缺口与风险 | 建议补测命令 |
|---|---|---|---|---|---|---|
| S1 | `boolean` | 已实现且有测试 | 正常 | 测试 `write: boolean and double columns roundtrip`；[out/122](protocol-audit/out/122-read-appended-flat.out) | — | `delta.exe read artifacts/protocol-audit/fixtures/appended-flat` |
| S2 | `integer` / `int` | 已实现且有测试 | 正常（映射 Int32） | 测试 `actions: parse protocol...` + `read: real Databricks-written covid table`；[src/schema.mbt:38-41](../src/schema.mbt#L38-L41) | — | `delta.exe read fixtures/delta/covid --limit 3` |
| S3 | `long` | 已实现且有测试 | 正常 | 测试 write/read、partition；[src/schema.mbt:41-42](../src/schema.mbt#L41-L42) | — | 同上 |
| S4 | `float` | 已实现（无测试） | 正常 | [out/83](protocol-audit/out/83-read-float-col.out)；[src/schema.mbt:43-44](../src/schema.mbt#L43-L44) | 无读取测试 | `delta.exe read artifacts/protocol-audit/fixtures/float-col` |
| S5 | `double` | 已实现且有测试 | 正常 | 测试 `write: boolean and double columns roundtrip` | — | 同上 |
| S6 | `string` | 已实现且有测试 | 正常 | 多个测试；[out/11](protocol-audit/out/11-read-covid-limit5.out) | — | — |
| S7 | `binary` | 已实现（无测试） | 正常（CellBin） | [src/read.mbt:40](../src/read.mbt#L40)、[src/write.mbt:49](../src/write.mbt#L49) | 无读写回环测试 | 新增 binary fixture + 写读测试 |
| S8 | `date` | 已实现（无测试） | 正常但输出原始天数（18262） | [out/82](protocol-audit/out/82-read-date-col.out)；[src/vendor/parquet/metadata.mbt:423-427](../src/vendor/parquet/metadata.mbt#L423-L427) 仅映射 converted_type=0 | 语义正确（Delta date 即 days），但 CLI 无 ISO 展示；无测试 | `delta.exe read artifacts/protocol-audit/fixtures/date-col` |
| S9 | `timestamp`（INT96 物理） | 已实现（无测试） | 正常（输出微秒整数） | [out/81](protocol-audit/out/81-read-int96-timestamp.out)；[src/vendor/parquet/metadata.mbt:417-418](../src/vendor/parquet/metadata.mbt#L417-L418) | 无测试；与 `timestamp_ntz` 不区分 | `delta.exe read artifacts/protocol-audit/fixtures/int96-timestamp` |
| S10 | `timestamp_ntz` | 只识别 | 映射为 `timestamp`，时区语义丢失 | [src/schema.mbt:55](../src/schema.mbt#L55) | 语义不正确 | 建议单独标记 UnknownType 或实现 ntz |
| S11 | `decimal(p,s)` | 明确拒绝 | inspect 显示 `decimal(10,2)`；read 返回 Unsupported | [out/60](protocol-audit/out/60-inspect-decimal-schema.out)、[out/61](protocol-audit/out/61-read-decimal-schema.out)；[src/schema.mbt:17-29](../src/schema.mbt#L17-L29)、[:124-127](../src/schema.mbt#L124-L127) | — | `delta.exe read artifacts/protocol-audit/fixtures/decimal-schema` |
| S12 | uuid（Delta schema 写 `"uuid"`） | 明确拒绝 | inspect 显示 `unknown(uuid)`；read 返回 Unsupported | [out/63](protocol-audit/out/63-inspect-uuid-schema-unknown.out)、[out/64](protocol-audit/out/64-read-uuid-schema-unknown.out)；[src/schema.mbt:120-123](../src/schema.mbt#L120-L123) | Delta 规范无该类型，归入未知类型是合理行为 | 同上 |
| S13 | Parquet UUID logical type + Delta schema 声明 `string` | 未覆盖 | **静默读成 Binary 字节，按 string 输出乱码** | [out/65](protocol-audit/out/65-inspect-uuid-parquet-logical.out)、[out/66](protocol-audit/out/66-read-uuid-parquet-logical.out)；[src/vendor/parquet/metadata.mbt:423-429](../src/vendor/parquet/metadata.mbt#L423-L429) | 静默错误数据（语义丢失），不是拒绝 | 新增测试：UUID 列应 Unsupported |
| S14 | `struct` schema | 明确拒绝 | parse/inspect 只识别 kind，read 明确拒绝并含列名+kind | [out/67](protocol-audit/out/67-inspect-nested-struct.out)、[out/68](protocol-audit/out/68-read-nested-struct.out)；测试 `schema: nested type is marked and rejected...`；[src/schema.mbt:62-70](../src/schema.mbt#L62-L70)、[:113-131](../src/schema.mbt#L113-L131) | 拒绝发生在 schema 阶段（早于 Parquet 打开，见 S17 与 [out/73](protocol-audit/out/73-read-nested-missing-file.out)） | `delta.exe read artifacts/protocol-audit/fixtures/nested-struct` |
| S15 | `array` / `list` schema | 明确拒绝 | 同上（kind=array） | [out/69](protocol-audit/out/69-inspect-nested-list.out)、[out/70](protocol-audit/out/70-read-nested-list.out)；测试 `read: nested parquet column is rejected...` | 该测试只覆盖 schema 层（未真正打开嵌套 Parquet，见 §5 第 6 条） | `delta.exe read artifacts/protocol-audit/fixtures/nested-list` |
| S16 | `map` schema | 明确拒绝 | 同上（kind=map） | [out/71](protocol-audit/out/71-inspect-nested-map.out)、[out/72](protocol-audit/out/72-read-nested-map.out) | 无测试 | `delta.exe read artifacts/protocol-audit/fixtures/nested-map` |
| S17 | Delta schema 撒谎为 flat、Parquet 物理列是 nested/repeated | 明确拒绝 | 第二层兜底：`error: unsupported: cannot read 'data.parquet': Repeated columns are not supported yet` | [out/75](protocol-audit/out/75-inspect-nested-parquet-lie.out)、[out/76](protocol-audit/out/76-read-nested-parquet-lie.out)；[src/vendor/parquet/reader.mbt:1307](../src/vendor/parquet/reader.mbt#L1307)、[:1472](../src/vendor/parquet/reader.mbt#L1472) | 说明两层拒绝都存在，但现有测试没走到这一层 | `delta.exe read artifacts/protocol-audit/fixtures/nested-parquet-lie` |
| S18 | `schema.fields` 缺失 | 明确拒绝 | ParseError | [src/schema.mbt:77-80](../src/schema.mbt#L77-L80) | 无 fixture/测试 | 新增 fixture |
| S19 | 字段缺 `name` 或 `type` | 明确拒绝 | ParseError | [src/schema.mbt:83-95](../src/schema.mbt#L83-L95) | 无 fixture/测试 | 新增 fixture |
| S20 | 字段 `nullable` 缺失 | 未覆盖 | **静默默认 false** | [src/schema.mbt:88-91](../src/schema.mbt#L88-L91) | 可能把 nullable 列误标 NOT NULL | 新增 fixture |
| S21 | 未知原始类型（如 `variant`） | 明确拒绝 | Unsupported | [src/schema.mbt:57-61](../src/schema.mbt#L57-L61)、[:120-123](../src/schema.mbt#L120-L123)；[out/64](protocol-audit/out/64-read-uuid-schema-unknown.out) | — | 构造未知类型 fixture |
| S22 | nested 错误的“字段路径”粒度 | 只识别 | 只报顶层字段名 + kind，不含深路径（如 `profile.name`） | [src/schema.mbt:114-129](../src/schema.mbt#L114-L129) | 文档 §4 要求“错误包含字段路径和 nested kind”，顶层字段满足；若未来要定位深路径需扩展 | — |

### 3.5 Parquet

| ID | 能力项 | 结论 | 行为特征 | 证据 | 缺口与风险 | 建议补测命令 |
|---|---|---|---|---|---|---|
| Q1 | UNCOMPRESSED codec | 已实现（无测试） | 正常 | [src/vendor/parquet/compression.mbt:110-111](../src/vendor/parquet/compression.mbt#L110-L111)；writer 自身写 UNCOMPRESSED（[writer.mbt:236](../src/vendor/parquet/writer.mbt#L236)） | 无读取 fixture/测试 | 用 pyarrow `compression="none"` 生成 fixture |
| Q2 | SNAPPY codec | 已实现且有测试 | 正常 | 测试 `read: real Databricks-written covid table (snappy parquet)`；[out/11](protocol-audit/out/11-read-covid-limit5.out)；[compression.mbt:112-113](../src/vendor/parquet/compression.mbt#L112-L113) | — | `delta.exe read fixtures/delta/covid --limit 5` |
| Q3 | ZSTD | 明确拒绝 | `Unsupported parquet codec: 6`，带文件路径 | [out/84-read-codec-zstd](protocol-audit/out/84-read-codec-zstd.out)；[compression.mbt:114-116](../src/vendor/parquet/compression.mbt#L114-L116) | 与 README/文档口径一致 | `delta.exe read artifacts/protocol-audit/fixtures/codec-zstd` |
| Q4 | GZIP | 明确拒绝 | `Unsupported parquet codec: 2` | [out/84-read-codec-gzip](protocol-audit/out/84-read-codec-gzip.out) | 同上 | 同上 fixtures/codec-gzip |
| Q5 | BROTLI | 明确拒绝 | `Unsupported parquet codec: 4` | [out/84-read-codec-brotli](protocol-audit/out/84-read-codec-brotli.out) | 同上 | 同上 fixtures/codec-brotli |
| Q6 | LZ4 | 明确拒绝 | `Unsupported parquet codec: 7` | [out/84-read-codec-lz4](protocol-audit/out/84-read-codec-lz4.out) | 同上 | 同上 fixtures/codec-lz4 |
| Q7 | INT96 timestamp | 已实现（无测试） | 正常（转 `TimestampMicros`） | [out/81](protocol-audit/out/81-read-int96-timestamp.out)；[metadata.mbt:417-418](../src/vendor/parquet/metadata.mbt#L417-L418)；[encodings.mbt:44](../src/vendor/parquet/encodings.mbt#L44) | 无测试 | 同上 fixtures/int96-timestamp |
| Q8 | DATE converted type | 已实现（无测试） | 正常（int32 天数） | [out/82](protocol-audit/out/82-read-date-col.out) | converted_type=6 未显式映射，靠物理类型兜底 | 同上 fixtures/date-col |
| Q9 | 嵌套/repeated 物理结构 | 明确拒绝 | `Repeated columns are not supported yet` | [out/76](protocol-audit/out/76-read-nested-parquet-lie.out)；[reader.mbt:1307](../src/vendor/parquet/reader.mbt#L1307)、[:1472](../src/vendor/parquet/reader.mbt#L1472) | — | 同上 fixtures/nested-parquet-lie |
| Q10 | 缺失数据文件 | 明确拒绝 | IoError，但**消息不含文件路径** | [out/86](protocol-audit/out/86-read-missing-data-file.out)、[out/15](protocol-audit/out/15-read-table-a-missing-data.out)；[src/read.mbt:149](../src/read.mbt#L149) 只包 msg | 定位困难；未说明是 `add.path` 指向的文件缺失 | `delta.exe read artifacts/protocol-audit/fixtures/missing-data-file` |
| Q11 | 损坏 Parquet（截断） | 明确拒绝 | ParseError，含路径与原因 | [out/85](protocol-audit/out/85-read-corrupt-parquet.out)；[src/read.mbt:147-148](../src/read.mbt#L147-L148) | — | 同上 fixtures/corrupt-parquet |
| Q12 | Delta schema 与 Parquet 物理类型不一致 | 未覆盖 | **静默转换**：`id: long` 读出 `abc` | [out/87](protocol-audit/out/87-read-type-mismatch.out)；[src/read.mbt:31-43](../src/read.mbt#L31-L43)、[:170-177](../src/read.mbt#L170-L177) 只按列名取 Cell，无类型校验 | **数据正确性风险**：应做 schema↔parquet 类型断言或拒绝 | `delta.exe read artifacts/protocol-audit/fixtures/type-mismatch` |
| Q13 | Parquet 有 schema 外的多余列 | 未覆盖 | **静默忽略** | [out/126](protocol-audit/out/126-read-overwritten.out)（schema 缩减后 `active` 列消失）；[src/read.mbt:155-186](../src/read.mbt#L155-L186) 只按 schema 遍历 | 与 T25 叠加会静默丢列 | 构造“数据文件多一列”的表 |
| Q14 | Parquet 缺 schema 声明的列 | 明确拒绝 | Unsupported，含列名与文件名 | [out/88](protocol-audit/out/88-read-missing-column.out)；[src/read.mbt:179-183](../src/read.mbt#L179-L183) | — | 同上 fixtures/missing-column |
| Q15 | 列裁剪 / row-group 级读取 | 未覆盖 | 每个文件整读 | [src/read.mbt:139-188](../src/read.mbt#L139-L188) | 性能问题（文档未承诺流式） | — |
| Q16 | Parquet 编码支持（dictionary/PLAIN/RLE/delta byte array） | 只识别 | PLAIN dictionary 之外有明确 Unsupported | [reader.mbt:1628](../src/vendor/parquet/reader.mbt#L1628)、[:1648](../src/vendor/parquet/reader.mbt#L1648)、[:1673](../src/vendor/parquet/reader.mbt#L1673)；covid（Databricks 写出）可通过说明常见组合可用 | 第三方编码可能被拒（结构化错误） | 用 pyarrow 各种 encoding 生成 fixture |

### 3.6 表操作（open/snapshot/history/scan/read/create/append）

| ID | 能力项 | 结论 | 行为特征 | 证据 | 缺口与风险 | 建议补测命令 |
|---|---|---|---|---|---|---|
| T1 | `DeltaTable::open`（无 `_delta_log`） | 明确拒绝 | NotFound | [src/snapshot.mbt:78-85](../src/snapshot.mbt#L78-L85)；[out/17](protocol-audit/out/17-inspect-nonexistent.out) | — | `delta.exe inspect artifacts/protocol-audit/fixtures/does-not-exist` |
| T2 | `snapshot()` 最新版本 | 已实现且有测试 | 正常 | 测试 `snapshot: replay 13 versions...`；[out/02](protocol-audit/out/02-inspect-table-a.out) | — | — |
| T3 | `snapshot_at(v)` 时间旅行 | 已实现且有测试 | 正常 | 测试同上；[probe-library](protocol-audit/out/probe-library.out)（at 5 → 50 files） | — | `moon test --target native` |
| T4 | `snapshot_at` 越界/负数 | 明确拒绝 | NotFound | [probe-library](protocol-audit/out/probe-library.out) `[snapshot-at] at 99/-1` | 无具名测试 | 新增测试 |
| T5 | `history()` | 已实现且有测试 | 正常 | 测试同上；[out/03](protocol-audit/out/03-history-table-a.out) | — | `delta.exe history fixtures/delta/table_a` |
| T6 | `history()` 遇到无 `commitInfo` 的版本 | 未覆盖 | **静默跳过**（历史出现空洞） | [out/45](protocol-audit/out/45-inspect-no-commitinfo.out)、[out/46](protocol-audit/out/46-history-no-commitinfo.out)（只有表头）；[src/snapshot.mbt:142-147](../src/snapshot.mbt#L142-L147) | 审计场景下不可接受；应报错或显式标记缺失 | `delta.exe history artifacts/protocol-audit/fixtures/no-commitinfo` |
| T7 | `scan()` 全量 | 已实现且有测试 | 正常 | 测试 `scan: stats pruning...`；[out/04](protocol-audit/out/04-scan-table-a.out) | — | — |
| T8 | `scan()` numeric min/max 剪枝 | 已实现且有测试 | 正常 | 测试 `scan: stats pruning selects 60 of 120...`；[out/05](protocol-audit/out/05-scan-table-a-id-ge-60.out)、[out/06](protocol-audit/out/06-scan-table-a-id-eq-7.out)、[out/97](protocol-audit/out/97-scan-stats-numeric-where.out) | — | `delta.exe scan fixtures/delta/table_a --where id>=60` |
| T9 | `scan()` 无 stats | 已实现且有测试 | 正常（不剪枝） | 测试 `scan: files without stats are kept...`；[out/101](protocol-audit/out/101-scan-stats-object-where.out) | — | — |
| T10 | `scan()` min==max 边界 | 已实现且有测试 | 正常 | 测试同上；[out/98](protocol-audit/out/98-scan-min-max-eq-gt.out)、[out/99](protocol-audit/out/99-scan-min-max-eq-ge.out) | — | — |
| T11 | `scan()` 字符串统计 | 未覆盖 | 安全地不剪枝（`json_as_double` 得 None） | [src/scan.mbt:54-78](../src/scan.mbt#L54-L78)；[out/95](protocol-audit/out/95-scan-stats-string-where.out)（CLI 解析失败后无过滤） | 与 F11 叠加会产生“以为过滤了其实没有” | 用库级 API 传字符串谓词（需扩展 `ColumnPredicate.value` 类型） |
| T12 | `scan()` 布尔统计 | 未覆盖 | 不剪枝 | [out/96](protocol-audit/out/96-scan-stats-bool-where.out) | 同上 | 同上 |
| T13 | CLI `scan --where` 非数字值 | 未覆盖 | **打印 error 后继续无过滤扫描，退出 0** | [src/cmd/delta/main.mbt:144-171](../src/cmd/delta/main.mbt#L144-L171)、[:203-205](../src/cmd/delta/main.mbt#L203-L205)；[out/14](protocol-audit/out/14-scan-covid-date-where.out)、[out/95](protocol-audit/out/95-scan-stats-string-where.out) | 用户会拿到未过滤结果 | `delta.exe scan fixtures/delta/covid --where date>=2020-05-01` |
| T14 | CLI `scan --partition` 指定非分区列 | 未覆盖 | **静默返回 0 个文件** | [src/scan.mbt:36-51](../src/scan.mbt#L36-L51)；[out/07](protocol-audit/out/07-scan-table-a-fake-partition.out)（selected 0 / skipped partition 120） | 应校验列是否在 `partitionColumns` 中 | `delta.exe scan fixtures/delta/table_a --partition id=7` |
| T15 | `scan()` 分区等值过滤 | 已实现且有测试 | 正常 | 测试 `read/scan: partitioned table...`；[out/91](protocol-audit/out/91-scan-partition-table-dt.out) | — | `delta.exe scan artifacts/protocol-audit/fixtures/partition-table --partition dt=2026-01-01` |
| T16 | 分区值为 JSON null | 未覆盖 | null 被丢弃 → 读时误报“列缺失” | [src/json_util.mbt:116-130](../src/json_util.mbt#L116-L130) 只保留 string/number；[out/93](protocol-audit/out/93-read-partition-null-value.out)、[out/94](protocol-audit/out/94-scan-partition-null-value.out) | 错误信息误导；null 分区语义丢失 | `delta.exe read artifacts/protocol-audit/fixtures/partition-null-value` |
| T17 | `read`（flat + SNAPPY） | 已实现且有测试 | 正常 | 测试 `read: real Databricks-written covid table`；[out/11](protocol-audit/out/11-read-covid-limit5.out) | — | `delta.exe read fixtures/delta/covid --limit 5` |
| T18 | CLI `read` 默认 `--limit 10` | 已实现（无测试） | 正常 | [src/cmd/delta/main.mbt:249](../src/cmd/delta/main.mbt#L249)；[out/12](protocol-audit/out/12-read-covid-default-limit.out) | 默认限制容易被误读为“表只有 10 行” | 新增测试/文档说明 |
| T19 | `read` limit=0 | 已实现（无测试） | 正常（0 file/0 row） | [out/13](protocol-audit/out/13-read-covid-limit0.out)；[probe-library](protocol-audit/out/probe-library.out) `[read-limit]` | — | — |
| T20 | `read` limit 跨文件 | 已实现（无测试） | 正常（只读到满足 limit 的文件） | [out/123](protocol-audit/out/123-read-appended-flat-limit2.out)；[src/read.mbt:133-142](../src/read.mbt#L133-L142) | — | — |
| T21 | `read` 分区列按 `partitionValues` 补齐 | 已实现且有测试 | 正常 | 测试 `read/scan: partitioned table...`；[out/92](protocol-audit/out/92-read-partition-table.out) | — | `delta.exe read artifacts/protocol-audit/fixtures/partition-table` |
| T22 | `create_table` | 已实现且有测试 | 正常 | 测试 `write: create + append + read back`；[out/110](protocol-audit/out/110-create-flat.out) | — | `delta.exe create <root> '<schema>'` |
| T23 | `create_table` 写 protocol reader=1/writer=2 | 已实现（无测试） | 正常 | [src/write.mbt:174](../src/write.mbt#L174)；[out/111](protocol-audit/out/111-inspect-created-empty.out) | 无测试断言 | 见 §6 |
| T24 | `create_table` 输入非法 schema JSON | 未覆盖 | **静默成功写日志**，之后 snapshot 才失败 | [out/113](protocol-audit/out/113-create-invalid-schema.out)、[out/114](protocol-audit/out/114-inspect-created-bad.out)；[probe-library](protocol-audit/out/probe-library.out) `[create-invalid-schema]`；[src/write.mbt:165-185](../src/write.mbt#L165-L185) 不解析 schema | 可产出坏表；违反“不静默降级” | `delta.exe create artifacts/protocol-audit/fixtures/cli-created-bad "{not a schema"` |
| T25 | `create_table` 作用于已存在的表 | 未覆盖 | **静默覆盖 v0**，schema 漂移，后续读静默丢列 | [out/124](protocol-audit/out/124-create-overwrite-existing.out)~[out/126](protocol-audit/out/126-read-overwritten.out)；[src/write.mbt:182-184](../src/write.mbt#L182-L184) 直接 `write_text` | 数据安全（可造成 schema 与数据文件不一致） | 复制一份已 append 的表后执行 `delta.exe create ...` |
| T26 | `append_rows`（2 个文件、NULL、boolean/double） | 已实现且有测试 | 正常 | 测试 `write: boolean and double columns roundtrip`；[out/120](protocol-audit/out/120-moon-run-interop-writer.out)（2 文件 5 行）、[out/122](protocol-audit/out/122-read-appended-flat.out) | 依赖 D2 新增示例产生 2 文件证据 | `moon run --target native src/examples/interop_flat_writer -- <root>` |
| T27 | `append_rows` 列数/行长校验 | 已实现（无测试） | 正常拒绝（ParseError） | [probe-library](protocol-audit/out/probe-library.out) `[append-column-count]`/`[append-row-lengths]`；[src/write.mbt:202-217](../src/write.mbt#L202-L217) | 无具名测试 | 新增测试 |
| T28 | `append_rows` 列名/类型与表 schema 一致性 | 未覆盖 | **静默写错** | [probe-library](protocol-audit/out/probe-library.out) `[append-type-mismatch]`/`[append-name-mismatch]`；[out/probe-cli-append-mismatch](protocol-audit/out/probe-cli-append-mismatch.out)；[src/write.mbt:224-237](../src/write.mbt#L224-L237) 直接用调用方给的 name/type | 严重：表 schema 与数据文件矛盾 | 见 §6 建议测试 |
| T29 | `append_rows` 对 NOT NULL 列的约束 | 未覆盖 | **静默写入 NULL** | [probe-library](protocol-audit/out/probe-library.out) `[append-not-null]`；[out/probe-cli-notnull](protocol-audit/out/probe-cli-notnull.out) | 违反表契约 | 新增测试 |
| T30 | `append_rows` 生成 Parquet 的 nullability | 未覆盖 | 恒写 `Optional`，与 schema `nullable=false` 不一致 | [src/write.mbt:227](../src/write.mbt#L227)；[probe-written-parquet](protocol-audit/out/probe-written-parquet.out)（nullable=True）；[appended-flat-parquet](protocol-audit/out/probe-written-parquet-appended.out)（schema 声明 not null 的文件仍 optional） | 外部引擎按 Delta schema 读时可能出现契约不一致 | 见 §6 |
| T31 | `append_rows` 的 `add.stats` 内容 | 已实现（无测试） | 只写 `numRecords` + 数值列 min/max；无 `nullCount`、无 string/bool min/max | [src/write.mbt:107-136](../src/write.mbt#L107-L136)；[out/120](protocol-audit/out/120-moon-run-interop-writer.out) 的两条 stats | 与“统计信息随日志写入”的表述有落差；下游剪枝能力受限 | 新增测试断言 stats 字段 |
| T32 | `append_rows` 对分区表拒绝 | 已实现（无测试） | 结构化 Unsupported | [probe-library](protocol-audit/out/probe-library.out) `[append-partitioned]`；[src/write.mbt:197-201](../src/write.mbt#L197-L201) | 无具名测试 | 新增测试 |
| T33 | `append_rows` 对 `minWriterVersion>2` 表 | 未覆盖 | **静默追加** | [probe-library](protocol-audit/out/probe-library.out) `[append-writer-v5]`；[out/probe-cli-writer-v5](protocol-audit/out/probe-cli-writer-v5.out) | 违反文档 §1“未实现的 writer protocol/feature 必须拒绝” | 新增测试 |
| T34 | 并发写冲突仲裁 | 未覆盖 | 无（用 `snapshot.version+1` 直接写） | [src/write.mbt:247](../src/write.mbt#L247) | 文档已声明不承诺 | — |
| T35 | MERGE / UPDATE / DELETE | 未覆盖 | 无实现 | `src/` 中 `MERGE`/`UPDATE` 0 命中（[code-evidence.out §10](protocol-audit/out/code-evidence.out)）；只有 `remove` action 收敛 | 文档已声明不承诺 | — |
| T36 | CDF / `cdc` action | 未覆盖 | **静默忽略**（配置与 action 都不报错） | [out/39](protocol-audit/out/39-inspect-cdf-enabled.out)（配置 `delta.enableChangeDataFeed=true` 被接受）、[out/32](protocol-audit/out/32-inspect-unknown-actions.out)（cdc action 被忽略）；[src/actions.mbt:148](../src/actions.mbt#L148) | 启用 CDF 的表会被当作普通表读，且不提示 | `delta.exe inspect artifacts/protocol-audit/fixtures/cdf-enabled` |
| T37 | 删除向量 `deletionVector` | 未覆盖 | **静默忽略，读全量（含已删除行）** | [out/35](protocol-audit/out/35-inspect-deletion-vector.out)、[out/36](protocol-audit/out/36-read-deletion-vector.out)；`deletionVector` 在 `src/` 0 命中 | **最严重的数据正确性问题**；文档 §1 明确“删除向量必须拒绝” | `delta.exe read artifacts/protocol-audit/fixtures/deletion-vector-add` |
| T38 | Column mapping | 未覆盖 | 未识别；读列缺失时报 Unsupported（原因错） | [out/37](protocol-audit/out/37-inspect-column-mapping.out)、[out/38](protocol-audit/out/38-read-column-mapping.out)；`columnMapping` 在 `src/` 0 命中 | 文档 §1 要求拒绝并说明原因；现在既不识别也可能误导 | `delta.exe read artifacts/protocol-audit/fixtures/column-mapping` |
| T39 | checkpoint 恢复快照 | 未覆盖 | 无 | [out/50](protocol-audit/out/50-inspect-checkpoint-only.out)、[out/51](protocol-audit/out/51-inspect-checkpoint-truncated.out) | 文档已声明不承诺；但截断表的静默行为是缺陷（C5） | — |
| T40 | 对象存储（S3/R2） | 未覆盖 | 仅本地 `@fs` | README 能力边界已声明不支持 | 文档一致 | — |

### 3.7 错误策略与 CLI 退出码

| ID | 能力项 | 结论 | 行为特征 | 证据 | 缺口与风险 | 建议补测命令 |
|---|---|---|---|---|---|---|
| E1 | `DeltaError` 闭集（IoError/ParseError/Unsupported/NotFound） | 已实现（无测试） | 正常 | [src/types.mbt:129-144](../src/types.mbt#L129-L144)；`src/` 内 40 处 `raise DeltaError::*`（[code-evidence.out §11](protocol-audit/out/code-evidence.out)） | 无错误分类/描述测试 | 新增测试对 4 类错误 `describe_error` 断言 |
| E2 | 闭集外输入得到结构化错误 | 已实现且有测试 | 部分（多数场景满足） | 测试 `checkpoint-only`/`nested`/`missing data file`；[out/40](protocol-audit/out/40-inspect-log-gap.out)、[out/43](protocol-audit/out/43-inspect-malformed-json.out)、[out/61](protocol-audit/out/61-read-decimal-schema.out)、[out/84](protocol-audit/out/84-read-codec-zstd.out) 等 | 但存在 P2/P3/P4/P6/P7/L10/S13/T24/T25/T28/T29/T33/T36/T37 等静默例外，不能给出“闭集外必报错”的结论 | 见 §6 |
| E3 | panic / 崩溃 | 未覆盖 | 121 次运行 0 panic；仅 3 个 `out` 的 stderr 非空且都是 `moon` 的 warning（[probe-library](protocol-audit/out/probe-library.out)、[out/120](protocol-audit/out/120-moon-run-interop-writer.out)、[out/127](protocol-audit/out/127-moon-run-read-covid-example.out)） | `out/` 全量统计；见 §2.3 | 未做 fuzz，不能证明所有畸形输入不 panic | 增加随机截断/畸形日志测试 |
| E4 | CLI 失败退出码 | 未覆盖 | **恒为 0**：121 次带退出码的运行（含全部 error 场景）全部 `[exit code: 0]` | [commands.txt](protocol-audit/commands.txt)；[src/cmd/delta/main.mbt:44-46](../src/cmd/delta/main.mbt#L44-L46) `fail()` 只 `println`，各 `catch` 分支 `return`，`main` 不设置退出码 | 自动化/CI 无法判断失败；与“结构化错误”目标不匹配 | `delta.exe inspect <不存在>` 后 `echo $LASTEXITCODE` 期望非 0 |
| E5 | CLI 未知子命令 | 未覆盖 | 打印 usage，退出 0 | [out/18](protocol-audit/out/18-unknown-subcommand.out)；[src/cmd/delta/main.mbt:380-382](../src/cmd/delta/main.mbt#L380-L382) | 同上 | `delta.exe frobnicate` |
| E6 | 静默 0 行 / 部分数据 | 未覆盖 | scan 伪分区 0 文件；stats object `rows: 0`；DV 多读行；history 空 | [out/07](protocol-audit/out/07-scan-table-a-fake-partition.out)、[out/100](protocol-audit/out/100-inspect-stats-object.out)、[out/36](protocol-audit/out/36-read-deletion-vector.out)、[out/46](protocol-audit/out/46-history-no-commitinfo.out) | 直接违反 README 第 102 行“不是静默 0 行” | 见 §6 |

---

## 4. 关键发现（按严重度）

> 每条含：现象 → 复现（命令/文件）→ 根因（代码行）→ 与口径的关系 → 建议（只建议，不改文档）。

### 4.1 F1（严重）删除向量被静默忽略，读到已删除行

- 现象：`add` 携带 `deletionVector`（cardinality=1）的 reader v3 表，`read` 返回 3 行，而正确结果应为 2 行。
- 复现：`delta.exe read artifacts/protocol-audit/fixtures/deletion-vector-add` → [out/36](protocol-audit/out/36-read-deletion-vector.out)；fixture 见 [gen_fixtures.py](protocol-audit/gen_fixtures.py) `deletion-vector-add`。
- 根因：`parse_add` 只取 path/partitionValues/size/modificationTime/dataChange/stats（[src/actions.mbt:72-92](../src/actions.mbt#L72-L92)）；`deletionVector` 在 `src/` 中 0 命中（[code-evidence.out §10](protocol-audit/out/code-evidence.out)）。
- 口径：docs §1“遇到…删除向量…必须拒绝并说明原因，不能静默忽略”；README 第 100 行把删除向量列为不支持。
- 建议：在 `parse_protocol`/`parse_add` 增加 feature/DV 检测，reader≥3 或出现 `deletionVector` 时抛 `Unsupported`。补测：reader v3 + DV 的负向测试。

### 4.2 F2（严重）截断日志 + checkpoint 的表被静默当作旧版本

- 现象：构造 `table_a` 的 JSON v0..v9 + checkpoint v10 + `_last_checkpoint=10`（即 v10..v12 被清理、只能依赖 checkpoint 的表），`inspect` 报 `current version: 9`、`files: 90`，`history` 只有 10 条，**无任何错误**；同时仍打印 `last checkpoint: version=10`。
- 复现：`delta.exe inspect artifacts/protocol-audit/fixtures/checkpoint-truncated` → [out/51](protocol-audit/out/51-inspect-checkpoint-truncated.out)、[out/52](protocol-audit/out/52-history-checkpoint-truncated.out)。
- 根因：`replay_until` 只要求 `versions[0]==0`（[src/snapshot.mbt:111-115](../src/snapshot.mbt#L111-L115)），并不检查 `_last_checkpoint.version` 是否大于最新 JSON 版本；checkpoint 只用于展示（[src/snapshot.mbt:195-202](../src/snapshot.mbt#L195-L202)）。
- 口径：docs §1“日志被截断或只能依靠 checkpoint 的表必须明确返回不支持错误”；§2 表格同义；README 第 97 行把“日志被截断的表”列为不支持。
- 建议：replay 前比较 `checkpoint_info.version` 与最新 JSON 版本；若 checkpoint 版本 > 最新 JSON 版本（或存在 `_last_checkpoint` 但对应 JSON 缺失）→ `Unsupported`。

### 4.3 F3（严重）reader v2/v3 + unknown required feature 被接受

- 现象：`protocol-reader-v2`（reader=2/writer=5）、`protocol-reader-v3-features`（reader=3/writer=7，且 `readerFeatures` 含 `unknownRequiredFeature`）两表 `inspect`/`read` 均成功；[probe-library](protocol-audit/out/probe-library.out) 显示库层 `accepted reader=3 writer=7` 且 read 3 行。
- 交叉证据（D2 提供，本审计未改动其文件）：delta-rs 1.6.6 写出的 reader=2 表 `artifacts/interop-flat/probe/protocol-reader2/` 被 CLI 接受：`protocol: reader=2 writer=5`、`read 1 row`、`exit 0`（[D2 inspect 输出](interop-flat/raw/f_probe_reader2_inspect.stdout.txt)、[D2 read 输出](interop-flat/raw/f_probe_reader2_read.stdout.txt)）。说明这不是自造 fixture 的偶然。
- 根因：`parse_protocol` 只读两个 Int 字段、缺省为 1，没有版本/feature 校验（[src/actions.mbt:15-25](../src/actions.mbt#L15-L25)）；`replay_until` 直接采用日志里的 protocol（[src/snapshot.mbt:129](../src/snapshot.mbt#L129)）；`readerFeatures`/`writerFeatures` 无任何解析。
- 口径：docs §1“遇到高于支持范围的 protocol version、未知必需 feature…读取端必须拒绝”；§2 表格“高于 reader v1 的 reader protocol”必须拒绝。
- 建议：实现最小校验：`minReaderVersion > 1` 或出现无法识别的 `readerFeatures` → `Unsupported`；对 `minWriterVersion > 2` 的写路径也拒绝（见 F14/T33）。

### 4.4 F4（严重）CLI 失败退出码恒为 0

- 现象：121 次带退出码的 CLI/脚本运行（含 `not found`、`parse error`、`unsupported`、缺失文件、损坏 Parquet、非法 JSON）全部 `[exit code: 0]`；见 [commands.txt](protocol-audit/commands.txt) 全量与 §3.7 E4。
- 根因：`fail()` 只 `println("error: ...")`（[src/cmd/delta/main.mbt:44-46](../src/cmd/delta/main.mbt#L44-L46)），所有子命令 `catch { err => { fail(...); return } }`，`main` 无退出码设置。
- 影响：CI/自动化无法区分成功与失败；与文档 §2“闭集之外必须得到结构化错误”在脚本层面的可用性冲突（虽然错误文本是结构化的）。
- 建议：错误路径设置非 0 退出码（MoonBit native 可用相应 API；或至少在 CLI 层统一 `raise`/exit）。

### 4.5 F5（严重）`append_rows` 不校列名/类型，写出与 schema 矛盾的数据

- 现象（库级探针，[probe-library](protocol-audit/out/probe-library.out)）：
  - `[append-type-mismatch]`：把 `id`（schema long）按 String 写、`name`（schema string）按 Int64 写，`append` 成功；随后 `read` 输出 `column=id declared=long read=not-a-long`、`column=name declared=string read=42`（[out/probe-cli-append-mismatch](protocol-audit/out/probe-cli-append-mismatch.out)）。
  - `[append-name-mismatch]`：写入列名 `other`（schema 里是 `name`），append 成功，之后 `read` 报 `column 'name' is missing`。
- 根因：`append_rows` 只校验“列数相同/行长相同”（[src/write.mbt:202-217](../src/write.mbt#L202-L217)），随后直接用调用方的 name/type 写 Parquet（[src/write.mbt:224-237](../src/write.mbt#L224-L237)），从不与 `snapshot.metadata.schema` 比对。
- 口径：申报书第 21 行“不支持的…写入类型在类型层与错误信息里明确拒绝，不静默降级”。
- 建议：append 前按字段名/类型/顺序校验；类型不符抛 `Unsupported`/`ParseError`。补测：3 个负向测试（类型错配、列名错配、NOT NULL 写 null）。

### 4.6 F6（高）Delta schema 与 Parquet 物理类型不一致时静默转换

- 现象：schema `id: long`，数据文件列 `id` 是 string，`read` 成功输出 `abc/def/ghi`；CLI 表头仍写 `id (long)`。
- 复现：`delta.exe read artifacts/protocol-audit/fixtures/type-mismatch` → [out/87](protocol-audit/out/87-read-type-mismatch.out)。
- 根因：`read_snapshot` 只按列名找 `@parquet.Column`，`convert_cell` 按物理值构造 Cell，不比对 Delta 声明类型（[src/read.mbt:31-43](../src/read.mbt#L31-L43)、[:170-177](../src/read.mbt#L170-L177)）。
- 建议：打开 Parquet 后校验列物理类型与 `DeltaType` 的映射，不一致时 `Unsupported`（或至少警告并标记）。

### 4.7 F7（高）`stats` 不是 JSON string 时被静默丢弃

- 现象：`add.stats` 为 JSON object（Delta 生态中存在该写法）时，`parse_add` 用 `json_field_string` 取不到 → `stats=None` → `inspect` 显示 `rows: 0`（实际 3 行），`scan --where id>=100` 不剪枝且 `matched_rows: 0`。
- 复现：`delta.exe inspect artifacts/protocol-audit/fixtures/stats-object` → [out/100](protocol-audit/out/100-inspect-stats-object.out)；`delta.exe scan ... --where id>=100` → [out/101](protocol-audit/out/101-scan-stats-object-where.out)。
- 根因：`parse_add` 的 `let stats = json_field_string(j, "stats")`（[src/actions.mbt:90](../src/actions.mbt#L90)）；`stats_num_records` 在 None 时返回 0（[src/snapshot.mbt:59-70](../src/snapshot.mbt#L59-L70)）。
- 建议：支持 object 形式的 stats（或至少识别后给出 `Unsupported`/警告），`total_rows` 缺失统计时不要以 0 冒充。

### 4.8 F8（高）未知 action 被静默忽略（cdc/txn/domainMetadata/sidecar）

- 现象：v1 仅含 `cdc`/`txn`/`domainMetadata`/`sidecar` action 时，replay 正常，`inspect` 显示 `current version: 1, files: 1, rows: 3`，无任何提示；`history` 里 v1 的 operation 仍是 `UNKNOWN` commitInfo。
- 复现：[out/32](protocol-audit/out/32-inspect-unknown-actions.out)~[out/34](protocol-audit/out/34-read-unknown-actions.out)；库层 `parse_action` 返回 `UnknownAction("unrecognized action")`（[probe-library](protocol-audit/out/probe-library.out) `[unknown-action-api]`）。
- 根因：`parse_action` 兜底返回 `UnknownAction`（[src/actions.mbt:148](../src/actions.mbt#L148)），replay 显式忽略（[src/snapshot.mbt:148](../src/snapshot.mbt#L148)）。
- 建议：区分“可安全忽略的元数据 action”与“影响数据的 action”：至少对 `cdc`、涉及 reader/writer feature 的 action 报 `Unsupported`；或在 API 暴露 unknown action 计数/警告。

### 4.9 F9（中）column mapping 未识别，错误原因误导

- 现象：`delta.columnMapping.mode=name`、物理列为 `col-5f0c`、逻辑名为 `id` 的表，`read` 报 `column 'id' is missing from parquet file 'data.parquet'`；这不是“column mapping 不支持”而是“列找不到”，用户无法判断根因。
- 复现：[out/37](protocol-audit/out/37-inspect-column-mapping.out)、[out/38](protocol-audit/out/38-read-column-mapping.out)。
- 根因：`configuration` 只被保存（[src/actions.mbt:64-67](../src/actions.mbt#L64-L67)）从不检查；`columnMapping` 在 `src/` 0 命中。
- 建议：检测 `delta.columnMapping.mode != none` → `Unsupported("column mapping is not supported")`；同时识别 `readerFeatures` 中的 `columnMapping`。

### 4.10 F10（中）`create_table` 不校验 schema 且可静默覆盖已有表

- 现象 1：`delta.exe create <root> '{not a schema'` 返回 `created (version 0)`；之后 `inspect` 才报 `invalid schema JSON`（[out/113](protocol-audit/out/113-create-invalid-schema.out)、[out/114](protocol-audit/out/114-inspect-created-bad.out)）。
- 现象 2：对已有 2 个数据文件的表执行 `create` 且 schema 缩减为 `id`（去掉 `name/active`）→ v0 被覆盖，`inspect` 显示新 schema，`read` 静默只返回 `id` 列，旧列消失（[out/124](protocol-audit/out/124-create-overwrite-existing.out)~[out/126](protocol-audit/out/126-read-overwritten.out)）。
- 根因：`create_table` 直接拼 JSON 写文件（[src/write.mbt:165-185](../src/write.mbt#L165-L185)），不调用 `parse_schema`、不检查表是否已存在。
- 建议：创建前 `parse_schema` 校验；`_delta_log` 非空时拒绝（或要求显式 overwrite 参数）。

### 4.11 F11（中）CLI 过滤降级与伪分区

- `--where` 值解析失败（字符串/日期/布尔）时：打印 `error: invalid number ...`，但 `predicate` 置 None 后继续执行完整扫描，退出 0（[src/cmd/delta/main.mbt:144-171](../src/cmd/delta/main.mbt#L144-L171)、[:203-205](../src/cmd/delta/main.mbt#L203-L205)；[out/14](protocol-audit/out/14-scan-covid-date-where.out)、[out/95](protocol-audit/out/95-scan-stats-string-where.out)）。
- `--partition` 指定非分区列（如 `id=7`）：所有 120 文件被 `skipped_partition`，返回 0 文件、0 行，无错误（[out/07](protocol-audit/out/07-scan-table-a-fake-partition.out)）。
- 建议：解析失败直接非 0 退出；分区列校验 `partitionColumns.contains(key)`。

### 4.12 F12（低）错误上下文缺失/误导

- 缺失数据文件：`error: io error: No such file or directory` 不含路径（[out/86](protocol-audit/out/86-read-missing-data-file.out)）；根因 `read.mbt:149` 直接透传 `@parquet.Io(msg)`。
- 分区 null：`partitionValues: {"dt": null}` 被 `json_object_pairs_string` 丢弃（[src/json_util.mbt:116-130](../src/json_util.mbt#L116-L130)），读时误报“column 'dt' is missing”（[out/93](protocol-audit/out/93-read-partition-null-value.out)）。
- checkpoint-only：错误为 `table ... has no log entries`（[out/50](protocol-audit/out/50-inspect-checkpoint-only.out)），未提示“checkpoint-only 不支持”。
- 非 20 位文件名：`log version 0 is missing`（[out/42](protocol-audit/out/42-inspect-nonstandard-name.out)），实际是文件名非法。

### 4.13 正向能力（已实测，可用于验收基线）

| 能力 | 证据 |
|---|---|
| table_a 13 版本回放 → 120 文件 / 120 行 / 54232 B | [out/02](protocol-audit/out/02-inspect-table-a.out) |
| `id>=60` 剪枝 120→60，`skipped stats: 60`；`id=7` 剪到 1 文件 | [out/05](protocol-audit/out/05-scan-table-a-id-ge-60.out)、[out/06](protocol-audit/out/06-scan-table-a-id-eq-7.out) |
| COVID 单文件读 5/10 行，6 列值正确；pyarrow 复核该文件 157,865 行 6 列 | [out/11](protocol-audit/out/11-read-covid-limit5.out)、§2.3 pyarrow 命令 |
| 分区表读 + 分区过滤（3 行、dt 补列、过滤 1 文件） | [out/91](protocol-audit/out/91-scan-partition-table-dt.out)、[out/92](protocol-audit/out/92-read-partition-table.out) |
| create + append 2 文件 + NULL/boolean/double 回读 5 行 | [out/120](protocol-audit/out/120-moon-run-interop-writer.out)~[out/123](protocol-audit/out/123-read-appended-flat-limit2.out)；测试 `write: ...` |
| INT96 timestamp / date / float 读取 | [out/81](protocol-audit/out/81-read-int96-timestamp.out)、[out/82](protocol-audit/out/82-read-date-col.out)、[out/83](protocol-audit/out/83-read-float-col.out) |
| nested/decimal/未知类型在 schema 阶段拒绝且带列名/kind | [out/68](protocol-audit/out/68-read-nested-struct.out)、[out/70](protocol-audit/out/70-read-nested-list.out)、[out/72](protocol-audit/out/72-read-nested-map.out)、[out/61](protocol-audit/out/61-read-decimal-schema.out)、[out/64](protocol-audit/out/64-read-uuid-schema-unknown.out) |
| 四种不支持 codec 均结构化拒绝并带路径 | [out/84](protocol-audit/out/84-read-codec-zstd.out) 等 |
| 重复 add/remove 收敛、删除不存在路径无副作用 | [out/47](protocol-audit/out/47-inspect-dup-add-remove.out) |

---

## 5. 申报/README/ROADMAP 声明与实现不一致清单（供 D4；本审计未修改任何文档）

| # | 位置（文件:行） | 原文（摘录） | 实测/代码事实 | 证据 | 建议口径 |
|---|---|---|---|---|---|
| 1 | [申报书.md:19](../申报书.md#L19) | “读写标准 Delta 目录，Spark / Databricks / delta-rs / DuckDB 可直接互通，已有表可以平移使用” | 对 reader≥2、含 readerFeatures、DV、column mapping 的标准表：不拒绝或读错/报误导错误；“已有表可平移”不成立（至少在 D1 闭集外）。D2 的 flat 双向对拍 49 PASS 只覆盖闭集内表 | §4.3、§4.1、§4.9；[D2 reader=2 表被接受](interop-flat/raw/f_probe_reader2_inspect.stdout.txt) | 采用 docs §5 建议措辞：“在 protocol reader v1 / writer v2、version 0 起连续 JSON、flat schema、支持的压缩与本地 fs 范围内…；嵌套/checkpoint-only/未实现 feature 当前明确拒绝” |
| 2 | [申报书.md:21](../申报书.md#L21) | “不支持的 schema、压缩、写入类型在类型层与错误信息里明确拒绝，不静默降级——数据基础设施宁可拒绝，不可返回错数据” | 压缩/schema 多数满足；但存在 13 类静默行为（P2/P3/P4/P6/P7/L10/S13/T24/T25/T28/T29/T33/T36/T37），其中 DV 与类型错配会返回**错数据** | §4.1、§4.5、§4.6、§4.7、§4.8 | 要么实现拒绝，要么把措辞改为“对已枚举的闭集做拒绝；当前仍有以下未覆盖项（列清单）”，不能保留“宁可拒绝”的绝对表述 |
| 3 | [申报书.md:43](../申报书.md#L43) | “不实现嵌套 schema（struct/list/map）与 ZSTD/GZIP/BROTLI/LZ4；不解析 checkpoint parquet（checkpoint-only 表明确拒绝）” | 嵌套/4 种压缩的拒绝均属实；checkpoint-only 返回 `NotFound: has no log entries`（结构化但消息未提 checkpoint-only）；**日志截断+checkpoint 的表被静默当旧版本读**，这条比“checkpoint-only”更危险 | [out/50](protocol-audit/out/50-inspect-checkpoint-only.out)、[out/51](protocol-audit/out/51-inspect-checkpoint-truncated.out)、§4.2 | 增加“日志被截断（含 checkpoint 版本高于 JSON 最新版本）必须拒绝”的实现与测试；文本保持“checkpoint 恢复未实现” |
| 4 | [申报书.md:46](../申报书.md#L46) | “14 个具名测试全部通过，含负向用例（嵌套列拒绝、checkpoint-only 表拒绝、缺失数据文件、统计边界）与分区表读取/过滤” | 14/14 通过属实（[test.out](protocol-audit/test.out)）；但名为 `snapshot: checkpoint-only table is rejected` 的用例实际构造的是“只有 v3 JSON、非 0 起点”的表，没有 checkpoint 文件（[src/delta_wbtest.mbt:230-247](../src/delta_wbtest.mbt#L230-L247)），断言命中只是因为错误文案里同时出现 “checkpoint-only”；真实 checkpoint-only 场景未被测试 | §3.3 C4；[code-evidence.out §13](protocol-audit/out/code-evidence.out) | 测试名/构造改为真实 checkpoint-only；或把描述改为“日志非 0 起点拒绝”并另补真实 checkpoint-only 测试 |
| 5 | [申报书.md:48](../申报书.md#L48) | “`moon check` / `moon test`（native）通过…（warning 全部来自 vendored parquet 上游代码）” | 0 errors、14/14 通过属实；但按 warning 定位统计：43 条告警中 **39 条定位在项目 `src/`**（以 `implicit_impl_as_method` 为主，另有 `src/cmd/delta/moon.pkg` duplicate alias、`src/examples/interop_flat_writer` 的 `env` 未 import），**仅 4 条定位在 `src/vendor/parquet`**（另有 1 条 duplicate alias 告警无文件定位、属项目工程配置）；因此“warning 全部来自 vendored parquet 上游代码”不成立 | [check.out](protocol-audit/check.out) 第 1、9-273 行 | 改为“warning 主要来自 vendored parquet；本项目另有 implicit_impl_as_method 等告警待清理”，或先清理这些告警 |
| 6 | [README.md:21](../README.md#L21)、[README.md:98](../README.md#L98) | “写入：`create_table` + `append_rows`（Int32/Int64/String/Binary/Boolean/Double，Required/Optional）” | 写入端**只会写 `Optional`**：`append_rows` 对所有列固定 `@parquet.Optional`（[src/write.mbt:227](../src/write.mbt#L227)）；`schema nullable=false` 的表写出的 Parquet 仍 nullable=True，且可以写入 NULL（[probe-written-parquet](protocol-audit/out/probe-written-parquet.out)、[out/probe-cli-notnull](protocol-audit/out/probe-cli-notnull.out)） | §4.5、§3.6 T29/T30 | 删除“Required/Optional”，改为“写入端列一律 Optional；NOT NULL 约束未在写入路径校验” |
| 7 | [README.md:90](../README.md#L90)、[README.md:102](../README.md#L102) | “能力边界（遇到即报错，不静默降级）”“已在测试里固化为明确的错误信息（不是 panic，也不是静默 0 行）” | 与事实不符：reader v2/v3 接受（P2/P3）、DV 读全量（T37）、未知 action 忽略（L10）、类型错配静默（Q12）、stats object `rows: 0`（T11/T7 F7）、scan 伪分区 0 文件（T14）、CLI 错误退出 0（E4） | §3、§4 | 改为“已对以下闭集固化明确错误；闭集外的以下场景当前仍是静默/未覆盖（列清单）”，并给出 D4 的具体清单 |
| 8 | [README.md:97](../README.md#L97) | 表维度“不支持：checkpoint-only / 日志被截断的表” | checkpoint-only 确实拒绝；**日志被截断的表没有拒绝**，被静默当旧版本（v9/90 文件） | [out/51](protocol-audit/out/51-inspect-checkpoint-truncated.out)、§4.2 | 与第 3 条同改：要么实现拒绝，要么明确写“截断检测未实现，可能读到旧快照（已知缺陷）” |
| 9 | [README.md:15](../README.md#L15)、[README.md:32](../README.md#L32)、[src/cmd/delta/main.mbt:349](../src/cmd/delta/main.mbt#L349) | “MVP（0.1.0）”“从 mooncakes.io 安装（v0.1.0 已发布）”“CLI `version` 输出 moondelta-core 0.1.0” | `moon.mod` 为 `version = "0.1.1"`，申报书第 49 行也称 0.1.1 已发布；CLI `version` 仍硬编码 0.1.0（[out/01](protocol-audit/out/01-version.out)） | [moon.mod:3](../moon.mod#L3)、[out/01](protocol-audit/out/01-version.out) | 统一 0.1.1（CLI 从构建信息读取或同步更新文案） |
| 10 | [ROADMAP.md:16](../docs/ROADMAP.md#L16) | “负向边界：嵌套 schema、checkpoint-only、缺失文件、未知类型 ✅ 明确报错” | 嵌套/缺失文件/未知类型行为属实；checkpoint-only 的“✅”依据的测试实际是非 0 起点（同第 4 条）；未知类型无具名测试（行为已实测 [out/64](protocol-audit/out/64-read-uuid-schema-unknown.out)） | §3.3 C4、§3.4 S21 | 拆开写：行为已实现 vs 测试已覆盖，不要把“行为”写成“测试通过” |
| 11 | [fixtures/README.md:21](../fixtures/README.md#L21) | “`covid/log0.json` # 5,624 B” | 实际路径是 `fixtures/delta/covid/_delta_log/00000000000000000000.json`（5,624 B）。README 的目录树与实际布局不一致（内容/大小一致） | `Get-ChildItem fixtures\delta\covid\_delta_log`；[fixtures/README.md:20-24](../fixtures/README.md#L20-L24) | 修正路径为 `_delta_log/00000000000000000000.json` |
| 12 | [fixtures/README.md:79-80](../fixtures/README.md#L79-L80) | list_columns.parquet“当前依赖 `mizchi/parquet` 对 repeated 列报 `Unsupported(Repeated columns are not supported yet)`，与本项目实测一致” | 该 Parquet 被测试 `read: nested parquet column is rejected` 使用时，Delta schema 已先被 `ensure_readable_schema` 拒绝，**测试根本没有打开 Parquet**；真正的 repeated 错误由本审计的 `nested-parquet-lie` 才验证到（消息确为 `Repeated columns are not supported yet`，[out/76](protocol-audit/out/76-read-nested-parquet-lie.out)） | [src/delta_wbtest.mbt:250-296](../src/delta_wbtest.mbt#L250-L296)；§3.4 S17 | 修正为“schema 层拒绝（测试）+ Parquet 层 repeated 拒绝（本审计补测）”，或补一个 schema 撒谎的测试 |
| 13 | [申报书.md:41](../申报书.md#L41) | “从零手写约 2,390 行 MoonBit（核心库 1,420、CLI 373、测试 552、示例 45）” | 当前行数：核心库 1,508、CLI 383、测试 570、示例 114（D2 新增示例后），合计 2,575；即使剔除 D2 新增示例，也与申报数字不符 | 行数统计见 [code-evidence.out](protocol-audit/out/code-evidence.out) 末尾说明（本报告 §2.1 命令） | 用脚本重新统计并更新，或去掉精确行数 |
| 14 | [申报书.md:47](../申报书.md#L47) | “已实测：…COVID-19_NYT 数据文件读取 157,865 行 6 列；`id>=60` 统计剪枝 120 → 60 个文件；create + append + 重新读回通过” | 三项均复核属实：pyarrow 读该 Parquet 元数据 157,865 行 6 列；CLI 剪枝 `selected 60, skipped stats 60`；append 2 文件回读 5 行 | §4.13；[out/05](protocol-audit/out/05-scan-table-a-id-ge-60.out)、[out/120](protocol-audit/out/120-moon-run-interop-writer.out) | 保留（D2 已完成逐值对拍，可加引用） |
| 15 | [申报书.md:48-49](../申报书.md#L48-L49) | “moon package 通过”“GitHub Actions 流水线已通过”“已发布 mooncakes.io … 空工程安装并编译验证通过” | D1 **未验证**：本审计未执行 `moon package`，未运行 CI，未做 mooncakes 安装验证（避免动环境/网络依赖），仅审阅了 [ci.yml](../.github/workflows/ci.yml) | §6.2 | 由其他任务或发布前复核；D4 引用时标注“待复核”或提供 CI 链接与安装命令输出 |

---

## 6. 测试覆盖缺口与建议补测（具体可抄）

### 6.1 现有 14 个测试未覆盖的矩阵项（优先补前 10）

| 优先级 | 缺口 | 建议测试名（加入 `src/delta_wbtest.mbt`） | 断言要点 |
|---|---|---|---|
| P0 | reader v2/v3 与未知必需 feature 拒绝 | `protocol: reader version > 1 is rejected`、`protocol: unknown required reader feature is rejected` | `snapshot_error_of` 含 `unsupported` 且含 `reader`/feature 名 |
| P0 | 截断日志 + checkpoint 检测 | `snapshot: checkpoint newer than last json is rejected` | 构造 v0..v9 + checkpoint v10 + `_last_checkpoint`，断言错误含 `checkpoint`/`truncated` |
| P0 | 删除向量拒绝 | `read: deletionVector add is rejected` | 错误含 `deletionVector` 或 `unsupported`，不得返回行 |
| P0 | CLI 错误退出码 | 用 `delta.exe` 的独立 CI 步骤断言 `$LASTEXITCODE -ne 0`（MoonBit 测试内可用进程 API，或 CI shell 断言） | 至少 3 个错误场景 |
| P0 | append 类型/列名/NOT NULL | `write: append rejects type mismatch`、`write: append rejects wrong column name`、`write: append rejects null for not-null column` | 抛 `Unsupported`/`ParseError`，且不写新版本 |
| P1 | Delta schema 与 parquet 物理类型不一致 | `read: parquet physical type mismatch is rejected` | `Unsupported`/`ParseError` 含列名 |
| P1 | stats 为 object | `snapshot: stats as json object is parsed or rejected` | 不得出现 `rows: 0` 冒充 |
| P1 | 未知 action 拒绝/告警 | `log: unknown data-changing action is rejected` | `cdc`/`txn` 至少产生 `Unsupported` 或可观测告警 |
| P1 | column mapping / deletion vector 检测 | `protocol: column mapping table is rejected` | 错误含 `columnMapping` |
| P1 | create 覆盖已有表 | `write: create_table refuses non-empty table` | 不覆盖 v0 |
| P2 | 非 20 位文件名 | `log: non standard log file name is rejected` | 错误含文件名/规范说明 |
| P2 | 分区 null 值 | `read: null partition value is preserved or rejected` | 不得报“列缺失” |
| P2 | 缺失 protocol/空日志 | `snapshot: missing protocol action is rejected` | `Unsupported`/`ParseError` |
| P2 | binary / float / date / timestamp / UNCOMPRESSED | `read: primitive type matrix roundtrip` | 5 类各一个 fixture |
| P3 | 字符串/布尔统计 | `scan: string and boolean stats never prune`（安全行为固化） | 不剪枝；不误剪 |
| P3 | `scan --partition` 非分区列 | `scan: unknown partition column is rejected` | 错误/0 文件行为按修正后定 |

### 6.2 建议的补测命令（可直接执行）

```powershell
# 复核本报告的全部实测（会重建 fixtures）
.tools\pyenv\Scripts\python.exe artifacts\protocol-audit\gen_fixtures.py
pwsh -NoProfile -File artifacts\protocol-audit\run_cli.ps1
pwsh -NoProfile -File artifacts\protocol-audit\run_probe.ps1
moon check --target native
moon test --target native
```

### 6.3 对实现的最小修复建议（供后续任务，不属于 D1 交付）

1. `parse_protocol` 增加 `readerFeatures/writerFeatures` 与版本校验（P2/P3/P6/P12）。
2. `replay_until` 前做 checkpoint 一致性检查（C5）。
3. `parse_add` 识别 `deletionVector`；`parse_metadata` 识别 `delta.columnMapping.*`、CDF 配置（T36/T37/T38）。
4. `read_snapshot` 增加 DeltaType ↔ Parquet ColumnType 断言（Q12）与多余列提示（Q13）。
5. `append_rows` 校验 name/type/nullable；按 schema nullable 选择 `Required/Optional`（T28/T29/T30）。
6. `create_table` 校验 schema JSON、拒绝覆盖非空表（T24/T25）。
7. CLI 错误路径设置非 0 退出码；`--where`/`--partition` 失败不降级（E4/F11）。
8. `stats` 支持 object 形式或显式拒绝（F7）。

---

## 7. 未覆盖 / 未验证项与原因

| 项 | 状态 | 原因 |
|---|---|---|
| `moon package` | 未验证 | 不属于 D1 协议行为；未执行以免改动打包产物 |
| GitHub Actions 流水线“已通过” | 未验证 | 无法从本地还原 CI 运行；仅审阅 [ci.yml](../.github/workflows/ci.yml) |
| mooncakes.io 0.1.1 安装验证 | 未验证 | 需联网安装到空工程；D1 未执行（网络可用但与本任务协议矩阵无关） |
| Spark/Databricks/delta-rs/DuckDB 目录级互通 | **D2 范围** | D1 只在协议层引用 D2 的 reader=2 交叉证据；完整 flat 对拍见 D2 报告 |
| 外部引擎读取本项目写出的表 | **D2 范围** | 同上 |
| checkpoint Parquet 的 schema/内容解析 | 未实现（文档已声明） | ROADMAP 0.2 才计划；本审计只验证“不误用” |
| 对象存储（S3/R2） | 未实现（文档已声明） | 本地 fs 之外无抽象 |
| 并发写冲突 | 未实现（文档已声明） | 无仲裁逻辑 |
| 大文件/流式/row-group 剪枝 | 未实现（文档已声明） | 整文件读入内存 |
| fuzz/畸形输入全覆盖 | 未做 | 时间与工具限制；已用 112 个编号 CLI case + 9 个探针/复核脚本覆盖主要分支 |
| 多段 checkpoint / `SetTransaction` 语义 | 未做 | 无 fixture；不影响当前闭集结论 |
| Arrow/DataFrame 互操作 | 未做（D4/D2 文本范围外） | 无相关实现 |

---

## 8. 附录

### 8.1 自造 fixture 清单（47 个表，SHA-256 见 [fixtures.sha256](protocol-audit/fixtures.sha256)）

| 分组 | fixture | 用途 | 对应报告项 |
|---|---|---|---|
| protocol | `protocol-reader-v2`、`protocol-reader-v3-features`、`no-protocol`、`protocol-missing-reader`、`protocol-string-version`、`writer-v5-features`、`empty-log` | 版本/feature/缺省 | P2-P10、T33 |
| actions/log | `unknown-actions`、`deletion-vector-add`、`column-mapping`、`cdf-enabled`、`log-gap`、`log-start-v1`、`nonstandard-log-name`、`malformed-json`、`add-missing-path`、`no-commitinfo`、`dup-add-remove` | action 与日志结构 | L5-L17、T36-T38 |
| checkpoint | `checkpoint-only`、`checkpoint-truncated`、`bad-last-checkpoint` | checkpoint 行为 | C2-C5 |
| schema | `decimal-schema`、`decimal-missing-file`、`uuid-schema-unknown`、`uuid-parquet-logical`、`nested-struct`、`nested-list`、`nested-map`、`nested-missing-file`、`nested-parquet-lie`、`type-mismatch`、`missing-column` | schema 矩阵 | S11-S17、Q12/Q14 |
| parquet | `int96-timestamp`、`date-col`、`float-col`、`codec-zstd/gzip/brotli/lz4`、`corrupt-parquet`、`missing-data-file` | 物理类型/压缩/损坏 | Q1-Q11 |
| 分区/统计 | `partition-table`、`partition-null-value`、`stats-string-bool`、`stats-min-max-eq`、`stats-object` | 分区与剪枝边界 | T9-T16、F7 |
| 基线 | `baseline-flat`、`appended-flat`（D2 示例生成）、`cli-*`（CLI create 生成）、`probe-*`（库级探针生成） | 正向基线 | T17-T33 |

> fixture 来源：全部由 [gen_fixtures.py](protocol-audit/gen_fixtures.py) 用 pyarrow 26.0.0 + 手写 JSON log 生成；checkpoint 相关两个 fixture 从仓库 `fixtures/delta/table_a` 复制（Apache-2.0，哈希见 [fixtures.sha256](protocol-audit/fixtures.sha256)）。未复制或修改任何上游实现代码。

### 8.2 `out/` 输出索引（122 个文件，按编号分组）

| 编号段 | 内容 | 数量 |
|---|---|---|
| 01-18 | 内置 fixtures（table_a/covid/list_columns）+ CLI 基础行为/错误退出码 | 18 |
| 20-31 | protocol version/feature/缺省 | 12 |
| 32-49 | actions / JSON log / 收敛 | 18 |
| 50-53 | checkpoint | 4 |
| 60-76 | schema（decimal/uuid/nested） | 17 |
| 80-84 | Parquet 压缩 codec（zstd/gzip/brotli/lz4 ×4）与 INT96/date/float | 8 |
| 85-88 | 损坏 Parquet/缺失数据文件/类型错配/缺列 | 4 |
| 90-102 | 分区/统计剪枝 | 13 |
| 110-119 | CLI create 各场景 | 10 |
| 120-127 | append 示例程序（D2）/ create 覆盖已有表 / 示例程序 | 8 |
| probe-* | 库级探针（1）+ 写出的 Parquet schema 复核（2）+ 探针表 CLI 观察（6） | 9 |
| code-evidence | 代码行号与“关键字不存在”证据（无退出码） | 1 |

### 8.3 代码行号索引（本报告引用处）

| 主题 | 位置 |
|---|---|
| protocol 解析/缺省 | [src/actions.mbt:15-25](../src/actions.mbt#L15-L25) |
| 未知 action | [src/actions.mbt:148](../src/actions.mbt#L148)、[src/snapshot.mbt:148](../src/snapshot.mbt#L148) |
| replay 起点/版本范围 | [src/snapshot.mbt:107-128](../src/snapshot.mbt#L107-L128) |
| 默认 protocol / 空 metadata | [src/snapshot.mbt:2-21](../src/snapshot.mbt#L2-L21) |
| checkpoint 识别 | [src/log.mbt:99-129](../src/log.mbt#L99-L129) |
| 日志版本发现/文件名 | [src/log.mbt:9-72](../src/log.mbt#L9-L72) |
| schema 解析/nested/decimal/未知类型 | [src/schema.mbt:17-131](../src/schema.mbt#L17-L131) |
| 读取错误映射/Cell 转换 | [src/read.mbt:31-43](../src/read.mbt#L31-L43)、[:144-150](../src/read.mbt#L144-L150) |
| 读列定位/分区补齐 | [src/read.mbt:58-114](../src/read.mbt#L58-L114)、[:155-186](../src/read.mbt#L155-L186) |
| 剪枝逻辑 | [src/scan.mbt:36-124](../src/scan.mbt#L36-L124) |
| 写入/append 校验 | [src/write.mbt:165-271](../src/write.mbt#L165-L271) |
| CLI fail/退出 | [src/cmd/delta/main.mbt:44-46](../src/cmd/delta/main.mbt#L44-L46) |
| DeltaError 定义 | [src/types.mbt:129-144](../src/types.mbt#L129-L144) |
| 14 个测试 | [src/delta_wbtest.mbt](../src/delta_wbtest.mbt)（`Select-String '^test "'` 见 code-evidence） |

### 8.4 复现性注意事项

1. **CLI 二进制**：仓库中 `_build` 的预编译 CLI 可能落后于 `src/`；复跑前务必 `moon build --target native src/cmd/delta`（本审计已重建，见 §2.1）。
2. **并行修改**：D2 会新增/修改 `src/examples/interop_flat_writer`；D3/D4 不碰 `src/`。若 `moon check/test` 因他人半成品失败，先记录后重跑；本审计首轮/收尾两轮均为 0 errors、14/14。
3. **fixture 可重放**：`gen_fixtures.py` 会清空并重建 `artifacts/protocol-audit/fixtures/`；其中 `appended-flat`、`cli-*`、`probe-*` 由 runner 生成，重建后需按 §2.3 顺序重跑。
4. **C5/C4 的 checkpoint fixture** 复制自 `fixtures/delta/table_a`，其哈希在 [fixtures.sha256](protocol-audit/fixtures.sha256) 中可对账；不要用上游新版本替换（参见 [fixtures/README.md:103](../fixtures/README.md#L103)）。
