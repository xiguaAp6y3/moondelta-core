# nested 对照速览（自动生成）

> 自动生成，完整报告见 `README.md`；机器可读结论见 `comparison.json`。

## 表级对照

| 表 | delta-rs | DuckDB | moondelta inspect schema | moondelta read 错误 | exit | 判定 |
|---|---|---|---|---|---|---|
| `engine-written/` | ok | ok | id: long / profile: struct / tags: array / attrs: map | unsupported: nested column 'profile' (type=struct) is not supported by the parquet reader; only flat schemas are readable | 0 | 能力缺口：外部成功、本项目拒绝 |
| `kind-struct-only/` | ok | ok | id: long / profile: struct | unsupported: nested column 'profile' (type=struct) is not supported by the parquet reader; only flat schemas are readable | 0 | 能力缺口：外部成功、本项目拒绝 |
| `kind-array-only/` | ok | ok | id: long / tags: array | unsupported: nested column 'tags' (type=array) is not supported by the parquet reader; only flat schemas are readable | 0 | 能力缺口：外部成功、本项目拒绝 |
| `kind-map-only/` | ok | ok | id: long / attrs: map | unsupported: nested column 'attrs' (type=map) is not supported by the parquet reader; only flat schemas are readable | 0 | 能力缺口：外部成功、本项目拒绝 |
| `checkpoint-nested/` | ok | ok | id: long / profile: struct / tags: array / attrs: map | unsupported: nested column 'profile' (type=struct) is not supported by the parquet reader; only flat schemas are readable | 0 | 同主表；checkpoint 存在不改变 read 拒绝 |
| `checkpoint-only-nested/` | ok | ok | - | not found: table 'artifacts/interop-nested/checkpoint-only-nested' has no log entries | 0 | checkpoint-only：外部引擎可读、本项目 not found |
| `negative-physical-nested/` | n/a | n/a | int64_list: long / utf8_list: string | unsupported: cannot read 'data/list_columns.parquet': Repeated columns are not supported yet | 0 | 负向用例：Parquet 层拒绝（不计通过） |

## 七项门槛

| # | 门槛 | 状态 |
|---|---|---|
| 1 | Parquet definition/repetition level，正确处理 struct、list（标准 3-level LIST）和 map 的 null、empty、重复元素与嵌套组合 | 未完成 |
| 2 | Delta schema JSON 与 Parquet schema 双向映射，保留 nullable、字段顺序、字段名和嵌套路径 | 未完成 |
| 3 | add.stats 对嵌套字段的缺失、null 和 min/max 不能被当作 flat 值误用；无法安全剪枝时必须放弃剪枝 | 未完成 |
| 4 | checkpoint 中的嵌套 action schema 可解析，能从 checkpoint 加载快照，并与 JSON 回放快照逐字段一致 | 未完成 |
| 5 | 外部引擎写入和读取对拍：Spark/Databricks、delta-rs 或 DuckDB 至少两种，分别覆盖 flat 与 nested 表 | 未完成 |
| 6 | 对 nested struct、array/list、map 各提供至少一个正向 fixture、一个 null/empty 边界用例、一个外部引擎结果对照 | 已完成 |
| 7 | 写入端生成的 nested Parquet 必须能被外部引擎读回；外部引擎生成的 nested Delta 必须能被本项目读回（只做“识别后报错”不算完成） | 未完成 |

统计：已完成 1 / 部分完成 0 / 未完成 6。

## 结论

- 外部引擎（delta-rs、DuckDB）读取 nested fixture 成功，oracle 逐字段一致；
- 本项目 `read` 在 schema 校验阶段结构化拒绝，未读出任何嵌套行；
- 因此这是**能力缺口对照**，不是互操作通过；不要引用为 nested 兼容证据。
