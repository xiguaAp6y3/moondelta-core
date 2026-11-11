///|
/// 保守恢复与受管清理（S3）。
///
/// 原则：
/// - 只有 intent 精确列出的临时文件/正式文件才可能被删除；未知文件、软链接、
///   越界路径、损坏 intent 一律保留并报告。
/// - 正式 Parquet 只有在日志自版本 0 起连续可解析、路径/长度/摘要吻合、
///   且不在历史或 checkpoint 保护集合中时才删。
/// - dry-run 是默认动作；只有显式标记为受管、且调用方要求 apply 时才真正删除。
/// - 删除顺序：数据/临时文件（并同步父目录）→ 最后 intent（并同步维护目录）。
///   任一步失败保留 intent 供下次重试；提交成功不会因清理失败被改报失败。

///|
struct RecoverySink {
  entries : Array[RecoveryEntry]
  mut deleted : Int32
}

///|
fn sink_keep(
  sink : RecoverySink,
  txn_id : String,
  version : Int32,
  path : String,
  reason : String,
) -> Unit {
  sink.entries.push({ txn_id, version, path, action: "keep", reason, })
}

///|
fn sink_delete(
  sink : RecoverySink,
  txn_id : String,
  version : Int32,
  path : String,
  reason : String,
  apply : Bool,
) -> Bool {
  if !apply {
    sink.entries.push({ txn_id, version, path, action: "would-delete", reason, })
    return true
  }
  let mut failure : String? = None
  let removed = unlink_synced(path) catch {
    err => {
      failure = Some(describe_error(err))
      false
    }
  }
  match failure {
    Some(message) => {
      sink_keep(sink, txn_id, version, path, "delete failed: " + message)
      false
    }
    None => {
      if removed {
        sink.entries.push({ txn_id, version, path, action: "delete", reason, })
        sink.deleted = sink.deleted + 1
      } else {
        sink.entries.push(
          { txn_id, version, path, action: "delete", reason: reason + " (already absent)", },
        )
      }
      true
    }
  }
}

///|
fn count_entries(entries : Array[RecoveryEntry], action : String) -> Int32 {
  let mut total = 0
  for entry in entries {
    if entry.action == action {
      total = total + 1
    }
  }
  total
}

///|
/// 从全部可保留 JSON 历史与受支持 checkpoint 构造保护集合。
/// 返回 (保护路径, 日志是否完整可解析)。任何解析失败都让日志判为不完整。
fn collect_protected_paths(root : String) -> (Array[String], Bool) {
  let protected_paths : Array[String] = []
  let versions = list_versions(root) catch { _ => return (protected_paths, false) }
  if versions.length() == 0 || versions[0] != 0 {
    return (protected_paths, false)
  }
  let mut i = 0
  while i < versions.length() {
    if i > 0 && versions[i] != versions[i - 1] + 1 {
      return (protected_paths, false)
    }
    let path = log_dir(root) + "/" + version_file_name(versions[i])
    let text = read_text(path) catch { _ => return (protected_paths, false) }
    for line in text.replace_all(old="\r", new="").split("\n") {
      if line.char_length() == 0 {
        continue
      }
      let action = parse_action(line, versions[i]) catch {
        _ => return (protected_paths, false)
      }
      match action {
        AddAction(f) =>
          if !protected_paths.contains(f.path) {
            protected_paths.push(f.path)
          }
        RemoveAction(r) =>
          if !protected_paths.contains(r.path) {
            protected_paths.push(r.path)
          }
        _ => ()
      }
    }
    i = i + 1
  }
  let checkpoints = list_checkpoint_versions(root) catch {
    _ => return (protected_paths, false)
  }
  for checkpoint_version in checkpoints {
    let path = log_dir(root) + "/" + checkpoint_file_name(checkpoint_version)
    let (_, _, files) = parse_checkpoint(path) catch {
      _ => return (protected_paths, false)
    }
    for f in files {
      if !protected_paths.contains(f.path) {
        protected_paths.push(f.path)
      }
    }
  }
  (protected_paths, true)
}

