///|
/// 从字符串视图解析十进制整数（只接受数字，忽略其它字符）
fn digits_to_int(sv : StringView) -> Int32 {
  let s = sv.to_owned()
  let mut n = 0
  for c in s {
    let d = c.to_int() - 48
    if d >= 0 && d <= 9 {
      n = n * 10 + d
    }
  }
  n
}

///|
/// 解析 `decimal(p,s)` 形式
fn parse_decimal_type(s : String) -> DeltaType {
  let body = s.replace(old="decimal(", new="")
  let body = body.replace(old=")", new="")
  let parts = Array::from_iter(body.split(","))
  if parts.length() == 2 {
    DecimalType(
      precision=digits_to_int(parts[0]),
      scale=digits_to_int(parts[1]),
    )
  } else {
    UnknownType(raw=s)
  }
}

///|
/// 解析 array 类型的 elementType / containsNull 为单字段子树（合成名 "element"）
fn parse_array_node(j : Json) -> DeltaType raise DeltaError {
  let element_type = match json_field(j, "elementType") {
    Some(t) => parse_type_node(t)
    None => UnknownType(raw="<missing elementType>")
  }
  let contains_null = match json_field_bool(j, "containsNull") {
    Some(b) => b
    None => true
  }
  NestedType(
    kind="array",
    fields=[{ name: "element", type_: element_type, nullable: contains_null }],
  )
}

///|
/// 解析 map 类型的 keyType / valueType / valueContainsNull 为 key/value 子树
fn parse_map_node(j : Json) -> DeltaType raise DeltaError {
  let key_type = match json_field(j, "keyType") {
    Some(t) => parse_type_node(t)
    None => UnknownType(raw="<missing keyType>")
  }
  let value_type = match json_field(j, "valueType") {
    Some(t) => parse_type_node(t)
    None => UnknownType(raw="<missing valueType>")
  }
  let value_contains_null = match json_field_bool(j, "valueContainsNull") {
    Some(b) => b
    None => true
  }
  NestedType(
    kind="map",
    fields=[
      { name: "key", type_: key_type, nullable: false },
      { name: "value", type_: value_type, nullable: value_contains_null },
    ],
  )
}

///|
/// 解析 struct 类型的 fields 子树
fn parse_struct_node(j : Json) -> DeltaType raise DeltaError {
  let fields = parse_schema_fields(j)
  NestedType(kind="struct", fields~)
}

///|
/// 解析 Delta schema 中的单个类型节点。
///
/// 扁平原始类型按名字映射；`struct` / `array` / `map` 保留完整子树
/// （M2 起可读取），读取或写入时由上层按能力拒绝。
fn parse_type_node(j : Json) -> DeltaType raise DeltaError {
  match json_as_string(j) {
    Some(s) =>
      if s == "integer" || s == "int" {
        Int32Type
      } else if s == "long" {
        Int64Type
      } else if s == "byte" {
        ByteType
      } else if s == "short" {
        ShortType
      } else if s == "float" {
        FloatType
      } else if s == "double" {
        DoubleType
      } else if s == "boolean" {
        BooleanType
      } else if s == "string" {
        StringType
      } else if s == "binary" {
        BinaryType
      } else if s == "date" {
        DateType
      } else if s == "timestamp" || s == "timestamp_ntz" {
        TimestampType
      } else if s.has_prefix("decimal") {
        parse_decimal_type(s)
      } else {
        UnknownType(raw=s)
      }
    None =>
      match json_field_string(j, "type") {
        Some(kind) =>
          if kind == "struct" {
            parse_struct_node(j)
          } else if kind == "array" {
            parse_array_node(j)
          } else if kind == "map" {
            parse_map_node(j)
          } else {
            UnknownType(raw=kind)
          }
        None => UnknownType(raw="<missing type>")
      }
  }
}

///|
/// 解析 `fields` 数组
pub fn parse_schema_fields(j : Json) -> Array[DeltaField] raise DeltaError {
  let arr = match json_field_array(j, "fields") {
    Some(a) => a
    None => raise DeltaError::ParseError("schema.fields is missing")
  }
  let out : Array[DeltaField] = []
  for f in arr {
    let name = match json_field_string(f, "name") {
      Some(n) => n
      None => raise DeltaError::ParseError("schema field without name")
    }
    let nullable = match json_field_bool(f, "nullable") {
      Some(b) => b
      None => false
    }
    let ty = match json_field(f, "type") {
      Some(t) => parse_type_node(t)
      None => raise DeltaError::ParseError("schema field without type")
    }
    out.push({ name, type_: ty, nullable, })
  }
  out
}

///|
/// 解析 Delta schemaString（形如 `{"type":"struct","fields":[...]}`）
pub fn parse_schema(raw : String) -> DeltaSchema raise DeltaError {
  let j = @json.parse(raw) catch {
    _ => raise DeltaError::ParseError("invalid schema JSON")
  }
  let fields = parse_schema_fields(j)
  { fields, raw_json: raw, }
}

///|
/// 判断 schema 是否能在当前读取路径上安全读取；不能则给出明确原因。
/// M2 起 struct/array/map 可读；decimal/uuid 等仍拒绝（显式例外清单）。
pub fn ensure_readable_schema(schema : DeltaSchema) -> Unit raise DeltaError {
  for f in schema.fields {
    ensure_readable_type(f.name, f.type_)
  }
}

///|
fn ensure_readable_type(name : String, ty : DeltaType) -> Unit raise DeltaError {
  match ty {
    UnknownType(raw~) =>
      raise DeltaError::Unsupported(
        "unsupported delta type '\{raw}' for column '\{name}'",
      )
    DecimalType(precision~, scale~) =>
      raise DeltaError::Unsupported(
        "decimal(\{precision},\{scale}) column '\{name}' is not supported (logical type is lost by the reader)",
      )
    NestedType(fields~, ..) =>
      for f in fields {
        ensure_readable_type("\{name}.\{f.name}", f.type_)
      }
    _ => ()
  }
}

///|
/// 判断某个类型是否能由写入路径产出
pub fn is_writable_type(ty : DeltaType) -> Bool {
  match ty {
    Int32Type => true
    Int64Type => true
    StringType => true
    BinaryType => true
    BooleanType => true
    DoubleType => true
    _ => false
  }
}
