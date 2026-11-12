# S5 掉电矩阵：内容一致性门槛生效后的 100 轮重跑

运行：[43176818126](https://github.com/xiguaAp6y3/moondelta-core/actions/runs/43176818126)（提交 `5fad340992a3d8ef8b707d8fb5387aec7128325d`，`workflow_dispatch`，`rounds=100`，`seed=1748647336`）。

**结论：100 轮有效切断全部 PASS，`failed=0`、`invalid=0`，并且每一轮都通过了新增的内容一致性对照。** 声明范围仍严格限定于本记录的环境：GitHub 托管 `ubuntu-24.04`（`ImageVersion 20261101.360.1`）、guest kernel 7.0.0-1017-azure、256 MiB 可丢弃 raw ext4、KVM 加速的 QEMU + 易失 NBD 设备模型，以及该提交的 writer 实现。不覆盖真实硬件掉电、Windows/NTFS、WSL `/mnt/*`、NFS/SMB/FUSE 或其他文件系统。

## 与上一轮（`ci-2026-11-12/`）的差别

上一轮（run 43157333594）的断言只核对版本、日志结构、引用文件长度、双引擎“可读”。2026-11-12 复验用同长度 payload 篡改与伪造 delta-rs 行数证明这不够（见 `../transaction-2026-11-12-review/README.md`）。本轮每轮额外强制：

1. 恢复日志的 Schema 与切断前 host 记录的基线逐字段一致；
2. 项目 reader（`delta read` 全表）与 delta-rs 返回**相同**的行数与逐行内容摘要，且都等于预期；
3. 预期内容由 host 端在切断前确定：基线行摘要 + 目标事务必须新增的那一行（`id = payload + 1000 + round`，`payload = 'x' * payload`）；
4. 既有数据文件的 SHA-256 不变；
5. `judge_round` 还要求 project / deltaRs / expected / inspect 四处行数与摘要互相一致，缺内容对照直接 FAILED。

因此两轮结果不可混用：上一轮不能用来说明内容正确，本轮才是。

## 结果摘要（取自 `results-100.json`）

| 项 | 值 |
|---|---|
| `status` | `PASS` |
| 轮次 | 100（`minimumRounds=100`） |
| PASS / FAILED / INVALID | 100 / 0 / 0 |
| 提交存活 / 丢失 | 6 / 94 |
| 必需边界缺失 | 无（17 个边界全部至少被覆盖一次） |
| 内容分支 | `survived` 6 轮、`lost` 94 轮，两种分支都实际出现过 |
| `results-100.json` SHA-256 | `97acd8f0e95e042e5baa6762d6078e8c5bf3ea998472f3ee5ee4790456467538` |

耐久性边界断言：在 `commit-publish-dir-fsynced`（`_delta_log` 目录项已同步）之后切断的 6 轮，提交 6/6 存活；其余 16 个边界上的 94 轮全部按“提交丢失”收敛，且每一轮恢复后的内容都精确等于“基线”或“基线 + 目标行”，没有出现第三种状态。

## 复现

```bash
gh workflow run s5-powerloss.yml -f rounds=100 -f seed=1748647336
```

产物包含 `results-100.json`、逐轮 console/events/nbd trace、`boundary-trace.log`、`ci-context.txt` 与两个受测二进制。本地复算：`python artifacts/acceptance/powerloss/selfcheck.py` 只做静态自洽检查，不能替代本记录。

## 口径

- 这是**模型化**切断：易失写缓存由 `volatile_nbd.py` 提供，正向控制先证明“未 flush 写丢失、已完成 FLUSH 存活”，否则整组判 INVALID；
- 结果只证明该配置下 writer 对 flush 屏障的使用与该文件系统/kernel/设备模型组合的行为；
- Windows 仍只声明进程崩溃一致性；`artifacts/acceptance/lock_threads.c` 的 Windows 记录是进程内线程锁竞争，不是断电证据。
