///|
fn usage() -> Unit {
  println("moondelta-core 0.3.0 — MoonBit Delta Lake table-format kernel")
  println("")
  println("Usage:")
  println("  delta inspect <table-root>")
  println("  delta history <table-root>")
  println(
    "  delta scan    <table-root> [--partition k=v]... [--where col>=number]",
  )
  println("  delta read    <table-root> [--limit N]")
  println(
    "  delta create  <table-root> <schema.json|schemaString> [--partition k1,k2]",
  )
  println("  delta version")
}

///|
fn tail(args : Array[String], start : Int32) -> Array[String] {
  let out : Array[String] = []
  let mut i = start
  while i < args.length() {
    out.push(args[i])
    i = i + 1
  }
  out
}

///|
fn join(items : Array[String], sep : String) -> String {
  let sb = StringBuilder()
  let mut i = 0
  while i < items.length() {
    if i > 0 {
      sb.write_string(sep)
    }
    sb.write_string(items[i])
    i = i + 1
  }
  sb.to_string()
}

///|
/// 错误输出到 stderr；配合各 cmd_* 返回 false → main 以非 0 退出码结束
fn fail(message : String) -> Unit {
  eprintln("error: " + message)
}

///|
fn cmd_inspect(root : String) -> Bool {
  let table = @delta.DeltaTable::open(root) catch {
    err => {
      fail("\{err}")
      return false
    }
  }
  let inspected = table.inspect() catch {
    err => {
      fail("\{err}")
      return false
    }
  }
  let (snapshot, checkpoint, has_checkpoint_file) = inspected
  let versions = table.versions() catch {
    err => {
      fail("\{err}")
      return false
    }
  }
  println("table            : " + root)
  println(
    "current version  : " +
    snapshot.version.to_string() +
    "  (log versions: " +
    versions.length().to_string() +
    ")",
  )
  println(
    "files            : " +
    snapshot.files.length().to_string() +
    "  rows: " +
    snapshot.total_rows.to_string() +
    "  bytes: " +
    snapshot.total_bytes.to_string(),
  )
  println(
    "protocol         : reader=" +
    snapshot.protocol.min_reader_version.to_string() +
    " writer=" +
    snapshot.protocol.min_writer_version.to_string(),
  )
  println("format           : " + snapshot.metadata.format_provider)
  let partitions = if snapshot.metadata.partition_columns.length() == 0 {
    "(none)"
  } else {
    join(snapshot.metadata.partition_columns, ", ")
  }
  println("partition columns: " + partitions)
  println("schema:")
  for f in snapshot.metadata.schema.fields {
    let nn = if f.nullable { "" } else { " (not null)" }
    println("  " + f.name + ": " + @delta.delta_type_name(f.type_) + nn)
  }
  match checkpoint {
    Some(cp) => println("last checkpoint  : version=" + cp.version.to_string())
    None => ()
  }
  if has_checkpoint_file {
    println(
      "note             : checkpoint present; truncated / checkpoint-only tables are recovered from the latest single-file checkpoint",
    )
  }
  if snapshot.ignored_actions.length() > 0 {
    println(
      "note             : ignored (snapshot-irrelevant) actions in log: " +
      join(snapshot.ignored_actions, ", "),
    )
  }
  true
}

///|
fn cmd_history(root : String) -> Bool {
  let table = @delta.DeltaTable::open(root) catch {
    err => {
      fail("\{err}")
      return false
    }
  }
  let history = table.history() catch {
    err => {
      fail("\{err}")
      return false
    }
  }
  println("version | timestamp     | operation")
  println("--------+---------------+----------")
  for h in history {
    let ts = match h.timestamp {
      Some(t) => t.to_string()
      None => "-"
    }
    let op = match h.operation {
      Some(o) => o
      None => "-"
    }
    println(h.version.to_string() + " | " + ts + " | " + op)
  }
  true
}

///|
fn parse_where(text : String) -> @delta.ColumnPredicate? {
  let cleaned = text.replace_all(old=" ", new="")
  let ops : Array[(String, @delta.CompareOp)] = [
    (">=", GeV),
    ("<=", LeV),
    (">", GtV),
    ("<", LtV),
    ("=", EqV),
  ]
  for pair in ops {
    let (op_text, op) = pair
    match cleaned.split_once(op_text) {
      Some(split) => {
        let (left, right) = split
        let value = @string.parse_double(right) catch {
          _ => {
            fail("invalid number in --where: " + text)
            return None
          }
        }
        if left.char_length() == 0 {
          fail("invalid --where expression, missing column: " + text)
          return None
        }
        return Some({ column: left.to_owned(), op, value, })
      }
      None => ()
    }
  }
  fail("invalid --where expression, expected like: id>=60 (no spaces)")
  None
}

///|
/// 解析 scan 的选项。返回 None 表示参数错误（已输出错误信息）。
fn parse_scan_options(rest : Array[String]) -> (@delta.ScanFilter)? {
  let partitions : Array[(String, String)] = []
  let mut predicate : @delta.ColumnPredicate? = None
  let mut i = 0
  while i < rest.length() {
    if rest[i] == "--partition" {
      if i + 1 >= rest.length() {
        fail("--partition requires a k=v value")
        return None
      }
      match rest[i + 1].split_once("=") {
        Some(split) => {
          let (k, v) = split
          partitions.push((k.to_owned(), v.to_owned()))
        }
        None => {
          fail("invalid --partition, expected k=v")
          return None
        }
      }
      i = i + 2
    } else if rest[i] == "--where" {
      if i + 1 >= rest.length() {
        fail("--where requires an expression")
        return None
      }
      match parse_where(rest[i + 1]) {
        Some(pred) => predicate = Some(pred)
        None => return None
      }
      i = i + 2
    } else {
      fail("unknown argument '\{rest[i]}'")
      return None
    }
  }
  Some({ partitions, predicate, })
}

