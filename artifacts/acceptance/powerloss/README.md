# S5 掉电持久性 harness：可执行装置与验收规则

状态：**HARNESS_VALIDATED_ON_WSL / NOT_RUN_ON_QUALIFIED_HOST**。装置已齐，并在本机通过离线自检与 WSL2 端到端冒烟（边界 trace + 20 轮真实切断，产物一律记 `smokeTest: true`、结论恒为 `INVALID`）。但**没有**在合格 Linux host 上跑过 100 轮矩阵，因此 S5 仍是 **BLOCKED / NOT_PASSED**，任何文档都不得据此声明断电持久性。

## 1. 为什么单独设门禁

S1–S4 的 SIGKILL、I/O 注入、目录同步顺序与竞争测试只证明进程崩溃与故障路径语义。它们不能证明：

- 未 `fsync` 的写入会在掉电后消失；
- 已完成 `fsync` 的写入会在掉电后保留；
- 主机保留脏页缓存时，杀死 QEMU 进程不等于设备缓存丢失。

因此实验用 volatile NBD 服务把未 flush 写保留在服务进程内存，完成 NBD `FLUSH` 才写入 raw backing 并 `fdatasync`。断电时需要强制终止 QEMU 和 NBD 服务；服务进程退出会丢弃易失缓存，而此前完成的 flush 留在 backing。该模型可以检验 guest 文件系统和应用对 flush 屏障的使用，不能替代真实硬件掉电或物理设备缓存行为。它只在正向控制确认缓存语义后才有实验效力。

## 2. 前置条件（全部满足才允许开始）

1. 非 WSL 专用 Linux host；缓存断电模型通过用户态 NBD 服务提供，guest 运行在 QEMU 中；
2. **可丢弃的 raw ext4 数据盘**（不是 WSL `/mnt/*`、不是宿主共享目录、不是已挂载文件系统）；
3. `qemu-system-x86_64`、`fsck.ext4`、`cpio`、`gzip`、`depmod`、静态 `busybox`、Python 3、root 权限；`/dev/kvm` 有则快，无则自动退回 TCG（慢但语义相同）；
4. 记录 QEMU 版本、guest kernel、ext4 feature/mount 选项、virtio/NBD 配置、宿主文件系统与脚本版本（编排器自动写入 `results-*.json` 的 `environment`）；
5. 独立 host 控制端记录每次事务发起、返回成功/unknown、txn ID 与序号；控制端日志不能与 guest 数据盘一起丢失（本装置的 host 侧事件流即控制端）；
6. 先跑**正向控制用例**：证明模型会丢弃未 flush 写、保留已完成 flush 写。控制用例失败则整组结果无效。

## 3. 装置组成

| 文件 | 职责 |
|---|---|
| `volatile_nbd.py` | QEMU 可连接的 Unix-domain NBD export。普通写入留在进程 page cache；NBD `FLUSH`/`FUA` 才 `pwrite` + `fdatasync` 到 raw backing；杀死进程即丢弃未 flush 页。`--self-test` 是协议级正反向控制 |
| `prepare_raw_ext4.py` | 创建可丢弃 raw ext4 数据盘；覆盖已有目标必须显式 `--overwrite` |
| `mk_initramfs.py` | 构建最小 guest：`-kernel` + initramfs（静态 busybox + writer 二进制及其 glibc、按 `modprobe --show-depends` 取到的 ext4/virtio 模块并 `depmod`）。缺件时报错，不静默降级 |
| `guest/init` | guest 内一次性 agent：挂载数据盘（即 ext4 journal replay = “重启”）→ 建表 → 干净 append → 目标 append（探针定点阻塞），事件全部走 `ttyS1` |
| `boundaries.json` | 17 个边界名称的单一事实来源；`required` 是 S5 必须覆盖的 9 个 |
| `src/powerloss_probe.c` | 边界探针：`MOONDELTA_POWERLOSS_AT` 命中时向 `ttyS1` 通告 `BOUNDARY name=…` 并**阻塞**，让 host 定点切断而不是与 guest 赛跑。未设置环境变量时完全空转 |
| `run_powerloss.py` | 100 轮编排器：正向控制 → 干净基线 → 每轮（起 NBD/QEMU、等边界、按序 SIGKILL、拷贝镜像、fsck、只读挂载、校验）→ `results-100.json` |
| `verify_round.py` | 主机侧结构校验：日志连续、每条 commit 可解析、引用文件存在且长度一致、遗留 intent 与 commit 摘要互证、`delta inspect/read/recover` 一致、可选 delta-rs 交叉读取 |
| `selfcheck.py` | 离线静态自检：C 侧能发出的边界名与 `boundaries.json` 完全一致、guest/编排器的 cmdline 契约一致、切断顺序正确、100 轮计划覆盖全部必需边界、门禁脚本永不输出 PASS |
| `check_environment.py` | 独立前置门禁；环境不满足时输出 `BLOCKED`，永不输出 PASS |

