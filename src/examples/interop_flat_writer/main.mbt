///|
/// 互操作测试用的写入示例：创建一张 flat Delta 表并追加两批数据。
///
/// 用法：
///   moon run --target native src/examples/interop_flat_writer -- <table-root>
///
/// 表结构（与 docs/协议兼容性与互操作范围.md 第 4 节案例 A 对齐）：
///   id: long NOT NULL, name: string, active: boolean
/// 追加两批 => 两个数据文件、两次 commit、含 null 值。
fn main {
  let args = @env.args()
  let root = if args.length() >= 2 { args[1] } else { "artifacts/interop-flat/moondelta-written" }
  let schema = "{\"type\":\"struct\",\"fields\":[" +
    "{\"name\":\"id\",\"type\":\"long\",\"nullable\":false,\"metadata\":{}}," +
    "{\"name\":\"name\",\"type\":\"string\",\"nullable\":true,\"metadata\":{}}," +
    "{\"name\":\"active\",\"type\":\"boolean\",\"nullable\":true,\"metadata\":{}}" +
    "]}"
  let created = @delta.create_table(root, schema, []) catch {
    err => {
      println("create failed: \{err}")
      return
    }
  }
  println("created version \{created} at \{root}")
  let batch1 : Array[@delta.ReadColumn] = [
    { name: "id", type_: @delta.Int64Type, cells: [@delta.CellI64(1L), @delta.CellI64(2L), @delta.CellI64(3L)] },
    { name: "name", type_: @delta.StringType, cells: [@delta.CellStr("alice"), @delta.CellNull, @delta.CellStr("bob")] },
    { name: "active", type_: @delta.BooleanType, cells: [@delta.CellBool(true), @delta.CellBool(false), @delta.CellNull] },
  ]
  let v1 = @delta.append_rows(root, batch1, 1750000000000L) catch {
    err => {
      println("append batch1 failed: \{err}")
      return
    }
  }
  println("appended batch1 => version \{v1}")
  let batch2 : Array[@delta.ReadColumn] = [
    { name: "id", type_: @delta.Int64Type, cells: [@delta.CellI64(4L), @delta.CellI64(5L)] },
    { name: "name", type_: @delta.StringType, cells: [@delta.CellStr("carol"), @delta.CellNull] },
    { name: "active", type_: @delta.BooleanType, cells: [@delta.CellBool(true), @delta.CellNull] },
  ]
  let v2 = @delta.append_rows(root, batch2, 1750000001000L) catch {
    err => {
      println("append batch2 failed: \{err}")
      return
    }
  }
  println("appended batch2 => version \{v2}")
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
  println("self-check: version=\{snapshot.version} files=\{snapshot.files.length()} rows=\{snapshot.total_rows}")
  for f in snapshot.files {
    let stats = match f.stats {
      Some(s) => s
      None => "(no stats)"
    }
    println("  file: \{f.path} size=\{f.size} stats=\{stats}")
  }
}
