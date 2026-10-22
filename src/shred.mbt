///|
/// 嵌套记录装配（M2）：把 shredded 叶列槽位流按 Delta schema 树装配成 Cell。
///
/// 规则（标准 Parquet def/rep 语义）：
/// - rep == 0 开启一条新记录；
/// - 槽位的 def < 某字段自身的 def level ⇔ 该字段在此次出现中为 null；
/// - list 条目边界：rep == 该字段 repetition level（首条目 rep == 外层 rep）；
/// - map 条目 = key（REQUIRED）+ value（可空）各 1 槽。
/// 所有游标在同一子树内严格同步前进，每装配一个实例恰好消费该子树的全部槽位。

///|
priv struct LeafCursor {
  leaf : @parquet.LeafColumn
  mut pos : Int
}

///|
fn LeafCursor::peek_def(self : LeafCursor) -> Int32 {
  self.leaf.def_levels[self.pos]
}

///|
fn LeafCursor::peek_rep(self : LeafCursor) -> Int32 {
  self.leaf.rep_levels[self.pos]
}

///|
fn LeafCursor::at_end(self : LeafCursor) -> Bool {
  self.pos >= self.leaf.def_levels.length()
}

///|
/// 将叶列按根字段名分桶（path[0] == 字段名），保持文件顺序
fn cursors_under_field(
  cursors : Array[LeafCursor],
  name : String,
) -> Array[LeafCursor] {
  let out : Array[LeafCursor] = []
  for c in cursors {
    if c.leaf.path.length() > 0 && c.leaf.path[0] == name {
      out.push(c)
    }
  }
  out
}

///|
/// struct 子字段的光标：路径中最后一个分量等于子字段名
fn cursors_under_child(
  cursors : Array[LeafCursor],
  name : String,
) -> Array[LeafCursor] {
  let out : Array[LeafCursor] = []
  for c in cursors {
    let n = c.leaf.path.length()
    if n > 0 && c.leaf.path[n - 1] == name {
      out.push(c)
    }
  }
  out
}

///|
/// map 的 key / value 光标
fn cursor_for_key(cursors : Array[LeafCursor]) -> LeafCursor raise DeltaError {
  for c in cursors {
    let n = c.leaf.path.length()
    if n > 0 && c.leaf.path[n - 1] == "key" {
      return c
    }
  }
  raise DeltaError::Unsupported(
    "map column is missing its 'key' leaf in the parquet schema",
  )
}

///|
fn cursor_for_value(
  cursors : Array[LeafCursor],
) -> LeafCursor raise DeltaError {
  for c in cursors {
    let n = c.leaf.path.length()
    if n > 0 && c.leaf.path[n - 1] == "value" {
      return c
    }
  }
  raise DeltaError::Unsupported(
    "map column is missing its 'value' leaf in the parquet schema",
  )
}

///|
/// 把 parquet 原始值转成 Cell（按叶列物理类型）
fn shred_value_to_cell(v : @parquet.Value) -> Cell {
  match v {
    @parquet.Int32(x) => CellI32(x)
    @parquet.Int64(x) => CellI64(x)
    @parquet.TimestampMicros(x) => CellTs(x)
    @parquet.Float(x) => CellF32(x)
    @parquet.Double(x) => CellF64(x)
    @parquet.Boolean(x) => CellBool(x)
    @parquet.String(x) => CellStr(x)
    @parquet.Binary(x) => CellBin(x)
    @parquet.Null => CellNull
  }
}

///|
/// 消费所有游标各 1 槽（对应"该字段为 null"的一次出现）
fn skip_all_null(cursors : Array[LeafCursor]) -> Unit raise DeltaError {
  for c in cursors {
    if c.at_end() {
      raise DeltaError::ParseError(
        "parquet level stream ended early while reading a null slot",
      )
    }
    c.pos = c.pos + 1
  }
}

///|
/// 装配一个字段实例。cursors 为该字段子树下的全部叶游标（非空）。
/// field_def：该字段"非空"对应的 def level；field_rep：外层 repetition level。
fn assemble_cell(
  ty : DeltaType,
  cursors : Array[LeafCursor],
  field_def : Int32,
  field_rep : Int32,
) -> Cell raise DeltaError {
  if cursors.length() == 0 {
    raise DeltaError::Unsupported(
      "delta schema field has no matching parquet leaf column",
    )
  }
  match ty {
    NestedType(kind~, fields~) =>
      if kind == "struct" {
        assemble_struct(fields, cursors, field_def, field_rep)
      } else if kind == "array" {
        assemble_list(fields, cursors, field_def, field_rep)
      } else if kind == "map" {
        assemble_map(fields, cursors, field_def, field_rep)
      } else {
        raise DeltaError::Unsupported("unknown nested kind '\{kind}'")
      }
    _ => {
      // 原始类型：本字段直接对应 1 个叶、1 个槽
      let c = cursors[0]
      if c.at_end() {
        raise DeltaError::ParseError("parquet level stream ended early")
      }
      let def = c.peek_def()
      let cell = if def == c.leaf.max_definition_level {
        shred_value_to_cell(c.leaf.values[c.pos])
      } else {
        CellNull
      }
      c.pos = c.pos + 1
      cell
    }
  }
}

