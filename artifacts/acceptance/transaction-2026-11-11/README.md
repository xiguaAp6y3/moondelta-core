# 事务验收：S2/S3 实现与本机矩阵（2026-11-11）

日期：2026-11-11。结论：**S2（事务身份 / intent / 互斥 / 查询）与 S3（dry-run/apply 恢复与受管清理）的代码及本机 Linux + Windows 验收矩阵通过**。整体事务验收仍为 `NOT_PASSED`：S5 掉电试验未执行（当前环境没有 QEMU harness），远端 CI 未实际运行。

## 实际变更

- `src/txn.mbt`：事务身份与 intent。OS 随机 32 位十六进制 txn ID（POSIX `getrandom`/`/dev/urandom`，Windows `ProcessPrng` 或 `SystemFunction036` 动态导出）；不可变 intent 在任何被管理文件创建前原子持久发布（文件 `fsync` → 排他 `link` → intent 目录 `fsync`）；intent 记录格式版本、txn ID、目标版本、临时/正式相对路径、长度、数据 SHA-256 与 JSON commit SHA-256；严格校验路径不越界/无别名；只读 `query_transaction` 比较摘要并在匹配后重新同步文件/目录，区分 committed / committed-unconfirmed / conflict / not-committed / unresolved。
- `src/sha256.mbt`：纯 MoonBit FIPS 180-4 SHA-256（JSON/Wasm 目标也可编译），单测使用标准向量。
- `src/recover.mbt`：`recover_table(root, dry_run=true)`、`managed_table`/`set_managed_table`、受管 writer 操作前的一次非阻塞恢复。保护集合来自全部可保留 JSON 历史与受支持 checkpoint；正式 Parquet 仅在日志自版本 0 连续可解析、路径/长度/摘要吻合且不受保护时删除；只删 intent 精确列出的临时名；旧命名/未知文件/软链接一律保留并报告；删除顺序为数据/临时文件后 intent，`ENOENT` 幂等。
- `src/atomic_file.mbt`、`src/atomic_file_native.c`：预固定临时名的排他发布入口、文件/目录同步、unlink+目录同步、`stat`、symlink 检查、OS 随机源；POSIX `flock` 固定 `.moondelta.lock`（共享/独占/非阻塞独占）；Windows 固定锁文件句柄 + `LockFileEx`（不可继承、进程退出释放）。
- `add.path` 使用 RFC 3986 最小 percent-encoding（unreserved 字符保持字面）；修复此前把 `-`/`.` 编码成 `%2D`/`%2E` 导致外部引擎 `remove` 无法与写出的 `add` 匹配的问题。
- `src/write.mbt`：create/append 全流程改为 intent → 数据 → JSON commit → 清理；txn ID 进入数据文件名与 `commitInfo.moondeltaTxnId`；`PublishOutcomeUnknown(txn, version, path, phase, code)` / `CommitOutcomeUnknown(...)` 携带事务上下文；`append_rows_with_txn` / `create_table_with_txn` 返回 txn ID。
- Windows 的结果查询保持 `committed-unconfirmed`：没有目录项同步原语时不把文件同步升级为确认持久。
- CLI 与 worker：`delta recover|query|manage`，worker 新增 `recover|query|manage` 与 txn ID 输出。
- 测试：单元测试 39 → 56；Linux 集成脚本新增 5 类 intent 故障场景（共 23 项含 3 个建表）、SIGKILL 残留恢复、查询状态、恢复策略与幂等、真实 remove/checkpoint/checkpoint-only/软链接保护、日志缺口、锁忙、未知文件与越界/别名 intent 保留、跨进程 txn ID 唯一性；新增 Windows 独立验收脚本与 S5 前置门禁。

## 环境

| 平台 | 细节 |
|---|---|
| Linux（主矩阵） | WSL2 Ubuntu 24.04；Linux `6.6.114.1-microsoft-standard-WSL2`；发行版根目录 ext4（`/dev/sdd`）；MoonBit `0.1.20261104`；GCC 13.3.0；Python 3.12.3；deltalake 1.6.6；pyarrow 26.0.0 |
| Windows | Windows 11 `10.0.28000`；MoonBit `0.1.20261012`；Python 3.15.0；无第三方 Python 依赖（`ctypes` 直接调用 `LockFileEx`） |

数据表位于 Linux 根文件系统临时目录（`/tmp/moondelta-atomic-*`）与 Windows `%TEMP%\moondelta-win-*`，未在 `/mnt/e` 上做持久性测试。

## 可复现命令

Linux（仓库根目录，Linux Native，本地文件系统；需 GCC、findmnt、Python 3）：

```bash
moon build --target native
moon check --target native --no-mbti
moon test --target native
python3 -m venv .venv && .venv/bin/pip install deltalake==1.6.6 pyarrow==26.0.0
.venv/bin/python -u artifacts/acceptance/test_atomic_commit_wsl.py \
  --iterations 100 --output _build/transaction-acceptance/results-100.json
```

Windows（仓库根目录）：

```powershell
moon build --target native
moon check --target native
moon test --target native
python artifacts/acceptance/test_transactions_windows.py --output artifacts/acceptance/transaction-2026-11-11/windows-results.json
```

## 结果与原始证据

