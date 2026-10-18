///|
fn usage() -> Unit {
  println("moondelta-core 0.1.0 — MoonBit Delta Lake table-format kernel")
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
fn fail(message : String) -> Unit {
  println("error: " + message)
}

///|
fn cmd_inspect(root : String) -> Unit {
  let table = @delta.DeltaTable::open(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let inspected = table.inspect() catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let (snapshot, checkpoint, has_checkpoint_file) = inspected
  let versions = table.versions() catch {
    err => {
      fail("\{err}")
      return
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
      "note             : checkpoint files exist; checkpoint-only tables are not readable by this kernel",
    )
  }
}

///|
fn cmd_history(root : String) -> Unit {
  let table = @delta.DeltaTable::open(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let history = table.history() catch {
    err => {
      fail("\{err}")
      return
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
        return Some({ column: left.to_owned(), op, value, })
      }
      None => ()
    }
  }
  fail("invalid --where expression, expected like: id>=60 (no spaces)")
  None
}

///|
fn cmd_scan(root : String, rest : Array[String]) -> Unit {
  let table = @delta.DeltaTable::open(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let snapshot = table.snapshot() catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let partitions : Array[(String, String)] = []
  let mut predicate : @delta.ColumnPredicate? = None
  let mut i = 0
  while i < rest.length() {
    if rest[i] == "--partition" && i + 1 < rest.length() {
      match rest[i + 1].split_once("=") {
        Some(split) => {
          let (k, v) = split
          partitions.push((k.to_owned(), v.to_owned()))
        }
        None => {
          fail("invalid --partition, expected k=v")
          return
        }
      }
      i = i + 2
    } else if rest[i] == "--where" && i + 1 < rest.length() {
      let parsed = parse_where(rest[i + 1])
      predicate = parsed
      i = i + 2
    } else {
      i = i + 1
    }
  }
  let report = @delta.scan(snapshot, { partitions, predicate, })
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
}

///|
fn cmd_read(root : String, rest : Array[String]) -> Unit {
  let table = @delta.DeltaTable::open(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let snapshot = table.snapshot() catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let mut limit : Int32? = Some(10)
  let mut i = 0
  while i < rest.length() {
    if rest[i] == "--limit" && i + 1 < rest.length() {
      limit = Some(
        @string.parse_int(rest[i + 1], base=10) catch {
          _ => {
            fail("invalid --limit")
            return
          }
        },
      )
      i = i + 2
    } else {
      i = i + 1
    }
  }
  let result = @delta.read_snapshot(root, snapshot, limit) catch {
    err => {
      fail("\{err}")
      return
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
}

///|
fn cmd_create(root : String, rest : Array[String]) -> Unit {
  if rest.length() < 1 {
    usage()
    return
  }
  let schema_arg = rest[0]
  let schema_json = if @fs.path_exists(schema_arg) {
    @delta.read_text(schema_arg) catch {
      err => {
        fail("\{err}")
        return
      }
    }
  } else {
    schema_arg
  }
  let partitions : Array[String] = []
  let mut i = 1
  while i < rest.length() {
    if rest[i] == "--partition" && i + 1 < rest.length() {
      for p in rest[i + 1].split(",") {
        partitions.push(p.to_owned())
      }
      i = i + 2
    } else {
      i = i + 1
    }
  }
  let version = @delta.create_table(root, schema_json, partitions) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  println(
    "created Delta table at " + root + " (version " + version.to_string() + ")",
  )
}

///|
fn main {
  let args = @env.args()
  if args.length() < 2 {
    usage()
    return
  }
  let cmd = args[1]
  let rest = tail(args, 2)
  if cmd == "version" {
    println("moondelta-core 0.1.0")
  } else if cmd == "inspect" {
    if rest.length() < 1 {
      usage()
    } else {
      cmd_inspect(rest[0])
    }
  } else if cmd == "history" {
    if rest.length() < 1 {
      usage()
    } else {
      cmd_history(rest[0])
    }
  } else if cmd == "scan" {
    if rest.length() < 1 {
      usage()
    } else {
      cmd_scan(rest[0], tail(rest, 1))
    }
  } else if cmd == "read" {
    if rest.length() < 1 {
      usage()
    } else {
      cmd_read(rest[0], tail(rest, 1))
    }
  } else if cmd == "create" {
    if rest.length() < 2 {
      usage()
    } else {
      cmd_create(rest[0], tail(rest, 1))
    }
  } else {
    usage()
  }
}