///|
fn assemble_struct(
  fields : Array[DeltaField],
  cursors : Array[LeafCursor],
  field_def : Int32,
  field_rep : Int32,
) -> Cell raise DeltaError {
  let c0 = cursors[0]
  if c0.at_end() {
    raise DeltaError::ParseError("parquet level stream ended early")
  }
  // 任一后代槽位的 def < field_def ⇔ 整个 struct 为 null（各后代恰好 1 槽）
  if c0.peek_def() < field_def {
    skip_all_null(cursors)
    return CellNull
  }
  let members : Array[(String, Cell)] = []
  for f in fields {
    let child_cursors = cursors_under_child(cursors, f.name)
    let child_def = field_def + (if f.nullable { 1 } else { 0 })
    let cell = assemble_cell(f.type_, child_cursors, child_def, field_rep)
    members.push((f.name, cell))
  }
  CellStruct(members)
}

///|
/// 标准 3-level LIST：字段(optional) -> repeated 组 -> 元素。
/// list 为空：所有后代各 1 槽且 def == field_def；null：def < field_def。
fn assemble_list(
  fields : Array[DeltaField],
  cursors : Array[LeafCursor],
  field_def : Int32,
  field_rep : Int32,
) -> Cell raise DeltaError {
  let c0 = cursors[0]
  if c0.at_end() {
    raise DeltaError::ParseError("parquet level stream ended early")
  }
  let def0 = c0.peek_def()
  if def0 < field_def {
    skip_all_null(cursors)
    return CellNull
  }
  if def0 == field_def {
    // list 非空组但无条目（空 list）
    skip_all_null(cursors)
    return CellList([])
  }
  let entry_def = field_def + 1
  let entry_rep = field_rep + 1
  let element = fields[0]
  let items : Array[Cell] = []
  let mut first = true
  while true {
    if c0.at_end() {
      break
    }
    let rep0 = c0.peek_rep()
    if !first && rep0 <= field_rep {
      // 下一个槽位属于外层（新条目边界之外）
      break
    }
    if first && rep0 < field_rep {
      // 首条目的 rep 应为 field_rep（新记录）——更深表示流错位
      raise DeltaError::ParseError(
        "unexpected repetition level \{rep0} at list entry boundary",
      )
    }
    let cell = assemble_cell(element.type_, cursors, entry_def, entry_rep)
    items.push(cell)
    first = false
  }
  CellList(items)
}

///|
/// MAP：字段(optional) -> key_value(repeated) -> key(REQUIRED) + value(可空)。
fn assemble_map(
  fields : Array[DeltaField],
  cursors : Array[LeafCursor],
  field_def : Int32,
  field_rep : Int32,
) -> Cell raise DeltaError {
  if fields.length() != 2 {
    raise DeltaError::Unsupported("map field must declare key and value types")
  }
  let key_ty = fields[0].type_
  let value_field = fields[1]
  let c0 = cursors[0]
  if c0.at_end() {
    raise DeltaError::ParseError("parquet level stream ended early")
  }
  let def0 = c0.peek_def()
  if def0 < field_def {
    skip_all_null(cursors)
    return CellNull
  }
  if def0 == field_def {
    skip_all_null(cursors)
    return CellMap([])
  }
  let entry_def = field_def + 1
  let entry_rep = field_rep + 1
  let key_cursors = [cursor_for_key(cursors)]
  let value_cursors = [cursor_for_value(cursors)]
  let entries : Array[(Cell, Cell)] = []
  let mut first = true
  while true {
    if c0.at_end() {
      break
    }
    let rep0 = c0.peek_rep()
    if !first && rep0 <= field_rep {
      break
    }
    if first && rep0 < field_rep {
      raise DeltaError::ParseError(
        "unexpected repetition level \{rep0} at map entry boundary",
      )
    }
    // key：REQUIRED，存在即 def == entry_def
    let key_cell = assemble_cell(key_ty, key_cursors, entry_def, entry_rep)
    let value_cell = assemble_cell(
      value_field.type_,
      value_cursors,
      entry_def + (if value_field.nullable { 1 } else { 0 }),
      entry_rep,
    )
    entries.push((key_cell, value_cell))
    first = false
  }
  CellMap(entries)
}