///|
fn file_digest(path : String) -> String? {
  let bytes = @fs.read_file_to_bytes(path) catch { _ => return None }
  Some(sha256_hex(bytes))
}

/// Reject a symlink in any path component, including directories. Checking
/// only the final name would allow a relative intent path to escape the table.
fn path_has_symlink_component(root : String, relative : String) -> Bool {
  let mut current = root
  for segment in relative.split("/") {
    current = current + "/" + segment.to_string()
    if @fs.path_exists(current) &&
      (is_symlink_checked(current) catch { _ => true }) {
      return true
    }
  }
  false
}

///|
fn evaluate_intent(
  root : String,
  intent : TxnIntent,
  protected_paths : Array[String],
  log_complete : Bool,
  apply : Bool,
  sink : RecoverySink,
) -> Unit {
  let txn = intent.txn_id
  let commit_abs = root + "/" + intent.commit_path
  let commit_exists = @fs.path_exists(commit_abs)
  let mut committed = false
  let mut unresolved : String? = None
  if commit_exists {
    let digest = file_digest(commit_abs)
    match digest {
      Some(d) =>
        if d == intent.commit_digest {
          committed = true
        } else {
          match commit_txn_marker(commit_abs) {
            Some(marker) =>
              if marker == txn {
                unresolved = Some(
                  "the commit at the target version names this transaction but does not match its digest",
                )
              }
            None => ()
          }
        }
      None =>
        unresolved = Some("the commit at the target version cannot be read")
    }
  } else if !log_complete {
    unresolved = Some("the JSON log is incomplete; absence of the commit proves nothing")
  } else {
    let latest = match latest_log_version(root) {
      Some(v) => v
      None => -1
    }
    if latest >= intent.version {
      unresolved = Some("a later version exists without this transaction's commit")
    }
  }

  // 软链接一律保留：不跟随、不删除，避免清理器被诱导删除表外目标。
  let mut symlinked : String? = None
  let probe : Array[String] = [intent.commit_temp]
  if intent.data_temp is Some(p) {
    probe.push(p)
  }
  if intent.data_path is Some(p) {
    probe.push(p)
  }
  for relative in probe {
    if path_has_symlink_component(root, relative) {
      symlinked = Some(relative)
      break
    }
  }
  match symlinked {
    Some(relative) => {
      sink_keep(sink, txn, intent.version, relative, "symbolic link is never deleted or followed")
      sink_keep(sink, txn, intent.version, intent_path(root, txn), "linked transaction entry kept for manual review")
      return
    }
    None => ()
  }

  match unresolved {
    Some(reason) => {
      sink_keep(sink, txn, intent.version, intent_path(root, txn), "unresolved: " + reason)
      return
    }
    None => ()
  }

  let mut clean = true
  // 临时名只属于本事务；提交确认后可无条件清理。
  clean =
    sink_delete(
      sink,
      txn,
      intent.version,
      root + "/" + intent.commit_temp,
      "transaction-pinned temporary commit name",
      apply,
    ) &&
    clean
  match intent.data_temp {
    Some(relative) =>
      clean =
        sink_delete(
          sink,
          txn,
          intent.version,
          root + "/" + relative,
          "transaction-pinned temporary data name",
          apply,
        ) &&
        clean
    None => ()
  }
  match intent.data_path {
    Some(relative) =>
      if committed {
        let absolute = root + "/" + relative
        let mut consistent = @fs.path_exists(absolute)
        if consistent {
          let size = file_size_checked(absolute) catch { _ => -1L }
          let digest = file_digest(absolute)
          let matches = match digest {
            Some(d) => d == intent.data_digest.unwrap_or("")
            None => false
          }
          consistent = size == intent.data_length && matches
        }
        if consistent {
          sink_keep(
            sink,
            txn,
            intent.version,
            absolute,
            "referenced by this transaction's committed version \{intent.version}",
          )
        } else {
          sink_keep(
            sink,
            txn,
            intent.version,
            absolute,
            "committed version's data file is missing or does not match the intent; evidence preserved",
          )
          clean = false
        }
      } else if !log_complete {
        sink_keep(
          sink,
          txn,
          intent.version,
          root + "/" + relative,
          "log is incomplete: an unreferenced-looking file cannot be proven unreferenced",
        )
        clean = false
      } else if protected_paths.contains(relative) {
        sink_keep(
          sink,
          txn,
          intent.version,
          root + "/" + relative,
          "protected_paths by a reachable JSON history or checkpoint reference",
        )
      } else {
        let absolute = root + "/" + relative
        if @fs.path_exists(absolute) {
          let size = file_size_checked(absolute) catch {
            _ => -1L
          }
          let digest = file_digest(absolute)
          let matches = match digest {
            Some(d) => d == intent.data_digest.unwrap_or("")
            None => false
          }
          if size == intent.data_length && matches {
            clean =
              sink_delete(
                sink,
                txn,
                intent.version,
                absolute,
                "unreferenced final data file matching this transaction's length and digest",
                apply,
              ) &&
              clean
          } else {
            sink_keep(
              sink,
              txn,
              intent.version,
              absolute,
              "final data file does not match the intent's length and digest; evidence preserved",
            )
            clean = false
          }
        }
      }
    None => ()
  }
  if clean {
    clean =
      sink_delete(
        sink,
        txn,
        intent.version,
        intent_path(root, txn),
        "transaction intent removed after its files were settled",
        apply,
      ) &&
      clean
  } else {
    sink_keep(
      sink,
      txn,
      intent.version,
      intent_path(root, txn),
      "intent retained because a file for this transaction was not settled",
    )
  }
}

