# 发布前验收（按 docs/协议兼容性与互操作范围.md）

> 验收人：Team Lead　｜　验收时点：2026-10-21 08:4x（Asia/Hong_Kong）
> 验收依据：[docs/协议兼容性与互操作范围.md](../docs/协议兼容性与互操作范围.md) 第 1/2/3 节（协议口径与闭集）、第 6 节（D1–D4 验收）、第 7 节（证据与发布门槛）。
> 本报告只做验收取证，不修复实现、不改文档。以下结论**仅覆盖本机当前工作区可观测状态**。

## 0. 验收前提：本机无法确认“已推送更新”

| 检查 | 结果 | 证据 |
|---|---|---|
| 本地 HEAD | `3376045 docs: add roadmap/changelog and release 0.1.1`（与 10-20 相同） | `git log --oneline -3` |
| 工作区 | 仍是未提交状态：`M .gitignore`、`?? artifacts/`、`?? docs/协议兼容性与互操作范围.md`、`?? src/examples/interop_flat_writer/` | `git status --short` |
| 远程跟踪 | `origin/main` 仍指向 `3376045`；`HEAD..origin/main` 为空 | `git branch -avv` |
| 拉取远程 | 失败：`The requested URL returned error: 502`；`git ls-remote` 同样失败 | `git fetch origin` / `git ls-remote origin main` |
| DNS | `github.com` / `raw.githubusercontent.com` / `api.github.com` 在 hosts 中被指向 `127.0.0.1`，经本地代理返回 502；`mooncakes.io` 解析到保留地址且 TLS 失败 | `Resolve-DnsName`、`Invoke-WebRequest` |
| 本地是否残留新提交 | `git fsck --unreachable` 仅有 10-19 的 2 个历史 amend 提交，无 10-21 提交 | `git fsck --unreachable --no-reflogs` |

**结论**：本机没有收到任何推送内容，且无法访问 GitHub / mooncakes.io 去核对远端。若更新确实已推送，请提供 **commit SHA / 分支 / 可访问的副本**；否则本报告的“不通过”项只代表本机工作区。

## 1. 验收结论总览

| 验收对象 | 结论 | 依据 |
|---|---|---|
| D1 协议审计（`artifacts/protocol-audit.md`） | **通过** | 122 项矩阵覆盖 §6 要求维度；17 项抽查可复现；无“支持所有 Delta”类无证据结论 |
| D2 flat 双向互操作（`artifacts/interop-flat/`） | **通过** | delta-rs 1.6.6 + DuckDB 1.5.6 双引擎双向读；49 PASS/0 FAIL/2 DIFF；`d2_verify.py` 17/17 |
| D3 nested 缺口对照（`artifacts/interop-nested/`） | **通过** | 8 个 fixture 覆盖 struct/3-level LIST/MAP + null/empty；双 oracle；七项门槛 1/7；`d3_verify.py` 38/38 |
| D4 口径补丁（`artifacts/claim-alignment.patch` v3） | **通过（交付物）** | 9 hunk；`git apply -p1 --check` exit 0；应用后三份文档无超范围表述（0 命中） |
| **§7 发布门槛（整体）** | **不通过** | 见 §3：措辞门槛未落地、发布证据缺失、证据未入库 |
| **§1/§2 强制拒绝行为（项目级）** | **不通过** | 见 §4：reader>1、未知 feature、删除向量、截断日志均未拒绝 |
| 工具链 | 通过 | `moon check` 0 errors / 43 warnings；`moon test` 14/14 passed |

## 2. D1–D4 按第 6 节验收（通过项）

### D1（§6 D1 验收：矩阵覆盖 + 无无证据结论）

- 矩阵维度齐全：§3.1 protocol、§3.2 JSON log 与 actions、§3.3 checkpoint、§3.4 schema、§3.5 Parquet、§3.6 表操作、§3.7 错误策略与退出码。
- 122 项分布：已实现且有测试 27 / 已实现无测试 20 / 只识别 4 / 明确拒绝 24 / 未覆盖 47（27 项为静默行为）。
- 每条结论均附 `文件:行号`、测试名或 `out/` 原始输出；全报告无“支持所有 Delta / 完整实现 Delta”等无证据表述。
- 本次复验（2026-10-21）抽查严重项仍可复现，见 §4 命令输出。