`src/powerloss_probe.c` 的插入点（`src/atomic_file_native.c` 的发布/删除原语内部，因此是真正的系统调用边界）：

```
intent-publish-{temp-created,written,fsynced,linked,dir-fsynced}
data-publish-{temp-created,written,fsynced,linked,dir-fsynced}
commit-publish-{temp-created,written,fsynced,linked,dir-fsynced}
cleanup-{unlinked,dir-fsynced}
```

## 4. 运行流程

在**合格 Linux host** 上，按下面顺序执行；每一步失败都不得跳到下一步。

```bash
# 0. 离线自检（任何机器都能跑，不产生证据）
python3 artifacts/acceptance/powerloss/selfcheck.py

# 1. 环境门禁：必须输出 PRECONDITIONS_MET，否则到此为止
python3 artifacts/acceptance/powerloss/check_environment.py \
  --image /var/tmp/moondelta-s5/data.raw --kernel /boot/vmlinuz-$(uname -r)

# 2. 可丢弃数据盘 + guest 构建
python3 artifacts/acceptance/powerloss/prepare_raw_ext4.py \
  --image /var/tmp/moondelta-s5/data.raw --size-mib 2048
moon build --target native     # 得到 atomic_commit_worker 与 delta
python3 artifacts/acceptance/powerloss/mk_initramfs.py \
  --kernel /boot/vmlinuz-$(uname -r) \
  --worker _build/native/debug/build/examples/atomic_commit_worker/atomic_commit_worker.exe \
  --out /var/tmp/moondelta-s5/initramfs.cpio.gz

# 3. 边界可达性 trace：证明 9 个必需边界都能被触发且顺序正确，不做切断
python3 artifacts/acceptance/powerloss/run_powerloss.py \
  --image /var/tmp/moondelta-s5/data.raw \
  --kernel /boot/vmlinuz-$(uname -r) \
  --initramfs /var/tmp/moondelta-s5/initramfs.cpio.gz \
  --delta-cli _build/native/debug/build/cmd/delta/delta.exe \
  --workdir /var/tmp/moondelta-s5/run --trace

# 4. 正式矩阵：100 次有效切断
python3 artifacts/acceptance/powerloss/run_powerloss.py \
  --image /var/tmp/moondelta-s5/data.raw \
  --kernel /boot/vmlinuz-$(uname -r) \
  --initramfs /var/tmp/moondelta-s5/initramfs.cpio.gz \
  --delta-cli _build/native/debug/build/cmd/delta/delta.exe \
  --workdir /var/tmp/moondelta-s5/run --rounds 100 --seed <recorded-seed>
```

一轮的实际动作：

1. 起 volatile NBD（`--trace` 记录 `write_cached`/`flush_durable`）与 QEMU（系统=initramfs，数据盘=该 NBD 上的 virtio-blk）；
2. guest 挂载数据盘（ext4 journal replay 就是“重启”），跑一次目标 append，探针在指定边界向 `ttyS1` 通告后阻塞；
3. host 读到 `BOUNDARY` 后**立即** `SIGKILL` QEMU，随后 `SIGKILL` NBD（顺序不能反：先丢 QEMU 自己的缓存，再丢设备缓存）；
4. host 拷贝镜像 → `fsck.ext4 -fy`（等价于开机 journal replay）→ 只读挂载 → `verify_round.py`；
5. 通过则只保留日志与校验报告，失败则连同镜像一起保留。