///|
fn count_path_uses(intents : Array[TxnIntent], path : String) -> Int32 {
  let mut total = 0
  for intent in intents {
    if intent.data_path is Some(p) && p == path {
      total = total + 1
    }
    if intent.commit_path == path {
      total = total + 1
    }
  }
  total
}

///|
/// 报告表根与 `_delta_log` 中不属于任何有效 intent 的临时/旧命名文件。
/// 这些文件永不自动删除，只作为证据列出。
fn report_unowned_temporaries(
  root : String,
  owned : Array[String],
  intents : Array[TxnIntent],
  sink : RecoverySink,
) -> Unit {
  let directories = [root, log_dir(root)]
  for directory in directories {
    if !@fs.path_exists(directory) {
      continue
    }
    let names = @fs.read_dir(directory) catch { _ => continue }
    for name in names {
      if !name.contains(".moondelta-tmp-") && !name.has_prefix("mdt") {
        continue
      }
      let relative = if directory == root {
        name
      } else {
        "_delta_log/" + name
      }
      if owned.contains(relative) {
        continue
      }
      let mut known = false
      for intent in intents {
        if intent.data_temp is Some(p) && p == relative {
          known = true
        }
        if intent.commit_temp == relative {
          known = true
        }
      }
      if known {
        continue
      }
      sink_keep(
        sink,
        "",
        -1,
        directory + "/" + name,
        "unowned temporary / legacy name without an intent; never deleted by wildcard",
      )
    }
  }
}

///|
/// 多部分 checkpoint（`<v>.checkpoint.<part>.<total>.parquet`）本实现不解析。
/// 见到任何非单文件 checkpoint 的 `.checkpoint.` 名称都按“历史证据不完整”处理，
/// 让清理器只报告不删除。
fn has_multipart_checkpoint(root : String) -> Bool {
  let dir = log_dir(root)
  if !@fs.path_exists(dir) {
    return false
  }
  let names = @fs.read_dir(dir) catch { _ => return true }
  for name in names {
    if name.contains(".checkpoint.") && !name.has_suffix(".checkpoint.parquet") {
      return true
    }
  }
  false
}