///|
fn cmd_scan(root : String, rest : Array[String]) -> Bool {
  let table = @delta.DeltaTable::open(root) catch {
    err => {
      fail("\{err}")
      return false
    }
  }
  let snapshot = table.snapshot() catch {
    err => {
      fail("\{err}")
      return false
    }
  }
  let filter = match parse_scan_options(rest) {
    Some(f) => f
    None => return false
  }
  let report = @delta.scan(snapshot, filter) catch {
    err => {
      fail("\{err}")
      return false
    }
  }
  println(
    "table            : " +
    root +
    " (version " +
    snapshot.version.to_string() +
    ")",
  )
  println("total files      : " + snapshot.files.length().to_string())
  println("selected files   : " + report.files.length().to_string())
  println("skipped partition: " + report.skipped_partition.to_string())
  println("skipped stats    : " + report.skipped_stats.to_string())
  println("matched rows     : " + report.matched_rows.to_string())
  println("matched bytes    : " + report.matched_bytes.to_string())
  let mut printed = 0
  for f in report.files {
    if printed >= 5 {
      break
    }
    println("  - " + f.path)
    printed = printed + 1
  }
  true
}

///|
fn cmd_read(root : String, rest : Array[String]) -> Bool {
  let table = @delta.DeltaTable::open(root) catch {
    err => {
      fail("\{err}")
      return false
    }
  }
  let snapshot = table.snapshot() catch {
    err => {
      fail("\{err}")
      return false
    }
  }
  let mut limit : Int32? = Some(10)
  let mut i = 0
  while i < rest.length() {
    if rest[i] == "--limit" {
      if i + 1 >= rest.length() {
        fail("--limit requires a number")
        return false
      }
      limit = Some(
        @string.parse_int(rest[i + 1], base=10) catch {
          _ => {
            fail("invalid --limit")
            return false
          }
        },
      )
      i = i + 2
    } else {
      fail("unknown argument '\{rest[i]}'")
      return false
    }
  }
  let result = @delta.read_snapshot(root, snapshot, limit) catch {
    err => {
      fail("\{err}")
      return false
    }
  }
  println(
    "read " +
    result.files_read.to_string() +
    " file(s), " +
    result.row_count.to_string() +
    " row(s), version " +
    snapshot.version.to_string(),
  )
  let header : Array[String] = []
  for c in result.columns {
    header.push(c.name + " (" + @delta.delta_type_name(c.type_) + ")")
  }
  println(join(header, " | "))
  let mut r = 0
  while r < result.row_count && r < 10 {
    let cells : Array[String] = []
    for c in result.columns {
      cells.push(@delta.cell_to_display(c.cells[r]))
    }
    println(join(cells, " | "))
    r = r + 1
  }
  if result.row_count > 10 {
    println("... (" + (result.row_count - 10).to_string() + " more rows)")
  }
  true
}

///|
fn cmd_create(root : String, rest : Array[String]) -> Bool {
  if rest.length() < 1 {
    usage()
    return false
  }
  let schema_arg = rest[0]
  let schema_json = if @fs.path_exists(schema_arg) {
    @delta.read_text(schema_arg) catch {
      err => {
        fail("\{err}")
        return false
      }
    }
  } else {
    schema_arg
  }
  let partitions : Array[String] = []
  let mut i = 1
  while i < rest.length() {
    if rest[i] == "--partition" {
      if i + 1 >= rest.length() {
        fail("--partition requires a comma-separated list")
        return false
      }
      for p in rest[i + 1].split(",") {
        partitions.push(p.to_owned())
      }
      i = i + 2
    } else {
      fail("unknown argument '\{rest[i]}'")
      return false
    }
  }
  let version = @delta.create_table(root, schema_json, partitions) catch {
    err => {
      fail("\{err}")
      return false
    }
  }
  println(
    "created Delta table at " + root + " (version " + version.to_string() + ")",
  )
  true
}

///|
fn main {
  let args = @env.args()
  if args.length() < 2 {
    usage()
    @sys.exit(1)
    return
  }
  let cmd = args[1]
  let rest = tail(args, 2)
  let ok = if cmd == "version" {
    println("moondelta-core 0.3.0")
    true
  } else if cmd == "inspect" {
    if rest.length() < 1 {
      usage()
      false
    } else {
      cmd_inspect(rest[0])
    }
  } else if cmd == "history" {
    if rest.length() < 1 {
      usage()
      false
    } else {
      cmd_history(rest[0])
    }
  } else if cmd == "scan" {
    if rest.length() < 1 {
      usage()
      false
    } else {
      cmd_scan(rest[0], tail(rest, 1))
    }
  } else if cmd == "read" {
    if rest.length() < 1 {
      usage()
      false
    } else {
      cmd_read(rest[0], tail(rest, 1))
    }
  } else if cmd == "create" {
    if rest.length() < 2 {
      usage()
      false
    } else {
      cmd_create(rest[0], tail(rest, 1))
    }
  } else {
    usage()
    false
  }
  if not(ok) {
    @sys.exit(1)
  }
}
