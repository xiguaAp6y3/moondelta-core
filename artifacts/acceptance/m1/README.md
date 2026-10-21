# M1 协议与工程基线 —— 验收取证（2026-10-22）

路线图 `docs/ROADMAP.md` M1（截止 10-23）的完成证据。

- `m1-evidence.txt`：`moon check`（0 errors）/ `moon test`（31/31）+ 七个负向
  fixture 的错误信息与退出码（全部"结构化错误 + exit 1"）+ CLI 参数校验 +
  正向回归（covid 读取、table_a 不受影响）。
- `checkpoint-truncated` 仍读旧快照（exit 0）：唯一保留的静默缺口，属 M3
  （checkpoint 恢复），ROADMAP §5 声明为硬门槛。

对应的单元测试（`src/delta_wbtest.mbt`，31 个）覆盖：协议版本/feature 拒绝、
删除向量拒绝、未知 action 拒绝、cdc 显式忽略 + txn 识别、非 20 位日志名拒绝、
percent-encoded path 读取、byte/short 回读、create 防重、append 列名/类型/
NOT NULL 校验、put-if-absent、writer>2 拒绝、stats 损坏报错。
