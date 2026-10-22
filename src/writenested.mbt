///|
/// 嵌套写入（M4）：把 Cell 列拆成叶级槽位流（值 + def/rep levels）并按
/// Delta schema 生成 Parquet schema 树，交给 vendored 的 shredded 写入口。
/// 布局约定与 delta-rs/Spark 的标准形态一致：
/// - struct：optional/required group，子字段同名；
/// - array：字段 group（LIST 注解）→ "list" repeated group → element
///   （可空性 = containsNull）；
/// - map：字段 group（MAP 注解）→ "key_value" repeated group →
///   key（REQUIRED）+ value（可空性 = valueContainsNull）。
/// def/rep 语义与读取侧（src/shred.mbt）严格互为镜像。
/// 叶累加器按"点分路径 → 下标"索引：重复条目复用同一个叶累加器。

///|
priv struct LeafAcc {
  path : Array[String]
  column_type : @parquet.ColumnType
  max_definition_level : Int32
  max_repetition_level : Int32
  values : Array[@parquet.Value]
  def_levels : Array[Int32]
  rep_levels : Array[Int32]
}

///|
struct NestWriteCtx {
  /// 叶路径（点分）→ accs 下标；重复条目复用同一个叶累加器
  acc_index : Map[String, Int]
  accs : Array[LeafAcc]
}

