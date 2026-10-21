///|
/// D1 协议审计的库级探针。
///
/// 为什么需要它：CLI 只有 inspect/history/scan/read/create，`append_rows`、
/// `snapshot_at`、`create_table` 的库级行为无法从 CLI 触发；而仓库根的
/// moon.mod `source = "src"` 只索引 src/ 下的包，所以这里用一份隔离的模块副本
/// （artifacts/protocol-audit/probe-module）加探针包，不改仓库 src/。
///
/// 运行：cd artifacts/protocol-audit/probe-module && moon run --target native src/probe
/// 输出：artifacts/protocol-audit/out/probe-library.out（由 run_probe.ps1 收集）

///|
fn snapshot_desc(table : @delta.DeltaTable) -> String {
  let s = table.snapshot() catch {
    err => return "error: " + @delta.describe_error(err)
  }
  "version=" + s.version.to_string() + " files=" + s.files.length().to_string()
}

///|
fn snapshot_at_desc(table : @delta.DeltaTable, v : Int32) -> String {
  let s = table.snapshot_at(v) catch {
    err => return "error: " + @delta.describe_error(err)
  }
  "version=" + s.version.to_string() + " files=" + s.files.length().to_string()
}

///|
fn run_append_type_mismatch(base : String) -> Unit {
  let root = base + "/probe-append-mismatch"
  let schema = "{\"type\":\"struct\",\"fields\":[{\"name\":\"id\",\"type\":\"long\",\"nullable\":true,\"metadata\":{}},{\"name\":\"name\",\"type\":\"string\",\"nullable\":true,\"metadata\":{}}]}"
  ignore(
    @delta.create_table(root, schema, []) catch {
      err => {
        println("[append-type-mismatch] create_table failed: " + @delta.describe_error(err))
        return
      }
    },
  )
  let columns : Array[@delta.ReadColumn] = [
    { name: "id", type_: @delta.StringType, cells: [@delta.CellStr("not-a-long")] },
    { name: "name", type_: @delta.Int64Type, cells: [@delta.CellI64(42L)] },
  ]
  let v = @delta.append_rows(root, columns, 1000L) catch {
    err => {
      println("[append-type-mismatch] append rejected: " + @delta.describe_error(err))
      return
    }
  }
  println("[append-type-mismatch] append accepted despite type swap => version " + v.to_string())
  let table = @delta.DeltaTable::open(root) catch {
    err => {
      println("  open: " + @delta.describe_error(err))
      return
    }
  }
  let snap = table.snapshot() catch {
    err => {
      println("  snapshot: " + @delta.describe_error(err))
      return
    }
  }
  let result = @delta.read_snapshot(root, snap, None) catch {
    err => {
      println("  read: " + @delta.describe_error(err))
      return
    }
  }
  for c in result.columns {
    let first = if c.cells.length() > 0 { @delta.cell_to_display(c.cells[0]) } else { "-" }
    println(
      "  column=" + c.name + " declared=" + @delta.delta_type_name(c.type_) + " read=" + first,
    )
  }
}

///|
fn run_append_name_mismatch(base : String) -> Unit {
  let root = base + "/probe-append-name-mismatch"
  let schema = "{\"type\":\"struct\",\"fields\":[{\"name\":\"id\",\"type\":\"long\",\"nullable\":true,\"metadata\":{}},{\"name\":\"name\",\"type\":\"string\",\"nullable\":true,\"metadata\":{}}]}"
  ignore(
    @delta.create_table(root, schema, []) catch {
      err => {
        println("[append-name-mismatch] create_table failed: " + @delta.describe_error(err))
        return
      }
    },
  )
  let columns : Array[@delta.ReadColumn] = [
    { name: "id", type_: @delta.Int64Type, cells: [@delta.CellI64(1L)] },
    { name: "other", type_: @delta.StringType, cells: [@delta.CellStr("x")] },
  ]
  let v = @delta.append_rows(root, columns, 1001L) catch {
    err => {
      println("[append-name-mismatch] append rejected: " + @delta.describe_error(err))
      return
    }
  }
  println("[append-name-mismatch] append accepted with wrong column names => version " + v.to_string())
  let table = @delta.DeltaTable::open(root) catch { _ => return }
  let snap = table.snapshot() catch { _ => return }
  let result = @delta.read_snapshot(root, snap, None) catch {
    err => {
      println("  read: " + @delta.describe_error(err))
      return
    }
  }
  println("  read unexpectedly succeeded with " + result.row_count.to_string() + " rows")
}

