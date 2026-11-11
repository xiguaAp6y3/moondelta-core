///|
/// 事务身份、持久 intent 清单与只读结果查询（S2）。
///
/// 维护元数据全部位于表根目录的隐藏目录 `.moondelta/`：
///
/// ```text
/// <root>/.moondelta.lock          固定表级维护锁（永不 unlink/替换）
/// <root>/.moondelta/intents/*.json 不可变事务 intent（每事务一份）
/// <root>/.moondelta/policy.json   显式受管表策略（只有它能授权自动清理）
/// ```
///
/// intent 在任何被管理文件创建之前原子发布并同步；数据与正式 JSON commit
/// 的临时名由 intent 预先固定，使用排他创建。Delta 日志本身仍是标准 JSON，
/// 不新增 action；txn ID 只写进 commitInfo 的附加字段，供本实现核对归属。
fn maintenance_dir(root : String) -> String {
  root + "/.moondelta"
}

///|
fn intents_dir(root : String) -> String {
  root + "/.moondelta/intents"
}

///|
fn policy_path(root : String) -> String {
  root + "/.moondelta/policy.json"
}

///|
fn intent_path(root : String, txn_id : String) -> String {
  intents_dir(root) + "/" + txn_id + ".json"
}

///|
fn temp_name(txn_id : String) -> String {
  ".moondelta-tmp-" + txn_id
}

///|
/// intent 自身的临时名使用独立后缀，便于把“intent 发布故障”与
/// “正式 JSON commit 发布故障”分别注入和观测。
fn intent_temp_name(txn_id : String) -> String {
  ".moondelta-itmp-" + txn_id
}

///|
fn is_hex_string(s : String, length : Int) -> Bool {
  if s.char_length() != length {
    return false
  }
  for c in s {
    let is_digit = c >= '0' && c <= '9'
    let is_lower = c >= 'a' && c <= 'f'
    if !is_digit && !is_lower {
      return false
    }
  }
  true
}

///|
fn is_txn_id(s : String) -> Bool {
  is_hex_string(s, 32)
}

///|
fn is_digest(s : String) -> Bool {
  is_hex_string(s, 64)
}

///|
/// intent 中的路径必须是表内相对路径：不以 `/` 开头、不含反斜杠、不含空段、
/// 不含 `.`/`..` 段。拒绝路径别名和越界路径比“先删再发现”更便宜。
fn is_safe_relative_path(path : String) -> Bool {
  if path.char_length() == 0 || path.has_prefix("/") || path.has_prefix("\\") {
    return false
  }
  if path.contains("\\") || path.contains("//") {
    return false
  }
  for segment in path.split("/") {
    if segment.char_length() == 0 || segment == "." || segment == ".." {
      return false
    }
  }
  true
}

///|
struct TxnIntent {
  format_version : Int32
  txn_id : String
  kind : String
  version : Int32
  data_temp : String?
  data_path : String?
  data_length : Int64
  data_digest : String?
  commit_temp : String
  commit_path : String
  commit_digest : String
}

///|
fn json_string_or_null(value : String?) -> String {
  match value {
    Some(s) => json_string(s)
    None => "null"
  }
}

///|
fn txn_intent_to_json(intent : TxnIntent) -> String {
  "{\"formatVersion\":" +
  intent.format_version.to_string() +
  ",\"txnId\":" +
  json_string(intent.txn_id) +
  ",\"kind\":" +
  json_string(intent.kind) +
  ",\"version\":" +
  intent.version.to_string() +
  ",\"dataTempPath\":" +
  json_string_or_null(intent.data_temp) +
  ",\"dataPath\":" +
  json_string_or_null(intent.data_path) +
  ",\"dataLength\":" +
  intent.data_length.to_string() +
  ",\"dataDigest\":" +
  json_string_or_null(intent.data_digest) +
  ",\"commitTempPath\":" +
  json_string(intent.commit_temp) +
  ",\"commitPath\":" +
  json_string(intent.commit_path) +
  ",\"commitDigest\":" +
  json_string(intent.commit_digest) +
  "}"
}