///|
fn path_text(path : Array[String]) -> String {
  let sb = StringBuilder()
  let mut i = 0
  while i < path.length() {
    if i > 0 {
      sb.write_char('.')
    }
    sb.write_string(path[i])
    i = i + 1
  }
  sb.to_string()
}

///|
/// 从 shredded 叶列 + Delta schema 装配出全部行（列式结果，行数 = row_count）。
/// 返回与 schema.fields 顺序一致的列。
pub fn shred_rows(
  schema : DeltaSchema,
  leaves : Array[@parquet.LeafColumn],
  row_count : Int32,
) -> Array[Array[Cell]] raise DeltaError {
  let cursors : Array[LeafCursor] = []
  for leaf in leaves {
    cursors.push({ leaf, pos: 0 })
  }
  let columns : Array[Array[Cell]] = []
  for f in schema.fields {
    let field_cursors = cursors_under_field(cursors, f.name)
    let field_def = if f.nullable { 1 } else { 0 }
    let column : Array[Cell] = []
    for _ in 0..<row_count {
      if field_cursors.length() > 0 && !field_cursors[0].at_end() {
        // 顶层字段：新记录由 rep == 0 起始；空游标（整列无槽）按 null 处理
        let cell = assemble_cell(f.type_, field_cursors, field_def, 0)
        column.push(cell)
      } else {
        column.push(CellNull)
      }
    }
    columns.push(column)
  }
  // Delta schema 是读取事实的来源：不属于任何根字段的 parquet 叶列
  // （schema 演进后残留的列）直接忽略；被引用的列必须装配干净。
  for c in cursors {
    let mut referenced = false
    for f in schema.fields {
      if c.leaf.path.length() > 0 && c.leaf.path[0] == f.name {
        referenced = true
      }
    }
    if referenced && !c.at_end() {
      raise DeltaError::ParseError(
        "parquet column '\{path_text(c.leaf.path)}' has unconsumed slots after assembly",
      )
    }
  }
  columns
}

///|
fn schema_has_nested(schema : DeltaSchema) -> Bool {
  for f in schema.fields {
    if f.type_ is NestedType(..) {
      return true
    }
  }
  false
}

///|
/// 嵌套 schema 的读取路径：shredded 解码（值 + def/rep levels）后按 Delta
/// schema 树装配。文件遍历、limit 语义与分区列补齐与扁平路径一致。
pub fn read_snapshot_shredded(
  root : String,
  snapshot : Snapshot,
  limit : Int32?,
) -> ReadResult raise DeltaError {
  let fields = snapshot.metadata.schema.fields
  let columns : Array[ReadColumn] = []
  for f in fields {
    columns.push({ name: f.name, type_: f.type_, cells: [] })
  }
  let max_rows = match limit {
    Some(l) => l
    None => 1000000000
  }
  let mut row_count = 0
  let mut files_read = 0
  for file in snapshot.files {
    if row_count >= max_rows {
      break
    }
    let path = root + "/" + file.path
    let data = @fs.read_file_to_bytes(path) catch {
      @fs.IOError(msg) =>
        raise DeltaError::IoError("cannot read '\{file.path}': \{msg}")
    }
    let (leaves, file_rows) = @parquet.read_bytes_shredded(data) catch {
      @parquet.Unsupported(msg) =>
        raise DeltaError::Unsupported("cannot read '\{file.path}': \{msg}")
      @parquet.InvalidData(msg) =>
        raise DeltaError::ParseError("corrupt parquet '\{file.path}': \{msg}")
      @parquet.Io(msg) =>
        raise DeltaError::IoError("cannot read '\{file.path}': \{msg}")
    }
    files_read = files_read + 1
    let assembled = shred_rows(snapshot.metadata.schema, leaves, file_rows)
    let remaining = max_rows - row_count
    let take = if file_rows < remaining { file_rows } else { remaining }
    let mut idx = 0
    while idx < fields.length() {
      match partition_value_of(file, fields[idx].name) {
        Some(raw) => {
          let cell = cell_from_partition(fields[idx].type_, raw)
          let mut r = 0
          while r < take {
            columns[idx].cells.push(cell)
            r = r + 1
          }
        }
        None => {
          let mut r = 0
          while r < take {
            columns[idx].cells.push(assembled[idx][r])
            r = r + 1
          }
        }
      }
      idx = idx + 1
    }
    row_count = row_count + take
  }
  { columns, row_count, files_read }
}
