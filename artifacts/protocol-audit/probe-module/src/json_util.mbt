///|
/// JSON 访问小工具。核心 json 包以 prelude 形式导出类型与构造子，
/// 这里统一用模式匹配读取，避免 deprecated accessor。
pub fn json_as_object(j : Json) -> Map[String, Json]? {
  match j {
    Object(m) => Some(m)
    _ => None
  }
}

///|
pub fn json_as_array(j : Json) -> Array[Json]? {
  match j {
    Array(a) => Some(a)
    _ => None
  }
}

///|
pub fn json_as_string(j : Json) -> String? {
  match j {
    String(s) => Some(s)
    _ => None
  }
}

///|
pub fn json_as_double(j : Json) -> Double? {
  match j {
    Number(n, repr=_) => Some(n)
    _ => None
  }
}

///|
pub fn json_as_int(j : Json) -> Int32? {
  match j {
    Number(n, repr=_) => Some(n.to_int())
    _ => None
  }
}

///|
pub fn json_as_int64(j : Json) -> Int64? {
  match j {
    Number(n, repr=_) => Some(n.to_int64())
    _ => None
  }
}

///|
pub fn json_as_bool(j : Json) -> Bool? {
  match j {
    True => Some(true)
    False => Some(false)
    _ => None
  }
}

///|
/// 读取对象字段
pub fn json_field(j : Json, key : String) -> Json? {
  match json_as_object(j) {
    Some(m) => m.get(key)
    None => None
  }
}

///|
/// 读取对象字段为字符串
pub fn json_field_string(j : Json, key : String) -> String? {
  match json_field(j, key) {
    Some(v) => json_as_string(v)
    None => None
  }
}

///|
/// 读取对象字段为 Int64
pub fn json_field_int64(j : Json, key : String) -> Int64? {
  match json_field(j, key) {
    Some(v) => json_as_int64(v)
    None => None
  }
}

///|
/// 读取对象字段为 Int
pub fn json_field_int(j : Json, key : String) -> Int32? {
  match json_field(j, key) {
    Some(v) => json_as_int(v)
    None => None
  }
}

///|
/// 读取对象字段为 Bool
pub fn json_field_bool(j : Json, key : String) -> Bool? {
  match json_field(j, key) {
    Some(v) => json_as_bool(v)
    None => None
  }
}

///|
/// 读取对象字段为数组
pub fn json_field_array(j : Json, key : String) -> Array[Json]? {
  match json_field(j, key) {
    Some(v) => json_as_array(v)
    None => None
  }
}

///|
/// JSON 对象 → (key, value-string) 列表（用于 partitionValues / configuration）
pub fn json_object_pairs_string(j : Json) -> Array[(String, String)] {
  let out : Array[(String, String)] = []
  match json_as_object(j) {
    Some(m) =>
      for k, v in m {
        match json_as_string(v) {
          Some(s) => out.push((k, s))
          None =>
            match json_as_double(v) {
              Some(d) => out.push((k, d.to_string()))
              None => ()
            }
        }
      }
    None => ()
  }
  out
}
