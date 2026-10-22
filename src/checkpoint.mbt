///|
/// checkpoint parquet 解析与快照恢复（M3）。
///
/// checkpoint 每行恰好一个 action（各 action 列中恰一列非空）。不同 writer
/// （Spark 经典 v1 / delta-rs）对列的可空性标注不同，因此这里不依赖声明式
/// schema，而是按 shredded 叶列的实际 def/rep levels 逐记录解析：
/// - 标量叶：def == 该叶 max_definition_level ⇔ 值存在；
/// - map/list 叶组：首条目 rep == 外层深度，后续条目 rep == 组最大深度，
///   "字段存在但无条目"占 1 个 def 较低的槽位；
/// - action 是否出现：以该 action 的规范必填标量（add.path 等）为判据。
/// 未解释的叶（tags / deletionVector / sidecar 等）按记录消耗槽位保持游标同步。

///|
priv struct CpCursor {
  c : LeafCursor
  mut token : Int
}

///|
fn cp_group(cursors : Array[CpCursor], action : String) -> Array[CpCursor] {
  let out : Array[CpCursor] = []
  for cc in cursors {
    if cc.c.leaf.path.length() > 0 && cc.c.leaf.path[0] == action {
      out.push(cc)
    }
  }
  out
}

///|
/// 标量叶：路径最后一个分量等于字段名（不限深度，如 metaData.format.provider）
fn cp_find_scalar(group : Array[CpCursor], name : String) -> CpCursor? {
  for cc in group {
    let n = cc.c.leaf.path.length()
    if n >= 2 && cc.c.leaf.path[n - 1] == name {
      return Some(cc)
    }
  }
  None
}

///|
/// list 叶：path = [action, <字段>, element|item]
fn cp_find_list(group : Array[CpCursor], name : String) -> CpCursor? {
  for cc in group {
    let n = cc.c.leaf.path.length()
    if n >= 3 {
      let last = cc.c.leaf.path[n - 1]
      if (last == "element" || last == "item") && cc.c.leaf.path[n - 2] == name {
        return Some(cc)
      }
    }
  }
  None
}

///|
/// map 叶：path = [action, <字段>, key|value]
fn cp_find_map(
  group : Array[CpCursor],
  name : String,
  child : String,
) -> CpCursor? {
  for cc in group {
    let n = cc.c.leaf.path.length()
    if n >= 3 && cc.c.leaf.path[n - 1] == child && cc.c.leaf.path[n - 2] == name {
      return Some(cc)
    }
  }
  None
}

///|
/// 消耗一个标量叶的当前槽位并返回其值（def == max ⇔ 值存在）
fn cp_take_scalar(cc : CpCursor, rec : Int) -> Cell raise DeltaError {
  if cc.c.at_end() {
    raise DeltaError::ParseError(
      "checkpoint column '\{path_text(cc.c.leaf.path)}' ended early",
    )
  }
  let def = cc.c.peek_def()
  let cell = if def == cc.c.leaf.max_definition_level {
    shred_value_to_cell(cc.c.leaf.values[cc.c.pos])
  } else {
    CellNull
  }
  cc.c.pos = cc.c.pos + 1
  cc.token = rec
  cell
}

///|
/// 消耗标量槽位；字段在文件中不存在时返回 None
fn cp_scalar(
  group : Array[CpCursor],
  name : String,
  rec : Int,
) -> Cell? raise DeltaError {
  match cp_find_scalar(group, name) {
    Some(cc) => Some(cp_take_scalar(cc, rec))
    None => None
  }
}

///|
/// 消耗一个记录的槽位（标量 1 个；map/list 叶按 rep 边界走完本记录）
fn cp_skip_record(cc : CpCursor, rec : Int) -> Unit raise DeltaError {
  if cc.c.at_end() {
    raise DeltaError::ParseError(
      "checkpoint column '\{path_text(cc.c.leaf.path)}' ended early",
    )
  }
  cc.c.pos = cc.c.pos + 1
  let max_rep = cc.c.leaf.max_repetition_level
  if max_rep > 0 {
    while !cc.c.at_end() && cc.c.peek_rep() == max_rep {
      cc.c.pos = cc.c.pos + 1
    }
  }
  cc.token = rec
}

///|
fn cp_cell_str(c : Cell?) -> String? {
  match c {
    Some(CellStr(s)) => Some(s)
    _ => None
  }
}

