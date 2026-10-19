///|
/// JSON 字符串转义
fn json_escape(s : String) -> String {
  let sb = StringBuilder()
  for c in s {
    match c {
      '"' => sb.write_string("\\\"")
      '\\' => sb.write_string("\\\\")
      '\n' => sb.write_string("\\n")
      '\r' => sb.write_string("\\r")
      '\t' => sb.write_string("\\t")
      _ => if c.to_int() < 0x20 { sb.write_char(' ') } else { sb.write_char(c) }
    }
  }
  sb.to_string()
}

///|
fn json_string(s : String) -> String {
  "\"" + json_escape(s) + "\""
}

///|
fn json_string_array(items : Array[String]) -> String {
  let sb = StringBuilder()
  sb.write_char('[')
  let mut i = 0
  while i < items.length() {
    if i > 0 {
      sb.write_char(',')
    }
    sb.write_string(json_string(items[i]))
    i = i + 1
  }
  sb.write_char(']')
  sb.to_string()
}

///|
fn json_object_empty() -> String {
  "{}"
}

///|
fn parquet_type_of(ty : DeltaType) -> @parquet.ColumnType raise DeltaError {
  match ty {
    Int32Type => @parquet.Int32
    Int64Type => @parquet.Int64
    StringType => @parquet.String
    BinaryType => @parquet.Binary
    BooleanType => @parquet.Boolean
    DoubleType => @parquet.Double
    _ =>
      raise DeltaError::Unsupported(
        "writer only supports int/long/string/binary/boolean/double columns; got \{delta_type_name(ty)}",
      )
  }
}

///|
fn parquet_value_of(cell : Cell) -> @parquet.Value raise DeltaError {
  match cell {
    CellI32(x) => @parquet.Int32(x)
    CellI64(x) => @parquet.Int64(x)
    CellStr(x) => @parquet.String(x)
    CellBin(x) => @parquet.Binary(x)
    CellBool(x) => @parquet.Boolean(x)
    CellF64(x) => @parquet.Double(x)
    CellNull => @parquet.Null
    _ =>
      raise DeltaError::Unsupported(
        "writer only supports int/long/string/binary/boolean/double values",
      )
  }
}

///|
fn numeric_bounds(cells : Array[Cell]) -> (Double?, Double?) {
  let mut lo : Double? = None
  let mut hi : Double? = None
  for cell in cells {
    let v = match cell {
      CellI32(x) => Some(x.to_double())
      CellI64(x) => Some(x.to_double())
      CellF32(x) => Some(x.to_double())
      CellF64(x) => Some(x)
      _ => None
    }
    match v {
      Some(d) => {
        lo = match lo {
          Some(old) => if d < old { Some(d) } else { Some(old) }
          None => Some(d)
        }
        hi = match hi {
          Some(old) => if d > old { Some(d) } else { Some(old) }
          None => Some(d)
        }
      }
      None => ()
    }
  }
  (lo, hi)
}

///|
fn build_stats_json(columns : Array[ReadColumn], row_count : Int32) -> String {
  let min_sb = StringBuilder()
  let max_sb = StringBuilder()
  let mut has_numeric = false
  let mut first = true
  for col in columns {
    let (lo, hi) = numeric_bounds(col.cells)
    match (lo, hi) {
      (Some(lo_v), Some(hi_v)) => {
        if !first {
          min_sb.write_char(',')
          max_sb.write_char(',')
        }
        first = false
        has_numeric = true
        min_sb.write_string(json_string(col.name) + ":" + lo_v.to_string())
        max_sb.write_string(json_string(col.name) + ":" + hi_v.to_string())
      }
      _ => ()
    }
  }
  let sb = StringBuilder()
  sb.write_string("{\"numRecords\":" + row_count.to_string())
  if has_numeric {
    sb.write_string(",\"minValues\":{" + min_sb.to_string() + "}")
    sb.write_string(",\"maxValues\":{" + max_sb.to_string() + "}")
  }
  sb.write_char('}')
  sb.to_string()
}

///|
/// 逐级创建目录（等价于 mkdir -p）
pub fn ensure_dir(path : String) -> Unit raise DeltaError {
  if @fs.path_exists(path) {
    return
  }
  let segments = path.split("/")
  let mut current = ""
  for segment in segments {
    if segment.char_length() == 0 {
      continue
    }
    current = if current == "" {
      segment.to_owned()
    } else {
      current + "/" + segment.to_owned()
    }
    if !@fs.path_exists(current) {
      @fs.create_dir(current) catch {
        @fs.IOError(msg) => raise DeltaError::IoError(msg)
      }
    }
  }
}

