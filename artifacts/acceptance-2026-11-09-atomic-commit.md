# 写入原子性与恢复一致性验收（2026-11-09）

## 首轮结论（2026-11-09 修复前）

**不通过。** 当前 `append_rows` 将 Parquet 写到最终文件路径，再将 JSON commit 直接写到正式版本路径。正式日志路径一创建，就会被 `list_versions` 认作一个版本；写入期间读者可能读到空文件、仅含部分 action 的日志或不完整 JSON。`write_text_new` 先检查路径是否存在，再调用普通写入，不提供原子创建或原子发布保证。当前实现不能满足 Delta commit 的不可变可见性、崩溃恢复和并发冲突要求。

验收标准见 [协议兼容性与互操作范围.md](../docs/协议兼容性与互操作范围.md) 的“提交原子性”与“写入事务验收”。

## 代码证据

- `src/write.mbt:314` 以 `@fs.write_bytes_to_file(file_path, data)` 直接写最终 Parquet 路径。写入失败或进程中断可留下半成品；路径名由 timestamp 和版本组成，重试可能撞上残留文件并失败。
- `src/write.mbt:335` 对正式版本 JSON 调用 `write_text_new`，没有先写临时日志再原子发布。
- `src/log.mbt:62-68` 的 `write_text_new` 实际是 `path_exists` 后 `write_text`。检查和写入之间存在竞态；它不是 put-if-absent 的原子操作。`write_text` 最终调用 `@fs.write_string_to_file`，直接创建/截断正式文件。
- `src/log.mbt:74-105` 的版本发现只按正式日志文件名列版本，未验证 commit 文件完整性或要求至少存在有效 action。因此部分或空的正式 JSON 文件会进入可见版本集合；空文件解析为空 action 列表，回放会将 snapshot version 推进到该版本而不改变文件清单。
- `src/write.mbt:246-249` 将输入行数计算为 0 后仍继续构造 Parquet 并 append；没有拒绝零行批次。
- `src/delta_wbtest.mbt:1268` 的用例只覆盖“完整目标文件已存在时拒绝覆盖”，没有验证并发创建、写入期间可见性、进程中断或恢复。

## 验收矩阵

| 场景 | 当前状态 | 依据 |
|---|---|---|
| 正常 append 后读到完整新版本 | 通过现有往返测试 | `src/delta_wbtest.mbt` 写入及读回用例 |
| 同一目标版本已存在时拒绝覆盖 | 部分通过 | 仅预先存在文件测试；不是原子 put-if-absent |
| 正式 commit 发布前保持旧快照 | 不通过 | 直接写正式路径；文件名出现即被版本发现 |
| commit 中途被读者观察时仍只见旧版或完整新版 | 不通过 | 没有临时日志与原子发布步骤 |
| 日志写入中断后恢复且无伪版本 | 不通过 | 正式路径可留下空/部分 JSON；没有恢复清理逻辑 |
| Parquet 写入中断后可安全重试 | 不通过 | 最终数据路径可能遗留部分文件并阻止重试 |
| 两个进程并发 append 同一表 | 不通过 | 版本选择基于快照；不存在原子版本占用或冲突仲裁，`path_exists` 检查也有竞态 |
| 空输入批次不会产生空提交 | 不通过 | `row_count == 0` 未拒绝，仍写文件和 commit |

## 命令验证

- `moon check --target native`：退出码 0；有现存编译警告。
- `moon test --target native`：退出码 0，37/37 通过。
- 上述测试结果不覆盖原子发布、中断和并发场景，不能据此判定事务原子性通过。

## 修复计划

按以下阶段顺序实施。每阶段退出条件通过后再进入下一阶段；原子提交复验全部通过之前，整体验收保持不通过。

