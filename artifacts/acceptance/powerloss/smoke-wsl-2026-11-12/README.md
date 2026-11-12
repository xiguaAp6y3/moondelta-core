# S5 冒烟记录：WSL2 端到端跑通（非证据）

日期：2026-11-12。结论：**`smokeTest: true`，整体 `INVALID`**。本目录只证明“装置能跑”，**不构成任何断电持久性证据**，也不改变 S5 的 `BLOCKED / NOT_PASSED` 状态。

## 为什么它不是证据

宿主是 WSL2，`check_environment.py` 与 `run_powerloss.py` 的前置门禁都会拒绝它：宿主页缓存留在 Windows 侧，无法证明“设备缓存真的丢了”。装置因此在 `--smoke-test` 下运行，该开关把任何结果强制降级为 `INVALID`。

## 环境

| 项 | 值 |
|---|---|
| host | WSL2，kernel 6.6.114.1-microsoft-standard-WSL2 |
| 加速 | `kvm`（WSL2 内 `/dev/kvm` 可用，嵌套虚拟化生效） |
| QEMU | 8.2.2 (Debian 1:8.2.2+ds-0ubuntu1.18) |
| guest kernel | 6.8.0-149-generic（ext4 / virtio_blk / virtio_pci / jbd2 均为 builtin，0 个模块） |
| 数据盘 | 256 MiB raw ext4，`data=ordered,barrier=1` |
| 读取器 | 本仓库 `delta`（Linux native 构建）+ `verify_round.py`；delta-rs 未安装（`SKIPPED`） |

## 结果

- `boundary-trace.json`：一轮内通告 23 个边界，9 个必需边界全部命中，`missing: []`、`orderOk: true`、`unknown: []`；
- `results-20.json`：20 轮真实切断，`pass: 20 / failed: 0 / invalid: 0`，`commitSurvived: 1`、`commitLost: 19`，17 个边界全覆盖，行数与版本不变量全部成立；
- 每轮动作：guest 启动 → 挂载 ext4（journal replay）→ append 到指定边界并阻塞 → 宿主 SIGKILL QEMU 与 NBD → 拷贝镜像 → `fsck.ext4 -fy` → 只读挂载 → `delta inspect/read` 校验。

## 这次冒烟修掉的装置缺陷

NBD simple reply 字段顺序（`magic,error,handle`）、未知选项的 `NBD_REP_ERR_UNSUP` 回退、`cpio` 未喂文件列表且二进制流误用文本模式、initramfs 缺 `/proc` 致 cmdline 参数被静默忽略、guest 调 `append` 少传表路径、`modprobe` 的 `builtin` 行被误判为“没有模块”、`verify_round` 把 intent 临时文件与“输给其它事务”的 intent 误判为损坏。

## 仍未做

合格 Linux host（或 `.github/workflows/s5-powerloss.yml`）上的 100 轮矩阵与 delta-rs 交叉读取。S5 的结论只能来自那里。
