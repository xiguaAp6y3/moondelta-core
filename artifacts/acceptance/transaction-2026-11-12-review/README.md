# 当前事务状态复验（2026-11-12）

受验提交：`fa4c2eb48be691816dcc51edeb1ec11e5d3d4dbc`。

**结论：完整事务验收仍为 `NOT_PASSED`，原因是验收断言和必需场景未闭合，不再是没有可运行的掉电实验。** 两次 S5 CI 试验确实成功；它们证明了现有断言覆盖的版本、结构、引用长度及指定同步边界存活性质，尚不足以满足路线图中 Schema、行值、数据摘要全部正确的完整门槛。本轮发现的是可复现的验收器漏检，不是已经观测到生产 writer 在掉电后损坏数据。

本轮范围为现行 S1–S5 事务路线图。未重新执行全部协议/扁平与嵌套互操作矩阵，也不据此宣布 reader 1 / writer 2 全部必需行为兼容。既有历史报告和 CI 原始结果保持原样。

## 证据核验与重跑

| 检查 | 结果与范围 |
|---|---|
| 当前提交远端 CI | [43160975199](https://github.com/xiguaAp6y3/moondelta-core/actions/runs/43160975199)：`check-test-build`、`windows-check-test` 均 success；已下载两平台 results.json 核验 |
| S5 首轮 | [43157333594](https://github.com/xiguaAp6y3/moondelta-core/actions/runs/43157333594)：提交 `8eff403`，100 PASS / 0 FAILED / 0 INVALID；17 边界全部覆盖 |
| S5 同 seed 重跑 | [43160174168](https://github.com/xiguaAp6y3/moondelta-core/actions/runs/43160174168)：提交 `9c4988f`，Actions success；第二次完整 JSON 本轮未下载重算，逐边界相同分布沿用既有记录 |
| S5 原始 JSON 完整性 | 仓库 `powerloss/ci-2026-11-12/results-100.json` 的 SHA-256 为 `e9666f574d8fec2b3021f280f6c4f6460ee8144d71408671d8260f26091d9bdb`，与记录一致；seed `1748647336`，存活 6 / 丢失 94 |
| 受验代码与掉电受测代码 | `git diff 8eff403 HEAD -- src artifacts/acceptance/powerloss/*.py .github/workflows/s5-powerloss.yml` 无变化；后续文档与普通 CI 安装器变更不改变上述受测实现 |
| WSL Ubuntu-24.04 静态检查 | Native、JS、Wasm 均退出 0；存在弃用/未使用等 warnings，不是零警告构建 |
| WSL Native 单测 | 56/56，0 failed |
| WSL 集成矩阵 | 100 轮竞争通过，23 个故障场景、恢复与历史保护矩阵通过；见本目录 `results-100.json`（测试数据位于 ext4 临时目录，不把源码所在 `/mnt/e` 当作 ext4 数据盘） |
| 掉电装置自检 | `selfcheck.py` 通过；`volatile_nbd.py --self-test` 验证未 flush 写丢失、已完成 FLUSH 的写存活 |
| 验收器原有自测 | 实表正向、4 种损坏拒绝、2 种合法残留接受，全部通过 |
| 本轮新增反例 | `reproduce_verifier_gaps.py` 成功复现两项漏检，原始输出见 `verifier-gap-results.json` |

本轮未重新启动 100 轮 VM 掉电矩阵；该部分使用经核验的远端运行证据。Windows 支持结论来自当前提交的远端 Windows 原生矩阵，本轮没有用 WSL 结果代替 Windows 结果。

## 尚未满足的要求

### P1：S5 内容一致性与双引擎判定存在漏检

依据：路线图 S5 执行矩阵第 2 项要求“长度/摘要正确、快照版本/Schema/行值正确”。

`powerloss/verify_round.py` 的 `parse_log` 检查引用文件存在与长度；intent 检查的是 commit 摘要，没有核对 Parquet 的预期摘要。`run_delta_rs` 返回版本和行数；`verify_table` 比较版本，但不比较两引擎行值、Schema。`run_powerloss.py::judge_round` 只要求 delta-rs 的状态为 PASSED，没有比较其行数；项目 reader 的行数缺失时也没有强制拒绝。

本轮实证：

1. 用项目 worker 创建合法的一行表，在 4301 字节 Parquet 的偏移 64 处将 payload 首字节从 `x` 改为 `y`。长度、版本、行数不变；PyArrow 确认行值已经改变；项目 reader 和 delta-rs 都能读取，`verify_table` 仍给出 `ok=true`、`problems=[]`。
2. 从已保存的真实掉电轮次复制记录，仅将 `verification.report.deltaRs.rows` 改为 `999999`，`judge_round` 仍给出 `PASS`、无失败原因。

需要补齐：在切断前由独立控制端保存基线和目标事务的预期 Schema、完整行内容或规范化内容摘要、数据文件摘要。恢复后按“目标事务存活/丢失”选取完整预期结果，强制两引擎逐字段/逐行对照；任何读取失败、输出无法解析、必需字段缺失或差异均拒绝通过。加入同长度内容篡改、Schema 改变、外部引擎行数不一致等负向回归；随后重跑受影响的 100 轮 S5 矩阵。

不能通过仅增加更多相同轮次来弥补缺少的内容断言，也不能把“可被两引擎读出”写成“与预期内容一致”。

### P2：S3 恢复器自身中断矩阵未完成

依据：路线图 S3 明确要求“在恢复每个 unlink/fsync 边界再次 SIGKILL，重启重复 recover”。

当前集成覆盖 writer 中断后的恢复、unlink 失败后重试，但没有逐一覆盖 recover apply 的数据删除、父目录同步、intent 删除和维护目录同步时的再次中断。S5 guest 的目标操作是 append，随后在宿主只读挂载上执行 recover dry-run；其 cleanup 边界覆盖不能替代 recover apply 的删除路径验证。

需要补齐：创建可删临时/正式受管文件及历史保护文件，在恢复器各删除/同步边界定点中断，重启重复 apply；证明只删除归属明确的垃圾、证据保留或可安全收敛、历史快照与外部读取结果不变，并覆盖中断后的再次 writer 操作。

### P2：S2 同进程真实多线程竞争缺证据

依据：路线图 S2 要求同进程独立句柄、多线程、多进程分别验证维护锁语义。路线图当前也承认真实多线程尚未覆盖。

现有同进程多句柄顺序调用、多进程 writer/recoverer，以及 Python reader 线程，分别证明了部分性质；Python reader 线程并不在项目进程内并发调用 writer/recoverer 锁接口。

需要补齐：真实同进程线程在屏障控制下并发获取共享/独占句柄、执行 writer/recovery，覆盖 busy、释放及异常退出路径；POSIX/Windows 按各自支持范围留证。若 API 明确不支持同进程并发调用，需正式修订 API 契约和路线图，不能将顺序多句柄视为已经完成线程竞争测试。

### P2：当前文档与最新事实存在冲突

路线图状态表 S2/S4 仍称远端 CI 未运行，S5 环境交付仍称“实跑与取证仍缺”；`docs/测试说明.md` 仍称当前工作区未推送运行、Windows 安装器失败。当前提交的两平台 CI 已成功，这些现时声明需要更新。历史日期段落可以保留，但应明确是历史记录。

另一方面，申报书的“S5 已通过”及“两套读取器逐轮读回一致”需要区分“现有实验断言通过”和“完整内容一致性门槛通过”，并显式列出本轮发现的剩余门槛。仅写剩余工作是 CI 与平台口径维护并不完整。

## 逐阶段结论

| 阶段 | 本轮验收结论 |
|---|---|
| S1 | 已有持久化顺序、错误阶段、可见/确认语义基线通过，本轮回归未发现退化 |
| S2 | 已实现部分及已有并发用例通过；按路线图完整验收尚缺真实同进程多线程证据 |
| S3 | 既有安全清理与幂等用例通过；按路线图完整验收尚缺 recover apply 各删除/同步边界再次中断证据 |
| S4 | 当前 Linux/Windows CI 和本轮 WSL 回归通过；作为 S2/S3 全部必需用例的汇总门槛尚未闭合 |
| S5 | 有效模型试验已完成、现有断言 100/100 通过；完整内容一致性验收因可复现漏检暂不通过，修正后须重跑 |
| 完整事务验收 | **NOT_PASSED**；不是环境 BLOCKED，也不是已发现 writer 数据损坏 |

S5 的已证明范围仍严格限定为记录中的 CI 镜像、QEMU/KVM、guest kernel、ext4 挂载和易失 NBD 模型及受测实现；不扩展至真实硬件断电、Windows/NTFS 强持久性、其他文件系统或设备缓存模型。Windows 当前仅维持进程崩溃一致性支持声明。

复现漏检：在 WSL/Linux 中构建 worker 与 delta CLI，使用安装了 deltalake/pyarrow 的 Python 执行：

```bash
.venv-acceptance/bin/python artifacts/acceptance/transaction-2026-11-12-review/reproduce_verifier_gaps.py
```

该诊断脚本退出 0 表示漏检反例成功复现，绝不代表事务或 S5 通过。修正验收器后，其“漏检仍存在”断言应失败；届时应将反例转入正式的负向回归，并保留本次原始结果。