### 4.1 在 GitHub CI 上运行（`.github/workflows/s5-powerloss.yml`）

可行，且已落地为一个手动触发（`workflow_dispatch`）的 job，不在每次 push 上跑。

- **KVM 可用**：GitHub 官方说明标准 2-vCPU Linux runner 已支持硬件加速，前提是用一条 udev 规则放开 `/dev/kvm` 权限（[changelog](https://github.blog/changelog/2024-04-02-gitHub-actions-hardware-accelerated-android-virtualization-now-available/)）。workflow 里已包含该步骤；即使没有 KVM 也会退回 TCG，语义不变、只是更慢，所以 `check_environment.py` 把 KVM 记为 **advisory** 而不是前置条件。
- **就地取证**：job 固定 `runs-on: ubuntu-24.04`（不用 `-latest`，结论必须绑定到记录下来的镜像），把 `ImageOS`/`ImageVersion`/`GITHUB_SHA`/run id 写进 `_build/s5-run/ci-context.txt`，`results-*.json` 的 `ci` 与 `environment` 字段记录同一批信息。
- **步骤**：装依赖 → 构建 → 取 guest kernel 及其 modules（优先用现成的 `/boot/vmlinuz-<ver>`，否则装 `linux-image-generic`）→ 造 256 MiB 可丢弃 ext4（小盘让逐轮校验拷贝变廉价，S5 语义与盘大小无关）→ 构建 initramfs → 离线自检 → 前置门禁 → 边界 trace → 100 轮矩阵 → **无论成败**上传产物。
- **成本**：KVM 下每轮约 10–20 秒（切断 + 拷贝 + `fsck` + 只读挂载 + 双读校验），100 轮约 25–40 分钟；job 上限设为 300 分钟。TCG 下会明显变长。
- **`rounds < 100` 一律降级为 `INVALID`**：CI 上跑 10 轮只能算冒烟，不能算验收。
- **不自动重试**：runner 被回收导致某轮没被切断时，该轮是 `INVALID`、整组不得为 PASS，必须人工重跑到干净结果——自动重试等于洗证据。
- **口径边界**：CI 通过只覆盖该次运行记录下来的 QEMU / guest kernel / ext4 选项 / NBD 模型 / commit 组合。托管镜像会随上游滚动，所以不承诺“下周再跑得到同样结论”；需要长期可复现结论时应改用自建 runner 并固定镜像。

## 5. 判定规则

| 条件 | 判定 |
|---|---|
| 前置条件任一不满足 | `BLOCKED` |
| 正向控制用例未证明缓存丢弃 | 整组 `INVALID`，不可写成 PASS |
| 某轮没有在通告的边界被切断 / 通告的边界不是请求的边界 / 主机校验无法执行 | 该轮 `INVALID`，整组不得为 PASS |
| 恢复后版本不是「原版本」或「恰好一个完整新版本」、行数与存活 append 数不符、引用文件缺失/长度不符、`fsck` 判废、delta-rs 交叉读取失败 | 该轮 `FAILED`，停止并修复根因 |
| 100 次有效运行全部满足不变量 | 仅可声明该**精确 VM / kernel / ext4 / 设备配置**的强持久性 |
| 其他文件系统、macOS、WSL `/mnt/*`、NFS/SMB/FUSE、真实硬件缓存 | 不因上述结果自动纳入支持范围 |

不变量（每轮都对同一份切断后的镜像检查）：

- `_delta_log` 从 0 连续，每个 JSON 可解析，每条 commit 至少一个 action；
- 每个 `add` 引用的文件存在且长度与 `add.size` 一致；
- 遗留 intent 若指向已存在的版本，其 `commitDigest` 必须与该 commit 内容一致；
- 恢复版本 ∈ {上一轮版本, 上一轮版本 + 1}；行数 == 版本号（版本 0 建表 0 行，其后每次 append 1 行）；
- 本项目 reader（`delta inspect`/`delta read`/`delta recover` 与 `verify_round.py`）读回一致；装有 `deltalake` 时另做 delta-rs 交叉读取，缺失记为 `SKIPPED` 而不是通过。

## 6. 已完成的验证 / 仍未验证

已完成（2026-11-12）：**离线自检** 与 **WSL2 上的端到端冒烟**。WSL 记录为 `smokeTest: true`、结论恒为 `INVALID`，不构成 S5 证据，但它把装置的每一段真实执行了一遍：

- `moon check/test --target native` 56/56（Windows 与 WSL 两个平台）；探针插入 `moondelta_publish_pinned`/`moondelta_unlink_sync` 后正常路径行为不变；
- `volatile_nbd.py --self-test` 在 POSIX 上通过：未 flush 写入消失、已完成 FLUSH 存活；自测现在同时断言 NBD simple reply 的**规范字段顺序**（`magic, error, handle`）与“未知选项返回 `NBD_REP_ERR_UNSUP` 后继续协商”；
- `qemu-img info 'nbd+unix:///powerloss?socket=…'` 能通过该 export 读出 `raw / 256 MiB`，即 QEMU 自己的 NBD 客户端认可该设备；
- `mk_initramfs.py` 用 Ubuntu 6.8.0-149 内核建成 3.4 MB initramfs（ext4/virtio_blk 在该内核为 builtin，0 个模块）；
- guest 实跑：`/proc/cmdline` 参数生效 → 挂载 `/dev/vda`（ext4）→ 建表 + managed 策略 → 执行 append；
- `--trace` 一轮内通告 **23 个边界**，9 个必需边界全部命中，且持久化顺序按阶段正确（intent → data → commit → cleanup，data 目录同步先于 JSON 发布）；
- **20 轮真实切断矩阵**：20/20 轮 `PASS`（切断 → `fsck` → 只读挂载 → `delta inspect/read`），17 个边界全部被覆盖至少一次，观测到 `commitSurvived=1` 与 `commitLost=19` 两种合法结果，行数/版本不变量全部成立；整体仍因 smoke/WSL 判 `INVALID`；
- `test_verifier.py`：正例（干净表）+ 4 个反例（截断 JSON、删除被引用 parquet、自有 intent 摘要冲突、日志缺口）+ 2 个“合法残留”形状（未发布的 intent 临时文件、输给其它事务的 intent）全部符合预期；
- `selfcheck.py` 全绿：C 侧可发出的 17 个边界名与 `boundaries.json` 完全一致、guest/编排器 cmdline 契约一致、切断顺序 QEMU→NBD、100 轮计划覆盖全部必需边界。

仍未验证（需要合格 Linux host）：

- 合格 host（非 WSL）上的 **100 轮**矩阵，以及 delta-rs 交叉读取（当前环境未安装 `deltalake`，记为 `SKIPPED`）；
- `.github/workflows/s5-powerloss.yml` 未实际触发过：CI 里的 KVM、内核模块安装、loop 挂载仍需首次实跑确认；
- 首次实跑中修掉的装置缺陷（NBD 应答字段顺序、未知选项处理、`cpio` 未喂文件列表、initramfs 缺 `/proc`、guest 少传表路径、intent 临时文件误判）说明：**“装置写完了”和“装置能跑”是两件事**，S5 的 100 轮结论仍必须先在本机或 CI 里跑通再谈。

## 7. 与项目状态的关系

- S5 未通过前，README、路线图、申报书均不得声明断电持久性；
- `artifacts/acceptance/transaction-2026-11-11/results-100.json` 明确记录 `power_loss: NOT_TESTED`；
- 本目录的存在与自检通过只表示“已经定义如何取证、且装置自洽”，不表示“已经取证”。Windows 仍只声明进程崩溃一致性。
