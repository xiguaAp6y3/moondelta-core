# mooncakes.io 0.3.1 发布与安装验证（2026-10-31）

## 发布

- `moon publish --dry-run`：Server 202 Accepted（package `xiguaAp6y3/moondelta-core` version `0.3.1`）
- `moon publish`：**Server 200 OK**
- 发布包含当前源码：11 种 primitive 与嵌套 logical type、checkpoint/路径安全加固、37 个具名单元测试

## 空工程安装验证

见 [install-log.txt](install-log.txt)：

```text
$ moon add xiguaAp6y3/moondelta-core@0.3.1
Downloading xiguaAp6y3/moondelta-core@0.3.1
$ moon run --target native src
ok: v0->v1 rows=1 id=7
```
