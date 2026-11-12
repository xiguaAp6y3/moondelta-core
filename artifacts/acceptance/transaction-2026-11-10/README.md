# 事务验收续测：Linux 目录同步、失败语义与确定性并发

日期：2026-11-10。结论：以下列出的代码修复与进程/I/O 注入用例通过；完整事务验收仍未通过。没有进行磁盘级断电试验，没有实现受管自动残留清理。

## 实际变更

- Linux 原子文件发布：文件 `fsync` → 不覆盖 `link` → 父目录 `fsync`，之后才清除临时名称；移除临时名后的目录同步作为尾部维护，不改变已确认发布的返回结果。
- 数据目录同步失败阻止 JSON 发布；若 JSON 已发布但必要目录同步失败，`write_text_new` 返回 `CommitOutcomeUnknown(path, errno)`。不删除已经可见的 commit，不将其当作可以安全重放的普通发布前错误。
- 文件内容、目录同步重试 `EINTR`。目录创建支持 POSIX 绝对路径；对已存在的路径分量也重试父目录同步，支持上次创建后同步失败的重启恢复。
- 事务 worker 新增按版本读回和空批次操作。竞争屏障移至 C 测试库拦截的正式 JSON `link` 调用前；原来 worker 内的 append 前屏障已移除。
- 没有添加事务 ID、intent 清单或结果查询 API。本轮的 OutcomeUnknown 只提供目标路径和错误码；需读取正式 commit 核对，不能视为通用幂等或自动恢复已完成。
- 共享日志读取入口拒绝零字节、空白或截断的正式 JSON；损坏的最新版本不能继续 append，旧版本仍可读取。合法的仅含 `commitInfo` 的提交不被误拒绝。
- POSIX Native writer 使用表根目录固定 `.moondelta.lock`，append 取得共享锁，create 取得独占锁；锁文件不删除。集成用 Python `flock` 持有独占锁时启动 append，确认 writer 阻塞且快照不变，释放后 writer 完成 version 1。SIGKILL 后重试通过，亦验证内核释放 writer 锁。
- 锁文件使用 `O_NOFOLLOW` 并校验普通文件类型；替换为符号链接时 append 被拒绝，表和链接目标均未改变。
- 本轮没有恢复器或删除动作；该锁只是安全恢复的协调基础，不代表自动清理已启用。Windows 当前使用 no-op 锁，尚无 Windows 维护锁保证。

## 环境

主测试在 WSL2 Ubuntu 24.04 发行版根文件系统 ext4 上运行，Linux 6.6.114.1，MoonBit 0.1.20261104，GCC 13.3.0，Python 3.12，deltalake 1.6.6，pyarrow 26.0.0。数据位于 `/tmp` 下独立临时目录；项目副本为 `/root/moondelta-wsl`。未在 `/mnt/e` 写受测 Delta 表。

Windows 回归使用 MoonBit 0.1.20261012，仅运行 Native 检查/单元测试，不将结果当作 Linux 目录同步或 Windows 断电保证。环境与故障详细轨迹见 `results.json`。

## 可复现命令

仓库根目录，Linux Native，本地文件系统；需 GCC、findmnt 和 Python 3：

```bash
moon build --target native
moon check --target native --no-mbti
moon test --target native
python3 -m venv .venv
.venv/bin/pip install deltalake==1.6.6 pyarrow==26.0.0
.venv/bin/python -u artifacts/acceptance/test_atomic_commit_wsl.py \
  --iterations 100 --output _build/transaction-acceptance/results.json
```

`--no-mbti` 只用于支持该选项的新版工具链；旧版改用 `moon check --target native`。CI 工作流中使用不带该选项的检查命令。

## 结果与证据

| 项目 | 本轮结果 | 原始证据 |
|---|---|---|
| Linux 全仓 Native build/check | 通过；check 86 warnings / 0 errors | `build.out`、`check-native.out` |
| Linux Native 单元测试 | 39/39 | `test-native.out` |
| Linux JS / Wasm 静态检查 | 通过；89 / 88 warnings，均 0 errors | `check-js.out`、`check-wasm.out` |
| Windows Native check/test | 通过；测试 39/39 | `windows-check.out`、`windows-test.out` |
| 部分 Parquet / JSON 写入后 SIGKILL 与新进程重试 | 通过，旧快照不变 | `integration.out`、`results.json` |
| I/O 与建表故障 | 18/18，逐项核实故障确实触发一次 | `results.json` 的 fault_cases 与 syscalls |
| 依赖顺序 | 数据文件同步 < 数据 link < 数据目录同步 < 日志文件同步 < 日志 link < 日志目录同步 | `results.json` 的 publication_order |
| POSIX 维护锁 | 独占锁阻塞 append，释放后提交；符号链接锁被拒绝且目标未改 | `integration.out`、`results.json` |
| 绝对路径建表、空批次零文件写入 | 通过 | `integration.out` |
| 发布前确定性竞争、连续 reader、不可覆盖、冲突重试与历史版本不变 | 100 轮通过 | `integration.out`、`results.json` |

18 个故障场景：数据写 EIO/ENOSPC、数据文件同步、数据 link、数据目录同步、日志写、日志文件同步、日志 link、日志目录同步 EIO/ENOSPC、尾部日志目录同步、数据/日志临时名 unlink、数据/日志目录同步 EINTR，以及建表目录/日志目录同步、version 0 发布后同步失败。

每轮竞争中，两个 writer 均在已构造 version 1 commit、尚未执行其 `link` 时暂停。先放行 writer A，确认成功并保存正式日志字节，再放行 writer B，确认冲突及日志字节未变。后台 reader 重复新建 DeltaTable，必须只观察完整 version 0 或 version 1；之后 B 用新快照重试为 version 2，并验证 version 1 仍只有 A 的行。本项目 worker 与 delta-rs 均参与读回，检查 id 与完整 payload。此顺序用于验证同目标竞争；不把受控调度称为所有调度交错已穷尽。

delta-rs 读回改用 Arrow dataset 的 `to_table(use_threads=False)`；本轮脚本正常退出 0，未通过 `os._exit` 绕过退出阶段。

`results.json` 的 `status` 仅指 `scope` 所列测试套件；`overall_transaction_acceptance` 明确为 `NOT_PASSED`。`source-sha256.txt` 记录本轮受测事务代码、单元测试、worker、故障库、集成脚本和包配置的 SHA-256；已逐项核对 WSL 副本与本地工作区内容一致，不以提交号代替未提交改动的身份。

## 剩余门槛

- 安全自动清理：仍需持久归属清单、共享/独占维护锁、历史引用保护及恢复接口；本轮只测试失败残留不改变快照、尾部清理失败不误报提交失败。
- 断电：调用顺序与错误注入不是掉电证明；还需具备缓存丢弃模型的专用 ext4 磁盘崩溃试验。
- 平台：Windows 目录项持久性、其他文件系统/macOS 尚未验证。
- CI：已配置 100 轮集成测试及 artifacts 保存，但尚未推送或触发远端运行。
- 版本发布：改动仍为本地工作区；0.3.1 registry 包不能视为包含本轮修复。
