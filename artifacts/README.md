# artifacts —— D1–D4 交付索引与验收记录

> 任务来源：[docs/协议兼容性与互操作范围.md 第 6 节（D1–D4 指派）与第 7 节（发布证据门槛）](../docs/协议兼容性与互操作范围.md)。
> 执行方式：Team Lead 拆分为共享任务，分别指派 4 个子 agent 并行完成 D1–D4，再由独立子 agent（D5）做证据复核；Lead 负责环境准备、合并与最终校验。
> 完成时间：2026-10-20（Asia/Hong_Kong）。仓库 HEAD：`3376045`。

## 1. 任务分配

| 编号 | 共享任务 | 执行者 | 交付物 | 状态 |
|---|---|---|---|---|
| D1 | task-1 | `protocol-auditor` | [protocol-audit.md](protocol-audit.md) + [protocol-audit/](protocol-audit/) | 完成，D5 复核通过 |
| D2 | task-2 | `flat-interop` | [interop-flat/](interop-flat/) | 完成，D5 复核通过 |
| D3 | task-3 | `nested-interop` | [interop-nested/](interop-nested/) | 完成，D5 复核通过 |
| D4 | task-4 → task-6 → task-7 | `claim-aligner` | [claim-alignment.patch](claim-alignment.patch) + [claim-alignment.md](claim-alignment.md) | 完成（v3，D5b 复核通过） |
| D5 | task-5 / task-8 | `evidence-verifier` | [acceptance-report.md](acceptance-report.md) + [acceptance/](acceptance/) | 完成，无阻塞 |

Lead 侧环境准备与合并工作：

- 修复工作区 Windows 文件权限、验证 MoonBit 工具链（`moon 0.1.20261012`）；
- 建立 Python 引擎环境 `.tools/pyenv`（已写入 `.gitignore`）：Python 3.12.14 / duckdb 1.5.6 / pyarrow 26.0.0 / deltalake 1.6.6 / pandas 3.0.6；
- 预置并构建 flat 写入示例 [../src/examples/interop_flat_writer/](../src/examples/interop_flat_writer/)（D2 使用，并入代码行数统计）；
- 最终校验：`moon check --target native`（0 errors / 43 warnings）、`moon test --target native`（14/14 passed）、`git apply -p1 --check artifacts/claim-alignment.patch`（exit 0）。

## 2. 交付物与 SHA-256（最终版）

| 文件 | 大小 | SHA-256 |
|---|---:|---|
| `artifacts/protocol-audit.md` | 83,960 B | `3C5D515A8ED0463AA997A66D12E003D33B46A9D2B7E83C35B77D02C796F4F811` |
| `artifacts/interop-flat/README.md` | 25,837 B | `9B6F99DC3DC09C1FA1850F1620E48DB4EBE5DAC19C1836E3BA65BD4C6FECB0BB` |
| `artifacts/interop-nested/README.md` | 30,964 B | `71870EF1934ADCDACE28277017FDE450F4283FCAF55A0171174184BA73C7ED5F` |
| `artifacts/claim-alignment.patch` | 22,063 B（172 行，9 hunk） | `DBDE5AB7E0578C43828ACD5C601A29EB360A20795DB0C9101F14AE108FDEA3EA` |
| `artifacts/claim-alignment.md` | 43,161 B（v3） | `0B4BB22025156F9CE3CCB52376055EEE2385D1E05DF3C3F505D142FE988291A7` |
| `artifacts/acceptance-report.md` | 55,660 B（含 D4 v3 增量复核附录） | `5A76FBBFC1E03FB3C4AC8A3F483BA241D2A3B9615097656574227E1C42F633C2` |

## 3. 关键结论

### D1 协议审计（122 项矩阵）

- 分布：已实现且有测试 27 / 已实现无测试 20 / 只识别 4 / 明确拒绝 24 / 未覆盖 47（其中 27 项为静默行为）。
- 严重缺口（均有自造 fixture 与原始输出）：
  - 删除向量 `add.deletionVector` 被静默忽略并读全量（3 行 fixture 读出 3 行，正确应为 2 行）；
  - 截断日志 + checkpoint 的表被静默当旧快照读（报 v9 / 90 文件，实际 checkpoint v10）；
  - reader v2/v3、`readerFeatures`/未知必需 feature 完全不校验（reader=3/writer=7 仍 read 成功）；
  - CLI 任何错误退出码恒 0；`append_rows` 不校验列名/类型/NOT NULL，写出列恒为 Parquet Optional；
  - `add.stats` 为 JSON object 时被丢弃（行数显示 0）、未知 action 静默忽略、类型错配静默读出错值。
- 正向基线：table_a 13 版本回放 120 文件；`id>=60` 剪枝 120→60；COVID 文件 157,865 行 6 列；nested/decimal/4 种不支持 codec 均为带上下文的 `Unsupported`，且 nested 拒绝发生在打开 Parquet 之前。

### D2 flat 双向互操作（49 PASS / 0 FAIL / 2 DIFF / 5 INFO）

- 外部引擎：delta-rs（deltalake 1.6.6，protocol reader 1 / writer 2，5 行 2 个 SNAPPY 文件 2 commits）+ DuckDB 1.5.6（delta 扩展 `delta_scan`）。
- delta-rs 写 → moondelta 读：schema/列序/类型/nullability/版本/文件清单/逐值含 null/history/add.stats 全部一致；`scan --where "id>=4"` 剪枝 2→1 文件。
- moondelta 写 → 外部读：3 commits / 2 个 UNCOMPRESSED 文件 / 5 行，delta-rs 与 DuckDB 读回 schema、行数、逐列值一致。
- 已记录差异：NOT NULL 列物理编码为 Optional；`add.stats` 缺 `nullCount` 与非数值列 min/max（影响剪枝，不影响读值）。