///|
fn run_append_column_count(base : String) -> Unit {
  let root = base + "/probe-append-column-count"
  let schema = "{\"type\":\"struct\",\"fields\":[{\"name\":\"id\",\"type\":\"long\",\"nullable\":true,\"metadata\":{}},{\"name\":\"name\",\"type\":\"string\",\"nullable\":true,\"metadata\":{}}]}"
  ignore(@delta.create_table(root, schema, []) catch { _ => return })
  let columns : Array[@delta.ReadColumn] = [
    { name: "id", type_: @delta.Int64Type, cells: [@delta.CellI64(1L)] },
  ]
  ignore(
    @delta.append_rows(root, columns, 1002L) catch {
      err => {
        println("[append-column-count] rejected: " + @delta.describe_error(err))
        return
      }
    },
  )
  println("[append-column-count] unexpectedly accepted")
}

///|
fn run_append_row_lengths(base : String) -> Unit {
  let root = base + "/probe-append-row-lengths"
  let schema = "{\"type\":\"struct\",\"fields\":[{\"name\":\"id\",\"type\":\"long\",\"nullable\":true,\"metadata\":{}},{\"name\":\"name\",\"type\":\"string\",\"nullable\":true,\"metadata\":{}}]}"
  ignore(@delta.create_table(root, schema, []) catch { _ => return })
  let columns : Array[@delta.ReadColumn] = [
    { name: "id", type_: @delta.Int64Type, cells: [@delta.CellI64(1L), @delta.CellI64(2L)] },
    { name: "name", type_: @delta.StringType, cells: [@delta.CellStr("x")] },
  ]
  ignore(
    @delta.append_rows(root, columns, 1003L) catch {
      err => {
        println("[append-row-lengths] rejected: " + @delta.describe_error(err))
        return
      }
    },
  )
  println("[append-row-lengths] unexpectedly accepted")
}

///|
fn run_append_partitioned(base : String) -> Unit {
  let root = base + "/probe-append-partition"
  let schema = "{\"type\":\"struct\",\"fields\":[{\"name\":\"id\",\"type\":\"long\",\"nullable\":true,\"metadata\":{}},{\"name\":\"dt\",\"type\":\"string\",\"nullable\":true,\"metadata\":{}}]}"
  ignore(
    @delta.create_table(root, schema, ["dt"]) catch {
      err => {
        println(
          "[append-partitioned] create_table(partitioned) failed: " + @delta.describe_error(err),
        )
        return
      }
    },
  )
  let columns : Array[@delta.ReadColumn] = [
    { name: "id", type_: @delta.Int64Type, cells: [@delta.CellI64(1L)] },
    { name: "dt", type_: @delta.StringType, cells: [@delta.CellStr("2026-01-01")] },
  ]
  ignore(
    @delta.append_rows(root, columns, 1004L) catch {
      err => {
        println("[append-partitioned] rejected: " + @delta.describe_error(err))
        return
      }
    },
  )
  println("[append-partitioned] unexpectedly accepted")
}

///|
fn run_append_not_null(base : String) -> Unit {
  let root = base + "/probe-append-notnull"
  let schema = "{\"type\":\"struct\",\"fields\":[{\"name\":\"id\",\"type\":\"long\",\"nullable\":false,\"metadata\":{}}]}"
  ignore(@delta.create_table(root, schema, []) catch { _ => return })
  let columns : Array[@delta.ReadColumn] = [
    { name: "id", type_: @delta.Int64Type, cells: [@delta.CellI64(1L), @delta.CellNull] },
  ]
  let v = @delta.append_rows(root, columns, 1005L) catch {
    err => {
      println("[append-not-null] append rejected: " + @delta.describe_error(err))
      return
    }
  }
  println("[append-not-null] append accepted NULL into NOT NULL column => version " + v.to_string())
  let table = @delta.DeltaTable::open(root) catch { _ => return }
  let snap = table.snapshot() catch { _ => return }
  let result = @delta.read_snapshot(root, snap, None) catch {
    err => {
      println("  read: " + @delta.describe_error(err))
      return
    }
  }
  let cells : Array[String] = []
  for cell in result.columns[0].cells {
    cells.push(@delta.cell_to_display(cell))
  }
  println("  read id cells: " + cells.join(","))
}

