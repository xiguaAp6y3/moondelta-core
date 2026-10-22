# M2 Parquet 嵌套读取层 —— 验收取证（2026-10-22）

路线图 `docs/ROADMAP.md` M2（截止 10-25）的完成证据，较排期提前。

## 实现
- vendored parquet：`shredded.mbt`（新增）——每个叶列输出"值 + def/rep level"
  逐槽对齐的三元组（v1/v2 页、字典/PLAIN/DELTA 编码）；`LeafColumnMeta` 补
  `path`；修复 `build_leaf_columns` 的 def level 递增规则（Parquet 规范要求
  OPTIONAL **与 REPEATED** 都递增，原实现只对 OPTIONAL 递增——扁平数据不受
  影响，嵌套必错）
- core：`shred.mbt`（新增）——按 Delta schema 树装配 struct/list/map
  （NULL / 空集合 / null 元素 / null 子字段 / Delta schema 外多余列忽略）；
  `Cell` 增 `CellStruct/CellList/CellMap`；`NestedType` 保留子树；嵌套 schema
  走 `read_snapshot_shredded`，扁平路径不变

## 证据
- `m2-evidence.txt`：5 个嵌套 fixture 全部正向读取且与 pyarrow/delta-rs
  oracle 逐值一致（exit 0）；negative-physical-nested（Delta schema 谎报
  flat）仍结构化拒绝（exit 1）；covid/byte-short 扁平回归不变
- 单元测试 32/32：list_columns.parquet 正读（[1,2,3]/[NULL,1]/[4]）、
  D3 engine-written 与 oracle 逐值断言、schema 子树解析
- `d3_verify.py` 39/39：检查 3 段改为正向断言（历史 raw/ 输出保留为 0.1.x
  证据）；门槛计数段落标注为 D3 历史快照