### D2（§6 D2 验收：可重放 + 逐项 diff + 外部引擎）

- 表结构 `id long NOT NULL, name string, active boolean`，含 null，外部引擎写出 2 个 SNAPPY 文件 / 2 次提交。
- `run_interop.py` 可重放；`results.json` 56 项检查 = 49 PASS / 0 FAIL / 2 DIFF / 5 INFO / 0 BLOCKED；`hashes.txt` 79 项可重算（本次 `d2_verify.py` 17/17 PASS）。
- 外部引擎 2 个：delta-rs 1.6.6（reader 1/writer 2）+ DuckDB 1.5.6（delta 扩展 6059958，`delta_scan`）。
- 已记录差异未被掩盖：NOT NULL 列物理编码为 Optional；`add.stats` 缺 `nullCount` 与非数值列 min/max。

### D3（§6 D3 验收：三类 nested + 负向不冒充通过）

- `engine-written/`：5 行，覆盖 `profile=NULL`、struct 子字段 null、`tags=[]`、`tags=NULL`、数组含 null 元素、`attrs={}`、`attrs=NULL`；protocol reader 1/writer 2。
- 双 oracle 成功：delta-rs 与 DuckDB 均读回 5 行，逐字段一致。
- moondelta：`inspect` 报告 nested kind，`read` 在打开 Parquet 前结构化拒绝（错误含列名 + kind）；无 0 行假成功；checkpoint-only 返回 NotFound；负向物理嵌套列在 Parquet 层拒绝。
- 七项门槛统计 1/7（仅证据门槛 6），明确写“不构成 nested 互操作”。

### D4（§6 D4 验收：每一处承诺可映射）

- v3 补丁 9 hunk（申报书 2 / README 5 / ROADMAP 2），逐 hunk 映射 D1 §5 的 15 条不一致、D2/D3 证据与本文闭集。
- 应用后：申报书 52 行、README 144 行、ROADMAP 68 行；`moon package`/CI/mooncakes 三项未验证断言改为“待复核”；超范围表述 0 命中。
- **注意**：补丁尚未应用到工作区文档（见 §3），交付物合格 ≠ 发布材料已合规。

## 3. §7 证据与发布门槛（不通过项）

| # | §7 要求 | 状态 | 证据/缺口 |
|---|---|---|---|
| 1 | `moon check --target native`、`moon test --target native` | ✅ | 0 errors / 43 warnings；14/14 passed（`artifacts/acceptance/moon-*`） |
| 2 | flat 对拍的外部引擎版本、命令、fixture 哈希、结果 diff | ✅ | D2 全套（`results.json`、`hashes.txt`、`raw/`） |
| 3 | nested 外部成功 + 本项目结构化拒绝日志 | ✅ | D3 双 oracle + `raw/` 114 份原始输出 |
| 4 | protocol 审计矩阵 + 申报/README 文本 diff | ✅（diff 存在） | D1 + `claim-alignment.patch` v3 |
| 5 | 新增 fixture 的来源、许可证、SHA-256 | ✅ | `fixtures/README.md`、`protocol-audit/fixtures.sha256`、`interop-nested/fixture-manifest.json`、D2 `hashes.txt` |
| 6 | **“版本标签和申报材料只能使用受限 flat 表述”** | ❌ | 工作区 `申报书.md`/`README.md` 仍是旧口吻：`可直接互通`、`已有表可以平移`、`Required/Optional`、`遇到即报错，不静默降级`、`checkpoint-only 表明确拒绝`、`warning 全部来自 vendored parquet`、`moon package 通过`、`流水线已通过`、`空工程安装并编译验证通过`（命中位置：申报书 L19/L21/L43/L46/L48/L49，README L21/L90/L98） |
| 7 | 发布证据可复核（package / CI / 安装） | ❌（未验证） | 本机未执行 `moon package`；GitHub Actions 与本机 mooncakes 安装均不可访问（网络被限制），无链接/日志 |
| 8 | 证据随材料入库 | ❌ | `artifacts/`、`docs/协议兼容性与互操作范围.md`、`src/examples/interop_flat_writer/` 均为未跟踪；若推送时未一并提交，补丁引用会失效 |