///|
/// 解析并严格校验 intent。任何不规范内容都让恢复器保留文件并报告，
/// 而不是猜测它属于哪个事务。
fn parse_txn_intent(
  text : String,
  expected_txn_id : String,
) -> TxnIntent raise DeltaError {
  let j = @json.parse(text) catch {
    _ => raise DeltaError::ParseError("intent is not valid JSON")
  }
  let format_version = match json_field_int(j, "formatVersion") {
    Some(v) => v
    None => raise DeltaError::ParseError("intent is missing integer formatVersion")
  }
  if format_version != 1 {
    raise DeltaError::ParseError(
      "unsupported intent formatVersion \{format_version} (this kernel understands 1)",
    )
  }
  let txn_id = match json_field_string(j, "txnId") {
    Some(s) => s
    None => raise DeltaError::ParseError("intent is missing txnId")
  }
  if !is_txn_id(txn_id) {
    raise DeltaError::ParseError("intent txnId is not 32 lowercase hex characters")
  }
  if txn_id != expected_txn_id {
    raise DeltaError::ParseError(
      "intent file name does not match its txnId (file '\{expected_txn_id}', intent '\{txn_id}')",
    )
  }
  let kind = match json_field_string(j, "kind") {
    Some(s) => s
    None => raise DeltaError::ParseError("intent is missing kind")
  }
  if kind != "create" && kind != "append" {
    raise DeltaError::ParseError("intent kind must be 'create' or 'append'")
  }
  let version = match json_field_int(j, "version") {
    Some(v) => v
    None => raise DeltaError::ParseError("intent is missing integer version")
  }
  if version < 0 {
    raise DeltaError::ParseError("intent version must be non-negative")
  }
  let commit_path = match json_field_string(j, "commitPath") {
    Some(s) => s
    None => raise DeltaError::ParseError("intent is missing commitPath")
  }
  if commit_path != "_delta_log/" + version_file_name(version) {
    raise DeltaError::ParseError(
      "intent commitPath '\{commit_path}' does not match target version \{version}",
    )
  }
  let commit_temp = match json_field_string(j, "commitTempPath") {
    Some(s) => s
    None => raise DeltaError::ParseError("intent is missing commitTempPath")
  }
  if commit_temp != commit_path + temp_name(txn_id) {
    raise DeltaError::ParseError(
      "intent commitTempPath is not the pinned temporary name for this transaction",
    )
  }
  if !is_safe_relative_path(commit_path) || !is_safe_relative_path(commit_temp) {
    raise DeltaError::ParseError("intent commit paths escape the table")
  }
  let commit_digest = match json_field_string(j, "commitDigest") {
    Some(s) => s
    None => raise DeltaError::ParseError("intent is missing commitDigest")
  }
  if !is_digest(commit_digest) {
    raise DeltaError::ParseError("intent commitDigest must be 64 lowercase hex characters")
  }
  let data_length = match json_field_int64(j, "dataLength") {
    Some(v) => v
    None => 0L
  }
  let data_temp = json_field_string(j, "dataTempPath")
  let data_path = json_field_string(j, "dataPath")
  let data_digest = json_field_string(j, "dataDigest")
  if kind == "append" {
    let temp = match data_temp {
      Some(s) => s
      None => raise DeltaError::ParseError("append intent is missing dataTempPath")
    }
    let final = match data_path {
      Some(s) => s
      None => raise DeltaError::ParseError("append intent is missing dataPath")
    }
    let digest = match data_digest {
      Some(s) => s
      None => raise DeltaError::ParseError("append intent is missing dataDigest")
    }
    if !is_safe_relative_path(temp) || !is_safe_relative_path(final) {
      raise DeltaError::ParseError("intent data paths escape the table")
    }
    if temp != final + temp_name(txn_id) {
      raise DeltaError::ParseError(
        "intent dataTempPath is not the pinned temporary name next to dataPath",
      )
    }
    if data_length <= 0L {
      raise DeltaError::ParseError("append intent dataLength must be positive")
    }
    if !is_digest(digest) {
      raise DeltaError::ParseError("intent dataDigest must be 64 lowercase hex characters")
    }
  }
  {
    format_version,
    txn_id,
    kind,
    version,
    data_temp,
    data_path,
    data_length,
    data_digest,
    commit_temp,
    commit_path,
    commit_digest,
  }
}

///|
fn read_txn_intent(root : String, txn_id : String) -> TxnIntent raise DeltaError {
  let path = intent_path(root, txn_id)
  if !@fs.path_exists(path) {
    raise DeltaError::NotFound("no transaction intent for '\{txn_id}' under '\{root}'")
  }
  parse_txn_intent(read_text(path), txn_id)
}