///|
fn run_create_invalid_schema(base : String) -> Unit {
  let root = base + "/probe-created-invalid"
  ignore(
    @delta.create_table(root, "{not a schema", []) catch {
      err => {
        println("[create-invalid-schema] create rejected: " + @delta.describe_error(err))
        return
      }
    },
  )
  println("[create-invalid-schema] create_table accepted invalid schema JSON (version 0 written)")
  let table = @delta.DeltaTable::open(root) catch {
    err => {
      println("  open: " + @delta.describe_error(err))
      return
    }
  }
  println("  later snapshot => " + snapshot_desc(table))
}

///|
fn run_snapshot_at(base : String) -> Unit {
  ignore(base)
  let table = @delta.DeltaTable::open("../../../fixtures/delta/table_a") catch {
    err => {
      println("[snapshot-at] open failed: " + @delta.describe_error(err))
      return
    }
  }
  println("[snapshot-at] at 5 => " + snapshot_at_desc(table, 5))
  println("[snapshot-at] at 99 => " + snapshot_at_desc(table, 99))
  println("[snapshot-at] at -1 => " + snapshot_at_desc(table, -1))
}

///|
fn run_append_decimal_table(base : String) -> Unit {
  let root = base + "/probe-append-decimal"
  let schema = "{\"type\":\"struct\",\"fields\":[{\"name\":\"id\",\"type\":\"long\",\"nullable\":true,\"metadata\":{}},{\"name\":\"price\",\"type\":\"decimal(10,2)\",\"nullable\":true,\"metadata\":{}}]}"
  ignore(@delta.create_table(root, schema, []) catch { _ => return })
  let columns : Array[@delta.ReadColumn] = [
    { name: "id", type_: @delta.Int64Type, cells: [@delta.CellI64(1L)] },
    { name: "price", type_: @delta.Int64Type, cells: [@delta.CellI64(123L)] },
  ]
  let v = @delta.append_rows(root, columns, 1006L) catch {
    err => {
      println("[append-decimal] append rejected: " + @delta.describe_error(err))
      return
    }
  }
  println("[append-decimal] append into decimal-schema table accepted => version " + v.to_string())
  let table = @delta.DeltaTable::open(root) catch { _ => return }
  let snap = table.snapshot() catch { _ => return }
  ignore(
    @delta.read_snapshot(root, snap, None) catch {
      err => {
        println("  read: " + @delta.describe_error(err))
        return
      }
    },
  )
}

///|
fn run_append_writer_v5(base : String) -> Unit {
  let root = base + "/probe-append-writer-v5"
  ignore(
    @delta.ensure_dir(root + "/_delta_log") catch {
      err => {
        println("[append-writer-v5] mkdir failed: " + @delta.describe_error(err))
        return
      }
    },
  )
  let schema = "{\"type\":\"struct\",\"fields\":[{\"name\":\"id\",\"type\":\"long\",\"nullable\":true,\"metadata\":{}}]}"
  let escaped = schema.replace_all(old="\"", new="\\\"")
  let protocol_line = "{\"protocol\":{\"minReaderVersion\":1,\"minWriterVersion\":5,\"writerFeatures\":[\"appendOnly\",\"invariants\",\"changeDataFeed\"]}}"
  let meta_line = "{\"metaData\":{\"id\":\"probe-v5\",\"format\":{\"provider\":\"parquet\",\"options\":{}},\"schemaString\":\"" +
    escaped +
    "\",\"partitionColumns\":[],\"configuration\":{}}}\n"
  ignore(
    @delta.write_text(
      root + "/_delta_log/00000000000000000000.json",
      protocol_line + "\n" + meta_line,
    ) catch {
      err => {
        println("[append-writer-v5] write log failed: " + @delta.describe_error(err))
        return
      }
    },
  )
  let columns : Array[@delta.ReadColumn] = [
    { name: "id", type_: @delta.Int64Type, cells: [@delta.CellI64(7L)] },
  ]
  let v = @delta.append_rows(root, columns, 1007L) catch {
    err => {
      println("[append-writer-v5] append rejected: " + @delta.describe_error(err))
      return
    }
  }
  println("[append-writer-v5] append into minWriterVersion=5 table accepted => version " + v.to_string())
  let table = @delta.DeltaTable::open(root) catch { _ => return }
  let snap = table.snapshot() catch { _ => return }
  println(
    "  snapshot version=" +
    snap.version.to_string() +
    " reader=" +
    snap.protocol.min_reader_version.to_string() +
    " writer=" +
    snap.protocol.min_writer_version.to_string(),
  )
}

