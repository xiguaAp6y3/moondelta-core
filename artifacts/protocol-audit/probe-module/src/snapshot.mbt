///|
fn default_protocol() -> Protocol {
  { min_reader_version: 1, min_writer_version: 1, }
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
fn stats_num_records(stats : String?) -> Int64 {
  match stats {
    Some(s) => {
      let j = @json.parse(s) catch { _ => return 0L }
      match json_field_int64(j, "numRecords") {
        Some(v) => v
        None => 0L
      }
    }
    None => 0L
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
  let mut v = 0
  while v <= target_version {
    let actions = read_actions(root, v)
    for action in actions {
      match action {
        ProtocolAction(p) => protocol = p
        MetaDataAction(m) => metadata = m
        AddAction(f) => upsert_file(files, f)
        RemoveAction(r) => drop_file(files, r.path)
        CommitInfoAction(c) =>
          history.push({
            version: c.version,
            timestamp: c.timestamp,
            operation: c.operation,
          })
        UnknownAction(_) => ()
      }
    }
    v = v + 1
  }
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