///|
/// 清理器只在“本实现能完整理解该表当前状态”时删除文件。
/// reader v2+、未知 action、缺失 protocol/metaData 等一律让状态不可判定，
/// 从而把删除行为降级为只报告。
fn table_state_is_supported(root : String) -> Bool {
  let table = DeltaTable::open(root) catch { _ => return false }
  let _ = table.snapshot() catch { _ => return false }
  true
}

///|
/// 在已持独占锁的前提下扫描并（按需）执行清理。
fn plan_recovery(root : String, apply : Bool) -> RecoveryReport raise DeltaError {
  let (protected_paths, collected_complete) = collect_protected_paths(root)
  let mut log_complete = collected_complete
  let sink : RecoverySink = { entries: [], deleted: 0 }
  if has_multipart_checkpoint(root) {
    log_complete = false
    sink_keep(
      sink,
      "",
      -1,
      root,
      "multi-part or nonstandard checkpoint names are not interpreted; deletion is disabled",
    )
  }
  if log_complete && !table_state_is_supported(root) {
    log_complete = false
    sink_keep(
      sink,
      "",
      -1,
      root,
      "table state is outside the supported reader v1 subset; all deletions are disabled",
    )
  }
  let names = list_intent_dir(root)
  let valid : Array[TxnIntent] = []
  for name in names {
    if !name.has_suffix(".json") || name.char_length() != 37 {
      sink_keep(
        sink,
        "",
        -1,
        intents_dir(root) + "/" + name,
        "unknown file in the maintenance directory; preserved",
      )
      continue
    }
    let txn_id = name.substring(start=0, end=32)
    if !is_txn_id(txn_id) {
      sink_keep(
        sink,
        "",
        -1,
        intents_dir(root) + "/" + name,
        "intent file name is not a transaction id; preserved",
      )
      continue
    }
    let path = intents_dir(root) + "/" + name
    if (is_symlink_checked(path) catch { _ => true }) {
      sink_keep(sink, txn_id, -1, path, "symbolic link in the maintenance directory; preserved")
      continue
    }
    let parsed : TxnIntent? = Some(parse_txn_intent(read_text(path), txn_id)) catch {
      DeltaError::ParseError(message) => {
        sink_keep(sink, txn_id, -1, path, "intent is invalid: " + message)
        None
      }
      DeltaError::IoError(message) => {
        sink_keep(sink, txn_id, -1, path, "intent cannot be read: " + message)
        None
      }
      err => raise err
    }
    match parsed {
      Some(intent) => valid.push(intent)
      None => ()
    }
  }
  let owned : Array[String] = []
  for intent in valid {
    if intent.data_temp is Some(p) {
      owned.push(p)
    }
    if intent.data_path is Some(p) {
      owned.push(p)
    }
    owned.push(intent.commit_temp)
  }
  report_unowned_temporaries(root, owned, valid, sink)
  for intent in valid {
    let aliased = match intent.data_path {
      Some(p) => count_path_uses(valid, p) > 1
      None => count_path_uses(valid, intent.commit_path) > 1
    }
    if aliased {
      sink_keep(
        sink,
        intent.txn_id,
        intent.version,
        intent_path(root, intent.txn_id),
        "path alias: this transaction shares a target path with another intent",
      )
      continue
    }
    evaluate_intent(root, intent, protected_paths, log_complete, apply, sink)
  }
  let kept = count_entries(sink.entries, "keep")
  {
    dry_run: !apply,
    managed: managed_table(root),
    applied: apply,
    lock_busy: false,
    log_complete,
    blocked: None,
    entries: sink.entries,
    deleted: sink.deleted,
    kept,
  }
}

///|
fn empty_recovery_report(dry_run : Bool, managed : Bool) -> RecoveryReport {
  {
    dry_run,
    managed,
    applied: false,
    lock_busy: false,
    log_complete: false,
    blocked: None,
    entries: [],
    deleted: 0,
    kept: 0,
  }
}

