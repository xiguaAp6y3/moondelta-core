///|
/// 递归删除测试目录（测试内使用，避免残留数据影响断言）
fn reset_dir(path : String) -> Unit {
  if !@fs.path_exists(path) {
    return
  }
  let names = @fs.read_dir(path) catch { _ => return }
  for name in names {
    let child = path + "/" + name
    let is_dir = @fs.is_dir(child) catch { _ => false }
    if is_dir {
      reset_dir(child)
    } else {
      @fs.remove_file(child) catch {
        _ => ()
      }
    }
  }
  @fs.remove_dir(path) catch {
    _ => ()
  }
}

///|
fn schema_is_readable(schema : DeltaSchema) -> Bool {
  ensure_readable_schema(schema) catch {
    _ => return false
  }
  true
}

///|
fn snapshot_error_of(root : String) -> String {
  let table = DeltaTable::open(root) catch { err => return "\{err}" }
  let snapshot = table.snapshot() catch { err => return "\{err}" }
  ignore(snapshot)
  ""
}

///|
fn read_error_of(root : String, snapshot : Snapshot) -> String {
  let result = read_snapshot(root, snapshot, None) catch {
    err => return "\{err}"
  }
  "unexpectedly read " + result.row_count.to_string() + " rows"
}

///|
test "schema: parse flat delta schema" {
  let raw = "{\"type\":\"struct\",\"fields\":[{\"name\":\"id\",\"type\":\"long\",\"nullable\":true,\"metadata\":{}},{\"name\":\"name\",\"type\":\"string\",\"nullable\":false,\"metadata\":{}}]}"
  let schema = parse_schema(raw) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(schema.fields.length(), 2)
  assert_eq(schema.fields[0].name, "id")
  assert_eq(schema.fields[0].type_, Int64Type)
  assert_eq(schema.fields[0].nullable, true)
  assert_eq(schema.fields[1].type_, StringType)
  assert_eq(schema.fields[1].nullable, false)
  assert_true(schema_is_readable(schema))
}

///|
test "schema: nested type is marked and rejected with a clear error" {
  let raw = "{\"type\":\"struct\",\"fields\":[{\"name\":\"tags\",\"type\":{\"type\":\"array\",\"elementType\":\"string\",\"containsNull\":true},\"nullable\":true,\"metadata\":{}}]}"
  let schema = parse_schema(raw) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(schema.fields[0].type_, NestedType(kind="array"))
  assert_false(schema_is_readable(schema))
}

///|
test "actions: parse protocol and metadata from real log line" {
  let protocol = parse_action(
    "{\"protocol\":{\"minReaderVersion\":1,\"minWriterVersion\":2}}", 0,
  ) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  match protocol {
    ProtocolAction(p) => {
      assert_eq(p.min_reader_version, 1)
      assert_eq(p.min_writer_version, 2)
    }
    _ => fail("expected protocol action")
  }
  let actions = read_actions("fixtures/delta/table_a", 0) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let mut found_metadata = false
  for action in actions {
    match action {
      MetaDataAction(m) => {
        found_metadata = true
        assert_eq(m.schema.fields.length(), 1)
        assert_eq(m.schema.fields[0].name, "id")
        assert_eq(m.schema.fields[0].type_, Int32Type)
        assert_eq(m.partition_columns.length(), 0)
      }
      _ => ()
    }
  }
  assert_true(found_metadata)
}

///|
test "log: table_a exposes 13 versions with 20-digit names" {
  let versions = list_versions("fixtures/delta/table_a") catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(versions.length(), 13)
  assert_eq(versions[12], 12)
  assert_eq(version_file_name(12), "00000000000000000012.json")
  let cp = checkpoint_info("fixtures/delta/table_a") catch { _ => None }
  match cp {
    Some(info) => assert_eq(info.version, 10)
    None => fail("expected _last_checkpoint to be parsed")
  }
  assert_true(has_checkpoint_file("fixtures/delta/table_a"))
}

///|
test "snapshot: replay 13 versions to 120 files and time travel" {
  let table = DeltaTable::open("fixtures/delta/table_a") catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let latest = table.snapshot() catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(latest.version, 12)
  assert_eq(latest.files.length(), 120)
  assert_eq(latest.protocol.min_reader_version, 1)
  assert_eq(latest.protocol.min_writer_version, 1)
  assert_eq(latest.metadata.schema.fields[0].name, "id")
  assert_eq(latest.total_rows, 120L)
  let first = table.snapshot_at(0) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(first.files.length(), 0)
  let history = table.history() catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(history.length(), 13)
}

