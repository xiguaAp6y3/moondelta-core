# M3 checkpoint 恢复 —— 验收取证（2026-10-22）

路线图 `docs/ROADMAP.md` M3（截止 10-27）的完成证据，较排期提前。

## 实现
- `src/checkpoint.mbt`（新增）：checkpoint parquet 专用解析器。不同 writer
  （Spark 经典 / delta-rs）列可空性标注不同，故按 shredded 叶列的实际
  def/rep levels 逐记录解析（标量 def==max；map/list 条目按 rep 边界；
  action 出现与否以规范必填标量判定；未解释列按记录消耗槽位保持同步）。
  多部分 checkpoint 显式拒绝（结构化错误）。
- `snapshot.mbt`：replay_until 双路径——完整 JSON 日志（目标被日志覆盖）→
  从头回放（历史完整、行为与 0.1.x 一致）；否则从最新单文件 checkpoint
  恢复基底 + 叠加其后 JSON commit。顶端截断（checkpoint-truncated）与
  checkpoint-only 表不再静默读旧版本。current_version = max(JSON 最新,
  checkpoint 最新)。

## 证据（m3-evidence.txt）
- checkpoint-truncated：v10 / 100 文件 / 100 行（修复前为 v9 / 90——静默旧快照）
- checkpoint-only（flat）：v10 / 100 文件可读
- checkpoint-only-nested（delta-rs 写出）：恢复后 5 行嵌套值与 oracle 一致
- 互证：table_a snapshot_at(10) 经 JSON 回放 = 100 文件 = checkpoint 覆盖范围
- 时间旅行：完整日志覆盖的旧版本仍可回放（v9 → 90 文件）
- 单元测试 36/36（checkpoint 用例 5 个）；d3_verify 40/40