///|
/// 表是否被用户显式标记为“全部 writer 遵守 moondelta 维护锁”的受管表。
/// 未标记的普通 Delta 表永远只能 dry-run。
pub fn managed_table(root : String) -> Bool {
  let path = policy_path(root)
  if !@fs.path_exists(path) {
    return false
  }
  let text = read_text(path) catch { _ => return false }
  let j = @json.parse(text) catch { _ => return false }
  match json_field_int(j, "formatVersion") {
    Some(1) => ()
    _ => return false
  }
  match json_field_bool(j, "managed") {
    Some(value) => value
    None => false
  }
}

///|
/// 显式启用/停用受管策略。启用是“该目录下的写入方都遵守维护锁”的人工承诺，
/// 不是根据目录内容或文件年龄自动推断的。
pub fn set_managed_table(root : String, managed : Bool) -> Unit raise DeltaError {
  if !@fs.path_exists(log_dir(root)) {
    raise DeltaError::NotFound("'\{root}' is not a Delta table (no _delta_log directory)")
  }
  let lock = acquire_exclusive_lock(root)
  errdefer release_table_lock(lock)
  let _ = ensure_dir(maintenance_dir(root))
  if managed {
    atomic_write_file(
      policy_path(root),
      @utf8.encode("{\"formatVersion\":1,\"managed\":true}"),
    ) catch {
      AlreadyExists(_) => raise DeltaError::IoError(
          "managed policy file '\{policy_path(root)}' already exists but could not be replaced",
        )
      err => raise err
    }
  } else {
    ignore(unlink_synced(policy_path(root)) catch { _ => false })
  }
  release_table_lock(lock)
}

///|
/// 受管 writer 在每次操作前尝试一次非阻塞恢复独占锁：取得则先清理，锁忙则跳过，
/// 绝不等待或降级为强制清理。
fn auto_recover_managed(root : String) -> Unit {
  if !managed_table(root) {
    return
  }
  let lock = try_acquire_exclusive_lock(root) catch { _ => return }
  match lock {
    None => ()
    Some(handle) => {
      if managed_table(root) {
        ignore(plan_recovery(root, true) catch { _ => empty_recovery_report(false, true) })
      }
      release_table_lock(handle)
    }
  }
}

///|
/// 保守恢复入口。默认 `dry_run = true`；只有显式受管表在 `dry_run = false`
/// 时才可能真正删除文件。锁忙返回明确结果，不等待。
pub fn recover_table(root : String, dry_run : Bool) -> RecoveryReport raise DeltaError {
  if !@fs.path_exists(log_dir(root)) {
    raise DeltaError::NotFound("'\{root}' is not a Delta table (no _delta_log directory)")
  }
  let managed_before_lock = managed_table(root)
  let lock = try_acquire_exclusive_lock(root)
  match lock {
    None =>
      {
        dry_run,
        managed: managed_before_lock,
        applied: false,
        lock_busy: true,
        log_complete: false,
        blocked: Some(
          "the table maintenance lock is held by an active writer or recoverer; no files were inspected or removed",
        ),
        entries: [],
        deleted: 0,
        kept: 0,
      }
    Some(handle) => {
      let managed = managed_table(root)
      let apply = !dry_run && managed
      let report = plan_recovery(root, apply) catch {
        err => {
          release_table_lock(handle)
          raise err
        }
      }
      release_table_lock(handle)
      if !dry_run && !managed {
        {
          dry_run: report.dry_run,
          managed: report.managed,
          applied: report.applied,
          lock_busy: report.lock_busy,
          log_complete: report.log_complete,
          blocked: Some(
            "apply requires an explicitly managed table (run 'delta manage <root>' during a stop-writes maintenance window); this run only previewed candidates",
          ),
          entries: report.entries,
          deleted: report.deleted,
          kept: report.kept,
        }
      } else {
        report
      }
    }
  }
}
