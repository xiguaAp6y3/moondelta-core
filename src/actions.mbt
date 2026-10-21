///|
fn require_string(
  j : Json,
  key : String,
  ctx : String,
) -> String raise DeltaError {
  match json_field_string(j, key) {
    Some(s) => s
    None =>
      raise DeltaError::ParseError("\{ctx}: missing string field '\{key}'")
  }
}

///|
fn parse_feature_names(j : Json, key : String) -> Array[String] raise DeltaError {
  let out : Array[String] = []
  match json_field_array(j, key) {
    Some(arr) =>
      for item in arr {
        match json_as_string(item) {
          Some(s) => out.push(s)
          None =>
            raise DeltaError::ParseError(
              "protocol.\{key} contains a non-string feature name",
            )
        }
      }
    None => ()
  }
  out
}

///|
fn parse_protocol(j : Json) -> Protocol raise DeltaError {
  let min_reader = match json_field_int(j, "minReaderVersion") {
    Some(v) => v
    None => 1
  }
  let min_writer = match json_field_int(j, "minWriterVersion") {
    Some(v) => v
    None => 1
  }
  let reader_features = parse_feature_names(j, "readerFeatures")
  let writer_features = parse_feature_names(j, "writerFeatures")
  { min_reader_version: min_reader, min_writer_version: min_writer, reader_features, writer_features, }
}

///|
fn parse_partition_columns(j : Json) -> Array[String] {
  let out : Array[String] = []
  match json_field_array(j, "partitionColumns") {
    Some(arr) =>
      for item in arr {
        match json_as_string(item) {
          Some(s) => out.push(s)
          None => ()
        }
      }
    None => ()
  }
  out
}

///|
pub fn parse_metadata(j : Json) -> TableInfo raise DeltaError {
  let id = match json_field_string(j, "id") {
    Some(s) => s
    None => ""
  }
  let name = json_field_string(j, "name")
  let schema_string = match json_field_string(j, "schemaString") {
    Some(s) => s
    None => raise DeltaError::ParseError("metaData.schemaString is missing")
  }
  let schema = parse_schema(schema_string)
  let partition_columns = parse_partition_columns(j)
  let format_provider = match json_field(j, "format") {
    Some(fmt) =>
      match json_field_string(fmt, "provider") {
        Some(p) => p
        None => "parquet"
      }
    None => "parquet"
  }
  let configuration = match json_field(j, "configuration") {
    Some(cfg) => json_object_pairs_string(cfg)
    None => []
  }
  { id, name, schema, partition_columns, format_provider, configuration, }
}

///|
/// Delta 规范：add.path 是 percent-encoded 的 URI 路径成分，读取文件系统前必须解码。
pub fn decode_add_path(encoded : String) -> String raise DeltaError {
  let bytes = @percent.decode(encoded)
  @utf8.decode(bytes[:]) catch {
    Malformed(_) =>
      raise DeltaError::ParseError(
        "add.path is not valid percent-encoded UTF-8: '\{encoded}'",
      )
  }
}

///|
pub fn parse_add(j : Json) -> AddFile raise DeltaError {
  let path = decode_add_path(require_string(j, "path", "add"))
  let partition_values = match json_field(j, "partitionValues") {
    Some(pv) => json_object_pairs_string(pv)
    None => []
  }
  let size = match json_field_int64(j, "size") {
    Some(v) => v
    None => 0L
  }
  let modification_time = match json_field_int64(j, "modificationTime") {
    Some(v) => v
    None => 0L
  }
  let data_change = match json_field_bool(j, "dataChange") {
    Some(b) => b
    None => true
  }
  let stats = json_field_string(j, "stats")
  let has_deletion_vector = json_field(j, "deletionVector") is Some(_)
  { path, partition_values, size, modification_time, data_change, stats, has_deletion_vector, }
}

///|
pub fn parse_remove(j : Json) -> RemoveFile raise DeltaError {
  let path = require_string(j, "path", "remove")
  let deletion_timestamp = json_field_int64(j, "deletionTimestamp")
  let data_change = match json_field_bool(j, "dataChange") {
    Some(b) => b
    None => true
  }
  { path, deletion_timestamp, data_change, }
}

///|
pub fn parse_commit_info(j : Json, version : Int32) -> CommitInfo {
  let timestamp = json_field_int64(j, "timestamp")
  let operation = json_field_string(j, "operation")
  let params = match json_field(j, "operationParameters") {
    Some(p) => json_object_pairs_string(p)
    None => []
  }
  { version, timestamp, operation, operation_parameters: params, }
}

///|
fn parse_txn(j : Json) -> Txn raise DeltaError {
  let app_id = require_string(j, "appId", "txn")
  let version = match json_field_int64(j, "version") {
    Some(v) => v
    None =>
      raise DeltaError::ParseError("txn: missing int64 field 'version'")
  }
  { app_id, version, }
}

///|
/// 解析一行 Delta 日志（一行一个 action）。
/// 已知但与快照无关的 action（cdc / domainMetadata）显式忽略并记录名字；
/// 未识别的 action 直接报错——不静默跳过。
pub fn parse_action(
  line : StringView,
  version : Int32,
) -> Action raise DeltaError {
  let j = @json.parse(line) catch {
    _ =>
      raise DeltaError::ParseError(
        "invalid JSON in _delta_log version \{version}",
      )
  }
  match json_field(j, "protocol") {
    Some(p) => return ProtocolAction(parse_protocol(p))
    None => ()
  }
  match json_field(j, "metaData") {
    Some(m) => return MetaDataAction(parse_metadata(m))
    None => ()
  }
  match json_field(j, "add") {
    Some(a) => return AddAction(parse_add(a))
    None => ()
  }
  match json_field(j, "remove") {
    Some(r) => return RemoveAction(parse_remove(r))
    None => ()
  }
  match json_field(j, "commitInfo") {
    Some(c) => return CommitInfoAction(parse_commit_info(c, version))
    None => ()
  }
  match json_field(j, "txn") {
    Some(t) => return TxnAction(parse_txn(t))
    None => ()
  }
  for known in ["cdc", "domainMetadata"] {
    if json_field(j, known) is Some(_) {
      return IgnoredAction(known)
    }
  }
  let mut unknown = "<non-object action>"
  if json_as_object(j) is Some(obj) {
    for k, _ in obj {
      unknown = k
      break
    }
  }
  raise DeltaError::Unsupported(
    "unknown action '\{unknown}' in _delta_log version \{version} (this kernel implements the action closed set: protocol/metaData/add/remove/commitInfo/txn, with cdc/domainMetadata explicitly ignored)",
  )
}