| 项目 | 结果 | 证据 |
|---|---|---|
| Linux 全仓 build | 通过 | `build.out` |
| Linux Native check | 0 errors（117 warnings） | `check-native.out`、`check-native-mbti.out` |
| Linux JS / Wasm check | 0 errors（125 / 124 warnings） | `check-js.out`、`check-wasm.out` |
| Linux Native 单测 | 56/56 | `test-native.out` |
| Linux 集成：23 项故障注入（20 个 syscall/intent + 3 个建表目录/version 0 同步） | 23/23，逐项断言故障确实触发一次 | `results-100.json` 的 `fault_cases` |
| Linux 集成：发布顺序 | intent 文件 sync < intent link < intent 目录 sync < 数据文件 sync < 数据 link < 数据目录 sync < 日志文件 sync < 日志 link < 日志目录 sync | `results-100.json` 的 `publication_order` |
| Linux 集成：100 轮发布前竞争 / 连续 reader / 冲突重试 | 通过 | `run-100.out`、`results-100.json` |
| Linux 集成：SIGKILL 残留恢复 | intent + 临时 Parquet 被清理，旧快照不变，随后 append 成功 | `results-100.json` 的 `recovery.sigkill_recovery` |
| Linux 集成：dry-run / 受管 apply / 幂等 | 通过 | `recovery.recovery_lifecycle` |
| Linux 集成：查询状态 | committed / not-committed / conflict / unresolved | `recovery.query_states` |
| Linux 集成：恢复锁忙 | 活跃 writer 时 `busy=true`，释放后恢复 | `recovery.recover_lock_busy` |
| Linux 集成：历史保护与日志缺口 | 受历史引用文件不删；缺口时零删除 | `recovery.history_protection` |
| Linux 集成：未知/旧命名文件 | 保留并报告，无通配符删除 | `recovery.unowned_preservation` |
| Linux 集成：writer 暂停时的互斥 | writer 暂停在数据发布后、JSON link 前，恢复器只得到 busy，活跃 intent/数据未被删除 | `recovery.recoverer_sees_paused_writer` |
| Linux 集成：清理 unlink 失败重试 | 失败后保留 intent 与证据，重复恢复收敛 | `recovery.recovery_unlink_retry` |
| Linux 集成：真实 commit-unknown 查询 | 目录同步故障后的未知结果表明为 committed，恢复保留 commit | `recovery.unknown_outcome_query` |
| Linux 集成：真实 remove 动作保护 | 旧版本仍可读、delta-rs time travel 不变，历史数据不被删除 | `recovery.remove_history_protection` |
| Linux 集成：checkpoint 引用保护 | table_a 单文件 checkpoint 引用的数据文件不被删除 | `recovery.checkpoint_protection` |
| Linux 集成：checkpoint-only | 日志不完整，零删除并保留 intent | `recovery.checkpoint_only_blocked` |
| Linux 集成：软链接 | 不跟随、不删除，目标不变并报告 | `recovery.symlink_preserved` |
| Linux 集成：跨进程 txn ID 唯一性 | 8 个并发进程、相同 payload/timestamp，全部唯一 | `recovery.txn_id_uniqueness` |
| Windows Native build/check/test | 0 errors；56/56 | `windows-build.out`、`windows-check-native.out`、`windows-test-native.out` |
| Windows 事务矩阵 | intent/提交/清理、查询（匹配后为 `committed-unconfirmed`，不冒充目录持久性）、恢复策略、未知文件、跨进程 `LockFileEx`、并发 txn ID 全通过 | `windows-results.json`、`windows-integration.out` |

20 个 syscall/intent 故障场景（另加 3 个建表场景）：intent 写 EIO/ENOSPC、intent 文件同步、intent link、intent 目录同步、数据写 EIO/ENOSPC、数据文件同步、数据 link、数据目录同步、日志写、日志文件同步、日志 link、日志目录同步 EIO/ENOSPC、尾部日志目录同步、数据/日志临时名 unlink、数据同步 EINTR、日志目录同步 EINTR，以及建表目录/日志目录同步与 version 0 发布后同步失败。

| S5 掉电前置门禁 | `BLOCKED`（无 QEMU harness / 无可信缓存丢弃模型）；脚本永不输出 PASS | `powerloss-gate.out`、`artifacts/acceptance/powerloss/README.md` |

## 源码身份

`source-sha256.txt` 记录本轮受测的 MoonBit/C 源码、包配置、worker、故障注入库、两个验收脚本与 S5 前置门禁的 SHA-256。WSL 测试副本由同一源码包展开，报告中的结果与清单对应；文档仅在本轮结果生成后更新，不改变受测代码。

## 仍未关闭的门槛

- **S5 掉电**：没有专用 Linux VM + 可丢弃 raw ext4 盘 + 块设备断电模型；`results-100.json` 明确记录 `power_loss: NOT_TESTED`，不宣称断电持久性。
- **远端 CI**：工作流已配置，但本轮没有推送并观察远端成功结果；本地矩阵不能替代。
- **Windows 持久性口径**：只证明进程崩溃一致性与 `LockFileEx` 互斥；不把 `FlushFileBuffers` 进程测试升级为 NTFS 掉电/名称持久性保证。
- **仍未覆盖的取证**：真实多线程共享进程的锁拍发（现以同进程多句柄调用等价覆盖）；S5 的真实掉电矩阵；远端 CI。真实 `remove`/checkpoint/checkpoint-only/percent 路径/软链接/多部分 checkpoint 与不支持协议的保守拒绝已由单测或集成覆盖（见上表）。
- `results-100.json` 的 `overall_transaction_acceptance` 仍为 `NOT_PASSED`。
