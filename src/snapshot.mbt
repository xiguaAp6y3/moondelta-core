///|
fn default_protocol() -> Protocol {
  { min_reader_version: 1, min_writer_version: 1, reader_features: [], writer_features: [], }
}

///|
fn empty_schema() -> DeltaSchema {
  { fields: [], raw_json: "", }
}

///|
fn empty_metadata() -> TableInfo {
  {
    id: "",
    name: None,
    schema: empty_schema(),
    partition_columns: [],
    format_provider: "parquet",
    configuration: [],
  }
}

///|
fn upsert_file(files : Array[AddFile], f : AddFile) -> Unit {
  let rebuilt : Array[AddFile] = []
  let mut replaced = false
  for x in files {
    if x.path == f.path {
      rebuilt.push(f)
      replaced = true
    } else {
      rebuilt.push(x)
    }
  }
  if !replaced {
    rebuilt.push(f)
  }
  files.clear()
  for x in rebuilt {
    files.push(x)
  }
}

///|
fn drop_file(files : Array[AddFile], path : String) -> Unit {
  let kept : Array[AddFile] = []
  for x in files {
    if x.path != path {
      kept.push(x)
    }
  }
  files.clear()
  for x in kept {
    files.push(x)
  }
}

///|
/// add.stats 的 numRecords。stats 损坏时显式报错——行数统计不允许静默失真。
fn stats_num_records(stats : String?) -> Int64 raise DeltaError {
  match stats {
    Some(s) => {
      let j = @json.parse(s) catch {
        _ =>
          raise DeltaError::ParseError(
            "add.stats is not valid JSON: '\{s}'",
          )
      }
      match json_field_int64(j, "numRecords") {
        Some(v) => v
        None =>
          raise DeltaError::ParseError(
            "add.stats is missing integer field 'numRecords': '\{s}'",
          )
      }
    }
    None => 0L
  }
}

fn feature_list_text(names : Array[String]) -> String {
  let sb = StringBuilder()
  let mut i = 0
  while i < names.length() {
    if i > 0 {
      sb.write_char(',')
    }
    sb.write_string(names[i])
    i = i + 1
  }
  sb.to_string()
}

///|
/// 协议版本与 feature 校验（读取端）：
/// - 只支持 reader protocol version 1；更高的 reader 版本门控 column mapping、
///   删除向量与 v2 checkpoint，必须拒绝并说明原因（Delta 规范行为）。
/// - reader=1 时不得携带 readerFeatures；writer=7 以下不得携带 writerFeatures
///   （解析期已拦截，这里兜底）。
fn validate_protocol(p : Protocol) -> Unit raise DeltaError {
  if p.min_reader_version != 1 {
    raise DeltaError::Unsupported(
      "table requires reader protocol version \{p.min_reader_version}; this kernel implements reader version 1 only (higher reader versions gate column mapping, deletion vectors and v2 checkpoints)",
    )
  }
  if p.reader_features.length() > 0 {
    raise DeltaError::Unsupported(
      "table declares readerFeatures [\{feature_list_text(p.reader_features)}]; reader version 1 has no feature list",
    )
  }
}

///|
pub(all) struct DeltaTable {
  root : String
}

///|
pub fn DeltaTable::open(root : String) -> DeltaTable raise DeltaError {
  if !@fs.path_exists(log_dir(root)) {
    raise DeltaError::NotFound(
      "'\{root}' is not a Delta table (no _delta_log directory)",
    )
  }
  { root, }
}

///|
pub fn DeltaTable::versions(self : DeltaTable) -> Array[Int32] raise DeltaError {
  list_versions(self.root)
}

///|
pub fn DeltaTable::current_version(self : DeltaTable) -> Int32 raise DeltaError {
  let versions = list_versions(self.root)
  if versions.length() == 0 {
    if has_checkpoint_file(self.root) {
      raise DeltaError::NotFound(
        "table '\{self.root}' has no JSON log entries (checkpoint-only table; loading snapshots from checkpoint parquet is not implemented)",
      )
    }
    raise DeltaError::NotFound("table '\{self.root}' has no log entries")
  }
  versions[versions.length() - 1]
}

