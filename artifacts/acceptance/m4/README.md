# M4 嵌套写入 + 统计 —— 验收取证（2026-10-22）

路线图 `docs/ROADMAP.md` M4（截止 10-29）的完成证据，较排期提前。

## 实现
- vendored parquet `writer_shredded.mbt`（新增）：shredded 写入口——调用方提供
  叶级槽位流（值 + def/rep levels）与前序遍历 WriteNode 树；v1 数据页
  [rep levels][def levels][PLAIN 密集值]；footer schema 树按前序遍历生成
  （含 LIST/MAP/UTF8 注解、组内元素计数）
- core `writenested.mbt`（新增）：Delta schema → WriteNode 树（布局约定与
  delta-rs/Spark 一致）；Cell → 槽位流的镜像装配（null 占位 / 空集合占位
  def=field_def / 重复条目复用同一叶累加器——按点分路径索引）
- `append_rows`：嵌套 schema 走新路径，扁平路径不变；NOT NULL / 列名 / 类型
  校验对嵌套同样生效；嵌套字段不写 min/max 统计（放弃剪枝、保证正确读取，F1）

## 关键发现
- MAP 注解的 converted_type=1（最初误用 4=ENUM 导致 pyarrow 拒读）
- map 的 repeated 中间组名为 `entries` 时 pyarrow/delta-rs 才正确解读条目
  （与 delta-rs 写出的实际形态一致；本项目读取端不依赖该名字）

## 证据
- `external-readback.json`：delta-rs 1.6.6 与 DuckDB 回读
  `artifacts/interop-nested/moondelta-nested-written/`（本项目写出），
  10/10 检查通过——5 行逐行逐字段一致（struct 含 null 子字段、list 含
  null 元素与空集合、map 含 null 值与空集合）
- `m4-evidence.txt`：CLI 读回写出的嵌套表；扁平回归不变
- 单元测试 37/37：嵌套 write→read 回环（与 D3 oracle 相同的数据形态）
