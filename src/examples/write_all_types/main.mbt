///|
/// 写入 11 种 Delta 基础类型，供 delta-rs / DuckDB 回读验证。
/// 用法：moon run --target native src/examples/write_all_types -- <table-root>
fn main {
  let args = @env.args()
  let root = if args.length() >= 2 {
    args[1]
  } else {
    "artifacts/interop-flat/all-primitives-written"
  }
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
  ignore(
    @delta.create_table(root, schema, []) catch {
      err => {
        println("create failed: \{err}")
        return
      }
    },
  )
  let columns : Array[@delta.ReadColumn] = [
    { name: "b", type_: @delta.ByteType, cells: [@delta.CellI32(7), @delta.CellNull] },
    { name: "s", type_: @delta.ShortType, cells: [@delta.CellI32(300), @delta.CellNull] },
    { name: "i", type_: @delta.Int32Type, cells: [@delta.CellI32(42), @delta.CellNull] },
    {
      name: "l",
      type_: @delta.Int64Type,
      cells: [@delta.CellI64(123456789L), @delta.CellNull],
    },
    { name: "f", type_: @delta.FloatType, cells: [@delta.CellF32(1.5), @delta.CellNull] },
    { name: "d", type_: @delta.DoubleType, cells: [@delta.CellF64(2.25), @delta.CellNull] },
    {
      name: "bo",
      type_: @delta.BooleanType,
      cells: [@delta.CellBool(true), @delta.CellNull],
    },
    {
      name: "st",
      type_: @delta.StringType,
      cells: [@delta.CellStr("hello"), @delta.CellNull],
    },
    {
      name: "bin",
      type_: @delta.BinaryType,
      cells: [@delta.CellBin(b"raw"), @delta.CellNull],
    },
    { name: "dt", type_: @delta.DateType, cells: [@delta.CellI32(20454), @delta.CellNull] },
    {
      name: "ts",
      type_: @delta.TimestampType,
      cells: [@delta.CellTs(1577836800000000L), @delta.CellNull],
    },
  ]
  let version = @delta.append_rows(root, columns, 1750000000000L) catch {
    err => {
      println("append failed: \{err}")
      return
    }
  }
  println("wrote 11 primitive types => version \{version} at \{root}")
}
