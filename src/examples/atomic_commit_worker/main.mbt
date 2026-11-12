///|
/// Process-level writer used by artifacts/acceptance/test_atomic_commit_wsl.py.
fn main {
  let args = @env.args()
  if args.length() < 3 {
    println(
      "usage: atomic_commit_worker <init|append|empty|read|recover|query|manage> <table-root> <value-or-version-or-txn-id> [id]",
    )
    return
  }
  let mode = args[1]
  let root = args[2]
  if mode == "query" {
    if args.length() < 4 {
      println("query requires a transaction id")
      return
    }
    let status = @delta.query_transaction(root, args[3]) catch {
      err => {
        println("query failed: \{err}")
        return
      }
    }
    println(
      "state=" +
      @delta.txn_state_name(status.state) +
      " version=" +
      status.version.to_string() +
      " phase=" +
      status.phase,
    )
    println(status.detail)
    return
  }
  if mode == "recover" {
    let apply = args.length() >= 4 && args[3] == "1"
    let report = @delta.recover_table(root, not(apply)) catch {
      err => {
        println("recover failed: \{err}")
        return
      }
    }
    println(
      "recover mode=" +
      (if apply { "apply" } else { "dry-run" }) +
      " managed=" +
      report.managed.to_string() +
      " busy=" +
      report.lock_busy.to_string() +
      " logComplete=" +
      report.log_complete.to_string() +
      " deleted=" +
      report.deleted.to_string() +
      " kept=" +
      report.kept.to_string(),
    )
    for entry in report.entries {
      println("  " + entry.action + " " + entry.path + " (\{entry.reason})")
    }
    if report.lock_busy {
      println("lock busy")
    }
    match report.blocked {
      Some(message) => println("blocked " + message)
      None => ()
    }
    return
  }
  if mode == "manage" {
    let managed = args.length() < 4 || args[3] != "0"
    @delta.set_managed_table(root, managed) catch {
      err => {
        println("manage failed: \{err}")
        return
      }
    }
    println("managed=" + managed.to_string())
    return
  }
  if args.length() < 4 {
    println("missing value")
    return
  }
  let value = @string.parse_int64(args[3], base=10) catch {
    _ => {
      println("value must be an integer")
      return
    }
  }
  if mode == "read" {
    let table = @delta.DeltaTable::open(root) catch {
      err => {
        println("read failed: \{err}")
        return
      }
    }
    let snapshot = table.snapshot_at(value.to_int()) catch {
      err => {
        println("read failed: \{err}")
        return
      }
    }
    let rows = @delta.read_snapshot(root, snapshot, None) catch {
      err => {
        println("read failed: \{err}")
        return
      }
    }
    println("version=\{snapshot.version} rows=\{rows.row_count}")
    for cell in rows.columns[0].cells {
      println(@delta.cell_to_display(cell))
    }
    return
  }
  let schema = "{\"type\":\"struct\",\"fields\":[" +
    "{\"name\":\"id\",\"type\":\"long\",\"nullable\":false,\"metadata\":{}}," +
    "{\"name\":\"payload\",\"type\":\"string\",\"nullable\":true,\"metadata\":{}}]}"
  if mode == "init" {
    let (version, txn) = @delta.create_table_with_txn(root, schema, []) catch {
      err => {
        println("create failed: \{err}")
        return
      }
    }
    println("created version \{version} txn=\{txn}")
    return
  }
  if mode != "append" && mode != "empty" {
    println("unknown mode: \{mode}")
    return
  }
  let payload_size = value.to_int()
  // Optional 4th argument: an explicit value for the `id` cell, so a caller can
  // make every appended row content-unique while keeping the payload size fixed.
  // Defaults to the payload size, which is what every existing caller expects.
  let row_id = if args.length() >= 5 {
    @string.parse_int64(args[4], base=10) catch {
      _ => {
        println("id must be an integer")
        return
      }
    }
  } else {
    value
  }
  let payload_builder = StringBuilder()
  let chunk = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
  let mut remaining = payload_size
  while remaining >= chunk.length() {
    payload_builder.write_string(chunk)
    remaining = remaining - chunk.length()
  }
  while remaining > 0 {
    payload_builder.write_char('x')
    remaining = remaining - 1
  }
  let columns : Array[@delta.ReadColumn] = [
    { name: "id", type_: @delta.Int64Type, cells: if mode == "empty" { [] } else { [@delta.CellI64(row_id)] } },
    { name: "payload", type_: @delta.StringType, cells: if mode == "empty" { [] } else { [@delta.CellStr(payload_builder.to_string())] } },
  ]
  let (version, txn) = @delta.append_rows_with_txn(root, columns, value) catch {
    err => {
      println("append failed: \{err}")
      return
    }
  }
  println("appended version \{version} txn=\{txn}")
}
