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
/// 当前版本 = max(最新 JSON commit, 最新 checkpoint)。checkpoint-only 表
/// 返回 checkpoint 版本。
pub fn DeltaTable::current_version(self : DeltaTable) -> Int32 raise DeltaError {
  let versions = list_versions(self.root)
  let cps = list_checkpoint_versions(self.root)
  if versions.length() == 0 && cps.length() == 0 {
    raise DeltaError::NotFound("table '\{self.root}' has no log entries")
  }
  let latest_json = if versions.length() > 0 {
    versions[versions.length() - 1]
  } else {
    -1
  }
  let latest_cp = if cps.length() > 0 { cps[cps.length() - 1] } else { -1 }
  if latest_json > latest_cp {
    latest_json
  } else {
    latest_cp
  }
}

///|
struct ReplayState {
  mut protocol : Protocol
  mut metadata : TableInfo
  files : Array[AddFile]
  history : Array[HistoryEntry]
  ignored : Array[String]
}

///|
fn apply_action(st : ReplayState, action : Action) -> Unit raise DeltaError {
  match action {
    ProtocolAction(p) => st.protocol = p
    MetaDataAction(m) => st.metadata = m
    AddAction(f) => {
      if f.has_deletion_vector {
        raise DeltaError::Unsupported(
          "file '\{f.path}' carries a deletionVector (deletion vectors require reader protocol version 3; this kernel implements reader version 1)",
        )
      }
      upsert_file(st.files, f)
    }
    RemoveAction(r) => drop_file(st.files, r.path)
    CommitInfoAction(c) =>
      st.history.push({
        version: c.version,
        timestamp: c.timestamp,
        operation: c.operation,
      })
    TxnAction(_) => ()
    IgnoredAction(name) =>
      if !st.ignored.contains(name) {
        st.ignored.push(name)
      }
  }
}

///|
fn finalize_snapshot(
  st : ReplayState,
  target_version : Int32,
) -> Snapshot raise DeltaError {
  validate_protocol(st.protocol)
  let mut total_rows = 0L
  let mut total_bytes = 0L
  for f in st.files {
    total_rows = total_rows + stats_num_records(f.stats)
    total_bytes = total_bytes + f.size
  }
  {
    version: target_version,
    protocol: st.protocol,
    metadata: st.metadata,
    files: st.files,
    total_rows,
    total_bytes,
    ignored_actions: st.ignored,
  }
}

///|
/// 回放日志到指定版本（None = 最新），返回 snapshot 与历史。
/// 日志从 0 完整时从头回放；否则从最新的单文件 checkpoint 恢复基底，
/// 再叠加其后的 JSON commit——截断日志与 checkpoint-only 表不再静默读旧版本。
fn replay_until(
  root : String,
  target : Int32?,
) -> (Snapshot, Array[HistoryEntry]) raise DeltaError {
  let versions = list_versions(root)
  let cp_versions = list_checkpoint_versions(root)
  let latest_json = if versions.length() > 0 {
    versions[versions.length() - 1]
  } else {
    -1
  }
  let cp_v = if cp_versions.length() > 0 {
    cp_versions[cp_versions.length() - 1]
  } else {
    -1
  }
  let effective_latest = if latest_json > cp_v { latest_json } else { cp_v }
  if effective_latest < 0 {
    raise DeltaError::NotFound("table '\{root}' has no log entries")
  }
  let full_log = versions.length() > 0 && versions[0] == 0
  let target_version = match target {
    Some(v) => v
    None => effective_latest
  }
  if target_version < 0 || target_version > effective_latest {
    raise DeltaError::NotFound(
      "requested version \{target_version} is out of range (latest = \{effective_latest})",
    )
  }
  let st : ReplayState = {
    protocol: default_protocol(),
    metadata: empty_metadata(),
    files: [],
    history: [],
    ignored: [],
  }
  // 路径一：JSON 从 0 起连续且目标版本被日志覆盖 → 从头回放（历史完整）。
  // 目标超出日志末端（顶端截断，checkpoint 在更前方）时走 checkpoint 恢复。
  if full_log && target_version <= latest_json {
    if !versions.contains(target_version) {
      raise DeltaError::NotFound("log version \{target_version} does not exist")
    }
    let mut v = 0
    while v <= target_version {
      let actions = read_actions(root, v)
      for action in actions {
        apply_action(st, action)
      }
      v = v + 1
    }
    let snapshot = finalize_snapshot(st, target_version)
    (snapshot, st.history)
  } else {
    // 路径二：checkpoint 恢复
    match cp_versions.last() {
      Some(c) => {
        if target_version < c {
          raise DeltaError::NotFound(
            "requested version \{target_version} predates the earliest available checkpoint \{c} and the JSON log no longer starts at version 0",
          )
        }
        let mut v = c + 1
        while v <= target_version {
          if !versions.contains(v) {
            raise DeltaError::NotFound(
              "log version \{v} is missing (gap after checkpoint \{c}; the full JSON log is required to replay past the checkpoint)",
            )
          }
          v = v + 1
        }
        let path = log_dir(root) + "/" + checkpoint_file_name(c)
        let (p_opt, m_opt, files) = parse_checkpoint(path)
        match p_opt {
          Some(p) => st.protocol = p
          None => ()
        }
        match m_opt {
          Some(m) => st.metadata = m
          None => ()
        }
        for f in files {
          apply_action(st, AddAction(f))
        }
        let mut v = c + 1
        while v <= target_version {
          let actions = read_actions(root, v)
          for action in actions {
            apply_action(st, action)
          }
          v = v + 1
        }
        let snapshot = finalize_snapshot(st, target_version)
        (snapshot, st.history)
      }
      None =>
        raise DeltaError::Unsupported(
          "log starts at version \{versions[0]}; checkpoint-only or truncated tables are not supported (replay requires the initial log entries)",
        )
    }
  }
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
