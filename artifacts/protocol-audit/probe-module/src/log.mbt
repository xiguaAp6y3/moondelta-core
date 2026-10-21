///|
/// `_delta_log` 目录路径
pub fn log_dir(root : String) -> String {
  root + "/_delta_log"
}

///|
/// 版本号 → 日志文件名（20 位零填充，Delta 规范）
pub fn version_file_name(version : Int32) -> String {
  let s = version.to_string()
  let mut pad = ""
  let mut i = 0
  while i < 20 - s.length() {
    pad = pad + "0"
    i = i + 1
  }
  pad + s + ".json"
}

///|
/// 从文件名解析版本号（"00000000000000000012.json" → 12）
fn version_from_name(name : String) -> Int32 {
  let mut v = 0
  for c in name {
    let d = c.to_int() - 48
    if d >= 0 && d <= 9 {
      v = v * 10 + d
    } else {
      break
    }
  }
  v
}

///|
/// 读取文本文件
pub fn read_text(path : String) -> String raise DeltaError {
  @fs.read_file_to_string(path) catch {
    @fs.IOError(msg) => raise DeltaError::IoError(msg)
  }
}

///|
/// 写入文本文件
pub fn write_text(path : String, content : String) -> Unit raise DeltaError {
  @fs.write_string_to_file(path, content) catch {
    @fs.IOError(msg) => raise DeltaError::IoError(msg)
  }
}

///|
/// 列出所有日志版本（升序、去重）
pub fn list_versions(root : String) -> Array[Int32] raise DeltaError {
  let dir = log_dir(root)
  if !@fs.path_exists(dir) {
    raise DeltaError::NotFound("no _delta_log directory under '\{root}'")
  }
  let names = @fs.read_dir(dir) catch {
    @fs.IOError(msg) => raise DeltaError::IoError(msg)
  }
  let out : Array[Int32] = []
  for name in names {
    if name.has_suffix(".json") && !name.has_prefix(".") {
      let v = version_from_name(name)
      if !out.contains(v) {
        out.push(v)
      }
    }
  }
  out.sort()
  out
}

///|
/// 读取某个版本的日志 actions
pub fn read_actions(
  root : String,
  version : Int32,
) -> Array[Action] raise DeltaError {
  let path = log_dir(root) + "/" + version_file_name(version)
  if !@fs.path_exists(path) {
    raise DeltaError::NotFound(
      "log version \{version} is missing (checkpoint-only or truncated table?)",
    )
  }
  let text = read_text(path).replace_all(old="\r", new="")
  let out : Array[Action] = []
  for line in text.split("\n") {
    if line.char_length() == 0 {
      continue
    }
    out.push(parse_action(line, version))
  }
  out
}

///|
/// 读取 `_last_checkpoint`（不存在返回 None）
pub fn checkpoint_info(root : String) -> CheckpointInfo? raise DeltaError {
  let path = log_dir(root) + "/_last_checkpoint"
  if !@fs.path_exists(path) {
    return None
  }
  let text = read_text(path)
  let j = @json.parse(text) catch { _ => return None }
  let version = match json_field_int(j, "version") {
    Some(v) => v
    None => return None
  }
  let size = json_field_int(j, "size")
  let parts = json_field_int(j, "parts")
  Some({ version, size, parts, })
}

///|
/// 是否存在 checkpoint 文件（`.checkpoint.parquet`）
pub fn has_checkpoint_file(root : String) -> Bool {
  let dir = log_dir(root)
  if !@fs.path_exists(dir) {
    return false
  }
  let names = @fs.read_dir(dir) catch { _ => return false }
  for name in names {
    if name.has_suffix(".checkpoint.parquet") {
      return true
    }
  }
  false
}