///|
/// 原子持久发布 intent：写完整清单 → 文件 fsync → 排他发布 → intents 目录
/// fsync。发布成功之前不会创建任何临时/正式数据文件。
fn publish_txn_intent(root : String, intent : TxnIntent) -> Unit raise DeltaError {
  // 只在目录尚不存在时创建并同步，避免每次提交都重复同步表根目录。
  if !@fs.path_exists(maintenance_dir(root)) {
    let _ = ensure_dir(maintenance_dir(root))
  }
  if !@fs.path_exists(intents_dir(root)) {
    let _ = ensure_dir(intents_dir(root))
  }
  let path = intent_path(root, intent.txn_id)
  let temp = path + intent_temp_name(intent.txn_id)
  let content = @utf8.encode(txn_intent_to_json(intent))
  publish_pinned_file(
    temp,
    path,
    content,
    intent.txn_id,
    intent.version,
    "intent-publish",
    false,
  ) catch {
    PublishOutcomeUnknown(txn, version, _path, phase, code) =>
      raise DeltaError::IoError(
        "intent publication for txn \{txn} (target version \{version}, phase \{phase}) is visible but its directory sync failed (platform error \{code}); no data has been created",
      )
    err => raise err
  }
  ignore(unlink_synced(temp) catch { _ => false })
}

///|
fn delete_txn_intent(root : String, txn_id : String) -> Bool raise DeltaError {
  let path = intent_path(root, txn_id)
  ignore(unlink_synced(path + intent_temp_name(txn_id)) catch { _ => false })
  unlink_synced(path)
}

///|
/// 枚举 `.moondelta/intents` 中的条目名（含未知文件，供恢复报告使用）。
fn list_intent_dir(root : String) -> Array[String] raise DeltaError {
  let dir = intents_dir(root)
  if !@fs.path_exists(dir) {
    return []
  }
  let names = @fs.read_dir(dir) catch {
    @fs.IOError(msg) => raise DeltaError::IoError(msg)
  }
  let out : Array[String] = []
  for name in names {
    out.push(name)
  }
  out.sort()
  out
}

///|
fn commit_txn_marker(commit_path : String) -> String? {
  let text = read_text(commit_path) catch { _ => return None }
  let mut first = ""
  for line in text.split("\n") {
    if line.char_length() > 0 {
      first = line.to_owned()
      break
    }
  }
  if first.char_length() == 0 {
    return None
  }
  let j = @json.parse(first) catch { _ => return None }
  match json_field(j, "commitInfo") {
    Some(info) => json_field_string(info, "moondeltaTxnId")
    None => None
  }
}

///|
/// 日志版本号是否从 0 连续。它是“可以推断某事务未提交”的必要前提，
/// 但不等于历史完整（解析与 checkpoint 校验在 `collect_protected_paths` 完成）。
fn log_versions_contiguous(root : String) -> Bool {
  let versions = list_versions(root) catch { _ => return false }
  if versions.length() == 0 || versions[0] != 0 {
    return false
  }
  let mut i = 1
  while i < versions.length() {
    if versions[i] != versions[i - 1] + 1 {
      return false
    }
    i = i + 1
  }
  true
}

/// A contiguous filename sequence is not enough evidence to infer that a
/// transaction was never committed. Every formal log file must contain at
/// least one parseable Delta action.
fn log_history_complete(root : String) -> Bool {
  if !log_versions_contiguous(root) {
    return false
  }
  let versions = list_versions(root) catch { _ => return false }
  for version in versions {
    let path = log_dir(root) + "/" + version_file_name(version)
    let text = @fs.read_file_to_bytes(path) catch { _ => return false }
    let content = @utf8.decode(text) catch { _ => return false }
    let mut actions = 0
    for line in content.replace_all(old="\r", new="").split("\n") {
      if line.trim().length() == 0 {
        continue
      }
      let _ = parse_action(line, version) catch { _ => return false }
      actions = actions + 1
    }
    if actions == 0 {
      return false
    }
  }
  true
}

///|
fn latest_log_version(root : String) -> Int32? {
  let versions = list_versions(root) catch { _ => return None }
  versions.last()
}

///|
/// 平台是否提供目录项持久性原语。Windows 只保留进程崩溃一致性，
/// 查询因此不能把“文件已同步”升级为“确认持久”。
#cfg(all(target="native", platform="windows"))
fn platform_confirms_directory_durability() -> Bool {
  false
}

///|
#cfg(not(all(target="native", platform="windows")))
fn platform_confirms_directory_durability() -> Bool {
  true
}

