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
test "checkpoint: truncated log is recovered from checkpoint (not stale replay)" {
  let root = "artifacts/protocol-audit/fixtures/checkpoint-truncated"
  let table = DeltaTable::open(root) catch {
    err => {
      fail("open failed")
      return
    }
  }
  // _last_checkpoint / checkpoint 在 v10；JSON 只到 v9 —— 当前版本必须是 10
  let cv = table.current_version() catch {
    err => {
      fail("current_version failed")
      return
    }
  }
  assert_eq(cv, 10)
  let snapshot = table.snapshot() catch {
    err => {
      fail("snapshot failed")
      return
    }
  }
  assert_eq(snapshot.version, 10)
  assert_eq(snapshot.files.length(), 100)
  assert_eq(snapshot.total_rows, 100L)
  // 目标版本 9 仍被 JSON 日志覆盖（0..9 连续）→ 完整回放 90 个文件
  let older = table.snapshot_at(9) catch {
    err => {
      fail("snapshot_at(9) failed")
      return
    }
  }
  assert_eq(older.version, 9)
  assert_eq(older.files.length(), 90)
}

///|
test "checkpoint: checkpoint-only flat table is readable (100 files)" {
  let snapshot = DeltaTable::open("artifacts/protocol-audit/fixtures/checkpoint-only").snapshot() catch {
    err => {
      fail("snapshot failed")
      return
    }
  }
  assert_eq(snapshot.version, 10)
  assert_eq(snapshot.files.length(), 100)

  if true {
    assert_true(snapshot_error_of("fixtures/checkpoint-negative/deletion-vector").contains("deletionVector"))
    assert_true(snapshot_error_of("fixtures/checkpoint-negative/unknown-action").contains("unknown action 'futureAction'"))
    assert_true(snapshot_error_of("fixtures/checkpoint-negative/missing-protocol").contains("no protocol action"))
    assert_true(snapshot_error_of("fixtures/checkpoint-negative/missing-metadata").contains("no metaData action"))
  }
}

///|
test "checkpoint: checkpoint state agrees with JSON replay (table_a v10)" {
  let table = DeltaTable::open("fixtures/delta/table_a") catch {
    err => {
      fail("open failed")
      return
    }
  }
  let replayed = table.snapshot_at(10) catch {
    err => {
      fail("snapshot_at failed")
      return
    }
  }
  // checkpoint 覆盖 v1..v10 共 100 个 add 文件；与 JSON 回放互证
  assert_eq(replayed.files.length(), 100)
  assert_eq(replayed.total_rows, 100L)
}

///|
test "checkpoint: checkpoint-only-nested reads 5 rows with nested values" {
  let root = "artifacts/interop-nested/checkpoint-only-nested"
  let snapshot = DeltaTable::open(root).snapshot() catch {
    err => {
      fail("snapshot failed")
      return
    }
  }
  let result = read_snapshot(root, snapshot, None) catch {
    err => {
      fail("read failed")
      return
    }
  }
  assert_eq(result.row_count, 5)
  assert_eq(cell_to_display(result.columns[1].cells[0]), "{\"name\": alice, \"age\": 30}")
  assert_eq(cell_to_display(result.columns[2].cells[1]), "[]")
  assert_eq(cell_to_display(result.columns[3].cells[2]), "{k: v}")
}


