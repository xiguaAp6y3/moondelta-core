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
fn parse_protocol(j : Json) -> Protocol {
  let min_reader = match json_field_int(j, "minReaderVersion") {
    Some(v) => v
    None => 1
  }
  let min_writer = match json_field_int(j, "minWriterVersion") {
    Some(v) => v
    None => 1
  }
  { min_reader_version: min_reader, min_writer_version: min_writer, }
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
pub fn parse_add(j : Json) -> AddFile raise DeltaError {
  let path = require_string(j, "path", "add")
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
  { path, partition_values, size, modification_time, data_change, stats, }
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
/// 解析一行 Delta 日志（一行一个 action）
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
  UnknownAction("unrecognized action")
}
