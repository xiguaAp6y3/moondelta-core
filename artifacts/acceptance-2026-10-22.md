# 验收续报：代码质量与要求差距（2026-10-22）

> 验收人：Team Lead　｜　验收时点：2026-10-22（Asia/Hong_Kong）
> 上轮报告：[artifacts/acceptance-2026-10-21.md](acceptance-2026-10-21.md)（D1–D4 交付物、§7 发布门槛、§1/§2 强制拒绝行为）
> 本轮范围：**源码代码质量**（上轮未覆盖）+ 上轮不通过项复核。依旧只做验收取证，不修复实现、不改文档。结论仅覆盖本机当前工作区。

## 0. 前提：工作区自上轮验收后无变化

| 检查 | 结果 | 证据 |
|---|---|---|
| 本地 HEAD | `3376045 docs: add roadmap/changelog and release 0.1.1`（与 10-20/10-21 相同） | `git log --oneline -3` |
| 工作区 | 仍是未提交状态：`M .gitignore`、`?? artifacts/`、`?? docs/协议兼容性与互操作范围.md`、`?? src/examples/interop_flat_writer/`（新增 `?? .mimosa/` 为本机扫描缓存，与项目无关） | `git status --short` |
| 源码改动 | `git diff` 仅 `.gitignore`（+4 行）；`src/*.mbt` mtime 停留在 10-18/10-19，早于上轮验收 | `git diff --stat`、`ls -lt src` |

上轮 §3/§4 的不通过项**不可能是修复后的状态**，本轮以运行时复核确认。

## 1. 结论总览

| 验收对象 | 结论 | 依据 |
|---|---|---|
| 上轮 §4 强制拒绝行为（§1/§2） | **仍不通过（原样复现）** | §2：删除向量、截断日志、reader>1、未知 feature、未知 action、CLI 退出码逐项复跑 |
| 上轮 §7 发布门槛 | **仍不通过（未动）** | 补丁未应用、交付物仍未入库、发布证据仍缺失 |
| 工具链 | 通过（与上轮一致） | `moon check --target native` 0 errors / 43 warnings；`moon test --target native` 14/14 passed |
| **核心源码质量（本轮新增）** | **有条件通过** | 架构与错误体系合格（§3）；但写入路径有 3 项 P1 正确性/数据安全隐患，读路径有 4 项 P2 健壮性缺口（§4） |
| warning 归属核查 | **旧 README 说法与事实相反（补丁修正正当）** | 43 条 warning 中 39 条在核心代码（types 27 / scan 8 / read 3 / examples 1），vendor 仅 4 条（§4-11） |

## 2. 上轮 §4 强制拒绝行为复核（全部原样，exit 恒 0）

CLI 为本机重建产物（`moon build --target native`）。fixture 路径 `artifacts/protocol-audit/fixtures/`：

```text
deletion-vector-add        → reader=3 writer=7 被接受，rows: 3（删除向量被静默忽略）        exit=0
checkpoint-truncated       → current version 9，静默回放 0..9（不校验 _last_checkpoint v10） exit=0
protocol-reader-v3-features→ reader=3 writer=7 + 未知 readerFeatures 被接受，rows: 3        exit=0
unknown-actions            → v1 含未知 action，静默跳过，rows: 3                            exit=0
log-gap                    → error: not found: log version 1 is missing (...)               exit=0
log-start-v1               → error: unsupported: log starts at version 1; ...               exit=0
nonstandard-log-name       → error: not found: log version 0 is missing (...)               exit=0（见 §4-5，报错误导）
```

`src/cmd/delta/main.mbt` 全文无 `exit` 调用（`fail()` 仅 `println`，且错误输出到 stdout）——"闭集外必须结构化错误 + 非零退出码"在代码层面即不成立，与运行时证据一致。

上轮 §7 门槛复核：`git apply -p1 --check` 补丁仍可应用但**未应用**；`申报书.md`/`README.md` 无改动；`artifacts/`、`docs/协议兼容性与互操作范围.md`、`src/examples/interop_flat_writer/` 仍为 untracked。第 6/7/8 项缺口未动。

## 3. 核心源码质量（本轮新增审查）

核心代码约 700 行（`src/*.mbt` 10 个文件，不含 vendor 6,844 行 / 示例 114 行 / 测试 571 行）。总体评价：**架构合格，写入路径与边界输入是短板**。

### 做得好的（通过项）

