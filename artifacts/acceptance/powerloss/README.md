# S5 掉电持久性 harness：前置门禁与验收规则

状态：**HARNESS_CONSTRUCTED / NOT_RUN**。已加入 volatile NBD 设备模型和 raw ext4 数据盘准备脚本；协议级正反向控制自测通过。当前没有合格的非 WSL Linux/QEMU host、guest 镜像或 100 轮恢复报告，因此 S5 仍是 **BLOCKED / NOT_PASSED**。

## 为什么单独设门禁

S1–S4 的 SIGKILL、I/O 注入、目录同步顺序与竞争测试只证明进程崩溃与故障路径语义。它们不能证明：

- 未 `fsync` 的写入会在掉电后消失；
- 已完成 `fsync` 的写入会在掉电后保留；
- 主机保留脏页缓存时，杀死 QEMU 进程不等于设备缓存丢失。

因此实验用 volatile NBD 服务把未 flush 写保留在服务进程内存，完成 NBD `FLUSH` 才写入 raw backing 并 `fdatasync`。断电时需要强制终止 QEMU 和 NBD 服务；服务进程退出会丢弃易失缓存，而此前完成的 flush 留在 backing。该模型可以检验 guest 文件系统和应用对 flush 屏障的使用，不能替代真实硬件掉电或物理设备缓存行为。它只在正向控制确认缓存语义后才有实验效力。

## 前置条件（全部满足才允许开始）

1. 非 WSL 专用 Linux host；本 harness 的缓存断电模型通过用户态 NBD 服务提供，guest 运行在 QEMU 中；
2. **可丢弃的 raw ext4 数据盘**（不是 WSL `/mnt/*`、不是宿主共享目录、不是已挂载文件系统）；
3. `qemu-system-x86_64`、`mkfs.ext4`、Python 3 可用；QEMU guest 的系统盘与被测数据盘分离；
4. 记录 QEMU 版本、guest kernel、ext4 feature/mount 选项、virtio/NBD 配置、宿主文件系统与脚本版本；
5. 独立 host 控制端记录每次事务发起、返回成功/unknown、txn ID 与序号；控制端日志不能与 guest 数据盘一起丢失；
6. 先跑**正向控制用例**：证明模型会丢弃未 flush 写、保留已完成 flush 写。控制用例失败则整组结果无效。

## 已构造的组件

- `volatile_nbd.py`：QEMU 可连接的 Unix-domain NBD export。普通写入在易失 page cache；NBD FLUSH/FUA 将 dirty pages 写入 raw backing 并同步；杀死服务进程丢弃尚未 flush 的页面。
- `prepare_raw_ext4.py`：创建单独的 raw ext4 数据盘；要求显式指定目标，覆盖已有目标必须传 `--overwrite`。
- 协议级正反向控制：直接作为 NBD client 发 WRITE/FLUSH/READ 请求，验证无 flush 的内容在服务重启后消失、flush 完成的内容保留。

运行控制自测（Linux）：

```bash
python3 artifacts/acceptance/powerloss/volatile_nbd.py \
  --self-test /tmp/moondelta-nbd-selftest
```

准备独立 guest 数据盘（仅在专用 Linux host 执行；指定的文件将被格式化）：

```bash
python3 artifacts/acceptance/powerloss/prepare_raw_ext4.py \
  --image /var/tmp/moondelta-s5/data.raw --size-mib 2048
```

QEMU 将该盘接到 NBD 服务，而系统盘仍使用单独的 guest 镜像。QEMU blockdev 示例：

```bash
python3 artifacts/acceptance/powerloss/volatile_nbd.py \
  --image /var/tmp/moondelta-s5/data.raw \
  --size 2147483648 \
  --socket /var/tmp/moondelta-s5/data.sock \
  --trace /var/tmp/moondelta-s5/nbd.trace

qemu-system-x86_64 ... \
  -blockdev driver=nbd,server.type=unix,server.path=/var/tmp/moondelta-s5/data.sock,export=powerloss,node-name=delta_data \
  -device virtio-blk-pci,drive=delta_data
```

QEMU/guest 配置必须实际将被测 Delta 表放在该 virtio-blk 数据盘上。每个断电轮次须由外部控制端等待 guest 边界事件，再立即杀死 QEMU 和 NBD 服务；重启时对同一 raw backing 使用新的 NBD 服务进程。系统盘、控制端日志与 raw backing 不得放入会随 guest 一起丢失的易失盘。当前仓库尚未提供 guest rootfs、边界事件代理和恢复读回 orchestrator，因此这几项仍是 100 轮 S5 运行前置工作。

环境自检：

```bash
python3 artifacts/acceptance/powerloss/check_environment.py \
  --image /path/to/disposable-raw-ext4.img --kernel /path/to/vmlinuz
```

当前 Windows/WSL 开发环境预期输出 `BLOCKED`。`volatile_nbd.py --self-test` 通过只证明用户态缓存模型工作，不证明 guest/QEMU 矩阵已执行。

## 执行矩阵（S5 通过的必要条件）

1. 在以下持久化边界随机切断 guest 电源/块设备，至少 **100 次**，保存随机种子与每轮注入位置：
   临时 Parquet 写、数据文件 `fsync`、数据 `link`、数据目录 `fsync`、JSON 临时写、JSON `fsync`、JSON `link`、`_delta_log` `fsync`、intent 清除；
2. 恢复后检查：JSON 日志连续、每个正式 commit 可解析、commit 引用文件存在且长度/摘要正确、快照版本/schema/行值正确；分别用本项目 reader 与 delta-rs 读取；
3. 已返回“提交成功”的事务必须存在且完整；未收到成功确认的事务允许不存在或作为完整新版本存在，不允许空/半截 commit、缺失引用文件或版本读回变化；unknown 事务按 `query_transaction` 语义核对；
4. 每组结果同时记录控制端日志、guest 镜像配置、逐轮注入点与随机种子；
5. Windows NTFS 单独建立结论；若无法构造可信掉电与名称持久化证据，公开标记“强持久性不支持/未验证”。

## 判定规则

| 条件 | 判定 |
|---|---|
| 前置条件任一不满足 | `BLOCKED`（本文件当前状态） |
| 正向控制用例未证明缓存丢弃 | 整组 `INVALID`，不可写成 PASS |
| 100 次有效运行全部满足不变量 | 仅可声明该 **精确 VM / kernel / ext4 / 设备配置** 的强持久性 |
| 任一失败未解释/未修复 | S5 不通过 |
| 其他文件系统、macOS、WSL `/mnt/*`、NFS/SMB/FUSE | 不因上述结果自动纳入支持范围 |

## 与项目状态的关系

- S5 未通过前，README、路线图、申报书均不得声明断电持久性；
- `artifacts/acceptance/transaction-2026-11-11/results-100.json` 明确记录 `power_loss: NOT_TESTED`；
- 本目录的存在只表示“已经定义如何取证”，不表示“已经取证”。
