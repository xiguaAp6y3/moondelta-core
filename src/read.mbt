///|
/// 读取路径的统一单元格表示（将 parquet 的值收敛成 Delta 语义类型）
pub(all) enum Cell {
  CellI32(Int32)
  CellI64(Int64)
  CellF32(Float)
  CellF64(Double)
  CellBool(Bool)
  CellStr(String)
  CellBin(Bytes)
  CellTs(Int64)
  CellNull
} derive(Debug)

///|
pub(all) struct ReadColumn {
  name : String
  type_ : DeltaType
  cells : Array[Cell]
} derive(Debug)

///|
pub(all) struct ReadResult {
  columns : Array[ReadColumn]
  row_count : Int32
  files_read : Int32
} derive(Debug)

///|
/// parquet 值 → Cell
fn convert_cell(v : @parquet.Value) -> Cell {
  match v {
    @parquet.Int32(x) => CellI32(x)
    @parquet.Int64(x) => CellI64(x)
    @parquet.TimestampMicros(x) => CellTs(x)
    @parquet.Float(x) => CellF32(x)
    @parquet.Double(x) => CellF64(x)
    @parquet.Boolean(x) => CellBool(x)
    @parquet.String(x) => CellStr(x)
    @parquet.Binary(x) => CellBin(x)
    @parquet.Null => CellNull
  }
}

///|
fn find_column_index(columns : Array[@parquet.Column], name : String) -> Int32? {
  let mut i = 0
  while i < columns.length() {
    if columns[i].name() == name {
      return Some(i)
    }
    i = i + 1
  }
  None
}

///|
fn partition_value_of(file : AddFile, name : String) -> String? {
  for pair in file.partition_values {
    let (k, v) = pair
    if k == name {
      return Some(v)
    }
  }
  None
}

///|
fn cell_from_partition(ty : DeltaType, raw : String) -> Cell raise DeltaError {
  match ty {
    Int32Type =>
      CellI32(
        @string.parse_int(raw, base=10) catch {
          _ =>
            raise DeltaError::ParseError(
              "invalid int32 partition value '\{raw}'",
            )
        },
      )
    Int64Type =>
      CellI64(
        @string.parse_int64(raw, base=10) catch {
          _ =>
            raise DeltaError::ParseError(
              "invalid int64 partition value '\{raw}'",
            )
        },
      )
    DoubleType =>
      CellF64(
        @string.parse_double(raw) catch {
          _ =>
            raise DeltaError::ParseError(
              "invalid double partition value '\{raw}'",
            )
        },
      )
    BooleanType => CellBool(raw == "true")
    StringType => CellStr(raw)
    DateType =>
      CellI32(
        @string.parse_int(raw, base=10) catch {
          _ =>
            raise DeltaError::ParseError(
              "invalid date partition value '\{raw}'",
            )
        },
      )
    _ =>
      raise DeltaError::Unsupported(
        "partition column of type \{delta_type_name(ty)} is not supported by the reader",
      )
  }
}

///|
/// 读取 snapshot 中的数据文件。
///
/// - `root` 是表目录（parquet 路径相对它）
/// - `limit` 限制读入行数（None = 全部）
/// 分区列不会出现在数据文件里，这里用 `partitionValues` 补齐常量列。
pub fn read_snapshot(
  root : String,
  snapshot : Snapshot,
  limit : Int32?,
) -> ReadResult raise DeltaError {
  ensure_readable_schema(snapshot.metadata.schema)
  let fields = snapshot.metadata.schema.fields
  let columns : Array[ReadColumn] = []
  for f in fields {
    columns.push({ name: f.name, type_: f.type_, cells: [], })
  }
  let max_rows = match limit {
    Some(l) => l
    None => 1000000000
  }
  let mut row_count = 0
  let mut files_read = 0
  for file in snapshot.files {
    if row_count >= max_rows {
      break
    }
    let path = root + "/" + file.path
    let pf = @parquet.read_file(path) catch {
      @parquet.Unsupported(msg) =>
        raise DeltaError::Unsupported("cannot read '\{file.path}': \{msg}")
      @parquet.InvalidData(msg) =>
        raise DeltaError::ParseError("corrupt parquet '\{file.path}': \{msg}")
      @parquet.Io(msg) => raise DeltaError::IoError(msg)
    }
    files_read = files_read + 1
    let rows = pf.rows()
    let remaining = max_rows - row_count
    let take = if rows.length() < remaining { rows.length() } else { remaining }
    let pcols = pf.columns()
    let mut idx = 0
    while idx < fields.length() {
      let field = fields[idx]
      let col = columns[idx]
      match partition_value_of(file, field.name) {
        Some(raw) => {
          let cell = cell_from_partition(field.type_, raw)
          let mut r = 0
          while r < take {
            col.cells.push(cell)
            r = r + 1
          }
        }
        None =>
          match find_column_index(pcols, field.name) {
            Some(pidx) => {
              let mut r = 0
              while r < take {
                let row = rows[r]
                col.cells.push(convert_cell(row[pidx]))
                r = r + 1
              }
            }
            None =>
              raise DeltaError::Unsupported(
                "column '\{field.name}' is missing from parquet file '\{file.path}'",
              )
          }
      }
      idx = idx + 1
    }
    row_count = row_count + take
  }
  { columns, row_count, files_read, }
}

///|
/// 单元格的展示形式（CLI / 示例用）
pub fn cell_to_display(cell : Cell) -> String {
  match cell {
    CellI32(x) => x.to_string()
    CellI64(x) => x.to_string()
    CellF32(x) => x.to_string()
    CellF64(x) => x.to_string()
    CellBool(x) => x.to_string()
    CellStr(x) => x
    CellBin(x) => x.to_unchecked_string()
    CellTs(x) => x.to_string()
    CellNull => "NULL"
  }
}