///|
test "write: nested struct/list/map roundtrip (write -> read)" {
  let root = "_build/test-tmp/table-nested-write"
  reset_dir(root)
  let schema = "{\"type\":\"struct\",\"fields\":[" +
    "{\"name\":\"id\",\"type\":\"long\",\"nullable\":true,\"metadata\":{}}," +
    "{\"name\":\"profile\",\"type\":{\"type\":\"struct\",\"fields\":[{\"name\":\"name\",\"type\":\"string\",\"nullable\":true,\"metadata\":{}},{\"name\":\"age\",\"type\":\"integer\",\"nullable\":true,\"metadata\":{}}]},\"nullable\":true,\"metadata\":{}}," +
    "{\"name\":\"tags\",\"type\":{\"type\":\"array\",\"elementType\":\"string\",\"containsNull\":true},\"nullable\":true,\"metadata\":{}}," +
    "{\"name\":\"attrs\",\"type\":{\"type\":\"map\",\"keyType\":\"string\",\"valueType\":\"string\",\"valueContainsNull\":true},\"nullable\":true,\"metadata\":{}}" +
    "]}"
  let created = create_table(root, schema, []) catch {
    err => {
      fail("create failed: \{err}")
      return
    }
  }
  assert_eq(created, 0)
  let columns : Array[ReadColumn] = [
    {
      name: "id",
      type_: Int64Type,
      cells: [CellI64(1L), CellI64(2L), CellI64(3L), CellI64(4L), CellI64(5L)],
    },
    {
      name: "profile",
      type_: NestedType(
        kind="struct",
        fields=[
          { name: "name", type_: StringType, nullable: true },
          { name: "age", type_: Int32Type, nullable: true },
        ],
      ),
      cells: [
        CellStruct([
          ("name", CellStr("alice")),
          ("age", CellI32(30)),
        ]),
        CellNull,
        CellStruct([("name", CellNull), ("age", CellNull)]),
        CellStruct([("name", CellStr("bob")), ("age", CellNull)]),
        CellStruct([("name", CellNull), ("age", CellI32(42))]),
      ],
    },
    {
      name: "tags",
      type_: NestedType(
        kind="array",
        fields=[{ name: "element", type_: StringType, nullable: true }],
      ),
      cells: [
        CellList([CellStr("red"), CellStr("green")]),
        CellList([]),
        CellNull,
        CellList([CellStr("blue"), CellNull]),
        CellList([]),
      ],
    },
    {
      name: "attrs",
      type_: NestedType(
        kind="map",
        fields=[
          { name: "key", type_: StringType, nullable: false },
          { name: "value", type_: StringType, nullable: true },
        ],
      ),
      cells: [
        CellMap([(CellStr("k1"), CellStr("v1")), (CellStr("k2"), CellStr("v2"))]),
        CellMap([]),
        CellMap([(CellStr("k"), CellStr("v"))]),
        CellMap([]),
        CellNull,
      ],
    },
  ]
  let version = append_rows(root, columns, 1000L) catch {
    err => {
      fail("append failed: \{err}")
      return
    }
  }
  assert_eq(version, 1)
  let snapshot = DeltaTable::open(root).snapshot() catch {
    err => {
      fail("snapshot failed: \{err}")
      return
    }
  }
  let result = read_snapshot(root, snapshot, None) catch {
    err => {
      fail("read failed: \{err}")
      return
    }
  }
  assert_eq(result.row_count, 5)
  // 与 delta-rs oracle 相同的期望值（D3 案例 B 数据形态）
  assert_eq(cell_to_display(result.columns[1].cells[0]), "{\"name\": alice, \"age\": 30}")
  assert_eq(cell_to_display(result.columns[1].cells[1]), "NULL")
  assert_eq(cell_to_display(result.columns[1].cells[2]), "{\"name\": NULL, \"age\": NULL}")
  assert_eq(cell_to_display(result.columns[2].cells[0]), "[red, green]")
  assert_eq(cell_to_display(result.columns[2].cells[1]), "[]")
  assert_eq(cell_to_display(result.columns[2].cells[3]), "[blue, NULL]")
  assert_eq(cell_to_display(result.columns[3].cells[0]), "{k1: v1, k2: v2}")
  assert_eq(cell_to_display(result.columns[3].cells[1]), "{}")
  assert_eq(cell_to_display(result.columns[3].cells[4]), "NULL")

  if true {
    let root = "_build/test-tmp/table-nested-primitives"
    reset_dir(root)
    let schema = "{\"type\":\"struct\",\"fields\":[" +
      "{\"name\":\"id\",\"type\":\"long\",\"nullable\":true,\"metadata\":{}}," +
      "{\"name\":\"payload\",\"type\":{\"type\":\"struct\",\"fields\":[" +
      "{\"name\":\"f\",\"type\":\"float\",\"nullable\":true,\"metadata\":{}}," +
      "{\"name\":\"dt\",\"type\":\"date\",\"nullable\":true,\"metadata\":{}}," +
      "{\"name\":\"ts\",\"type\":\"timestamp\",\"nullable\":true,\"metadata\":{}}]}," +
      "\"nullable\":true,\"metadata\":{}}]}"
    ignore(create_table(root, schema, []) catch {
      err => {
        fail("\{err}")
        return
      }
    })
    let payload = NestedType(
      kind="struct",
      fields=[
        { name: "f", type_: FloatType, nullable: true },
        { name: "dt", type_: DateType, nullable: true },
        { name: "ts", type_: TimestampType, nullable: true },
      ],
    )
    let columns : Array[ReadColumn] = [
      { name: "id", type_: Int64Type, cells: [CellI64(1L)] },
      {
        name: "payload",
        type_: payload,
        cells: [
          CellStruct([
            ("f", CellF32(1.5)),
            ("dt", CellI32(20454)),
            ("ts", CellTs(1577836800000000L)),
          ]),
        ],
      },
    ]
    ignore(append_rows(root, columns, 1000L) catch {
      err => {
        fail("\{err}")
        return
      }
    })
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
    let text = cell_to_display(result.columns[1].cells[0])
    assert_true(text.contains("\"f\": 1.5"))
    assert_true(text.contains("\"dt\": 20454"))
    assert_true(text.contains("\"ts\": 1577836800000000"))
  }
}