///|
test "scan: stats pruning selects 60 of 120 files for id>=60" {
  let table = DeltaTable::open("fixtures/delta/table_a") catch {
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
  let all = scan(snapshot, { partitions: [], predicate: None, })
  assert_eq(all.files.length(), 120)
  assert_eq(all.skipped_stats, 0)
  let pruned = scan(snapshot, {
    partitions: [],
    predicate: Some({ column: "id", op: GeV, value: 60.0, }),
  })
  assert_eq(pruned.files.length(), 60)
  assert_eq(pruned.skipped_stats, 60)
  assert_eq(pruned.matched_rows, 60L)
}

///|
test "read: real Databricks-written covid table (snappy parquet)" {
  let table = DeltaTable::open("fixtures/delta/covid") catch {
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
  assert_eq(snapshot.files.length(), 8)
  let result = read_snapshot("fixtures/delta/covid", snapshot, Some(3)) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(result.files_read, 1)
  assert_eq(result.row_count, 3)
  assert_eq(result.columns.length(), 6)
  assert_eq(result.columns[0].name, "date")
  assert_eq(cell_to_display(result.columns[0].cells[0]), "2020-01-21")
  assert_eq(cell_to_display(result.columns[1].cells[0]), "Snohomish")
  assert_eq(cell_to_display(result.columns[4].cells[0]), "1")
}

///|
test "snapshot: checkpoint-only table is rejected, not silently empty" {
  let root = "_build/test-tmp/table-checkpoint-only"
  reset_dir(root)
  ensure_dir(root) catch {
    _ => ()
  }
  ensure_dir(root + "/_delta_log") catch {
    _ => ()
  }
  write_text(
    root + "/_delta_log/00000000000000000003.json",
    "{\"protocol\":{\"minReaderVersion\":1,\"minWriterVersion\":1}}\n",
  ) catch {
    _ => fail("cannot write fixture")
  }
  let message = snapshot_error_of(root)
  assert_true(message.contains("checkpoint-only"))
}

///|
test "read: nested parquet column is rejected with a clear error" {
  let root = "_build/test-tmp/table-nested"
  reset_dir(root)
  ensure_dir(root) catch {
    _ => ()
  }
  ensure_dir(root + "/_delta_log") catch {
    _ => ()
  }
  let bytes = @fs.read_file_to_bytes("fixtures/parquet/list_columns.parquet") catch {
    _ => {
      fail("fixture missing")
      return
    }
  }
  @fs.write_bytes_to_file(root + "/data.parquet", bytes) catch {
    _ => {
      fail("cannot copy fixture")
      return
    }
  }
  let schema_json = "{\"type\":\"struct\",\"fields\":[{\"name\":\"int64_list\",\"type\":{\"type\":\"array\",\"elementType\":\"long\",\"containsNull\":true},\"nullable\":true,\"metadata\":{}}]}"
  let meta = "{\"metaData\":{\"id\":\"x\",\"format\":{\"provider\":\"parquet\",\"options\":{}},\"schemaString\":" +
    json_string(schema_json) +
    ",\"partitionColumns\":[],\"configuration\":{}}}"
  let add = "{\"add\":{\"path\":\"data.parquet\",\"partitionValues\":{},\"size\":2526,\"modificationTime\":0,\"dataChange\":true}}"
  write_text(
    root + "/_delta_log/00000000000000000000.json",
    meta + "\n" + add + "\n",
  ) catch {
    _ => ()
  }
  let table = DeltaTable::open(root) catch {
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
  let message = read_error_of(root, snapshot)
  assert_true(message.contains("nested column"))
}

///|
test "write: create + append + read back (int64/string, nullable)" {
  let root = "_build/test-tmp/table-write"
  reset_dir(root)
  let schema = "{\"type\":\"struct\",\"fields\":[{\"name\":\"id\",\"type\":\"long\",\"nullable\":true,\"metadata\":{}},{\"name\":\"name\",\"type\":\"string\",\"nullable\":true,\"metadata\":{}}]}"
  let created = create_table(root, schema, []) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(created, 0)
  let columns : Array[ReadColumn] = [
    {
      name: "id",
      type_: Int64Type,
      cells: [CellI64(1L), CellI64(2L), CellI64(3L)],
    },
    {
      name: "name",
      type_: StringType,
      cells: [CellStr("alice"), CellStr("bob"), CellNull],
    },
  ]
  let appended = append_rows(root, columns, 1000L) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(appended, 1)
  let table = DeltaTable::open(root) catch {
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
  assert_eq(snapshot.version, 1)
  assert_eq(snapshot.files.length(), 1)
  assert_eq(snapshot.total_rows, 3L)
  let result = read_snapshot(root, snapshot, None) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(result.row_count, 3)
  assert_eq(cell_to_display(result.columns[0].cells[2]), "3")
  assert_eq(cell_to_display(result.columns[1].cells[2]), "NULL")
}

///|
/// 构造一个合成的分区表：id(long) + dt(string, 分区列)，两个分区各一个数据文件
fn build_partitioned_table(root : String) -> Unit raise {
  reset_dir(root)
  ensure_dir(root + "/_delta_log") catch {
    _ => {
      fail("cannot create log dir")
      return
    }
  }
  ensure_dir(root + "/dt=2026-01-01") catch {
    _ => {
      fail("cannot create partition dir")
      return
    }
  }
  ensure_dir(root + "/dt=2026-01-02") catch {
    _ => {
      fail("cannot create partition dir")
      return
    }
  }
  let pcols : Array[@parquet.Column] = [
    @parquet.new_column("id", @parquet.Int64, @parquet.Optional),
  ]
  let rows1 : Array[Array[@parquet.Value]] = [
    [@parquet.Int64(1L)],
    [@parquet.Int64(2L)],
  ]
  @parquet.write_file(
    root + "/dt=2026-01-01/part-1.parquet",
    @parquet.new_parquet_file(pcols, rows1, None),
  ) catch {
    _ => {
      fail("cannot write parquet 1")
      return
    }
  }
  let rows2 : Array[Array[@parquet.Value]] = [[@parquet.Int64(3L)]]
  @parquet.write_file(
    root + "/dt=2026-01-02/part-1.parquet",
    @parquet.new_parquet_file(pcols, rows2, None),
  ) catch {
    _ => {
      fail("cannot write parquet 2")
      return
    }
  }
  let schema_json = "{\"type\":\"struct\",\"fields\":[{\"name\":\"id\",\"type\":\"long\",\"nullable\":true,\"metadata\":{}},{\"name\":\"dt\",\"type\":\"string\",\"nullable\":true,\"metadata\":{}}]}"
  let meta = "{\"metaData\":{\"id\":\"part\",\"format\":{\"provider\":\"parquet\",\"options\":{}},\"schemaString\":" +
    json_string(schema_json) +
    ",\"partitionColumns\":[\"dt\"],\"configuration\":{}}}"
  let add1 = "{\"add\":{\"path\":\"dt=2026-01-01/part-1.parquet\",\"partitionValues\":{\"dt\":\"2026-01-01\"},\"size\":0,\"modificationTime\":0,\"dataChange\":true,\"stats\":" +
    json_string("{\"numRecords\":2}") + "}}"
  let add2 = "{\"add\":{\"path\":\"dt=2026-01-02/part-1.parquet\",\"partitionValues\":{\"dt\":\"2026-01-02\"},\"size\":0,\"modificationTime\":0,\"dataChange\":true,\"stats\":" +
    json_string("{\"numRecords\":1}") + "}}"
  write_text(
    root + "/_delta_log/00000000000000000000.json",
    meta + "\n" + add1 + "\n" + add2 + "\n",
  ) catch {
    _ => fail("cannot write log")
  }
}

///|
test "write: boolean and double columns roundtrip" {
  let root = "_build/test-tmp/table-bool-double"
  reset_dir(root)
  let schema = "{\"type\":\"struct\",\"fields\":[{\"name\":\"ok\",\"type\":\"boolean\",\"nullable\":true,\"metadata\":{}},{\"name\":\"score\",\"type\":\"double\",\"nullable\":true,\"metadata\":{}}]}"
  let created = create_table(root, schema, []) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(created, 0)
  let columns : Array[ReadColumn] = [
    {
      name: "ok",
      type_: BooleanType,
      cells: [CellBool(true), CellBool(false), CellNull],
    },
    {
      name: "score",
      type_: DoubleType,
      cells: [CellF64(1.5), CellF64(-2.25), CellNull],
    },
  ]
  let appended = append_rows(root, columns, 1000L) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(appended, 1)
  let snapshot = DeltaTable::open(root).snapshot() catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let result = read_snapshot(root, snapshot, None) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(result.row_count, 3)
  assert_eq(cell_to_display(result.columns[0].cells[0]), "true")
  assert_eq(cell_to_display(result.columns[0].cells[1]), "false")
  assert_eq(cell_to_display(result.columns[1].cells[0]), "1.5")
  assert_eq(cell_to_display(result.columns[1].cells[1]), "-2.25")
  assert_eq(cell_to_display(result.columns[1].cells[2]), "NULL")
}

///|
test "read/scan: partitioned table fills partition values and filters by partition" {
  let root = "_build/test-tmp/table-partitioned"
  build_partitioned_table(root) catch {
    _ => {
      fail("build partitioned table failed")
      return
    }
  }
  let snapshot = DeltaTable::open(root).snapshot() catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(snapshot.files.length(), 2)
  assert_eq(snapshot.metadata.partition_columns.length(), 1)
  let wanted : Array[(String, String)] = [("dt", "2026-01-01")]
  let filtered = scan(snapshot, { partitions: wanted, predicate: None })
  assert_eq(filtered.files.length(), 1)
  assert_eq(filtered.skipped_partition, 1)
  assert_eq(filtered.matched_rows, 2L)
  let result = read_snapshot(root, snapshot, None) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(result.row_count, 3)
  assert_eq(result.columns.length(), 2)
  assert_eq(result.columns[0].name, "id")
  assert_eq(result.columns[1].name, "dt")
  assert_eq(cell_to_display(result.columns[1].cells[0]), "2026-01-01")
  assert_eq(cell_to_display(result.columns[1].cells[2]), "2026-01-02")
}

///|
test "scan: files without stats are kept, min==max boundary respected" {
  let root = "_build/test-tmp/table-stats-edge"
  reset_dir(root)
  ensure_dir(root + "/_delta_log") catch {
    _ => ()
  }
  let schema_json = "{\"type\":\"struct\",\"fields\":[{\"name\":\"id\",\"type\":\"long\",\"nullable\":true,\"metadata\":{}}]}"
  let meta = "{\"metaData\":{\"id\":\"edge\",\"format\":{\"provider\":\"parquet\",\"options\":{}},\"schemaString\":" +
    json_string(schema_json) + ",\"partitionColumns\":[],\"configuration\":{}}}"
  let stats_json = "{\"numRecords\":1,\"minValues\":{\"id\":5},\"maxValues\":{\"id\":5}}"
  let add_with_stats = "{\"add\":{\"path\":\"a.parquet\",\"partitionValues\":{},\"size\":0,\"modificationTime\":0,\"dataChange\":true,\"stats\":" +
    json_string(stats_json) + "}}"
  let add_without_stats = "{\"add\":{\"path\":\"b.parquet\",\"partitionValues\":{},\"size\":0,\"modificationTime\":0,\"dataChange\":true}}"
  write_text(
    root + "/_delta_log/00000000000000000000.json",
    meta + "\n" + add_with_stats + "\n" + add_without_stats + "\n",
  ) catch {
    _ => ()
  }
  let snapshot = DeltaTable::open(root).snapshot() catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let strict = scan(snapshot, {
    partitions: [],
    predicate: Some({ column: "id", op: GtV, value: 5.0 }),
  })
  assert_eq(strict.files.length(), 1)
  assert_eq(strict.skipped_stats, 1)
  let inclusive = scan(snapshot, {
    partitions: [],
    predicate: Some({ column: "id", op: GeV, value: 5.0 }),
  })
  assert_eq(inclusive.files.length(), 2)
}

///|
test "read: missing data file raises io error" {
  let root = "_build/test-tmp/table-missing-file"
  reset_dir(root)
  ensure_dir(root + "/_delta_log") catch {
    _ => ()
  }
  let schema_json = "{\"type\":\"struct\",\"fields\":[{\"name\":\"id\",\"type\":\"long\",\"nullable\":true,\"metadata\":{}}]}"
  let meta = "{\"metaData\":{\"id\":\"missing\",\"format\":{\"provider\":\"parquet\",\"options\":{}},\"schemaString\":" +
    json_string(schema_json) + ",\"partitionColumns\":[],\"configuration\":{}}}"
  let add = "{\"add\":{\"path\":\"missing.parquet\",\"partitionValues\":{},\"size\":0,\"modificationTime\":0,\"dataChange\":true}}"
  write_text(
    root + "/_delta_log/00000000000000000000.json",
    meta + "\n" + add + "\n",
  ) catch {
    _ => ()
  }
  let snapshot = DeltaTable::open(root).snapshot() catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let message = read_error_of(root, snapshot)
  assert_true(message.contains("io error"))
}