| 阶段 | 工作项 | 交付物 | 退出条件 |
|---|---|---|---|
| P0：确定存储原语 | 为当前 Native 本地文件系统验证可用的“同文件系统原子发布且目标已存在时失败”原语，并查清文件/目录同步能力及保证边界。若 MoonBit `fs` API 不提供该能力，在最小 storage adapter 中补 Native 实现；不要用 `exists` + 普通写入模拟。 | `AtomicFileStore`（或等价最小接口）及平台/文件系统限制说明 | 测试证明同卷发布对 reader 原子可见、目标不被覆盖；两个竞争发布仅一个成功；失败不会暴露目标文件的部分内容。明确这是进程崩溃保证还是也覆盖断电持久性。 |
| P1：提交状态机 | 将 `append_rows` 改为：拒绝空批次 → 校验 schema 并完整编码 → 用唯一名称写临时 Parquet → 校验可读性/长度 → 原子不覆盖地发布数据文件 → 完整写临时 JSON → 原子不覆盖地发布 `N+1.json`。正式 JSON 发布是唯一提交点；commit 冲突时不得覆盖，刷新快照后重试或返回明确冲突。`create_table` 的 version 0 也走同一发布原语。 | 写路径状态机实现、结构化 commit conflict 错误 | 提交点之前所有 reader 均看到旧 snapshot；提交点之后所有 reader 均看到完整新 snapshot；目标已有版本不会改变；空批次在任何文件写入前失败。 |
| P2：失败恢复与清理 | 给临时文件命名加入随机/唯一 token，确保重试不撞上残留文件。实现安全清理策略：可删除本项目临时前缀的残留文件；最终数据文件若无任何 commit 引用，只能在确认未被并发 writer 使用后回收。读路径忽略临时文件，只枚举规范版本 JSON；正式版本文件必须解析成功且至少包含有效 action，否则报告损坏并拒绝快照。 | 启动/显式维护清理逻辑，完整性检查与恢复文档 | 在每个中断点重启后，读者得到完整旧版或完整新版；没有空/部分 commit 被认作版本；同一批次可安全重试且不会复用损坏数据文件。 |
| P3：并发策略 | 用原子版本文件发布仲裁 `N+1`。先成功发布的 writer 提交；失败 writer 不覆盖 commit，重新读取最新 snapshot 后重建 add/commit，或返回可重试的冲突错误。若暂不实现自动重试，公开 API 与 README 明确写出 single-writer 限制，但原子发布和冲突拒绝仍必须满足。 | 并发语义说明与双 writer 测试 | 两 writer 同时 append 不会互相覆盖、不会产生一个版本对应两种内容；结果只能是串行成功，或一个成功、另一个收到可识别冲突并可重试。 |
| P4：故障注入与回归 | 增加可控 I/O 故障点，覆盖临时数据写到一半、数据发布失败、临时日志写到一半、正式日志发布失败、提交前/后 reader、两个 writer 竞争、进程重启与重试、空输入。保留正常 flat/nested 写后回读测试。 | 故障注入测试与新的验收证据 | 每个故障点断言 version、文件清单、行数、逐字段值、正式 JSON 完整性、可重试性和残留文件策略；`moon check --target native` 与 `moon test --target native` 全绿。 |
| P5：文档与正式复验 | 根据实际实现更新 README、申报书、路线图和本报告；如实现仍只保证单进程/单 writer 或不保证断电持久性，明确写出边界。重跑原子性验收并记录平台、文件系统、命令和测试结果。 | 修订文档、验收复验报告 | 不再把 `put-if-absent` 存在性检查称作原子提交；P0–P4 全部证据通过后才可将本项改判通过。 |

### 实施约束

- 正式 commit 文件必须不可变；发布正式版本是线性化点，禁止先创建正式路径再填充内容。
- 数据文件先于 commit 发布。发生崩溃时允许留下未引用的数据文件，但它不能改变任何 snapshot；清理必须避免误删并发 writer 正在使用的文件。
- 临时文件必须与最终目标位于同一文件系统，原子 rename/link 的具体选择由 P0 的实际 API 能力决定；若无法提供 no-replace 原子操作，就不能宣称并发提交安全。
- “进程中断后恢复一致”与“断电后持久性”是不同保证。只有文件和目录同步原语经验证后，才可声称覆盖断电；否则文档限定为进程崩溃及正常文件系统语义。
- 不接受仅增加 `path_exists` 检查、sleep/重试、清理所有未引用 Parquet、或只测正常往返作为修复。