///|
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

  if true {
    let root = "_build/test-tmp/table-all-primitives"
    reset_dir(root)
    let schema = "{\"type\":\"struct\",\"fields\":[" +
      "{\"name\":\"b\",\"type\":\"byte\",\"nullable\":true,\"metadata\":{}}," +
      "{\"name\":\"s\",\"type\":\"short\",\"nullable\":true,\"metadata\":{}}," +
      "{\"name\":\"i\",\"type\":\"integer\",\"nullable\":true,\"metadata\":{}}," +
      "{\"name\":\"l\",\"type\":\"long\",\"nullable\":true,\"metadata\":{}}," +
      "{\"name\":\"f\",\"type\":\"float\",\"nullable\":true,\"metadata\":{}}," +
      "{\"name\":\"d\",\"type\":\"double\",\"nullable\":true,\"metadata\":{}}," +
      "{\"name\":\"bo\",\"type\":\"boolean\",\"nullable\":true,\"metadata\":{}}," +
      "{\"name\":\"st\",\"type\":\"string\",\"nullable\":true,\"metadata\":{}}," +
      "{\"name\":\"bin\",\"type\":\"binary\",\"nullable\":true,\"metadata\":{}}," +
      "{\"name\":\"dt\",\"type\":\"date\",\"nullable\":true,\"metadata\":{}}," +
      "{\"name\":\"ts\",\"type\":\"timestamp\",\"nullable\":true,\"metadata\":{}}]}"
    ignore(create_table(root, schema, []) catch {
      err => {
        fail("\{err}")
        return
      }
    })
    let columns : Array[ReadColumn] = [
      { name: "b", type_: ByteType, cells: [CellI32(7), CellNull] },
      { name: "s", type_: ShortType, cells: [CellI32(300), CellNull] },
      { name: "i", type_: Int32Type, cells: [CellI32(42), CellNull] },
      { name: "l", type_: Int64Type, cells: [CellI64(123456789L), CellNull] },
      { name: "f", type_: FloatType, cells: [CellF32(1.5), CellNull] },
      { name: "d", type_: DoubleType, cells: [CellF64(2.25), CellNull] },
      { name: "bo", type_: BooleanType, cells: [CellBool(true), CellNull] },
      { name: "st", type_: StringType, cells: [CellStr("hello"), CellNull] },
      { name: "bin", type_: BinaryType, cells: [CellBin(b"raw"), CellNull] },
      { name: "dt", type_: DateType, cells: [CellI32(20454), CellNull] },
      {
        name: "ts",
        type_: TimestampType,
        cells: [CellTs(1577836800000000L), CellNull],
      },
    ]
    ignore(append_rows(root, columns, 1000L) catch {
      err => {
        fail("\{err}")
        return
      }
    })
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
    assert_eq(result.row_count, 2)
    assert_eq(cell_to_display(result.columns[0].cells[0]), "7")
    assert_eq(cell_to_display(result.columns[4].cells[0]), "1.5")
    assert_eq(cell_to_display(result.columns[9].cells[0]), "20454")
    assert_eq(cell_to_display(result.columns[10].cells[0]), "1577836800000000")
    assert_eq(cell_to_display(result.columns[10].cells[1]), "NULL")
  }
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

  if true {
    assert_eq(cell_to_display(cell_from_partition(DateType, "2026-01-01")), "20454")
    assert_eq(cell_to_display(cell_from_partition(ByteType, "7")), "7")
    assert_eq(cell_to_display(cell_from_partition(FloatType, "1.5")), "1.5")
    assert_eq(cell_to_display(cell_from_partition(BooleanType, "false")), "false")
    assert_eq(
      cell_to_display(cell_from_partition(TimestampType, "1577836800000000")),
      "1577836800000000",
    )
  }
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

  if true {
    let root = "_build/test-tmp/table-percent-remove"
    reset_dir(root)
    ensure_dir(root + "/_delta_log") catch { _ => () }
    let schema = "{\\\"type\\\":\\\"struct\\\",\\\"fields\\\":[{\\\"name\\\":\\\"id\\\",\\\"type\\\":\\\"long\\\",\\\"nullable\\\":true,\\\"metadata\\\":{}}]}"
    let header = "{\"protocol\":{\"minReaderVersion\":1,\"minWriterVersion\":2}}\n{\"metaData\":{\"id\":\"paths\",\"format\":{\"provider\":\"parquet\",\"options\":{}},\"schemaString\":\"" + schema + "\",\"partitionColumns\":[],\"configuration\":{}}}\n"
    write_text(root + "/_delta_log/00000000000000000000.json", header) catch { _ => () }
    write_text(
      root + "/_delta_log/00000000000000000001.json",
      "{\"add\":{\"path\":\"dir/my%20file.parquet\",\"partitionValues\":{},\"size\":1,\"modificationTime\":0,\"dataChange\":true,\"stats\":\"{\\\"numRecords\\\":1}\"}}\n",
    ) catch { _ => () }
    write_text(
      root + "/_delta_log/00000000000000000002.json",
      "{\"remove\":{\"path\":\"dir/my%20file.parquet\",\"deletionTimestamp\":1,\"dataChange\":true}}\n",
    ) catch { _ => () }
    let snapshot = DeltaTable::open(root).snapshot() catch {
      err => {
        fail("\{err}")
        return
      }
    }
    assert_eq(snapshot.files.length(), 0)
    assert_eq(snapshot.total_rows, 0L)
  }
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

  if true {
    let schema = parse_schema("{\"type\":\"struct\",\"fields\":[{\"name\":\"ts\",\"type\":\"timestamp_ntz\",\"nullable\":true,\"metadata\":{}}]}") catch {
      err => {
        fail("\{err}")
        return
      }
    }
    assert_false(schema_is_readable(schema))
  }
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
test "write: atomic publish exposes complete immutable files" {
  let root = "_build/test-tmp/table-commit-guard"
  reset_dir(root)
  ensure_dir(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let path = root + "/published.json"
  atomic_write_file(path, @utf8.encode("complete payload")) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(read_text(path), "complete payload")
  let message = write_new_error_of(path, "replacement")
  assert_true(message.contains("commit conflict"))
  assert_eq(read_text(path), "complete payload")
  for name in @fs.read_dir(root) {
    assert_false(name.contains("moondelta-tmp"))
  }
}

///|
test "write: empty append and staged log residue do not create a version" {
  let root = "_build/test-tmp/table-empty-append"
  reset_dir(root)
  let schema = "{\"type\":\"struct\",\"fields\":[{\"name\":\"id\",\"type\":\"long\",\"nullable\":true,\"metadata\":{}}]}"
  let _ = create_table(root, schema, []) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let columns : Array[ReadColumn] = [
    { name: "id", type_: Int64Type, cells: [] },
  ]
  assert_true(append_error_of(root, columns).contains("empty batches"))
  let snapshot = DeltaTable::open(root).snapshot() catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(snapshot.version, 0)
  assert_eq(snapshot.files.length(), 0)
  assert_true(@fs.path_exists(log_dir(root) + "/00000000000000000000.json"))
  assert_eq(@fs.read_dir(log_dir(root)).length(), 1)
  write_text(
    log_dir(root) + "/00000000000000000001.json.moondelta-tmp-crash",
    "{\"commitInfo\":",
  ) catch { _ => () }
  let versions = list_versions(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(versions.length(), 1)
  assert_eq(versions[0], 0)
}

///|
test "log: empty or truncated formal commits fail closed, commitInfo-only remains valid" {
  let root = "_build/test-tmp/table-empty-formal-log"
  reset_dir(root)
  let schema = "{\"type\":\"struct\",\"fields\":[{\"name\":\"id\",\"type\":\"long\",\"nullable\":true,\"metadata\":{}}]}"
  assert_eq(create_table(root, schema, []), 0)
  let path = log_dir(root) + "/" + version_file_name(1)
  let columns : Array[ReadColumn] = [
    { name: "id", type_: Int64Type, cells: [CellI64(1L)] },
  ]
  for content in ["", "\n\r\n", " \t\n", "{\"commitInfo\":"] {
    // Deliberately simulate a damaged external formal commit, bypassing publication.
    write_text(path, content)
    let message = snapshot_error_of(root)
    assert_true(message.length() > 0)
    if content == "" || content == "\n\r\n" {
      assert_true(message.contains("contains no actions"))
    }
    assert_eq(DeltaTable::open(root).snapshot_at(0).version, 0)
    assert_false(append_error_of(root, columns).contains("unexpectedly appended"))
    assert_true(@fs.path_exists(log_dir(root) + "/00000000000000000000.json"))
    assert_eq(@fs.read_dir(log_dir(root)).length(), 2)
  }
  write_text(path, "{\"commitInfo\":{\"operation\":\"TEST\"}}\n")
  let snapshot = DeltaTable::open(root).snapshot()
  assert_eq(snapshot.version, 1)
  assert_eq(snapshot.files.length(), 0)
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

  if true {
    let v5 = snapshot_error_of("artifacts/protocol-audit/fixtures/writer-v5-features")
    assert_true(v5.contains("writer protocol version 5"))
    let root = "_build/test-tmp/table-writer-features-read"
    reset_dir(root)
    ensure_dir(root + "/_delta_log") catch { _ => () }
    write_text(
      root + "/_delta_log/00000000000000000000.json",
      "{\"protocol\":{\"minReaderVersion\":1,\"minWriterVersion\":2,\"writerFeatures\":[\"appendOnly\"]}}\n",
    ) catch { _ => () }
    assert_true(snapshot_error_of(root).contains("writerFeatures"))
  }
  if true {
    let root = "_build/test-tmp/table-writer-features"
    reset_dir(root)
    ensure_dir(root + "/_delta_log") catch { _ => () }
    write_text(
      root + "/_delta_log/00000000000000000000.json",
      "{\"protocol\":{\"minReaderVersion\":1,\"minWriterVersion\":2,\"writerFeatures\":[\"appendOnly\"]}}\n{\"metaData\":{\"id\":\"wf\",\"format\":{\"provider\":\"parquet\",\"options\":{}},\"schemaString\":\"{\\\"type\\\":\\\"struct\\\",\\\"fields\\\":[{\\\"name\\\":\\\"id\\\",\\\"type\\\":\\\"long\\\",\\\"nullable\\\":true,\\\"metadata\\\":{}}]}\",\"partitionColumns\":[],\"configuration\":{}}}\n",
    ) catch { _ => () }
    let columns : Array[ReadColumn] = [
      { name: "id", type_: Int64Type, cells: [CellI64(1L)] },
    ]
    assert_true(append_error_of(root, columns).contains("writerFeatures"))
    assert_eq(list_versions(root).length(), 1)
  }
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

  if true {
    let message = snapshot_error_of("artifacts/protocol-audit/fixtures/stats-object")
    assert_true(message.contains("add.stats must be a JSON-encoded string"))
  }
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

  if true {
    let table = DeltaTable::open("artifacts/protocol-audit/fixtures/type-mismatch") catch {
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
    let message = read_error_of("artifacts/protocol-audit/fixtures/type-mismatch", snapshot)
    assert_true(message.contains("long in the Delta schema but"))
    assert_true(message.contains("String in parquet"))
  }
  if true {
    let root = "artifacts/protocol-audit/fixtures/int96-timestamp"
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
    assert_eq(result.row_count, 2)
    assert_eq(result.columns[0].type_, TimestampType)
  }
}


///|
test "sha256: FIPS 180-4 vectors and hex encoding" {
  let empty : Array[Byte] = []
  assert_eq(
    sha256_hex(Bytes::from_array(empty)),
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  )
  assert_eq(
    sha256_hex(@utf8.encode("abc")),
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
  )
  assert_eq(
    sha256_hex(@utf8.encode("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq")),
    "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
  )
  assert_eq(
    sha256_hex(@utf8.encode("The quick brown fox jumps over the lazy dog")),
    "d7a8fbb307d7809469ca9abcb0082e4f8d5651e46d3cdb762d02d0bf37c9e592",
  )
}

///|
fn test_columns(ids : Array[Int64]) -> Array[ReadColumn] {
  let cells : Array[Cell] = []
  for id in ids {
    cells.push(CellI64(id))
  }
  [{ name: "id", type_: Int64Type, cells }, { name: "name", type_: StringType, cells: {
    let names : Array[Cell] = []
    for _ in ids {
      names.push(CellStr("row"))
    }
    names
  } }]
}

///|
fn test_schema() -> String {
  "{\"type\":\"struct\",\"fields\":[{\"name\":\"id\",\"type\":\"long\",\"nullable\":false,\"metadata\":{}},{\"name\":\"name\",\"type\":\"string\",\"nullable\":true,\"metadata\":{}}]}"
}

///|
fn make_test_table(root : String) -> Unit raise {
  reset_dir(root)
  let _ = create_table(root, test_schema(), [])
}

///|
fn relative_files(root : String) -> Array[String] {
  let out : Array[String] = []
  let names = @fs.read_dir(root) catch { _ => return out }
  for name in names {
    let path = root + "/" + name
    if (@fs.is_dir(path) catch { _ => false }) {
      for child in relative_files(path) {
        out.push(name + "/" + child)
      }
    } else {
      out.push(name)
    }
  }
  out.sort()
  out
}

///|
fn count_matching(root : String, needle : String) -> Int32 {
  let mut total = 0
  for name in relative_files(root) {
    if name.contains(needle) {
      total = total + 1
    }
  }
  total
}

///|
fn count_suffix(root : String, suffix : String) -> Int32 {
  let mut total = 0
  for name in relative_files(root) {
    if name.has_suffix(suffix) {
      total = total + 1
    }
  }
  total
}

///|
fn count_formal_commits(root : String) -> Int32 {
  let names = @fs.read_dir(log_dir(root)) catch { _ => return 0 }
  let mut total = 0
  for name in names {
    if name.has_suffix(".json") {
      total = total + 1
    }
  }
  total
}

///|
test "txn: ids are OS-random, unique and embedded in the data file name" {
  let seen : Array[String] = []
  let mut i = 0
  while i < 200 {
    let id = random_txn_id() catch {
      err => {
        fail("\{err}")
        return
      }
    }
    assert_eq(id.char_length(), 32)
    assert_true(is_txn_id(id))
    assert_false(seen.contains(id))
    seen.push(id)
    i = i + 1
  }
  let root = "_build/test-tmp/txn-id-embed"
  make_test_table(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let (version, txn) = append_rows_with_txn(root, test_columns([7L]), 1000L) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(version, 1)
  assert_true(is_txn_id(txn))
  let data_name = "part-1000-1-" + txn + ".parquet"
  assert_true(@fs.path_exists(root + "/" + data_name))
  assert_eq(count_matching(root, txn), 1)
  let commit = read_text(log_dir(root) + "/" + version_file_name(1))
  assert_true(commit.contains("\"moondeltaTxnId\":\"" + txn + "\""))
  // 成功提交后 intent 与临时名都已清理
  assert_eq(list_intent_dir(root).length(), 0)
  assert_eq(count_matching(root, ".moondelta-tmp-"), 0)
}

///|
test "txn: query reports committed, not-committed, conflict and corrupt intent" {
  let root = "_build/test-tmp/txn-query"
  make_test_table(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  // 已提交但清理未执行：commit 可见、intent 仍在
  ignore(append_rows_impl(root, test_columns([11L]), 1000L, TxnAbortAfterCommit) catch {
    DeltaError::IoError(_) => (0, "")
    err => {
      fail("\{err}")
      return
    }
  })
  let intents = list_intent_dir(root)
  assert_eq(intents.length(), 1)
  let txn = intents[0].substring(start=0, end=32)
  let status = query_transaction(root, txn) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  if platform_confirms_directory_durability() {
    assert_eq(status.state, TxnCommitted)
  } else {
    assert_eq(status.state, TxnCommittedUnconfirmed)
  }
  assert_eq(status.version, 1)
  // 数据已发布、commit 未发布
  ignore(append_rows_impl(root, test_columns([12L]), 1001L, TxnAbortAfterDataPublish) catch {
    DeltaError::IoError(_) => (0, "")
    err => {
      fail("\{err}")
      return
    }
  })
  let pending_names = list_intent_dir(root)
  assert_eq(pending_names.length(), 2)
  let mut pending_txn = ""
  for name in pending_names {
    if name.substring(start=0, end=32) != txn {
      pending_txn = name.substring(start=0, end=32)
    }
  }
  let pending = query_transaction(root, pending_txn) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(pending.state, TxnNotCommitted)
  assert_eq(pending.version, 2)
  let version_zero = log_dir(root) + "/" + version_file_name(0)
  let valid_version_zero = read_text(version_zero)
  write_text(version_zero, "\n \t\n") catch { _ => () }
  let empty_history = query_transaction(root, pending_txn) catch {
    err => {
      fail("query failed for empty formal history: {err}")
      return
    }
  }
  assert_eq(empty_history.state, TxnUnresolved)
  write_text(version_zero, "{not json}\n") catch { _ => () }
  let corrupt_history = query_transaction(root, pending_txn) catch {
    err => {
      fail("query failed for corrupt formal history: {err}")
      return
    }
  }
  assert_eq(corrupt_history.state, TxnUnresolved)
  write_text(version_zero, valid_version_zero) catch { _ => () }
  // 目标版本被其他事务占用 → conflict
  let conflict_txn = random_txn_id() catch {
    _ => ""
  }
  let conflict : TxnIntent = {
    format_version: 1,
    txn_id: conflict_txn,
    kind: "append",
    version: 1,
    data_temp: Some("part-conflict.parquet" + temp_name(conflict_txn)),
    data_path: Some("part-conflict.parquet"),
    data_length: 4L,
    data_digest: Some("0000000000000000000000000000000000000000000000000000000000000000"),
    commit_temp: "_delta_log/" + version_file_name(1) + temp_name(conflict_txn),
    commit_path: "_delta_log/" + version_file_name(1),
    commit_digest: "1111111111111111111111111111111111111111111111111111111111111111",
  }
  write_text(intent_path(root, conflict_txn), txn_intent_to_json(conflict)) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let conflict_status = query_transaction(root, conflict_txn) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(conflict_status.state, TxnConflict)
  // intent 损坏 → unresolved，而不是“未提交”
  write_text(intent_path(root, conflict_txn), "{not json") catch { _ => () }
  let corrupt = query_transaction(root, conflict_txn) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(corrupt.state, TxnUnresolved)
}

///|
test "locks: shared writers coexist and block the non-blocking recoverer" {
  let root = "_build/test-tmp/lock-semantics"
  make_test_table(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let first = acquire_shared_lock(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let busy = try_acquire_exclusive_lock(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_true(busy is None)
  let second = acquire_shared_lock(root) catch {
    err => {
      fail("second shared lock failed: \{err}")
      return
    }
  }
  release_table_lock(first)
  let still_busy = try_acquire_exclusive_lock(root) catch { _ => None }
  assert_true(still_busy is None)
  release_table_lock(second)
  let exclusive = try_acquire_exclusive_lock(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  match exclusive {
    Some(handle) => release_table_lock(handle)
    None => fail("exclusive lock was not released with the last shared holder")
  }
}

///|
test "recover: dry-run previews, apply needs the managed policy, cleanup is idempotent" {
  let root = "_build/test-tmp/recover-policy"
  make_test_table(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  ignore(append_rows_impl(root, test_columns([21L]), 1000L, TxnAbortAfterDataPublish) catch {
    DeltaError::IoError(_) => (0, "")
    err => {
      fail("\{err}")
      return
    }
  })
  assert_eq(count_suffix(root, ".parquet"), 1)
  assert_eq(list_intent_dir(root).length(), 1)
  // 默认 dry-run：只报告，不删除
  let preview = recover_table(root, true) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_false(preview.applied)
  assert_eq(preview.deleted, 0)
  assert_eq(count_suffix(root, ".parquet"), 1)
  let mut saw_candidate = false
  for entry in preview.entries {
    if entry.action == "would-delete" {
      saw_candidate = true
    }
  }
  assert_true(saw_candidate)
  // 未标记受管：apply 被策略阻止
  let blocked = recover_table(root, false) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_false(blocked.applied)
  assert_true(blocked.blocked is Some(_))
  assert_eq(count_suffix(root, ".parquet"), 1)
  // 显式受管后才真正清理
  set_managed_table(root, true) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_true(managed_table(root))
  let applied = recover_table(root, false) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_true(applied.applied)
  assert_true(applied.deleted >= 2)
  assert_eq(count_suffix(root, ".parquet"), 0)
  assert_eq(count_matching(root, ".moondelta-tmp-"), 0)
  assert_eq(list_intent_dir(root).length(), 0)
  let snapshot = DeltaTable::open(root).snapshot() catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(snapshot.version, 0)
  assert_eq(snapshot.files.length(), 0)
  // 幂等：再次恢复删除 0 个文件
  let again = recover_table(root, false) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(again.deleted, 0)
  assert_eq(again.entries.length(), 0)
}

///|
test "recover: committed transaction keeps its data and only residue is removed" {
  let root = "_build/test-tmp/recover-committed"
  make_test_table(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  ignore(append_rows_impl(root, test_columns([31L]), 1000L, TxnAbortAfterCommit) catch {
    DeltaError::IoError(_) => (0, "")
    err => {
      fail("\{err}")
      return
    }
  })
  assert_eq(count_matching(root, ".moondelta-tmp-"), 2)
  assert_eq(list_intent_dir(root).length(), 1)
  set_managed_table(root, true) catch { _ => () }
  let report = recover_table(root, false) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_true(report.applied)
  // 正式数据与 commit 保留，临时名与 intent 清除
  assert_eq(count_matching(root, ".parquet.moondelta-tmp-"), 0)
  assert_eq(count_matching(root, ".json.moondelta-tmp-"), 0)
  assert_eq(list_intent_dir(root).length(), 0)
  assert_eq(count_suffix(root, ".parquet"), 1)
  assert_eq(count_formal_commits(root), 2)
  let snapshot = DeltaTable::open(root).snapshot() catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(snapshot.version, 1)
  assert_eq(snapshot.total_rows, 1L)
}

///|
test "recover: history protection keeps removed-but-reachable data, log gaps block deletion" {
  let root = "_build/test-tmp/recover-protection"
  make_test_table(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let (_, history_txn) = append_rows_with_txn(root, test_columns([41L]), 1000L) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let history_data = "part-1000-1-" + history_txn + ".parquet"
  let bytes = @fs.read_file_to_bytes(root + "/" + history_data) catch { _ => Bytes::make(0, 0) }
  let orphan_txn = random_txn_id() catch { _ => "" }
  let orphan : TxnIntent = {
    format_version: 1,
    txn_id: orphan_txn,
    kind: "append",
    version: 2,
    data_temp: Some(history_data + temp_name(orphan_txn)),
    data_path: Some(history_data),
    data_length: bytes.length().to_int64(),
    data_digest: Some(sha256_hex(bytes)),
    commit_temp: "_delta_log/" + version_file_name(2) + temp_name(orphan_txn),
    commit_path: "_delta_log/" + version_file_name(2),
    commit_digest: "2222222222222222222222222222222222222222222222222222222222222222",
  }
  write_text(intent_path(root, orphan_txn), txn_intent_to_json(orphan)) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  set_managed_table(root, true) catch { _ => () }
  let report = recover_table(root, false) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_true(@fs.path_exists(root + "/" + history_data))
  let mut protected_reported = false
  for entry in report.entries {
    if entry.action == "keep" && entry.reason.contains("protected") {
      protected_reported = true
    }
  }
  assert_true(protected_reported)
  // 日志缺口：候选正式数据不得删除，intent 保留
  ignore(append_rows_with_txn(root, test_columns([42L]), 1002L) catch {
    _ => (0, "")
  })
  @fs.remove_file(root + "/_delta_log/" + version_file_name(1)) catch { _ => () }
  let broken_txn = random_txn_id() catch { _ => "" }
  write_text(root + "/part-broken.parquet", "not a real parquet") catch { _ => () }
  let broken : TxnIntent = {
    format_version: 1,
    txn_id: broken_txn,
    kind: "append",
    version: 3,
    data_temp: Some("part-broken.parquet" + temp_name(broken_txn)),
    data_path: Some("part-broken.parquet"),
    data_length: 18L,
    data_digest: Some("3333333333333333333333333333333333333333333333333333333333333333"),
    commit_temp: "_delta_log/" + version_file_name(3) + temp_name(broken_txn),
    commit_path: "_delta_log/" + version_file_name(3),
    commit_digest: "4444444444444444444444444444444444444444444444444444444444444444",
  }
  write_text(intent_path(root, broken_txn), txn_intent_to_json(broken)) catch { _ => () }
  let gap_report = recover_table(root, false) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_false(gap_report.log_complete)
  assert_true(@fs.path_exists(root + "/part-broken.parquet"))
  assert_true(@fs.path_exists(intent_path(root, broken_txn)))
}

///|
test "recover: unowned legacy names and unknown files are preserved" {
  let root = "_build/test-tmp/recover-unknown"
  make_test_table(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let legacy = root + "/part-legacy.moondelta-tmp-abcdef"
  let windows_like = root + "/mdt1234.tmp"
  let unknown_intent = intents_dir(root) + "/notes.txt"
  write_text(legacy, "junk") catch { _ => () }
  write_text(windows_like, "junk") catch { _ => () }
  write_text(unknown_intent, "junk") catch { _ => () }
  set_managed_table(root, true) catch { _ => () }
  let report = recover_table(root, false) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_true(@fs.path_exists(legacy))
  assert_true(@fs.path_exists(windows_like))
  assert_true(@fs.path_exists(unknown_intent))
  let mut reported = 0
  for entry in report.entries {
    if entry.action == "keep" && entry.reason.contains("unowned") {
      reported = reported + 1
    }
  }
  assert_true(reported >= 2)
}

///|
test "recover: an active writer's shared lock reports busy and removes nothing" {
  let root = "_build/test-tmp/recover-lock-busy"
  make_test_table(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  ignore(append_rows_impl(root, test_columns([51L]), 1000L, TxnAbortAfterDataPublish) catch {
    DeltaError::IoError(_) => (0, "")
    err => {
      fail("\{err}")
      return
    }
  })
  set_managed_table(root, true) catch { _ => () }
  let writer = acquire_shared_lock(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let busy = recover_table(root, false) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_true(busy.lock_busy)
  assert_false(busy.applied)
  assert_eq(count_suffix(root, ".parquet"), 1)
  release_table_lock(writer)
  let after = recover_table(root, false) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_false(after.lock_busy)
  assert_true(after.deleted >= 1)
  assert_eq(count_suffix(root, ".parquet"), 0)
}

///|
test "recover: an intent-only residue is settled without touching table data" {
  let root = "_build/test-tmp/recover-intent-only"
  make_test_table(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  ignore(append_rows_impl(root, test_columns([61L]), 1000L, TxnAbortAfterIntent) catch {
    DeltaError::IoError(_) => (0, "")
    err => {
      fail("\{err}")
      return
    }
  })
  assert_eq(count_suffix(root, ".parquet"), 0)
  assert_eq(list_intent_dir(root).length(), 1)
  set_managed_table(root, true) catch { _ => () }
  let report = recover_table(root, false) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_true(report.deleted >= 1)
  assert_eq(list_intent_dir(root).length(), 0)
  let snapshot = DeltaTable::open(root).snapshot() catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(snapshot.version, 0)
  assert_eq(snapshot.files.length(), 0)
}

///|
test "recover: a managed writer settles stale residue before appending" {
  let root = "_build/test-tmp/recover-auto"
  make_test_table(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  set_managed_table(root, true) catch { _ => () }
  ignore(append_rows_impl(root, test_columns([71L]), 1000L, TxnAbortAfterDataPublish) catch {
    DeltaError::IoError(_) => (0, "")
    err => {
      fail("\{err}")
      return
    }
  })
  assert_eq(list_intent_dir(root).length(), 1)
  let (version, _) = append_rows_with_txn(root, test_columns([72L]), 1001L) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(version, 1)
  assert_eq(list_intent_dir(root).length(), 0)
  assert_eq(count_matching(root, ".moondelta-tmp-"), 0)
  assert_eq(count_suffix(root, ".parquet"), 1)
  let rows = read_snapshot(root, DeltaTable::open(root).snapshot() catch { _ => return }, None) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_eq(rows.row_count, 1)
  match rows.columns[0].cells[0] {
    CellI64(value) => assert_eq(value, 72L)
    _ => fail("unexpected first cell in managed writer test")
  }
}

///|
test "recover: a remove action keeps data reachable by older versions" {
  let root = "_build/test-tmp/recover-remove-history"
  make_test_table(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let (_, txn) = append_rows_with_txn(root, test_columns([81L]), 1000L) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let data_name = "part-1000-1-" + txn + ".parquet"
  let bytes = @fs.read_file_to_bytes(root + "/" + data_name) catch { _ => Bytes::make(0, 0) }
  let remove_log = "{\"commitInfo\":{\"timestamp\":1001,\"operation\":\"DELETE\"}}\n" +
    "{\"remove\":{\"path\":" +
    json_string(@percent.encode(data_name)) +
    ",\"deletionTimestamp\":1001,\"dataChange\":true}}\n"
  write_text(log_dir(root) + "/" + version_file_name(2), remove_log) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let table = DeltaTable::open(root) catch { _ => return }
  let v1 = table.snapshot_at(1) catch { _ => return }
  assert_eq(v1.files.length(), 1)
  let v2 = table.snapshot_at(2) catch { _ => return }
  assert_eq(v2.files.length(), 0)
  let orphan_txn = random_txn_id() catch { _ => "" }
  let orphan : TxnIntent = {
    format_version: 1,
    txn_id: orphan_txn,
    kind: "append",
    version: 3,
    data_temp: Some(data_name + temp_name(orphan_txn)),
    data_path: Some(data_name),
    data_length: bytes.length().to_int64(),
    data_digest: Some(sha256_hex(bytes)),
    commit_temp: "_delta_log/" + version_file_name(3) + temp_name(orphan_txn),
    commit_path: "_delta_log/" + version_file_name(3),
    commit_digest: "5555555555555555555555555555555555555555555555555555555555555555",
  }
  write_text(intent_path(root, orphan_txn), txn_intent_to_json(orphan)) catch { _ => () }
  set_managed_table(root, true) catch { _ => () }
  let report = recover_table(root, false) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_true(@fs.path_exists(root + "/" + data_name))
  let mut reported = false
  for entry in report.entries {
    if entry.action == "keep" && entry.reason.contains("protected") {
      reported = true
    }
  }
  assert_true(reported)
  let version1 = table.snapshot_at(1) catch { _ => return }
  assert_eq(version1.files.length(), 1)
  assert_eq(version1.files[0].path, data_name)
}

///|
test "recover: percent-encoded add paths protect their decoded file" {
  let root = "_build/test-tmp/recover-percent-path"
  make_test_table(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let decoded = "part-中.parquet"
  let bytes = @utf8.encode("percent-encoded-protection")
  write_text(root + "/" + decoded, "percent-encoded-protection") catch { _ => () }
  ignore(bytes)
  let add_line = "{\"add\":{\"path\":" +
    json_string(@percent.encode(decoded)) +
    ",\"partitionValues\":{},\"size\":27,\"modificationTime\":1000,\"dataChange\":true}}\n"
  write_text(log_dir(root) + "/" + version_file_name(1), add_line) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let (protected_paths, complete) = collect_protected_paths(root)
  assert_true(complete)
  assert_true(protected_paths.contains(decoded))
  let orphan_txn = random_txn_id() catch { _ => "" }
  let orphan : TxnIntent = {
    format_version: 1,
    txn_id: orphan_txn,
    kind: "append",
    version: 2,
    data_temp: Some(decoded + temp_name(orphan_txn)),
    data_path: Some(decoded),
    data_length: 27L,
    data_digest: Some(sha256_hex(@utf8.encode("percent-encoded-protection"))),
    commit_temp: "_delta_log/" + version_file_name(2) + temp_name(orphan_txn),
    commit_path: "_delta_log/" + version_file_name(2),
    commit_digest: "6666666666666666666666666666666666666666666666666666666666666666",
  }
  write_text(intent_path(root, orphan_txn), txn_intent_to_json(orphan)) catch { _ => () }
  set_managed_table(root, true) catch { _ => () }
  let report = recover_table(root, false) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_true(@fs.path_exists(root + "/" + decoded))
  let mut reported = false
  for entry in report.entries {
    if entry.action == "keep" && entry.reason.contains("protected") {
      reported = true
    }
  }
  assert_true(reported)
}

///|
test "recover: multi-part checkpoints disable deletion instead of guessing" {
  let root = "_build/test-tmp/recover-multipart-checkpoint"
  make_test_table(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  ignore(append_rows_with_txn(root, test_columns([82L]), 1000L) catch { _ => (0, "") })
  write_text(
    log_dir(root) + "/00000000000000000001.checkpoint.1.2.parquet",
    "not a real checkpoint",
  ) catch { _ => () }
  let junk = root + "/part-junk.parquet"
  write_text(junk, "junk") catch { _ => () }
  let txn = random_txn_id() catch { _ => "" }
  let intent : TxnIntent = {
    format_version: 1,
    txn_id: txn,
    kind: "append",
    version: 2,
    data_temp: Some("part-junk.parquet" + temp_name(txn)),
    data_path: Some("part-junk.parquet"),
    data_length: 4L,
    data_digest: Some(sha256_hex(@utf8.encode("junk"))),
    commit_temp: "_delta_log/" + version_file_name(2) + temp_name(txn),
    commit_path: "_delta_log/" + version_file_name(2),
    commit_digest: "7777777777777777777777777777777777777777777777777777777777777777",
  }
  write_text(intent_path(root, txn), txn_intent_to_json(intent)) catch { _ => () }
  set_managed_table(root, true) catch { _ => () }
  let report = recover_table(root, false) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_false(report.log_complete)
  assert_true(@fs.path_exists(junk))
  assert_true(@fs.path_exists(intent_path(root, txn)))
}

///|
test "recover: a table outside the supported reader subset is never cleaned" {
  let root = "_build/test-tmp/recover-unsupported-protocol"
  reset_dir(root)
  ensure_dir(log_dir(root)) catch { _ => return }
  let header = "{\"commitInfo\":{\"timestamp\":0,\"operation\":\"CREATE TABLE\"}}\n" +
    "{\"protocol\":{\"minReaderVersion\":2,\"minWriterVersion\":2}}\n" +
    "{\"metaData\":{\"id\":\"unsupported\",\"format\":{\"provider\":\"parquet\"},\"schemaString\":" +
    json_string(test_schema()) +
    ",\"partitionColumns\":[],\"configuration\":{}}}\n"
  write_text(log_dir(root) + "/" + version_file_name(0), header) catch { _ => return }
  ensure_dir(intents_dir(root)) catch { _ => return }
  let junk = root + "/part-junk.parquet"
  write_text(junk, "junk") catch { _ => () }
  let txn = random_txn_id() catch { _ => "" }
  let intent : TxnIntent = {
    format_version: 1,
    txn_id: txn,
    kind: "append",
    version: 1,
    data_temp: Some("part-junk.parquet" + temp_name(txn)),
    data_path: Some("part-junk.parquet"),
    data_length: 4L,
    data_digest: Some(sha256_hex(@utf8.encode("junk"))),
    commit_temp: "_delta_log/" + version_file_name(1) + temp_name(txn),
    commit_path: "_delta_log/" + version_file_name(1),
    commit_digest: "8888888888888888888888888888888888888888888888888888888888888888",
  }
  write_text(intent_path(root, txn), txn_intent_to_json(intent)) catch { _ => () }
  set_managed_table(root, true) catch { _ => () }
  let report = recover_table(root, false) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_false(report.log_complete)
  assert_true(@fs.path_exists(junk))
  assert_true(@fs.path_exists(intent_path(root, txn)))
  let mut reported = false
  for entry in report.entries {
    if entry.action == "keep" && entry.reason.contains("outside the supported") {
      reported = true
    }
  }
  assert_true(reported)
}

///|
test "log: delta path encoding keeps unreserved characters literal" {
  assert_eq(
    encode_action_path("part-1000-1-abc.parquet"),
    "part-1000-1-abc.parquet",
  )
  assert_eq(
    encode_action_path("part 中.parquet"),
    "part%20%E4%B8%AD.parquet",
  )
  assert_eq(encode_action_path("a_b~c.parquet"), "a_b~c.parquet")
}

///|
test "recover: traversal and aliased intents are preserved and reported" {
  let root = "_build/test-tmp/recover-hostile-intents"
  make_test_table(root) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  let digest = "9999999999999999999999999999999999999999999999999999999999999999"
  let bad_txn = random_txn_id() catch { _ => "" }
  let bad_json = "{\"formatVersion\":1,\"txnId\":" +
    json_string(bad_txn) +
    ",\"kind\":\"append\",\"version\":1,\"dataTempPath\":" +
    json_string("../victim.parquet" + temp_name(bad_txn)) +
    ",\"dataPath\":\"../victim.parquet\",\"dataLength\":4,\"dataDigest\":" +
    json_string(digest) +
    ",\"commitTempPath\":" +
    json_string("_delta_log/" + version_file_name(1) + temp_name(bad_txn)) +
    ",\"commitPath\":" +
    json_string("_delta_log/" + version_file_name(1)) +
    ",\"commitDigest\":" +
    json_string(digest) +
    "}"
  write_text(intent_path(root, bad_txn), bad_json) catch { _ => () }
  let first_txn = random_txn_id() catch { _ => "" }
  let second_txn = random_txn_id() catch { _ => "" }
  let shared = "part-shared.parquet"
  let first : TxnIntent = {
    format_version: 1,
    txn_id: first_txn,
    kind: "append",
    version: 1,
    data_temp: Some(shared + temp_name(first_txn)),
    data_path: Some(shared),
    data_length: 4L,
    data_digest: Some(digest),
    commit_temp: "_delta_log/" + version_file_name(1) + temp_name(first_txn),
    commit_path: "_delta_log/" + version_file_name(1),
    commit_digest: digest,
  }
  let second : TxnIntent = {
    format_version: 1,
    txn_id: second_txn,
    kind: "append",
    version: 2,
    data_temp: Some(shared + temp_name(second_txn)),
    data_path: Some(shared),
    data_length: 4L,
    data_digest: Some(digest),
    commit_temp: "_delta_log/" + version_file_name(2) + temp_name(second_txn),
    commit_path: "_delta_log/" + version_file_name(2),
    commit_digest: digest,
  }
  write_text(intent_path(root, first_txn), txn_intent_to_json(first)) catch { _ => () }
  write_text(intent_path(root, second_txn), txn_intent_to_json(second)) catch { _ => () }
  set_managed_table(root, true) catch { _ => () }
  let report = recover_table(root, false) catch {
    err => {
      fail("\{err}")
      return
    }
  }
  assert_true(@fs.path_exists(intent_path(root, bad_txn)))
  assert_true(@fs.path_exists(intent_path(root, first_txn)))
  assert_true(@fs.path_exists(intent_path(root, second_txn)))
  assert_false(@fs.path_exists("_build/test-tmp/victim.parquet"))
  let mut traversal_reported = false
  let mut alias_reported = 0
  for entry in report.entries {
    if entry.action == "keep" && entry.reason.contains("intent is invalid") {
      traversal_reported = true
    }
    if entry.action == "keep" && entry.reason.contains("path alias") {
      alias_reported = alias_reported + 1
    }
  }
  assert_true(traversal_reported)
  assert_eq(alias_reported, 2)
}