///|
fn run_unknown_action_api() -> Unit {
  let action = @delta.parse_action("{\"cdc\":{\"path\":\"x\"}}", 0) catch {
    err => {
      println("[unknown-action-api] parse_action raised: " + @delta.describe_error(err))
      return
    }
  }
  match action {
    @delta.UnknownAction(msg) =>
      println("[unknown-action-api] returned UnknownAction(" + msg + ")，replay 时被忽略")
    _ => println("[unknown-action-api] unexpected action kind")
  }
}

///|
fn run_nonstandard_log_name(base : String) -> Unit {
  let root = base + "/nonstandard-log-name"
  let table = @delta.DeltaTable::open(root) catch {
    err => {
      println("[nonstandard-log-name] open: " + @delta.describe_error(err))
      return
    }
  }
  let versions = table.versions() catch {
    err => {
      println("[nonstandard-log-name] versions: " + @delta.describe_error(err))
      return
    }
  }
  println(
    "[nonstandard-log-name] discovered versions from '0.json' = " + versions.length().to_string(),
  )
  println("[nonstandard-log-name] snapshot => " + snapshot_desc(table))
}

///|
fn run_reader_v3_library(base : String) -> Unit {
  let root = base + "/protocol-reader-v3-features"
  let table = @delta.DeltaTable::open(root) catch { _ => return }
  let snap = table.snapshot() catch {
    err => {
      println("[reader-v3-library] snapshot: " + @delta.describe_error(err))
      return
    }
  }
  println(
    "[reader-v3-library] accepted reader=" +
    snap.protocol.min_reader_version.to_string() +
    " writer=" +
    snap.protocol.min_writer_version.to_string(),
  )
  let result = @delta.read_snapshot(root, snap, None) catch {
    err => {
      println("  read: " + @delta.describe_error(err))
      return
    }
  }
  println("  read rows=" + result.row_count.to_string() + "（readerFeatures 未校验）")
}

///|
fn run_read_limit(base : String) -> Unit {
  let root = base + "/baseline-flat"
  let table = @delta.DeltaTable::open(root) catch { _ => return }
  let snap = table.snapshot() catch { _ => return }
  let r0 = @delta.read_snapshot(root, snap, Some(0)) catch {
    err => {
      println("[read-limit] limit=0 raised: " + @delta.describe_error(err))
      return
    }
  }
  println(
    "[read-limit] limit=0 => files_read=" +
    r0.files_read.to_string() +
    " rows=" +
    r0.row_count.to_string(),
  )
  let r100 = @delta.read_snapshot(root, snap, Some(100)) catch { _ => return }
  println(
    "[read-limit] limit=100 => files_read=" +
    r100.files_read.to_string() +
    " rows=" +
    r100.row_count.to_string(),
  )
}

///|
fn main {
  let base = "../fixtures"
  run_reader_v3_library(base)
  run_unknown_action_api()
  run_snapshot_at(base)
  run_nonstandard_log_name(base)
  run_read_limit(base)
  run_create_invalid_schema(base)
  run_append_type_mismatch(base)
  run_append_name_mismatch(base)
  run_append_column_count(base)
  run_append_row_lengths(base)
  run_append_partitioned(base)
  run_append_not_null(base)
  run_append_decimal_table(base)
  run_append_writer_v5(base)
}
