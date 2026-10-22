///|
/// 互操作测试用的嵌套写入示例：创建 struct/list/map 表并追加一批数据
/// （与 docs/协议兼容性与互操作范围.md 案例 B 的 D3 fixture 数据形态一致）。
///
/// 用法：
///   moon run --target native src/examples/write_nested -- <table-root>
fn main {
  let args = @env.args()
  let root = if args.length() >= 2 {
    args[1]
  } else {
    "artifacts/interop-nested/moondelta-nested-written"
  }
  let schema = "{\"type\":\"struct\",\"fields\":" +
    "[{\"name\":\"id\",\"type\":\"long\",\"nullable\":true,\"metadata\":{}}," +
    "{\"name\":\"profile\",\"type\":{\"type\":\"struct\",\"fields\":[" +
    "{\"name\":\"name\",\"type\":\"string\",\"nullable\":true,\"metadata\":{}}," +
    "{\"name\":\"age\",\"type\":\"integer\",\"nullable\":true,\"metadata\":{}}]},\"nullable\":true,\"metadata\":{}}," +
    "{\"name\":\"tags\",\"type\":{\"type\":\"array\",\"elementType\":\"string\",\"containsNull\":true},\"nullable\":true,\"metadata\":{}}," +
    "{\"name\":\"attrs\",\"type\":{\"type\":\"map\",\"keyType\":\"string\",\"valueType\":\"string\",\"valueContainsNull\":true},\"nullable\":true,\"metadata\":{}}" +
    "]}"
  let created = @delta.create_table(root, schema, []) catch {
    err => {
      println("create failed: \{err}")
      return
    }
  }
  println("created version \{created} at \{root}")
  let name_t = @delta.NestedType(
    kind="struct",
    fields=[
      { name: "name", type_: @delta.StringType, nullable: true },
      { name: "age", type_: @delta.Int32Type, nullable: true },
    ],
  )
  let tags_t = @delta.NestedType(
    kind="array",
    fields=[{ name: "element", type_: @delta.StringType, nullable: true }],
  )
  let attrs_t = @delta.NestedType(
    kind="map",
    fields=[
      { name: "key", type_: @delta.StringType, nullable: false },
      { name: "value", type_: @delta.StringType, nullable: true },
    ],
  )
  let columns : Array[@delta.ReadColumn] = [
    {
      name: "id",
      type_: @delta.Int64Type,
      cells: [
        @delta.CellI64(1L),
        @delta.CellI64(2L),
        @delta.CellI64(3L),
        @delta.CellI64(4L),
        @delta.CellI64(5L),
      ],
    },
    {
      name: "profile",
      type_: name_t,
      cells: [
        @delta.CellStruct([
          ("name", @delta.CellStr("alice")),
          ("age", @delta.CellI32(30)),
        ]),
        @delta.CellNull,
        @delta.CellStruct([
          ("name", @delta.CellNull),
          ("age", @delta.CellNull),
        ]),
        @delta.CellStruct([
          ("name", @delta.CellStr("bob")),
          ("age", @delta.CellNull),
        ]),
        @delta.CellStruct([
          ("name", @delta.CellNull),
          ("age", @delta.CellI32(42)),
        ]),
      ],
    },
    {
      name: "tags",
      type_: tags_t,
      cells: [
        @delta.CellList([@delta.CellStr("red"), @delta.CellStr("green")]),
        @delta.CellList([]),
        @delta.CellNull,
        @delta.CellList([@delta.CellStr("blue"), @delta.CellNull]),
        @delta.CellList([]),
      ],
    },
    {
      name: "attrs",
      type_: attrs_t,
      cells: [
        @delta.CellMap([
          (@delta.CellStr("k1"), @delta.CellStr("v1")),
          (@delta.CellStr("k2"), @delta.CellStr("v2")),
        ]),
        @delta.CellMap([]),
        @delta.CellMap([(@delta.CellStr("k"), @delta.CellStr("v"))]),
        @delta.CellMap([]),
        @delta.CellNull,
      ],
    },
  ]
  let version = @delta.append_rows(root, columns, 1750000000000L) catch {
    err => {
      println("append failed: \{err}")
      return
    }
  }
  println("appended => version \{version}")
  let table = @delta.DeltaTable::open(root) catch {
    err => {
      println("reopen failed: \{err}")
      return
    }
  }
  let snapshot = table.snapshot() catch {
    err => {
      println("snapshot failed: \{err}")
      return
    }
  }
  println(
    "self-check: version=\{snapshot.version} files=\{snapshot.files.length()} rows=\{snapshot.total_rows}",
  )
}