- **模块职责清晰**：`types`（类型+错误）/ `actions`（日志解析）/ `log`（文件枚举）/ `snapshot`（回放）/ `scan`（文件级剪枝）/ `read`（数据读取）/ `write`（建表+追加）/ `json_util`（JSON 访问）各司其职，核心无循环依赖；vendor 边界干净（核心只经 `@parquet` 包接口交互）。
- **结构化错误体系贯穿核心层**：`DeltaError` 四类（Io/Parse/Unsupported/NotFound）+ `describe_error`，嵌套类型、未知类型、decimal 均在读取前由 `ensure_readable_schema`（schema.mbt:113）给"列名 + 类型"的结构化拒绝，符合"不静默返回错误数据"的设计意图（schema.mbt:4-6 注释明确记录了该决策）。
- **解析 fail-safe 方向正确**：未知 schema 类型 → `UnknownType` → 读取前拒绝；stats 缺失 → 不剪枝（保守放行，scan.mbt:81-93，测试 `stats-edge` 覆盖 min==max 边界）。
- **测试质量尚可**：14 个白盒测试覆盖回放/时间旅行/剪枝/分区/布尔-double 回环/checkpoint-only 拒绝/嵌套拒绝/缺文件等正负路径，断言具体（版本号、文件数、行数、单元格值）。
- CLI 与示例输出格式一致，示例与《协议兼容性与互操作范围.md》第 4 节案例 A 对齐。

### 明显短板（与 §4 分级对应的根因）

- 写路径是"后加的"（commit ee53c6b 扩展 boolean/double），但缺基本防护：无表存在性检查、无 schema 一致性校验、无原子提交（→ §4 P1）。
- "遇到不认识的东西就跳过"的默认值风格（`UnknownAction` 忽略、stats 解析失败返 0、非字符串 partition 列丢弃）与文档 §1"不能静默忽略"的口径系统性相悖（→ §4 P2）。

## 4. 新增发现清单（按严重度）

### P1（写入路径正确性/数据安全，均有运行时证据）

| # | 问题 | 位置 | 证据 |
|---|---|---|---|
| 1 | **`create_table` 可静默覆盖已存在的表**：无 `_delta_log` 存在性检查，二次 create 直接重写 version 0（含 schema），已有数据文件残留但 schema 漂移 → 表损坏。CLI 无警告、exit 0 | write.mbt:165-185（`write_text` 无条件覆盖）、main.mbt:328 | 双次 `delta create`（long → string）后 `00000000000000000000.json` 的 schemaString 被替换为 `other:string`，exit=0 |
| 2 | **`append_rows` 不校验列名/类型与表 schema 一致**：只查列数（write.mbt:203）与各列行数（write.mbt:211），类型用输入侧的 `c.type_`（write.mbt:227）；向 long 表 append string 列不会被拒绝，读回时按 schema 顺序错位归属 | write.mbt:202-240 | 代码审查；`probe-append-mismatch`/`probe-append-name-mismatch` fixture 已在 D1 审计中记录该行为 |
| 3 | **提交非原子**：commit 文件用 `write_text` 直接写目标版本（Delta 规范要求 put-if-absent），并发写或对已有版本重写时静默覆盖、无冲突检测 | write.mbt:269 | 代码审查（同 #1 根因） |

### P2（读路径健壮性/静默行为，均有运行时或代码级证据）

| # | 问题 | 位置 | 证据 |
|---|---|---|---|
| 4 | **`add.path` percent-encoding 未解码**：Delta 规范中 `path` 是 percent-encoded；直接拼文件系统路径导致含空格/非 ASCII 文件名的表不可读 | read.mbt:143；全仓 grep 无 decode | 运行时：`add.path="my%20file.parquet"` + 磁盘 `my file.parquet` → `error: io error: No such file or directory`（且错误不含文件名） |
| 5 | **非 20 位日志名伪造版本号**：`version_from_name` 对任何数字前缀返回值（`0.json` → 0），`list_versions` 不校验文件名格式；`versions[0] != 0` 的 checkpoint-only/truncated 防护可被杂散文件绕过，随后报"checkpoint-only or truncated table?"的误导错误 | log.mbt:22-33、log.mbt:63-68、snapshot.mbt:111-115 | 运行时：fixture `nonstandard-log-name`（`_delta_log/0.json`）→ `error: not found: log version 0 is missing (checkpoint-only or truncated table?)` |
| 6 | **stats 解析失败静默返 0**：`total_rows`/`matched_rows` 在 stats JSON 损坏时静默失真，无告警（读路径的保守 fail-safe 与统计路径的静默 fallback 不对称） | snapshot.mbt:59-70 | 代码审查 |
| 7 | **`json_escape` 把 < 0x20 的其余控制字符静默替换为空格**：写路径篡改字符串数据且无任何提示（如 `\u0001` → `' '`） | write.mbt:12 | 代码审查 |
| 8 | **CLI 错误到 stdout、恒 exit 0、未知参数静默跳过**：`fail()` 用 `println`；`main` 无 exit；`cmd_scan`/`cmd_read` 对无法识别的 flag 落入 else 直接跳过（如末尾缺值的 `--partition`） | main.mbt:44-46、207-209、262-264、340-383 | §2 运行时证据（所有错误路径 exit=0） |

