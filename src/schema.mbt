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
/// 解析 Delta schema 中的单个类型节点。
///
/// 扁平原始类型按名字映射；`struct` / `array` / `map` 统一标记为 `NestedType`，
/// 读取或写入时由上层拒绝，避免把嵌套列静默读成错误数据。
fn parse_type_node(j : Json) -> DeltaType {
  match json_as_string(j) {
    Some(s) =>
      if s == "integer" || s == "int" {
        Int32Type
      } else if s == "long" {
        Int64Type
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
          if kind == "struct" || kind == "array" || kind == "map" {
            NestedType(kind~)
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
pub fn ensure_readable_schema(schema : DeltaSchema) -> Unit raise DeltaError {
  for f in schema.fields {
    match f.type_ {
      NestedType(kind~) =>
        raise DeltaError::Unsupported(
          "nested column '\{f.name}' (type=\{kind}) is not supported by the parquet reader; only flat schemas are readable",
        )
      UnknownType(raw~) =>
        raise DeltaError::Unsupported(
          "unsupported delta type '\{raw}' for column '\{f.name}'",
        )
      DecimalType(precision~, scale~) =>
        raise DeltaError::Unsupported(
          "decimal(\{precision},\{scale}) column '\{f.name}' is not supported (logical type is lost by the reader)",
        )
      _ => ()
    }
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
    _ => false
  }
}
