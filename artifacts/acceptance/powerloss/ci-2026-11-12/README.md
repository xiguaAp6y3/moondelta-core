# S5 CI 掉电矩阵记录（100 轮，PASS）

日期：2026-11-12。结论：**100 轮有效掉电模型运行全部满足不变量，S5 在该精确配置下通过。**

- 运行：<https://github.com/xiguaAp6y3/moondelta-core/actions/runs/43157333594>（`workflow_dispatch`，`rounds=100`）
- 提交：`8eff403a78a235a3d18a1867328ae7276fb95c61`（`main`）
- 随机种子：`1748647336`（切断计划由它决定，可复现）
- 证据：`results-100.json`（SHA-256 `E9666F574D8FEC2B3021F280F6C4F6460EE8144D71408671D8260F26091D9BDB`）
- 工件：`s5-powerloss-run-43157333594-1`（含逐轮 console / events / NBD trace 与当次构建的 `delta`、`atomic_commit_worker`）

## 精确配置（结论只覆盖这一组）

| 项 | 值 |
|---|---|
| host | GitHub 托管 runner，`ImageOS=ubuntu24`，`ImageVersion=20261101.360.1`，Linux x86_64 |
| 加速 | `kvm`（嵌套虚拟化，`/dev/kvm` 可用） |
| QEMU | 8.2.2 (Debian 1:8.2.2+ds-0ubuntu1.18) |
| guest kernel | 7.0.0-1017-azure（ext4 / virtio_blk / virtio_pci / jbd2 均为 builtin） |
| 数据盘 | 256 MiB raw ext4，guest 挂载 `data=ordered,barrier=1` |
| 设备模型 | `volatile_nbd.py`：未 flush 写留在服务进程内存，NBD FLUSH/FUA 才 `pwrite`+`fdatasync` 到 raw backing |
| 写入器 | 提交 `8eff403` 的 `atomic_commit_worker` |
| 读取器 | 本项目 `delta`（`inspect`/`read`/`recover`）+ delta-rs `deltalake 1.6.6` / `pyarrow 26.0.0` |

## 结果

- 100/100 轮 `PASS`，`failed=0`、`invalid=0`；前置正向控制通过；前置门禁返回 0。
- 17 个边界全部被覆盖（`boundaries.missing = []`），必需 9 个全部命中。
- 每轮都在通告的边界被切断（`qemuExit=-9`、`nbdExit=-9`，切断延迟 29–59 ms），随后 `fsck.ext4 -fy` 恢复 journal（102 份 `fsck rc=1`，说明每次镜像确实是脏的），只读挂载后由两套读取器分别校验。
- **强不变量被断言而非仅观察**：在 `commit-publish-dir-fsynced`（`_delta_log` 目录项已 fsync 之后）切断的 6 轮，提交 6/6 存活；在 `commit-publish-linked`（名字可见、目录项未同步）切断的 6 轮，提交 0/6 存活。
- 其他边界允许两种合法结果，实际观测 `commitSurvived=6`、`commitLost=94`。
- 每轮都验证：日志从 0 连续、每个 commit 可解析、引用文件存在且长度一致、行数 == 存活 append 数、两套读取器的版本一致。
- 残留检查：遗留 intent 一律是「未发布的临时文件」或「输给其它事务」，均按合法残留处理，不计为损坏。

## 口径边界（不得外推）

- 这是**模型化掉电**（用户态易失写缓存 + 定点切断），不是真实硬件掉电，也不覆盖物理设备缓存行为。
- 结论只覆盖上表这组 host 镜像 / 内核 / ext4 选项 / QEMU / NBD 配置；换镜像、换文件系统、换设备模型都需要重跑。
- **Windows / NTFS 不因本结果获得任何保证**：Windows 仍只声明进程崩溃一致性，强持久性标记为「不支持 / 未验证」。
- macOS、WSL `/mnt/*`、NFS/SMB/FUSE、对象存储不在范围内。

## 复现

```bash
gh workflow run s5-powerloss.yml --ref main -f rounds=100 -f seed=1748647336
```

已做过的可复现性检查：同一 seed 在提交 `9c4988f`（仅文档与 `ci.yml` 变化）上重跑，结果**逐项一致**——

- run 43157333594（`8eff403`）与 run 43160174168（`9c4988f`）都是 `PASS`，`100/100` 轮、`failed=0`、`invalid=0`；
- 切断计划（100 个边界名序列）完全相同，`commitSurvived=6`、`commitLost=94` 完全相同；
- 逐边界结果完全相同：`commit-publish-dir-fsynced` 6 轮全部存活，其余边界全部丢失。

runner 镜像滚动（`ImageVersion` 变化）后属于新配置，需要重新取证。