///|
/// 消耗一个 list 叶组本记录的槽位；存在条目时返回字符串列表
fn cp_list_strings(
  group : Array[CpCursor],
  name : String,
  rec : Int,
) -> Array[String]? raise DeltaError {
  let cc = match cp_find_list(group, name) {
    Some(c) => c
    None => return None
  }
  if cc.c.at_end() {
    raise DeltaError::ParseError(
      "checkpoint column '\{path_text(cc.c.leaf.path)}' ended early",
    )
  }
  let out : Array[String] = []
  let present_def = cc.c.leaf.max_definition_level - 1
  let def0 = cc.c.peek_def()
  if def0 <= present_def {
    // list 为 null（def 更低）或存在但为空（def == present_def）：各占 1 槽
    cc.c.pos = cc.c.pos + 1
    cc.token = rec
    return Some(out)
  }
  let max_rep = cc.c.leaf.max_repetition_level
  let mut first = true
  while !cc.c.at_end() {
    let rep = cc.c.peek_rep()
    if !first && rep < max_rep {
      break
    }
    if cc.c.peek_def() == cc.c.leaf.max_definition_level {
      match shred_value_to_cell(cc.c.leaf.values[cc.c.pos]) {
        CellStr(s) => out.push(s)
        _ =>
          raise DeltaError::ParseError(
            "checkpoint list '\{name}' has a non-string element",
          )
      }
    } else {
      out.push("")
    }
    cc.c.pos = cc.c.pos + 1
    first = false
  }
  cc.token = rec
  Some(out)
}

///|
/// 消耗一个 string→string map 叶组本记录的槽位；存在条目时返回键值对
fn cp_map_pairs(
  group : Array[CpCursor],
  name : String,
  rec : Int,
) -> Array[(String, String)]? raise DeltaError {
  let kcc = match cp_find_map(group, name, "key") {
    Some(c) => c
    None => return None
  }
  let vcc = match cp_find_map(group, name, "value") {
    Some(c) => c
    None => return None
  }
  if kcc.c.at_end() || vcc.c.at_end() {
    raise DeltaError::ParseError("checkpoint map '\{name}' ended early")
  }
  let out : Array[(String, String)] = []
  let present_def = kcc.c.leaf.max_definition_level - 1
  let def0 = kcc.c.peek_def()
  if def0 <= present_def {
    // map 为 null 或存在但为空：key/value 各占 1 槽
    kcc.c.pos = kcc.c.pos + 1
    vcc.c.pos = vcc.c.pos + 1
    kcc.token = rec
    vcc.token = rec
    return Some(out)
  }
  let max_rep = kcc.c.leaf.max_repetition_level
  let mut first = true
  while !kcc.c.at_end() && !vcc.c.at_end() {
    let rep = kcc.c.peek_rep()
    if !first && rep < max_rep {
      break
    }
    let key = match shred_value_to_cell(kcc.c.leaf.values[kcc.c.pos]) {
      CellStr(s) => s
      _ =>
        raise DeltaError::ParseError(
          "checkpoint map '\{name}' has a non-string key",
        )
    }
    let value = if vcc.c.peek_def() == vcc.c.leaf.max_definition_level {
      match shred_value_to_cell(vcc.c.leaf.values[vcc.c.pos]) {
        CellStr(s) => s
        _ => ""
      }
    } else {
      ""
    }
    kcc.c.pos = kcc.c.pos + 1
    vcc.c.pos = vcc.c.pos + 1
    out.push((key, value))
    first = false
  }
  kcc.token = rec
  vcc.token = rec
  Some(out)
}

///|
fn checkpoint_file_name(version : Int32) -> String {
  let s = version.to_string()
  let sb = StringBuilder()
  let mut i = 0
  while i < 20 - s.length() {
    sb.write_char('0')
    i = i + 1
  }
  sb.write_string(s)
  sb.write_string(".checkpoint.parquet")
  sb.to_string()
}

///|
/// 列出 _delta_log 中单文件 checkpoint 的版本（升序）。
/// 多部分 checkpoint（`<v>.checkpoint.<p>.parquet`）显式不支持并报错。
pub fn list_checkpoint_versions(root : String) -> Array[Int32] raise DeltaError {
  let dir = log_dir(root)
  if !@fs.path_exists(dir) {
    return []
  }
  let names = @fs.read_dir(dir) catch {
    @fs.IOError(msg) => raise DeltaError::IoError(msg)
  }
  let out : Array[Int32] = []
  for name in names {
    if name.has_suffix(".checkpoint.parquet") {
      let stem_len = name.char_length() - ".checkpoint.parquet".char_length()
      if stem_len == 20 {
        let mut ok = true
        let mut v = 0
        let mut i = 0
        for c in name {
          if i < 20 {
            let d = c.to_int() - 48
            if d < 0 || d > 9 {
              ok = false
              break
            }
            v = v * 10 + d
          }
          i = i + 1
        }
        if ok && !out.contains(v) {
          out.push(v)
        }
      } else {
        raise DeltaError::Unsupported(
          "multi-part or nonstandard checkpoint file '\{name}' is not supported (single-file <20-digit>.checkpoint.parquet only)",
        )
      }
    }
  }
  out.sort()
  out
}