本报告按源码静态路径和现有测试覆盖情况判定，没有通过人工强杀进程制造真实 I/O 中断；但当前实现缺少原子发布原语本身，已足以判定该项不通过。

## 修复执行记录（2026-11-09）

已开始执行上面的 P0–P5。当前实现和验证状态如下；此记录不等于整体验收通过。

| 阶段 | 状态 | 已交付 / 未完成 |
|---|---|---|
| P0：确定存储原语 | 部分完成 | Native POSIX 使用 `mkstemp`、`fsync`、`link`；Windows 使用 `GetTempFileNameW`、`FlushFileBuffers`、`CreateHardLinkW`。当前 Windows 环境完成编译和测试。未在 Linux/macOS、非本地/不支持硬链接的文件系统上验证；不承诺断电后的目录项持久性。 |
| P1：提交状态机 | 基础完成 | 数据文件、version 0 和 append JSON commit 都经原子不覆盖发布；正式 JSON 是可见性边界；空 append 在写文件前拒绝；重复目标映射为 `CommitConflict`。 |
| P2：失败恢复与清理 | 部分完成 | 中断产生的临时文件不匹配 `.json` 版本名、不会被回放；数据文件先发布，未被 commit 引用时不改变快照。尚未实现自动清理中断遗留的临时文件/未引用数据文件；调用方需据最新快照解析提交是否已成功。 |
| P3：并发策略 | 部分完成 | 同一版本的 commit 通过原子不覆盖发布仲裁，失败方得到 `CommitConflict`，可重读后重试。尚无自动重试或多进程压力测试；若数据文件名碰撞，也会安全报冲突。 |
| P4：故障注入与回归 | 部分完成 | 已测原子发布内容完整、目标不覆盖、成功后无临时文件、append 正常回读及空批次无提交。尚未加入真实中断注入、并发 reader/writer 和进程重启测试。 |
| P5：文档与正式复验 | 部分完成 | README、申报书和本报告已同步当前保证与限制。完整故障注入和跨平台复验通过前，原子性验收仍为不通过。 |

本轮 `moon check --target native` 与 `moon test --target native` 已通过；测试为 **38/38**。原子发布实现依赖本地文件系统硬链接创建的原子性；不支持硬链接或 Native backend 以外的写入会明确失败/不支持，不回退到普通写入。

## WSL 进程级复验（2026-11-10）

新增 Linux 进程级测试入口：`artifacts/acceptance/test_atomic_commit_wsl.py`。它构建 `src/examples/atomic_commit_worker`，用 `LD_PRELOAD` 测试闸门先向临时文件实际写入前缀（Parquet 为 4 KiB，JSON 为半份内容），再阻塞真实 worker，由父进程发送 `SIGKILL`，然后检查 Delta 版本、文件可见性、旧快照及重试结果。并发场景使用双进程 ready/start barrier，确保两个 writer 都已到达屏障再同时放行争抢同一个目标版本。测试只适用于 Linux Native、本地文件系统和可用 `LD_PRELOAD` 的环境。需要 Python 3、GCC 和 `deltalake==1.6.6` / `pyarrow==26.0.0`。

复验环境：WSL2 Ubuntu 24.04，Linux 6.6.114.1-microsoft-standard-WSL2，ext4（发行版根文件系统）；MoonBit 0.1.20261104；delta-rs 1.6.6 / pyarrow 26.0.0。原 Windows 工作区未用于写入测试产物，测试在发行版副本 `/root/moondelta-wsl` 中执行。

命令与结果：