### D3 nested 能力缺口（七项门槛 1/7，仅证据门槛 6）

- 8 个 fixture，覆盖 struct / 3-level LIST / MAP 与 null、empty、null 元素等边界；protocol reader 1 / writer 2。
- oracle：delta-rs 与 DuckDB 均成功读回 5 行，逐字段编码一致。
- moondelta：`inspect` 能报告 nested kind；`read` 在打开 Parquet 前结构化拒绝（`nested column 'profile' (type=struct) ...`），无假成功、无 0 行、无类型混淆；但 CLI 失败仍 exit 0。
- 结论口径：**flat 子集兼容、nested 明确拒绝**，不得当作 nested 互操作通过。

### D4 申报/README 口径对齐补丁（v3）

- 9 个 hunk：`申报书.md` 2、`README.md` 5、`docs/ROADMAP.md` 2（`CHANGELOG.md` 核对后无需改）。
- 覆盖：删除“Spark/Databricks/delta-rs/DuckDB 可直接互通/已有表平移”；写入实际对拍结论（delta-rs + DuckDB，Spark/Databricks 未验证）；协议版本/日志范围/flat 子集/压缩/本地文件系统；nested 1/7 不构成兼容；删除向量、截断 checkpoint、reader≥2、append 校验、CLI 退出码等如实标为“未检测/已知缺口”；`moon package`/CI/mooncakes 发布三项标为“待复核”；修正 warning 统计与测试覆盖表述。
- 应用方式（不会直接修改文档，需人工确认后应用）：

```powershell
git apply -p1 artifacts/claim-alignment.patch
```

### D5 独立验收

- 四份交付物均 **通过**；无阻塞项。D4 的 v2 遗留项（未验证发布断言、warning 计数、121 次口径）已在 v3 闭环。
- D1 剩余 3 项低风险文档性瑕疵（out/ 索引 122 vs 123、commands.txt 记录数与“exit 0”口径、47 表 vs 62 目录口径）已记录在 `acceptance-report.md` §8，不影响结论。
- 工作区卫生：仅 `.gitignore` 修改与 `artifacts/`、`docs/协议兼容性与互操作范围.md`、`src/examples/interop_flat_writer/` 三个未跟踪项。

## 4. 复现命令

```powershell
# 工具链
moon check --target native
moon test  --target native

# D1：重建自造 fixture + 逐 case 重放（输出写入 protocol-audit/out/）
.tools\pyenv\Scripts\python.exe artifacts\protocol-audit\gen_fixtures.py
pwsh -NoProfile -File artifacts\protocol-audit\run_cli.ps1
pwsh -NoProfile -File artifacts\protocol-audit\run_probe.ps1

# D2：一键复现（--build 可先重建 CLI；退出码 0 = 无 FAIL/BLOCKED）
.tools\pyenv\Scripts\python.exe artifacts\interop-flat\run_interop.py

# D3：一键复现
.tools\pyenv\Scripts\python.exe artifacts\interop-nested\run_nested.py

# D4：校验补丁
git apply -p1 --check artifacts\claim-alignment.patch

# D5：复核结论与原始证据
Get-Content artifacts\acceptance-report.md
```

## 5. 入库与残留待办

- **未跟踪需一并入库**：`artifacts/`（扣除 `.tools/`、`_build/`、`.mooncakes/` 等已忽略内容后约 2 MB）、`docs/协议兼容性与互操作范围.md`、`src/examples/interop_flat_writer/`；否则补丁新增的证据引用与 `moon add` 示例行数会失效。
- `.tools/pyenv` 为本地 Python 引擎环境，已写入 `.gitignore`，不入库。
- **发布前仍需人工补齐的证据**（D5 无法代为生成，补丁已如实标注“待复核”）：① `moon package` 实际输出；② GitHub Actions 运行链接/日志；③ mooncakes.io `0.1.1` 空工程安装日志与页面链接。
- 代码层待修复项（本批任务只审计与出补丁，不改实现）：删除向量检测、截断日志 + checkpoint 检测、protocol version/feature 校验、append 列名/类型/NOT NULL 校验、CLI 非 0 退出码、stats object 解析。修复前 README/申报书不得改回“明确拒绝/不静默降级”。
- 范围外小错建议另开文档修正任务：`fixtures/README.md` 两处路径/测试描述（D1 §5 #11/#12）、`docs/开发计划.md` L6 与 NOTICE 的 writer 描述矛盾。

## 6. 2026-10-21 发布前验收（按文档 §1/§2/§6/§7）

- 报告：[acceptance-2026-10-21.md](acceptance-2026-10-21.md)。
- 前提：本机未收到推送（HEAD 仍 `3376045`，工作区仍为未提交状态；GitHub / mooncakes.io 被本机网络屏蔽，`git fetch` 返回 502），本报告仅覆盖本机可观测状态。
- 结论：D1–D4 交付物 **通过**；§7 发布门槛 **不通过**（措辞门槛未落地、package/CI/安装证据缺失、证据未入库）；项目对 §1/§2“必须拒绝”行为 **不通过**（reader>1、未知 feature、删除向量、截断日志、未知 action）。
- 达成通过的最小条件、复验命令与证据见报告 §5/§6。