///|
/// 解析 checkpoint parquet，返回其中的 protocol / metaData / add 集合。
pub fn parse_checkpoint(
  path : String,
) -> (Protocol?, TableInfo?, Array[AddFile]) raise DeltaError {
  let data = @fs.read_file_to_bytes(path) catch {
    _ => raise DeltaError::IoError("cannot read '\{path}'")
  }
  let (leaves, row_count) = @parquet.read_bytes_shredded(data) catch {
    @parquet.Unsupported(msg) =>
      raise DeltaError::Unsupported("cannot read '\{path}': \{msg}")
    @parquet.InvalidData(msg) =>
      raise DeltaError::ParseError("corrupt parquet '\{path}': \{msg}")
    @parquet.Io(msg) =>
      raise DeltaError::IoError("cannot read '\{path}': \{msg}")
  }
  let cursors : Array[CpCursor] = []
  for leaf in leaves {
    cursors.push({ c: { leaf, pos: 0 }, token: -1 })
  }
  let mut protocol : Protocol? = None
  let mut metadata : TableInfo? = None
  let files : Array[AddFile] = []
  let removed : Array[String] = []
  let mut rec = 0
  while rec < row_count {
    // ---- protocol ----
    let min_r = cp_scalar(cp_group(cursors, "protocol"), "minReaderVersion", rec)
    let vr = match min_r {
      Some(CellI32(x)) => Some(x)
      _ => None
    }
    if vr is Some(_) {
      let min_w = cp_scalar(cp_group(cursors, "protocol"), "minWriterVersion", rec)
      let vw = match min_w {
        Some(CellI32(x)) => x
        _ =>
          raise DeltaError::ParseError(
            "checkpoint protocol.minWriterVersion is not an int",
          )
      }
      let rf = match cp_list_strings(
        cp_group(cursors, "protocol"), "readerFeatures", rec,
      ) {
        Some(items) => items
        None => []
      }
      let wf = match cp_list_strings(
        cp_group(cursors, "protocol"), "writerFeatures", rec,
      ) {
        Some(items) => items
        None => []
      }
      protocol = Some({
        min_reader_version: vr.unwrap(),
        min_writer_version: vw,
        reader_features: rf,
        writer_features: wf,
      })
    }
    // ---- metaData ----
    let meta_id = cp_scalar(cp_group(cursors, "metaData"), "id", rec)
    if meta_id is Some(CellStr(_)) {
      let id = match cp_cell_str(meta_id) {
        Some(s) => s
        None => ""
      }
      let schema_string = match cp_cell_str(
        cp_scalar(cp_group(cursors, "metaData"), "schemaString", rec),
      ) {
        Some(s) => s
        None =>
          raise DeltaError::ParseError(
            "checkpoint metaData.schemaString is missing",
          )
      }
      let provider = match cp_cell_str(
        cp_scalar(cp_group(cursors, "metaData"), "provider", rec),
      ) {
        Some(s) => s
        None => "parquet"
      }
      let partition_columns = match cp_list_strings(
        cp_group(cursors, "metaData"), "partitionColumns", rec,
      ) {
        Some(items) => items
        None => []
      }
      let configuration = match cp_map_pairs(
        cp_group(cursors, "metaData"), "configuration", rec,
      ) {
        Some(pairs) => pairs
        None => []
      }
      metadata = Some({
        id,
        name: None,
        schema: parse_schema(schema_string),
        partition_columns,
        format_provider: provider,
        configuration,
      })
    }
    // ---- add ----
    let add_path = cp_scalar(cp_group(cursors, "add"), "path", rec)
    if add_path is Some(CellStr(_)) {
      let p = match cp_cell_str(add_path) {
        Some(s) => s
        None => ""
      }
      let size = match cp_scalar(cp_group(cursors, "add"), "size", rec) {
        Some(CellI64(x)) => x
        _ => 0L
      }
      let modification_time = match cp_scalar(
        cp_group(cursors, "add"), "modificationTime", rec,
      ) {
        Some(CellI64(x)) => x
        _ => 0L
      }
      let data_change = match cp_scalar(cp_group(cursors, "add"), "dataChange", rec) {
        Some(CellBool(b)) => b
        _ => true
      }
      let stats = cp_cell_str(cp_scalar(cp_group(cursors, "add"), "stats", rec))
      let partition_values = match cp_map_pairs(
        cp_group(cursors, "add"), "partitionValues", rec,
      ) {
        Some(pairs) => pairs
        None => []
      }
      files.push({
        path: decode_add_path(p),
        partition_values,
        size,
        modification_time,
        data_change,
        stats,
        has_deletion_vector: false,
      })
    }
    // ---- remove（收集被剔除路径） ----
    let remove_path = cp_scalar(cp_group(cursors, "remove"), "path", rec)
    match cp_cell_str(remove_path) {
      Some(s) => removed.push(s)
      None => ()
    }
    // ---- 全组兜底：未解释/漏掉的叶按记录消耗，保持游标同步 ----
    for cc in cursors {
      if cc.token != rec {
        cp_skip_record(cc, rec)
      }
    }
    rec = rec + 1
  }
  let kept : Array[AddFile] = []
  for f in files {
    let mut removed_f = false
    for rp in removed {
      if f.path == rp {
        removed_f = true
      }
    }
    if !removed_f {
      kept.push(f)
    }
  }
  (protocol, metadata, kept)
}
