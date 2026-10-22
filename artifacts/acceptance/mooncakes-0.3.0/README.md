# mooncakes.io 0.3.0 发布与安装验证（2026-10-22）

## 发布
- `moon package`：Check passed → `_build/publish/xiguaAp6y3-moondelta-core-0.3.0.zip`
- `moon publish --dry-run`：Server 202 Accepted（package xiguaAp6y3/moondelta-core version 0.3.0）
- `moon publish`：**Server 200 OK，exit 0**
- mooncakes.io 页面确认：https://mooncakes.io/docs/xiguaAp6y3/moondelta-core 显示
  **0.3.0（latest）**（另列 0.1.0）

## 空工程安装验证（本目录 install-log.txt）
最小工程 `moon add xiguaAp6y3/moondelta-core`（拉取 @0.3.0）→ 编译运行
create → append → snapshot → read_snapshot 全链路：
`ok: v0->v1 rows=1 id=7`