///|
fn nest_path_text(path : Array[String]) -> String {
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
fn nest_cell_to_value(cell : Cell) -> @parquet.Value raise DeltaError {
  match cell {
    CellI32(x) => @parquet.Int32(x)
    CellI64(x) => @parquet.Int64(x)
    CellF32(x) => @parquet.Float(x)
    CellF64(x) => @parquet.Double(x)
    CellBool(x) => @parquet.Boolean(x)
    CellStr(x) => @parquet.String(x)
    CellBin(x) => @parquet.Binary(x)
    CellNull => @parquet.Null
    _ =>
      raise DeltaError::Unsupported(
        "nested write: unexpected container cell at a primitive leaf",
      )
  }
}

///|
fn nest_parquet_type(ty : DeltaType) -> @parquet.ColumnType raise DeltaError {
  match ty {
    Int32Type | ByteType | ShortType => @parquet.Int32
    Int64Type => @parquet.Int64
    StringType => @parquet.String
    BinaryType => @parquet.Binary
    BooleanType => @parquet.Boolean
    DoubleType => @parquet.Double
    _ =>
      raise DeltaError::Unsupported(
        "nested write does not support column type '\{delta_type_name(ty)}'",
      )
  }
}

///|
/// 按字段构建 schema 子树并登记叶累加器（前序遍历顺序）
fn nest_build_node(
  name : String,
  ty : DeltaType,
  nullable : Bool,
  parent_def : Int32,
  parent_rep : Int32,
  path : Array[String],
  ctx : NestWriteCtx,
) -> @parquet.WriteNode raise DeltaError {
  let repetition = if nullable { @parquet.Optional } else { @parquet.Required }
  let field_def = if nullable { parent_def + 1 } else { parent_def }
  let next_path = path + [name]
  match ty {
    NestedType(kind~, fields~) =>
      if kind == "struct" {
        let children : Array[@parquet.WriteNode] = []
        for f in fields {
          children.push(
            nest_build_node(
              f.name, f.type_, f.nullable, field_def, parent_rep, next_path, ctx,
            ),
          )
        }
        {
          name,
          repetition,
          column_type: None,
          converted_type: None,
          children,
        }
      } else if kind == "array" {
        let element = fields[0]
        let list_group_def = field_def + 1
        let list_group_rep = parent_rep + 1
        let element_node = nest_build_node(
          "element",
          element.type_,
          element.nullable,
          list_group_def,
          list_group_rep,
          next_path + ["list"],
          ctx,
        )
        {
          name,
          repetition,
          column_type: None,
          converted_type: Some(@parquet.converted_type_list),
          children: [
            {
              name: "list",
              repetition: @parquet.Repeated,
              column_type: None,
              converted_type: None,
              children: [element_node],
            },
          ],
        }
      } else if kind == "map" {
        if fields.length() != 2 {
          raise DeltaError::Unsupported("map field must declare key and value")
        }
        let key_field = fields[0]
        let value_field = fields[1]
        let kv_def = field_def + 1
        let kv_rep = parent_rep + 1
        let kv_path = next_path + ["entries"]
        let key_node = nest_build_node(
          "key", key_field.type_, false, kv_def, kv_rep, kv_path, ctx,
        )
        let value_node = nest_build_node(
          "value",
          value_field.type_,
          value_field.nullable,
          kv_def,
          kv_rep,
          kv_path,
          ctx,
        )
        {
          name,
          repetition,
          column_type: None,
          converted_type: Some(@parquet.converted_type_map),
          children: [
            {
              name: "entries",
              repetition: @parquet.Repeated,
              column_type: None,
              converted_type: None,
              children: [key_node, value_node],
            },
          ],
        }
      } else {
        raise DeltaError::Unsupported("unknown nested kind '\{kind}'")
      }
    Int32Type | ByteType | ShortType | Int64Type | StringType | BinaryType | BooleanType | DoubleType => {
      let column_type = nest_parquet_type(ty)
      ctx.accs.push({
        path: next_path,
        column_type,
        max_definition_level: field_def,
        max_repetition_level: parent_rep,
        values: [],
        def_levels: [],
        rep_levels: [],
      })
      {
        name,
        repetition,
        column_type: Some(column_type),
        converted_type: None,
        children: [],
      }
    }
    _ => {
      let _ = repetition
      raise DeltaError::Unsupported(
        "nested write does not support column type '\{delta_type_name(ty)}'",
      )
    }
  }
}

///|
fn nest_acc_for(ctx : NestWriteCtx, path : Array[String]) -> LeafAcc raise DeltaError {
  match ctx.acc_index.get(nest_path_text(path)) {
    Some(idx) => ctx.accs[idx]
    None =>
      raise DeltaError::ParseError(
        "nested write: no leaf accumulator for '\{nest_path_text(path)}'",
      )
  }
}

///|
/// 整棵子树的占位槽（字段为 null 或空集合）：每个后代叶 1 槽
fn nest_emit_placeholder_slots(
  node : @parquet.WriteNode,
  ctx : NestWriteCtx,
  path : Array[String],
  def_level : Int32,
  rep_level : Int32,
) -> Unit raise DeltaError {
  match node.column_type {
    Some(_) => {
      let acc = nest_acc_for(ctx, path)
      acc.def_levels.push(def_level)
      acc.rep_levels.push(rep_level)
    }
    None =>
      for child in node.children {
        nest_emit_placeholder_slots(
          child, ctx, path + [child.name], def_level, rep_level,
        )
      }
  }
}

///|
fn nested_member_type(ty : DeltaType, name : String) -> DeltaType? {
  match ty {
    NestedType(fields~, ..) =>
      for f in fields {
        if f.name == name {
          return Some(f.type_)
        }
      }
    _ => ()
  }
  None
}

///|
/// 装配一个字段实例（Cell → 槽位流）。node 与 ty 同构（构建期保证）。
fn nest_emit_field(
  ty : DeltaType,
  node : @parquet.WriteNode,
  path : Array[String],
  cell : Cell,
  parent_def : Int32,
  parent_rep : Int32,
  nullable : Bool,
  ctx : NestWriteCtx,
) -> Unit raise DeltaError {
  let field_def = if nullable { parent_def + 1 } else { parent_def }
  match cell {
    CellNull => {
      if !nullable {
        raise DeltaError::ParseError(
          "nested write: NOT NULL field received null",
        )
      }
      nest_emit_placeholder_slots(node, ctx, path, parent_def, parent_rep)
      return
    }
    _ => ()
  }
  match ty {
    NestedType(kind~, fields~) =>
      if kind == "struct" {
        match cell {
          CellStruct(members) => {
            if members.length() != node.children.length() {
              raise DeltaError::ParseError(
                "nested write: struct member count does not match schema",
              )
            }
            let mut i = 0
            while i < members.length() {
              let member_node = node.children[i]
              let (member_name, member_cell) = members[i]
              if member_name != member_node.name {
                raise DeltaError::ParseError(
                  "nested write: member '\{member_name}' does not match schema field '\{member_node.name}'",
                )
              }
              let member_ty = match nested_member_type(ty, member_name) {
                Some(t) => t
                None =>
                  raise DeltaError::ParseError(
                    "nested write: member '\{member_name}' not in schema",
                  )
              }
              let member_nullable = match member_node.repetition {
                @parquet.Optional => true
                _ => false
              }
              nest_emit_field(
                member_ty,
                member_node,
                path + [member_node.name],
                member_cell,
                field_def,
                parent_rep,
                member_nullable,
                ctx,
              )
              i = i + 1
            }
          }
          _ =>
            raise DeltaError::ParseError(
              "nested write: struct field received a non-struct cell",
            )
        }
      } else if kind == "array" {
        let list_group_def = field_def + 1
        let entry_rep = parent_rep + 1
        let list_group = node.children[0]
        let element_node = list_group.children[0]
        let element_path = path + [list_group.name, element_node.name]
        let element_ty = fields[0].type_
        let element_nullable = fields[0].nullable
        match cell {
          CellList(items) => {
            if items.is_empty() {
              // 空 list：占位槽 def = field_def（list 存在但无条目）
              nest_emit_placeholder_slots(node, ctx, path, field_def, parent_rep)
            }
            let mut k = 0
            while k < items.length() {
              let item_rep = if k == 0 { parent_rep } else { entry_rep }
              nest_emit_field(
                element_ty,
                element_node,
                element_path,
                items[k],
                list_group_def,
                item_rep,
                element_nullable,
                ctx,
              )
              k = k + 1
            }
          }
          _ =>
            raise DeltaError::ParseError(
              "nested write: array field received a non-list cell",
            )
        }
      } else if kind == "map" {
        if fields.length() != 2 {
          raise DeltaError::Unsupported("map field must declare key and value")
        }
        let kv_def = field_def + 1
        let entry_rep = parent_rep + 1
        let kv_group = node.children[0]
        let key_node = kv_group.children[0]
        let value_node = kv_group.children[1]
        let key_ty = fields[0].type_
        let value_field = fields[1]
        let kv_path = path + [kv_group.name]
        match cell {
          CellMap(entries) => {
            if entries.is_empty() {
              // 空 map：占位槽 def = field_def（map 存在但无条目）
              nest_emit_placeholder_slots(node, ctx, path, field_def, parent_rep)
            }
            let mut k = 0
            while k < entries.length() {
              let (key_cell, value_cell) = entries[k]
              let item_rep = if k == 0 { parent_rep } else { entry_rep }
              nest_emit_field(
                key_ty,
                key_node,
                kv_path + [key_node.name],
                key_cell,
                kv_def,
                item_rep,
                false,
                ctx,
              )
              nest_emit_field(
                value_field.type_,
                value_node,
                kv_path + [value_node.name],
                value_cell,
                kv_def,
                item_rep,
                value_field.nullable,
                ctx,
              )
              k = k + 1
            }
          }
          _ =>
            raise DeltaError::ParseError(
              "nested write: map field received a non-map cell",
            )
        }
      } else {
        raise DeltaError::Unsupported("unknown nested kind '\{kind}'")
      }
    _ => {
      // 原始叶
      let acc = nest_acc_for(ctx, path)
      acc.def_levels.push(field_def)
      acc.rep_levels.push(parent_rep)
      acc.values.push(nest_cell_to_value(cell))
    }
  }
}

///|
/// 把（与表 schema 同构的）列式输入拆成叶级槽位流 + schema 树并写出 parquet。
pub fn write_nested_parquet_bytes(
  schema : DeltaSchema,
  columns : Array[ReadColumn],
  row_count : Int32,
) -> Bytes raise DeltaError {
  let ctx : NestWriteCtx = { acc_index: Map::new(), accs: [] }
  let root_children : Array[@parquet.WriteNode] = []
  for f in schema.fields {
    root_children.push(nest_build_node(f.name, f.type_, f.nullable, 0, 0, [], ctx))
  }
  let mut idx = 0
  for acc in ctx.accs {
    ctx.acc_index[nest_path_text(acc.path)] = idx
    idx = idx + 1
  }
  let mut r = 0
  while r < row_count {
    let mut i = 0
    while i < schema.fields.length() {
      let f = schema.fields[i]
      nest_emit_field(
        f.type_,
        root_children[i],
        [f.name],
        columns[i].cells[r],
        0,
        0,
        f.nullable,
        ctx,
      )
      i = i + 1
    }
    r = r + 1
  }
  let leaves : Array[@parquet.WriteLeaf] = []
  for acc in ctx.accs {
    leaves.push({
      path: acc.path,
      column_type: acc.column_type,
      max_definition_level: acc.max_definition_level,
      max_repetition_level: acc.max_repetition_level,
      values: acc.values,
      def_levels: acc.def_levels,
      rep_levels: acc.rep_levels,
    })
  }
  @parquet.write_bytes_shredded(root_children, leaves, row_count) catch {
    @parquet.Unsupported(msg) => raise DeltaError::Unsupported(msg)
    @parquet.InvalidData(msg) => raise DeltaError::ParseError(msg)
    @parquet.Io(msg) => raise DeltaError::IoError(msg)
  }
}
