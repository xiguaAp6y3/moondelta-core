# 事务完整性复验（2026-11-11）

## 结论

本轮 S1–S4 可执行的软件与进程级验收通过当前覆盖的本机矩阵；完整事务验收仍为 **NOT_PASSED**。S5 掉电实验前置门禁为 **BLOCKED**，远端 CI 尚无本轮提交对应的运行记录。不得据此声明断电持久性或完整 Delta 事务验收通过。

## 本轮代码范围

- `852d579`：事务查询在正式日志版本连续但内容为空或无法解析时返回 `unresolved`；增加空白/损坏日志回归用例。
- `cc8ac53`：恢复清理逐组件检查符号链接；受管策略更新与恢复锁串行；Windows 固定锁不共享删除权限；加入父目录链接集成用例。
- `edb74a0`：增加 Windows 策略撤销并发验收。

## 执行结果

| 检查 | 结果 | 环境/说明 |
|---|---|---|
| `moon check --target native` | 通过，0 errors | WSL2 Ubuntu 24.04；120 warnings |
| `moon test --target native` | 56/56 通过 | WSL2 Ubuntu 24.04 |
| Native worker build | 通过，0 errors | WSL2 Ubuntu 24.04 |
| `moon check --target js` | 通过，0 errors | WSL2 Ubuntu 24.04；128 warnings |
| `moon check --target wasm` | 通过，0 errors | WSL2 Ubuntu 24.04；127 warnings |
| Linux 事务集成 | 通过 | WSL2 Linux 6.6.114.1、发行版 ext4、Python 3.12.3、deltalake 1.6.6、pyarrow 26.0.0 |
| 故障注入 | 20 syscall/intent 场景 + 建表失败场景通过 | 含 EIO/ENOSPC/EINTR、unknown outcome 与清理失败重试 |
| 恢复与历史保护 | 通过 | 含 commit-unknown 查询、remove time travel、checkpoint-only、单文件 checkpoint、未知文件与软链接保护 |
| 父目录符号链接 | 通过 | 表内链接指向表外文件，apply 保留目标和 intent |
| 发布竞争 | 100 轮通过 | 连续 reader、不可变 commit、冲突重试 |
| Native worker build | 通过，0 errors | Windows 11；覆盖构建产物后重新构建 Windows worker |
| `moon test --target native` | 56/56 通过 | Windows 11 原生测试结果此前已复验；本轮 Windows 独立矩阵使用新构建 worker |
| Windows 事务独立矩阵 | 通过 | 含 LockFileEx、固定锁不可改名、受管策略撤销等待与 apply 禁止 |
| Python 脚本语法检查 | 通过 | 两个事务验收脚本 `py_compile` |
| S5 环境门禁 | BLOCKED | WSL/Windows 无 QEMU 与可丢弃 raw ext4 guest disk；无掉电模型 |
| 远端 CI | 未运行 | 最近可见的历史成功运行不包含本轮提交 |

Linux 原始集成结果由 `artifacts/acceptance/test_atomic_commit_wsl.py` 生成，运行参数为 `--iterations 100`；结果 JSON 在当前工作区 `_build/transaction-acceptance/results-current.json`（构建目录，不纳入版本控制）。Windows 结果 JSON 在 `_build/windows-acceptance/results-current.json`（构建目录，不纳入版本控制）。

## 阶段判定

| 阶段 | 判定 | 依据/未完成项 |
|---|---|---|
| S1 | 软件故障与发布顺序部分通过 | syscall 注入、发布顺序和 Linux/Windows 进程测试通过；真实掉电未验证 |
| S2 | 本机矩阵通过，远端复核未完成 | 日志解析 fail-closed、事务查询、Linux 锁和 Windows LockFileEx 矩阵通过；真实多线程锁拍发仍未测试 |
| S3 | 本机安全恢复矩阵通过 | 路径逐组件 symlink 防护、策略锁序列化、历史保护与幂等恢复通过；外部 writer 仍只能按受管策略约束 |
| S4 | 本机矩阵通过，CI 门槛未通过 | Linux/Windows 本机结果齐全；路线图要求的远端 CI 成功运行尚无证据 |
| S5 | 未通过 / BLOCKED | 没有有效 VM/块设备掉电实验 |
| 完整事务验收 | **未通过** | S1–S5 并非全部通过，远端 CI 与 S5 仍未关闭 |

## 复现命令

Linux WSL（先确保 WSL 使用与此工作区相同的源码）：

```bash
export PATH="$HOME/.moon/bin:$PATH"
moon check --target native
moon test --target native
moon check --target js
moon check --target wasm
moon build --target native src/examples/atomic_commit_worker
python3 -m venv .venv-acceptance
.venv-acceptance/bin/pip install deltalake==1.6.6 pyarrow==26.0.0
mkdir -p _build/transaction-acceptance
.venv-acceptance/bin/python artifacts/acceptance/test_atomic_commit_wsl.py \
  --iterations 100 --output _build/transaction-acceptance/results-current.json
```

Windows：

```powershell
moon test --target native
moon build --target native src/examples/atomic_commit_worker
python artifacts/acceptance/test_transactions_windows.py `
  --output _build/windows-acceptance/results-current.json
```