- `moon build --target native`：通过，包含 CLI、三个既有示例和原子提交 worker。
- `moon check --target native --no-mbti`：通过，86 warnings / 0 errors。三个示例包缺少 `moonbitlang/core/env` 的错误已修复。
- `moon test --target native`：38/38 通过。
- `.venv/bin/python artifacts/acceptance/test_atomic_commit_wsl.py`：通过 3 组进程级场景：
  - 已写入 4 KiB 后 `SIGKILL`：Parquet 临时文件没有成为正式数据文件，版本仍为 0，之后 append 重试成功。
  - JSON 临时 commit 已写入一半后 `SIGKILL`：版本仍为 0；允许遗留一个已发布但未引用的 Parquet 文件；delta-rs 读到的旧快照仍为 0 行，之后 append 重试成功并只产生一个可读新版本。
  - 两个 writer 同时 append：只有一个 writer 发布 version 1，另一个收到 `CommitConflict`；delta-rs 读回恰好一行，且内容属于成功 writer。

本轮证据覆盖了进程强杀、旧快照可读、重试和同版本竞争。测试闸门在临时文件已写入前缀后暂停并强杀进程，没有声称覆盖任意字节偏移的短写故障；自动清除中断产生的临时文件及未引用 Parquet 仍未实现。POSIX 文件 `fsync` 已调用，但没有验证目录项断电持久性，因此不宣称断电恢复保证。完整验收仍需补齐残留清理/维护策略并在 Windows Native 复跑对应原子发布用例。

## 后续关闭方案（2026-11-10，待实施）

详细设计见 [残留清理与断电持久性解决方案](../docs/残留清理与断电持久性解决方案.md)。采用持久事务归属清单、writer 共享锁与恢复器独占锁，保护保留历史中的引用，自动清理仅面向明确启用受管维护策略的表。Linux 补齐数据与日志父目录同步，区分提交可见与确认持久化，增加 `CommitOutcomeUnknown` 恢复语义；Windows 强持久性单独验证。

按 S1–S6 执行：发布结果和同步 → 事务归属与锁 → 安全恢复清理 → 进程/同步故障回归 → 专属磁盘断电测试 → 文档改判。现有竞争测试的 start barrier 位于调用 append 前，不能保证双方已经选中同一快照；后续须增加 JSON 发布前屏障，连续 reader 和冲突后重试测试。没有运行证据前不改变完整验收未通过的结论。

## 同步失败与确定性竞争续验（2026-11-10）

本节取代上节对“现有竞争测试”的状态描述。新增实现与原始证据见 [transaction-2026-11-10](acceptance/transaction-2026-11-10/README.md)。

- Linux 已补文件发布后的父目录同步，以及建表路径各级目录项同步，修复绝对路径建表。数据文件和数据目录同步完成后才开始 JSON 提交。
- 已增加 `PublishOutcomeUnknown(path, code)` 与 `CommitOutcomeUnknown(path, code)`，区分已可见但必要同步失败的发布结果；尚无事务 ID/intent 清单和自动查询恢复，不得盲目重放。
- 18 个 I/O/建表故障场景通过，保存 syscall trace 并核实每个故障确实触发；包含 EIO/ENOSPC/EINTR、发布前/后错误及尾部清理失败。
- 竞争屏障已移动至正式 JSON 的 `link` 调用前；100 轮同时选中 version 1 的竞争、连续 reader、日志字节不可覆盖、冲突后重试为 version 2 和 version 1 历史读回均通过。
- 零字节、空白和截断的正式 JSON 日志均被拒绝，损坏最新版本不能继续 append，仍可读取旧版本；合法仅含 `commitInfo` 的日志仍可回放。修复位于共享 `read_actions` 入口。
- WSL 全仓 Native build/check/test 通过，测试 39/39；JS/Wasm 静态检查通过。Windows Native check/test 通过，39/39；该回归不代表 Windows 目录同步或断电持久性已验证。
- Linux CI 已配置执行新的 100 轮集成测试并保存结果；尚未推送触发远端 CI。delta-rs 的本轮 dataset 单线程读回自然退出为 0，没有使用强制退出绕过清理。

**完整验收仍未通过。** S1 的目录同步与发布阶段错误已落实，S4 的同步故障、确定性竞争和连续 reader 已补齐；S2 目前只完成 POSIX writer 锁，持久事务归属、结果查询和恢复器尚未实现；S3 安全自动清理与 S5 磁盘级断电验证仍未完成。本轮不宣称断电恢复已验收，也不宣称一般的幂等写入已实现。