///|
/// 重新同步一个已可见 commit 及其引用的文件/目录。全部成功才允许把
/// `committed-unconfirmed` 升级为确认持久。
fn resync_visible_transaction(
  root : String,
  intent : TxnIntent,
) -> String? {
  sync_file_checked(root + "/" + intent.commit_path) catch {
    err => return Some("commit file sync failed: \{err}")
  }
  if !platform_confirms_directory_durability() {
    return Some(
      "this platform only provides process-crash consistency; directory-entry durability is not verified",
    )
  }
  sync_directory_entry(root + "/" + intent.commit_path) catch {
    err => return Some("commit directory sync failed: \{err}")
  }
  match intent.data_path {
    Some(relative) =>
      if @fs.path_exists(root + "/" + relative) {
        sync_file_checked(root + "/" + relative) catch {
          err => return Some("data file sync failed: \{err}")
        }
        sync_directory_entry(root + "/" + relative) catch {
          err => return Some("data directory sync failed: \{err}")
        }
      }
    None => ()
  }
  None
}

///|
/// 只读查询某事务的结果。不删除、不回滚、不重放任何文件。
pub fn query_transaction(
  root : String,
  txn_id : String,
) -> TransactionStatus raise DeltaError {
  if !is_txn_id(txn_id) {
    raise DeltaError::ParseError(
      "'\{txn_id}' is not a 32-character lowercase hex transaction id",
    )
  }
  let intent = read_txn_intent(root, txn_id) catch {
    DeltaError::ParseError(msg) =>
      return {
        txn_id,
        version: -1,
        state: TxnUnresolved,
        phase: "intent-read",
        data_path: "",
        commit_path: "",
        detail: "intent is unreadable or corrupt: \{msg}",
      }
    err => raise err
  }
  if (is_symlink_checked(intent_path(root, txn_id)) catch { _ => true }) {
    return {
      txn_id,
      version: intent.version,
      state: TxnUnresolved,
      phase: "intent-symlink",
      data_path: "",
      commit_path: intent.commit_path,
      detail: "intent path is a symbolic link; transaction identity cannot be trusted",
    }
  }
  let commit_abs = root + "/" + intent.commit_path
  let data_path = match intent.data_path {
    Some(p) => p
    None => ""
  }
  if @fs.path_exists(commit_abs) {
    let bytes = @fs.read_file_to_bytes(commit_abs) catch {
      @fs.IOError(msg) => raise DeltaError::IoError(msg)
    }
    let digest = sha256_hex(bytes)
    if digest == intent.commit_digest {
      match resync_visible_transaction(root, intent) {
        Some(failure) =>
          return {
            txn_id,
            version: intent.version,
            state: TxnCommittedUnconfirmed,
            phase: "commit-visible-resync-failed",
            data_path,
            commit_path: intent.commit_path,
            detail: "commit matches the intent but is not re-confirmed durable: \{failure}",
          }
        None =>
          return {
            txn_id,
            version: intent.version,
            state: TxnCommitted,
            phase: "commit-visible-and-resynced",
            data_path,
            commit_path: intent.commit_path,
            detail: "commit content matches the intent digest and file/directory sync succeeded",
          }
      }
    }
    match commit_txn_marker(commit_abs) {
      Some(marker) =>
        if marker == txn_id {
          return {
            txn_id,
            version: intent.version,
            state: TxnUnresolved,
            phase: "commit-digest-mismatch",
            data_path,
            commit_path: intent.commit_path,
            detail: "the commit at the target version names this transaction but does not match its digest",
          }
        }
      None => ()
    }
    return {
      txn_id,
      version: intent.version,
      state: TxnConflict,
      phase: "version-occupied-by-other-transaction",
      data_path,
      commit_path: intent.commit_path,
      detail: "target version is occupied by a different transaction; this transaction did not win",
    }
  }
  if !log_history_complete(root) {
    return {
      txn_id,
      version: intent.version,
      state: TxnUnresolved,
      phase: "log-incomplete",
      data_path,
      commit_path: intent.commit_path,
      detail: "the JSON log is not contiguous from version 0, so absence of the commit proves nothing",
    }
  }
  let latest = match latest_log_version(root) {
    Some(v) => v
    None => -1
  }
  if latest >= intent.version {
    return {
      txn_id,
      version: intent.version,
      state: TxnUnresolved,
      phase: "log-advanced-without-this-commit",
      data_path,
      commit_path: intent.commit_path,
      detail: "a later version exists without this transaction's commit",
    }
  }
  {
    txn_id,
    version: intent.version,
    state: TxnNotCommitted,
    phase: "commit-absent-log-contiguous",
    data_path,
    commit_path: intent.commit_path,
    detail: "no commit exists at the target version and the JSON log is contiguous from version 0",
  }
}
