///|
fn main {
  let root = "fixtures/delta/covid"
  let table = @delta.DeltaTable::open(root) catch {
    err => {
      println("open failed: \{err}")
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
    "table \{root}: version=\{snapshot.version}, files=\{snapshot.files.length()}, rows=\{snapshot.total_rows}",
  )
  println("schema:")
  for f in snapshot.metadata.schema.fields {
    println("  \{f.name}: \{@delta.delta_type_name(f.type_)}")
  }
  let result = @delta.read_snapshot(root, snapshot, Some(5)) catch {
    err => {
      println("read failed: \{err}")
      return
    }
  }
  println("")
  println("first 5 rows (read from \{result.files_read} parquet file):")
  let mut header = ""
  for c in result.columns {
    header = header + c.name + "\t"
  }
  println(header)
  let mut r = 0
  while r < result.row_count && r < 5 {
    let mut line = ""
    for c in result.columns {
      line = line + @delta.cell_to_display(c.cells[r]) + "\t"
    }
    println(line)
    r = r + 1
  }
}