### P3（质量/一致性小项）

| # | 问题 | 位置 |
|---|---|---|
| 9 | 注释过期：write.mbt:189 "只支持非分区表 + int/long/string/binary 列"——ee53c6b 已加 boolean/double，注释未跟上 | write.mbt:189 |
| 10 | 回放复杂度 O(n²)：`upsert_file`/`drop_file` 每次 add/remove 全量重建数组（snapshot.mbt:24-56）；`list_versions` 的 `contains` 去重同病（log.mbt:63-68）。MVP 可用，但应在文档注明规模上限 | snapshot.mbt、log.mbt |
| 11 | **warning 归属与旧 README 相反**：43 条 = `implicit_impl_as_method` 38（核心 types 27 / scan 8 / read 3 / examples 1）+ vendor 4（`unused_value` 3、`unused_package` 1）+ `core_package_not_imported` 1（examples 的 moon.pkg 未显式 import `env`/`string`）。旧 README"warning 全部来自 vendored parquet"不成立——D4 补丁将其改为"待复核"是正确修正，本条为其提供证据 | `moon check --target native` 输出分组 |
| 12 | Int64 列的 min/max 剪枝经 Double 承载（scan.mbt:16、write.mbt:78-104），\|x\|>2^53 时边界比较可能失真；`row_count` 为 Int32（read.mbt:25），>2^31 行溢出；`limit` 缺省用魔法数 1000000000（read.mbt:135） | scan.mbt、write.mbt、read.mbt |
| 13 | fs 层错误无上下文：`read_text`/`write_text` 不带路径（log.mbt:37-49），parquet 层补了文件名（read.mbt:144-149）但 fs 层没补——`io error: No such file or directory` 无法定位是哪个文件（与 #4 复现一致） | log.mbt、read.mbt |
| 14 | 解析辅助静默丢数据：`parse_partition_columns` 丢非字符串项（actions.mbt:30-39）、`json_object_pairs_string` 丢非字符串/数字值（json_util.mbt:116-133）、`digits_to_int` 忽略负号（`decimal(-1,2)` → precision=1，schema.mbt:3-13） | actions.mbt、json_util.mbt、schema.mbt |

## 5. 复验命令

```powershell
git log --oneline -3; git status --short; git diff --stat
moon check --target native; moon test --target native
_build\native\debug\build\cmd\delta\delta.exe inspect artifacts\protocol-audit\fixtures\deletion-vector-add
_build\native\debug\build\cmd\delta\delta.exe inspect artifacts\protocol-audit\fixtures\checkpoint-truncated
_build\native\debug\build\cmd\delta\delta.exe inspect artifacts\protocol-audit\fixtures\protocol-reader-v3-features
_build\native\debug\build\cmd\delta\delta.exe inspect artifacts\protocol-audit\fixtures\nonstandard-log-name
# 双次 create 覆盖验证（临时目录）
delta create <root> '<schema-long>'; delta create <root> '<schema-string>'; cat <root>\_delta_log\00000000000000000000.json
# percent-encoding 验证：复制 appended-flat，重命名数据文件含空格，日志中 path 改为 %20 编码后 read
```

## 6. 结论与最小条件（在上轮 §6 基础上追加）

上轮 §6 的 4 条最小条件（语境可核对、口径落地、行为补齐、发布证据）**全部仍未满足**，维持不变。本轮追加：

5. **写入路径防护（对应 P1）**：`create_table` 拒绝已存在的表；`append_rows` 校验列名+类型与 metaData.schema 一致；commit 改为"存在即失败"的写入语义。三项各配一个负向测试。
6. **读路径边界（对应 P2）**：日志文件名严格按 20 位数字校验；`add.path` 做 percent-decoding；静默 fallback（stats、控制字符、未知参数）要么改为显式错误、要么在文档"已知缺口"中如实申报。

P1 三项建议在下次发布前修复；在修复前，README/申报书除 flat 受限表述外，也不应暗示写入路径可用于真实数据（当前补丁文本已满足此口径，应用补丁即可）。