> 第 6 项修复只需一条命令（应用已验收的补丁）：`git apply -p1 artifacts/claim-alignment.patch`；第 7 项需在可联网环境补 CI 链接、`moon package` 输出、空工程安装日志，或删除对应断言。

## 4. 项目级行为与第 1/2 节“必须拒绝”要求的差距（不通过项）

文档第 1 节：“遇到高于支持范围的 protocol version、未知必需 feature、删除向量、column mapping……读取端必须拒绝并说明原因，不能静默忽略”；第 2 节表格同义。2026-10-21 复验（CLI 为源码重建产物）：

```text
# ① 删除向量：3 行 fixture（声明 cardinality=1）读出 3 行，无错误，exit 0
read 1 file(s), 3 row(s), version 0
1 / 2 / 3

# ② 截断日志 + checkpoint：静默当 v9 读，无错误，exit 0
current version  : 9  (log versions: 10)
files            : 90  rows: 90

# ③ reader=3 / writer=7 + 未知必需 feature：被接受并读 3 行，exit 0
current version  : 0  (log versions: 1)
files            : 1  rows: 3
protocol         : reader=3 writer=7

# ④ checkpoint-only：结构化 NotFound（消息未提 checkpoint-only），exit 0
error: not found: table '...' has no log entries

# ⑤ nested：结构化拒绝且带列名/kind，但 CLI 仍 exit 0
error: unsupported: nested column 'profile' (type=struct) ...
```

| 文档要求（§1/§2） | 实现行为 | 结论 |
|---|---|---|
| reader > v1 必须拒绝 | reader=2/v3 均被接受 | ❌ |
| 未知必需 feature 必须拒绝 | `readerFeatures` 不解析、不拒绝 | ❌ |
| 删除向量必须拒绝 | 静默忽略，读到已删除行 | ❌ |
| 日志被截断/依赖 checkpoint 必须拒绝 | 静默读旧版本（v9，而非 checkpoint v10） | ❌ |
| 未知 action 不能静默忽略 | `cdc`/`txn`/`domainMetadata` 静默忽略 | ❌ |
| column mapping 拒绝并说明原因 | 拒绝，但错误为“列缺失”（原因误导） | ⚠️ 部分 |
| checkpoint-only 必须拒绝 | NotFound（未说明 checkpoint 原因） | ⚠️ 部分 |
| 闭集外必须结构化错误 | CLI 错误退出码恒 0（脚本无法判定失败） | ❌ |

D1 已把这些记录为“已知缺口”，补丁也如实写为“未检测/待修复”；但按文档口径，这些属于**必须实现的行为**，不是可长期保留的表述问题。修复前，项目不能宣称满足本文的协议兼容性口径，只能宣称“受限 flat 子集可读，闭集外行为未保证”。

## 5. 复验命令

```powershell
git log --oneline -3; git status --short; git branch -vv
git fetch origin                       # 本机返回 502，需可联网环境核对推送
moon check --target native; moon test --target native
.tools\pyenv\Scripts\python.exe artifacts\acceptance\d2_verify.py
.tools\pyenv\Scripts\python.exe artifacts\acceptance\d3_verify.py
.tools\pyenv\Scripts\python.exe artifacts\acceptance\d3_duckdb_check.py
git apply -p1 --check artifacts\claim-alignment.patch
_build\native\debug\build\cmd\delta\delta.exe inspect artifacts\protocol-audit\fixtures\checkpoint-truncated
_build\native\debug\build\cmd\delta\delta.exe read    artifacts\protocol-audit\fixtures\deletion-vector-add
```

## 6. 达成“通过”的最小条件

1. **语境可核对**：提供已推送的 commit SHA，或让本机可 fetch；本报告按该提交重跑 §5 命令。
2. **口径落地**：应用 `artifacts/claim-alignment.patch`（已验证 0 冲突），并把 `artifacts/`、本文档、`src/examples/interop_flat_writer/` 一并提交。
3. **行为补齐**（或明确降级承诺）：实现 §4 表中的拒绝行为——reader/feature 校验、删除向量、截断日志/checkpoint、column mapping 提示、CLI 非 0 退出码；补对应负向测试后，才能使用文档第 5 节“明确拒绝”措辞。
4. **发布证据**：`moon package` 输出、GitHub Actions 运行链接、mooncakes.io 空工程安装日志（或删除对应断言）。
