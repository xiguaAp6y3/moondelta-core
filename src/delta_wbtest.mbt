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
fn append_error_of(root : String, columns : Array[ReadColumn]) -> String {
  let result = append_rows(root, columns, 1000L) catch {
    err => return "\{err}"
  }
  ignore(result)
  "unexpectedly appended to " + root
}

///|
fn write_new_error_of(path : String, content : String) -> String {
  let done = write_text_new(path, content) catch {
    err => return "\{err}"
  }
  ignore(done)
  "unexpectedly overwrote"
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
test "schema: array type preserves the element subtree" {
  let raw = "{\"type\":\"struct\",\"fields\":[{\"name\":\"tags\",\"type\":{\"type\":\"array\",\"elementType\":\"string\",\"containsNull\":true},\"nullable\":true,\"metadata\":{}}]}"
  let schema = parse_schema(raw) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  match schema.fields[0].type_ {
    NestedType(kind~, fields~) => {
      assert_eq(kind, "array")
      assert_eq(fields.length(), 1)
      assert_eq(fields[0].name, "element")
      assert_eq(fields[0].type_, StringType)
      assert_eq(fields[0].nullable, true)
    }
    _ => fail("expected nested type")
  }
  assert_true(schema_is_readable(schema))
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
test "read: nested parquet list column is read (list_columns.parquet)" {
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
  let result = read_snapshot(root, snapshot, None) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  // parquet-testing list_columns.parquet: 3 行 [[1,2,3], [NULL,1], [4]]
  assert_eq(result.row_count, 3)
  assert_eq(cell_to_display(result.columns[0].cells[0]), "[1, 2, 3]")
  assert_eq(cell_to_display(result.columns[0].cells[1]), "[NULL, 1]")
  assert_eq(cell_to_display(result.columns[0].cells[2]), "[4]")
}

///|
/// D3 案例 B（engine-written，delta-rs 写出）与 oracle 逐值一致：
/// struct / 3-level list / map，覆盖 NULL、空集合、null 元素/子字段。
test "read: D3 nested fixture matches delta-rs oracle field by field" {
  let root = "artifacts/interop-nested/engine-written"
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
  assert_eq(snapshot.version, 0)
  let result = read_snapshot(root, snapshot, None) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(result.row_count, 5)
  assert_eq(result.columns.length(), 4)
  // id
  for i in 0..<5 {
    assert_eq(cell_to_display(result.columns[0].cells[i]), (i + 1).to_string())
  }
  // profile: struct<name:string, age:int>
  assert_eq(
    cell_to_display(result.columns[1].cells[0]),
    "{\"name\": alice, \"age\": 30}",
  )
  assert_eq(cell_to_display(result.columns[1].cells[1]), "NULL")
  assert_eq(
    cell_to_display(result.columns[1].cells[2]),
    "{\"name\": NULL, \"age\": NULL}",
  )
  assert_eq(
    cell_to_display(result.columns[1].cells[3]),
    "{\"name\": bob, \"age\": NULL}",
  )
  assert_eq(
    cell_to_display(result.columns[1].cells[4]),
    "{\"name\": NULL, \"age\": 42}",
  )
  // tags: array<string>
  assert_eq(cell_to_display(result.columns[2].cells[0]), "[red, green]")
  assert_eq(cell_to_display(result.columns[2].cells[1]), "[]")
  assert_eq(cell_to_display(result.columns[2].cells[2]), "NULL")
  assert_eq(cell_to_display(result.columns[2].cells[3]), "[blue, NULL]")
  assert_eq(cell_to_display(result.columns[2].cells[4]), "[]")
  // attrs: map<string,string>
  assert_eq(cell_to_display(result.columns[3].cells[0]), "{k1: v1, k2: v2}")
  assert_eq(cell_to_display(result.columns[3].cells[1]), "{}")
  assert_eq(cell_to_display(result.columns[3].cells[2]), "{k: v}")
  assert_eq(cell_to_display(result.columns[3].cells[3]), "{}")
  assert_eq(cell_to_display(result.columns[3].cells[4]), "NULL")
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

///|
test "protocol: reader v2 is rejected with a reason" {
  let message = snapshot_error_of("artifacts/protocol-audit/fixtures/protocol-reader-v2")
  assert_true(message.contains("reader protocol version 2"))
  assert_true(message.contains("reader version 1"))
}

///|
test "protocol: reader v3 with readerFeatures is rejected" {
  let message = snapshot_error_of("artifacts/protocol-audit/fixtures/protocol-reader-v3-features")
  assert_true(message.contains("reader protocol version 3"))
}

///|
test "protocol: readerFeatures on a v1 protocol are rejected at snapshot level" {
  let root = "_build/test-tmp/table-v1-features"
  reset_dir(root)
  ensure_dir(root + "/_delta_log") catch {
    _ => ()
  }
  write_text(
    root + "/_delta_log/00000000000000000000.json",
    "{\"protocol\":{\"minReaderVersion\":1,\"minWriterVersion\":2,\"readerFeatures\":[\"deletionVectors\"]}}\n",
  ) catch {
    _ => ()
  }
  let message = snapshot_error_of(root)
  assert_true(message.contains("readerFeatures"))
}

///|
test "protocol: deletion vector is rejected, not silently ignored" {
  let message = snapshot_error_of("artifacts/protocol-audit/fixtures/deletion-vector-add")
  assert_true(message.contains("deletionVector"))
  assert_true(message.contains("reader protocol version 3"))
}

///|
test "actions: unknown action is a structured error" {
  let root = "_build/test-tmp/table-unknown-action"
  reset_dir(root)
  ensure_dir(root + "/_delta_log") catch {
    _ => ()
  }
  write_text(
    root + "/_delta_log/00000000000000000000.json",
    "{\"protocol\":{\"minReaderVersion\":1,\"minWriterVersion\":1}}\n{\"futureAction\":{\"x\":1}}\n",
  ) catch {
    _ => ()
  }
  let message = snapshot_error_of(root)
  assert_true(message.contains("unknown action 'futureAction'"))
}

///|
test "actions: unknown 'sidecar' action is a structured error" {
  let message = snapshot_error_of("artifacts/protocol-audit/fixtures/unknown-actions")
  assert_true(message.contains("unknown action 'sidecar'"))
}

///|
test "actions: cdc is explicitly ignored and surfaced, txn is recognized" {
  let root = "_build/test-tmp/table-cdc-txn"
  reset_dir(root)
  ensure_dir(root + "/_delta_log") catch {
    _ => ()
  }
  write_text(
    root + "/_delta_log/00000000000000000000.json",
    "{\"protocol\":{\"minReaderVersion\":1,\"minWriterVersion\":1}}\n{\"metaData\":{\"id\":\"cdc\",\"format\":{\"provider\":\"parquet\",\"options\":{}},\"schemaString\":\"{\\\"type\\\":\\\"struct\\\",\\\"fields\\\":[{\\\"name\\\":\\\"id\\\",\\\"type\\\":\\\"long\\\",\\\"nullable\\\":true,\\\"metadata\\\":{}}]}\",\"partitionColumns\":[],\"configuration\":{}}}\n{\"cdc\":{\"path\":\"cdc-1.parquet\",\"partitionValues\":{},\"size\":1,\"dataChange\":false}}\n{\"txn\":{\"appId\":\"app-1\",\"version\":3}}\n",
  ) catch {
    _ => ()
  }
  let message = snapshot_error_of(root)
  assert_true(message == "", msg="unexpected error: " + message)
  let snapshot = DeltaTable::open(root).snapshot() catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_true(snapshot.ignored_actions.contains("cdc"))
  let txn = parse_action("{\"txn\":{\"appId\":\"app-1\",\"version\":3}}", 0) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  match txn {
    TxnAction(t) => {
      assert_eq(t.app_id, "app-1")
      assert_eq(t.version, 3L)
    }
    _ => fail("expected txn action")
  }
}

///|
test "log: non-20-digit log file names are rejected" {
  let message = snapshot_error_of("artifacts/protocol-audit/fixtures/nonstandard-log-name")
  assert_true(message.contains("invalid _delta_log file name '0.json'"))
}

///|
test "log: version_file_name round-trips through parse_log_name" {
  for v in [0, 1, 12, 999999] {
    assert_true(parse_log_name(version_file_name(v)) == Some(v))
  }
  assert_true(parse_log_name("0.json") is None)
  assert_true(parse_log_name("0000000000000000000x.json") is None)
  assert_true(parse_log_name("000000000000000000012.json") is None)
}

///|
test "read: percent-encoded add.path is decoded before file access" {
  let root = "_build/test-tmp/table-percent-path"
  reset_dir(root)
  let schema = "{\"type\":\"struct\",\"fields\":[{\"name\":\"id\",\"type\":\"long\",\"nullable\":true,\"metadata\":{}}]}"
  let created = create_table(root, schema, []) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(created, 0)
  let pcols : Array[@parquet.Column] = [
    @parquet.new_column("id", @parquet.Int64, @parquet.Optional),
  ]
  let rows : Array[Array[@parquet.Value]] = [[@parquet.Int64(42L)]]
  @parquet.write_file(root + "/my file.parquet", @parquet.new_parquet_file(pcols, rows, None)) catch {
    _ => fail("cannot write parquet")
  }
  let add = "{\"add\":{\"path\":\"my%20file.parquet\",\"partitionValues\":{},\"size\":0,\"modificationTime\":0,\"dataChange\":true,\"stats\":\"{\\\"numRecords\\\":1}\"}}"
  write_text(
    root + "/_delta_log/00000000000000000001.json",
    add + "\n",
  ) catch {
    _ => ()
  }
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
  assert_eq(result.row_count, 1)
  assert_eq(cell_to_display(result.columns[0].cells[0]), "42")
}

///|
test "schema: byte and short map to readable types" {
  let raw = "{\"type\":\"struct\",\"fields\":[{\"name\":\"b\",\"type\":\"byte\",\"nullable\":true,\"metadata\":{}},{\"name\":\"s\",\"type\":\"short\",\"nullable\":true,\"metadata\":{}}]}"
  let schema = parse_schema(raw) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(schema.fields[0].type_, ByteType)
  assert_eq(schema.fields[1].type_, ShortType)
  assert_true(schema_is_readable(schema))
  assert_eq(delta_type_name(schema.fields[0].type_), "byte")
}

///|
test "write: create refuses to overwrite an existing table" {
  let root = "_build/test-tmp/table-create-guard"
  reset_dir(root)
  let schema = "{\"type\":\"struct\",\"fields\":[{\"name\":\"id\",\"type\":\"long\",\"nullable\":true,\"metadata\":{}}]}"
  let created = create_table(root, schema, []) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(created, 0)
  let second = create_table(root, schema, []) catch {
    err => {
      assert_true("\{err}".contains("already exists"))
      return
    }
  }
  ignore(second)
  fail("unexpectedly recreated table")
}

///|
test "write: append validates column names, types and NOT NULL" {
  let root = "_build/test-tmp/table-append-guard"
  reset_dir(root)
  let schema = "{\"type\":\"struct\",\"fields\":[{\"name\":\"id\",\"type\":\"long\",\"nullable\":false,\"metadata\":{}},{\"name\":\"name\",\"type\":\"string\",\"nullable\":true,\"metadata\":{}}]}"
  let _ = create_table(root, schema, []) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let name_mismatch : Array[ReadColumn] = [
    { name: "num", type_: Int64Type, cells: [CellI64(1L)] },
    { name: "name", type_: StringType, cells: [CellStr("a")] },
  ]
  assert_true(append_error_of(root, name_mismatch).contains("is named 'num'"))
  let type_mismatch : Array[ReadColumn] = [
    { name: "id", type_: StringType, cells: [CellStr("1")] },
    { name: "name", type_: StringType, cells: [CellStr("a")] },
  ]
  assert_true(append_error_of(root, type_mismatch).contains("has type string"))
  let null_violation : Array[ReadColumn] = [
    { name: "id", type_: Int64Type, cells: [CellNull] },
    { name: "name", type_: StringType, cells: [CellStr("a")] },
  ]
  assert_true(append_error_of(root, null_violation).contains("NOT NULL"))
}

///|
test "write: commit is put-if-absent (write_text_new refuses existing files)" {
  let root = "_build/test-tmp/table-commit-guard"
  reset_dir(root)
  let schema = "{\"type\":\"struct\",\"fields\":[{\"name\":\"id\",\"type\":\"long\",\"nullable\":true,\"metadata\":{}}]}"
  let _ = create_table(root, schema, []) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let commit_path = root + "/_delta_log/00000000000000000001.json"
  write_text(commit_path, "{\"commitInfo\":{\"timestamp\":0}}\n") catch {
    _ => ()
  }
  let message = write_new_error_of(commit_path, "{\"commitInfo\":{}}\n")
  assert_true(message.contains("put-if-absent"))
  // append 的数据文件同样不覆盖：预创建同名数据文件后 append 必须失败
  let columns : Array[ReadColumn] = [
    { name: "id", type_: Int64Type, cells: [CellI64(1L)] },
  ]
  let pcols : Array[@parquet.Column] = [
    @parquet.new_column("id", @parquet.Int64, @parquet.Optional),
  ]
  @parquet.write_file(
    root + "/part-0000-1000-2.parquet",
    @parquet.new_parquet_file(pcols, [[@parquet.Int64(9L)]], None),
  ) catch {
    _ => ()
  }
  assert_true(append_error_of(root, columns).contains("data file already exists"))
}

///|
test "write: tables with writer protocol > 2 refuse appends" {
  let root = "_build/test-tmp/table-writer-gate"
  reset_dir(root)
  ensure_dir(root + "/_delta_log") catch {
    _ => ()
  }
  write_text(
    root + "/_delta_log/00000000000000000000.json",
    "{\"protocol\":{\"minReaderVersion\":1,\"minWriterVersion\":3}}\n{\"metaData\":{\"id\":\"w3\",\"format\":{\"provider\":\"parquet\",\"options\":{}},\"schemaString\":\"{\\\"type\\\":\\\"struct\\\",\\\"fields\\\":[{\\\"name\\\":\\\"id\\\",\\\"type\\\":\\\"long\\\",\\\"nullable\\\":true,\\\"metadata\\\":{}}]}\",\"partitionColumns\":[],\"configuration\":{}}}\n",
  ) catch {
    _ => ()
  }
  let columns : Array[ReadColumn] = [
    { name: "id", type_: Int64Type, cells: [CellI64(1L)] },
  ]
  assert_true(append_error_of(root, columns).contains("writer protocol version 3"))
}

///|
test "stats: malformed add.stats is a structured error, not silent zero" {
  let root = "_build/test-tmp/table-bad-stats"
  reset_dir(root)
  ensure_dir(root + "/_delta_log") catch {
    _ => ()
  }
  write_text(
    root + "/_delta_log/00000000000000000000.json",
    "{\"protocol\":{\"minReaderVersion\":1,\"minWriterVersion\":1}}\n{\"metaData\":{\"id\":\"bad\",\"format\":{\"provider\":\"parquet\",\"options\":{}},\"schemaString\":\"{\\\"type\\\":\\\"struct\\\",\\\"fields\\\":[{\\\"name\\\":\\\"id\\\",\\\"type\\\":\\\"long\\\",\\\"nullable\\\":true,\\\"metadata\\\":{}}]}\",\"partitionColumns\":[],\"configuration\":{}}}\n{\"add\":{\"path\":\"data.parquet\",\"partitionValues\":{},\"size\":0,\"modificationTime\":0,\"dataChange\":true,\"stats\":\"{oops\"}}\n",
  ) catch {
    _ => ()
  }
  let message = snapshot_error_of(root)
  assert_true(message.contains("add.stats"))
}

///|
test "read: byte and short columns round-trip from a delta-rs-written table" {
  let table = DeltaTable::open("fixtures/delta/byte-short") catch {
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
  assert_eq(snapshot.metadata.schema.fields[0].type_, ByteType)
  assert_eq(snapshot.metadata.schema.fields[1].type_, ShortType)
  let result = read_snapshot("fixtures/delta/byte-short", snapshot, None) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(result.row_count, 3)
  assert_eq(cell_to_display(result.columns[0].cells[0]), "1")
  assert_eq(cell_to_display(result.columns[0].cells[1]), "NULL")
  assert_eq(cell_to_display(result.columns[0].cells[2]), "3")
  assert_eq(cell_to_display(result.columns[1].cells[0]), "100")
  assert_eq(cell_to_display(result.columns[1].cells[1]), "200")
  assert_eq(cell_to_display(result.columns[1].cells[2]), "NULL")
}