///|
/// 回放日志到指定版本（None = 最新），返回 snapshot 与历史
fn replay_until(
  root : String,
  target : Int32?,
) -> (Snapshot, Array[HistoryEntry]) raise DeltaError {
  let versions = list_versions(root)
  if versions.length() == 0 {
    if has_checkpoint_file(root) {
      raise DeltaError::NotFound(
        "table '\{root}' has no JSON log entries (checkpoint-only table; loading snapshots from checkpoint parquet is not implemented)",
      )
    }
    raise DeltaError::NotFound("table '\{root}' has no log entries")
  }
  if versions[0] != 0 {
    raise DeltaError::Unsupported(
      "log starts at version \{versions[0]}; checkpoint-only or truncated tables are not supported (replay requires the initial log entries)",
    )
  }
  let latest = versions[versions.length() - 1]
  let target_version = match target {
    Some(v) => v
    None => latest
  }
  if target_version < 0 || target_version > latest {
    raise DeltaError::NotFound(
      "requested version \{target_version} is out of range (latest = \{latest})",
    )
  }
  if !versions.contains(target_version) {
    raise DeltaError::NotFound("log version \{target_version} does not exist")
  }
  let mut protocol = default_protocol()
  let mut metadata = empty_metadata()
  let files : Array[AddFile] = []
  let history : Array[HistoryEntry] = []
  let ignored : Array[String] = []
  let mut v = 0
  while v <= target_version {
    let actions = read_actions(root, v)
    for action in actions {
      match action {
        ProtocolAction(p) => protocol = p
        MetaDataAction(m) => metadata = m
        AddAction(f) => {
          if f.has_deletion_vector {
            raise DeltaError::Unsupported(
              "file '\{f.path}' carries a deletionVector (deletion vectors require reader protocol version 3; this kernel implements reader version 1)",
            )
          }
          upsert_file(files, f)
        }
        RemoveAction(r) => drop_file(files, r.path)
        CommitInfoAction(c) =>
          history.push({
            version: c.version,
            timestamp: c.timestamp,
            operation: c.operation,
          })
        TxnAction(_) => ()
        IgnoredAction(name) =>
          if !ignored.contains(name) {
            ignored.push(name)
          }
      }
    }
    v = v + 1
  }
  validate_protocol(protocol)
  let mut total_rows = 0L
  let mut total_bytes = 0L
  for f in files {
    total_rows = total_rows + stats_num_records(f.stats)
    total_bytes = total_bytes + f.size
  }
  let snapshot = {
    version: target_version,
    protocol,
    metadata,
    files,
    total_rows,
    total_bytes,
    ignored_actions: ignored,
  }
  (snapshot, history)
}

///|
pub fn DeltaTable::snapshot(self : DeltaTable) -> Snapshot raise DeltaError {
  let (snapshot, _) = replay_until(self.root, None)
  snapshot
}

///|
pub fn DeltaTable::snapshot_at(
  self : DeltaTable,
  version : Int32,
) -> Snapshot raise DeltaError {
  let (snapshot, _) = replay_until(self.root, Some(version))
  snapshot
}

///|
pub fn DeltaTable::history(
  self : DeltaTable,
) -> Array[HistoryEntry] raise DeltaError {
  let (_, history) = replay_until(self.root, None)
  history
}

///|
/// 读取表的最新版本号 + snapshot + 是否检测到 checkpoint（用于 CLI 提示）
pub fn DeltaTable::inspect(
  self : DeltaTable,
) -> (Snapshot, CheckpointInfo?, Bool) raise DeltaError {
  let (snapshot, _) = replay_until(self.root, None)
  let cp = checkpoint_info(self.root)
  let has_cp = has_checkpoint_file(self.root)
  (snapshot, cp, has_cp)
}
