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
/// 单测/故障注入用的确定性中断点。公开 API 始终传 `TxnNoFault`。
enum TxnFault {
  TxnNoFault
  /// intent 已持久发布，尚未创建任何被管理文件
  TxnAbortAfterIntent
  /// 正式数据已发布，JSON commit 尚未发布
  TxnAbortAfterDataPublish
  /// 正式 JSON commit 已发布，尾部临时名/intent 清理尚未执行
  TxnAbortAfterCommit
}

///|
fn parquet_type_of(ty : DeltaType) -> @parquet.ColumnType raise DeltaError {
  match ty {
    ByteType | ShortType | Int32Type => @parquet.Int32
    DateType => @parquet.Date
    Int64Type => @parquet.Int64
    TimestampType => @parquet.TimestampMicros64
    FloatType => @parquet.Float
    DoubleType => @parquet.Double
    StringType => @parquet.String
    BinaryType => @parquet.Binary
    BooleanType => @parquet.Boolean
    _ =>
      raise DeltaError::Unsupported(
        "writer does not support column type '\{delta_type_name(ty)}'",
      )
  }
}

///|
fn parquet_value_of(cell : Cell) -> @parquet.Value raise DeltaError {
  match cell {
    CellI32(x) => @parquet.Int32(x)
    CellI64(x) => @parquet.Int64(x)
    CellTs(x) => @parquet.TimestampMicros(x)
    CellF32(x) => @parquet.Float(x)
    CellF64(x) => @parquet.Double(x)
    CellStr(x) => @parquet.String(x)
    CellBin(x) => @parquet.Binary(x)
    CellBool(x) => @parquet.Boolean(x)
    CellNull => @parquet.Null
    _ =>
      raise DeltaError::Unsupported(
        "writer does not support container values for flat columns",
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
/// 逐级创建目录（等价于 mkdir -p），每创建一个目录项就同步其父目录。
pub fn ensure_dir(path : String) -> Unit raise DeltaError {
  let segments = path.split("/")
  let mut current = if path.has_prefix("/") { "/" } else { "" }
  for segment in segments {
    if segment.char_length() == 0 {
      continue
    }
    current = if current == "" || current == "/" {
      current + segment.to_owned()
    } else {
      current + "/" + segment.to_owned()
    }
    if !@fs.path_exists(current) {
      @fs.create_dir(current) catch {
        @fs.IOError(msg) => if !@fs.path_exists(current) { raise DeltaError::IoError(msg) }
      }
    }
    sync_directory_entry(current)
  }
}

///|
/// 提交成功后清理本事务的临时名与 intent。失败只影响“维护待办”，
/// 不改变已确认提交的返回结果；清理不完整时保留 intent 供下次恢复。
fn cleanup_transaction_after_commit(root : String, intent : TxnIntent) -> Unit {
  let mut clean = true
  let _ = unlink_synced(root + "/" + intent.commit_temp) catch {
    _ => {
      clean = false
      false
    }
  }
  match intent.data_temp {
    Some(relative) => {
      let _ = unlink_synced(root + "/" + relative) catch {
        _ => {
          clean = false
          false
        }
      }
    }
    None => ()
  }
  if clean {
    ignore(delete_txn_intent(root, intent.txn_id) catch { _ => false })
  }
}

///|
/// 创建一张新的 Delta 表（写入 version 0：commitInfo + protocol + metaData）。
/// 表已存在时拒绝——绝不覆盖既有日志。返回 (版本, txn ID)。
pub fn create_table_with_txn(
  root : String,
  schema_json : String,
  partition_columns : Array[String],
) -> (Int32, String) raise DeltaError {
  let _ = ensure_dir(root)
  if @fs.path_exists(log_dir(root)) {
    auto_recover_managed(root)
  }
  let lock = acquire_exclusive_lock(root)
  errdefer release_table_lock(lock)
  let result = create_table_locked(root, schema_json, partition_columns)
  release_table_lock(lock)
  result
}

///|
/// 创建表（不返回 txn ID 的兼容入口）。
pub fn create_table(
  root : String,
  schema_json : String,
  partition_columns : Array[String],
) -> Int32 raise DeltaError {
  let (version, _) = create_table_with_txn(root, schema_json, partition_columns)
  version
}

///|
fn create_table_locked(
  root : String,
  schema_json : String,
  partition_columns : Array[String],
) -> (Int32, String) raise DeltaError {
  let log = log_dir(root)
  if @fs.path_exists(log) {
    let names = @fs.read_dir(log) catch {
      @fs.IOError(msg) => raise DeltaError::IoError(msg)
    }
    for name in names {
      if parse_log_name(name) is Some(_) {
        raise DeltaError::Unsupported(
          "cannot create table at '\{root}': a Delta log already exists (version file '\{name}')",
        )
      }
    }
  }
  ensure_dir(root)
  ensure_dir(log)
  let txn_id = random_txn_id()
  let commit_info = "{\"commitInfo\":{\"timestamp\":0,\"operation\":\"CREATE TABLE\",\"operationParameters\":{},\"isolationLevel\":\"Serializable\",\"isBlindAppend\":true,\"engineInfo\":\"moondelta-core\",\"moondeltaTxnId\":" +
    json_string(txn_id) +
    "}}"
  let protocol = "{\"protocol\":{\"minReaderVersion\":1,\"minWriterVersion\":2}}"
  let meta_data = "{\"metaData\":{\"id\":" +
    json_string("moondelta-" + root) +
    ",\"format\":{\"provider\":\"parquet\",\"options\":{}},\"schemaString\":" +
    json_string(schema_json) +
    ",\"partitionColumns\":" +
    json_string_array(partition_columns) +
    ",\"configuration\":{},\"createdTime\":0}}"
  let content = commit_info + "\n" + protocol + "\n" + meta_data + "\n"
  let commit_path = "_delta_log/" + version_file_name(0)
  let intent : TxnIntent = {
    format_version: 1,
    txn_id,
    kind: "create",
    version: 0,
    data_temp: None,
    data_path: None,
    data_length: 0L,
    data_digest: None,
    commit_temp: commit_path + temp_name(txn_id),
    commit_path,
    commit_digest: sha256_hex(@utf8.encode(content)),
  }
  publish_txn_intent(root, intent)
  publish_pinned_file(
    root + "/" + intent.commit_temp,
    root + "/" + commit_path,
    @utf8.encode(content),
    txn_id,
    0,
    "commit-publish",
    true,
  ) catch {
    AlreadyExists(_) =>
      raise DeltaError::CommitConflict(
        "cannot create table at '\{root}': target version 0 already exists",
      )
    err => raise err
  }
  cleanup_transaction_after_commit(root, intent)
  (0, txn_id)
}

///|
/// 追加一批行（列式输入），写 parquet 数据文件并提交新版本。
/// 只支持非分区表；列必须与表 schema 逐列同名同型，NOT NULL 列不得含 null。
/// 返回 (版本, txn ID)。
pub fn append_rows_with_txn(
  root : String,
  columns : Array[ReadColumn],
  timestamp : Int64,
) -> (Int32, String) raise DeltaError {
  append_rows_impl(root, columns, timestamp, TxnNoFault)
}

///|
/// 追加（不返回 txn ID 的兼容入口）。
pub fn append_rows(
  root : String,
  columns : Array[ReadColumn],
  timestamp : Int64,
) -> Int32 raise DeltaError {
  let (version, _) = append_rows_impl(root, columns, timestamp, TxnNoFault)
  version
}

///|
fn append_rows_impl(
  root : String,
  columns : Array[ReadColumn],
  timestamp : Int64,
  fault : TxnFault,
) -> (Int32, String) raise DeltaError {
  if @fs.path_exists(log_dir(root)) {
    auto_recover_managed(root)
  }
  let lock = acquire_shared_lock(root)
  errdefer release_table_lock(lock)
  let result = append_rows_locked(root, columns, timestamp, fault)
  release_table_lock(lock)
  result
}

///|
fn append_rows_locked(
  root : String,
  columns : Array[ReadColumn],
  timestamp : Int64,
  fault : TxnFault,
) -> (Int32, String) raise DeltaError {
  let table = DeltaTable::open(root)
  let snapshot = table.snapshot()
  if snapshot.protocol.min_writer_version > 2 {
    raise DeltaError::Unsupported(
      "table requires writer protocol version \{snapshot.protocol.min_writer_version}; the writer implements writer version 2 only",
    )
  }
  if snapshot.protocol.writer_features.length() > 0 {
    raise DeltaError::Unsupported(
      "append requires unsupported writerFeatures [\{feature_list_text(snapshot.protocol.writer_features)}]; this kernel implements writer version 2 without feature lists",
    )
  }
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
  if row_count == 0 {
    raise DeltaError::ParseError("append: empty batches are not committed")
  }
  // 列名与类型必须与 metaData.schema 一致；NOT NULL 列不得含 null
  i = 0
  while i < columns.length() {
    let field = schema_fields[i]
    let col = columns[i]
    if col.name != field.name {
      raise DeltaError::ParseError(
        "append: column \{i} is named '\{col.name}' but table schema declares '\{field.name}'",
      )
    }
    if col.type_ != field.type_ {
      raise DeltaError::ParseError(
        "append: column '\{col.name}' has type \{delta_type_name(col.type_)} but table schema declares \{delta_type_name(field.type_)}",
      )
    }
    if !field.nullable {
      for cell in col.cells {
        if cell is CellNull {
          raise DeltaError::ParseError(
            "append: column '\{col.name}' is NOT NULL but input contains null",
          )
        }
      }
    }
    i = i + 1
  }
  // 构造 parquet：嵌套 schema 走 shredded 写入（M4），扁平走原路径
  let data = if schema_has_nested(snapshot.metadata.schema) {
    write_nested_parquet_bytes(snapshot.metadata.schema, columns, row_count)
  } else {
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
    @parquet.write_bytes(pf) catch {
      @parquet.Unsupported(msg) => raise DeltaError::Unsupported(msg)
      @parquet.InvalidData(msg) => raise DeltaError::ParseError(msg)
      @parquet.Io(msg) => raise DeltaError::IoError(msg)
    }
  }
  let next_version = snapshot.version + 1
  let txn_id = random_txn_id()
  // 事务 ID 进入数据文件名，取代“时间戳 + 版本 + 进程内时钟”的碰撞假设。
  let file_name = "part-" +
    timestamp.to_string() +
    "-" +
    next_version.to_string() +
    "-" +
    txn_id +
    ".parquet"
  let stats = build_stats_json(columns, row_count)
  let commit_info = "{\"commitInfo\":{\"timestamp\":" +
    timestamp.to_string() +
    ",\"operation\":\"WRITE\",\"operationParameters\":{\"mode\":\"Append\",\"partitionBy\":\"[]\"},\"isolationLevel\":\"Serializable\",\"isBlindAppend\":true,\"engineInfo\":\"moondelta-core\",\"moondeltaTxnId\":" +
    json_string(txn_id) +
    "}}"
  // add.path 按规范做最小 percent-encode（unreserved 字符保持字面，避免其它引擎
  // 的 remove 无法与我们的 add 匹配）
  let add = "{\"add\":{\"path\":" +
    json_string(encode_action_path(file_name)) +
    ",\"partitionValues\":" +
    json_object_empty() +
    ",\"size\":" +
    data.length().to_int64().to_string() +
    ",\"modificationTime\":" +
    timestamp.to_string() +
    ",\"dataChange\":true,\"stats\":" +
    json_string(stats) +
    "}}"
  let commit_content = commit_info + "\n" + add + "\n"
  let commit_path = "_delta_log/" + version_file_name(next_version)
  let intent : TxnIntent = {
    format_version: 1,
    txn_id,
    kind: "append",
    version: next_version,
    data_temp: Some(file_name + temp_name(txn_id)),
    data_path: Some(file_name),
    data_length: data.length().to_int64(),
    data_digest: Some(sha256_hex(data)),
    commit_temp: commit_path + temp_name(txn_id),
    commit_path,
    commit_digest: sha256_hex(@utf8.encode(commit_content)),
  }
  // 1. 先持久发布不可变 intent：数据/日志临时名与摘要此时已经固定。
  publish_txn_intent(root, intent)
  match fault {
    TxnAbortAfterIntent =>
      raise DeltaError::IoError(
        "injected fault: intent published, no managed data created",
      )
    _ => ()
  }
  // 2. 数据：完整写入预定的临时名 → 文件 fsync → 不覆盖发布 → 父目录 fsync。
  publish_pinned_file(
    root + "/" + intent.data_temp.unwrap_or(""),
    root + "/" + file_name,
    data,
    txn_id,
    next_version,
    "data-publish",
    false,
  ) catch {
    AlreadyExists(_) =>
      raise DeltaError::IoError("data file already exists: '\{file_name}'")
    err => raise err
  }
  match fault {
    TxnAbortAfterDataPublish =>
      raise DeltaError::IoError(
        "injected fault: data published before the JSON commit",
      )
    _ => ()
  }
  // 3. JSON commit：完整写入预定的临时名 → fsync → 不覆盖发布 → `_delta_log` fsync。
  publish_pinned_file(
    root + "/" + intent.commit_temp,
    root + "/" + commit_path,
    @utf8.encode(commit_content),
    txn_id,
    next_version,
    "commit-publish",
    true,
  ) catch {
    AlreadyExists(_) => raise DeltaError::CommitConflict(root + "/" + commit_path)
    err => raise err
  }
  match fault {
    TxnAbortAfterCommit =>
      raise DeltaError::IoError(
        "injected fault: commit published, transaction cleanup not executed",
      )
    _ => ()
  }
  // 4. 尾部维护清理；失败不撤销已确认提交。
  cleanup_transaction_after_commit(root, intent)
  (next_version, txn_id)
}