///|
/// 创建一张新的 Delta 表（写入 version 0：commitInfo + protocol + metaData）。
pub fn create_table(
  root : String,
  schema_json : String,
  partition_columns : Array[String],
) -> Int32 raise DeltaError {
  ensure_dir(root)
  let log = log_dir(root)
  ensure_dir(log)
  let commit_info = "{\"commitInfo\":{\"timestamp\":0,\"operation\":\"CREATE TABLE\",\"operationParameters\":{},\"isolationLevel\":\"Serializable\",\"isBlindAppend\":true,\"engineInfo\":\"moondelta-core\"}}"
  let protocol = "{\"protocol\":{\"minReaderVersion\":1,\"minWriterVersion\":2}}"
  let meta_data = "{\"metaData\":{\"id\":" +
    json_string("moondelta-" + root) +
    ",\"format\":{\"provider\":\"parquet\",\"options\":{}},\"schemaString\":" +
    json_string(schema_json) +
    ",\"partitionColumns\":" +
    json_string_array(partition_columns) +
    ",\"configuration\":{},\"createdTime\":0}}"
  let content = commit_info + "\n" + protocol + "\n" + meta_data + "\n"
  write_text(log + "/" + version_file_name(0), content)
  0
}

///|
/// 追加一批行（列式输入），写 parquet 数据文件并提交新版本。
/// 只支持非分区表 + int/long/string/binary 列。
pub fn append_rows(
  root : String,
  columns : Array[ReadColumn],
  timestamp : Int64,
) -> Int32 raise DeltaError {
  let table = DeltaTable::open(root)
  let snapshot = table.snapshot()
  if snapshot.metadata.partition_columns.length() > 0 {
    raise DeltaError::Unsupported(
      "append to partitioned tables is not supported yet (MVP writes only unpartitioned tables)",
    )
  }
  let schema_fields = snapshot.metadata.schema.fields
  if columns.length() != schema_fields.length() {
    raise DeltaError::ParseError(
      "append: expected \{schema_fields.length()} columns, got \{columns.length()}",
    )
  }
  let mut i = 0
  if columns.length() > 0 {
    let row_count = columns[0].cells.length()
    while i < columns.length() {
      if columns[i].cells.length() != row_count {
        raise DeltaError::ParseError("append: column lengths differ")
      }
      i = i + 1
    }
  }
  let row_count = if columns.length() > 0 {
    columns[0].cells.length()
  } else {
    0
  }
  // 构造 parquet 列
  let pcols : Array[@parquet.Column] = []
  for c in columns {
    pcols.push(
      @parquet.new_column(c.name, parquet_type_of(c.type_), @parquet.Optional),
    )
  }
  // 构造行
  let rows : Array[Array[@parquet.Value]] = []
  let mut r = 0
  while r < row_count {
    let row : Array[@parquet.Value] = []
    for c in columns {
      row.push(parquet_value_of(c.cells[r]))
    }
    rows.push(row)
    r = r + 1
  }
  let pf = @parquet.new_parquet_file(pcols, rows, None)
  let data = @parquet.write_bytes(pf) catch {
    @parquet.Unsupported(msg) => raise DeltaError::Unsupported(msg)
    @parquet.InvalidData(msg) => raise DeltaError::ParseError(msg)
    @parquet.Io(msg) => raise DeltaError::IoError(msg)
  }
  let next_version = snapshot.version + 1
  let file_name = "part-0000-\{timestamp}-\{next_version}.parquet"
  let file_path = root + "/" + file_name
  @fs.write_bytes_to_file(file_path, data) catch {
    @fs.IOError(msg) => raise DeltaError::IoError(msg)
  }
  let stats = build_stats_json(columns, row_count)
  let commit_info = "{\"commitInfo\":{\"timestamp\":" +
    timestamp.to_string() +
    ",\"operation\":\"WRITE\",\"operationParameters\":{\"mode\":\"Append\",\"partitionBy\":\"[]\"},\"isolationLevel\":\"Serializable\",\"isBlindAppend\":true,\"engineInfo\":\"moondelta-core\"}}"
  let add = "{\"add\":{\"path\":" +
    json_string(file_name) +
    ",\"partitionValues\":" +
    json_object_empty() +
    ",\"size\":" +
    data.length().to_int64().to_string() +
    ",\"modificationTime\":" +
    timestamp.to_string() +
    ",\"dataChange\":true,\"stats\":" +
    json_string(stats) +
    "}}"
  let content = commit_info + "\n" + add + "\n"
  write_text(log_dir(root) + "/" + version_file_name(next_version), content)
  next_version
}
