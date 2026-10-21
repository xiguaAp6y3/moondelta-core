///|
/// Delta Lake 状态类型（本实现支持的类型子集 + 明确的"不支持"标记）
///
/// 说明：Delta 的 schema 可以任意嵌套（struct/array/map）。本内核的读取依赖
/// `mizchi/parquet`，而该库当前只支持 flat 列；因此嵌套类型在 schema 解析阶段
/// 被标记为 `Nested`，读取/写入时给出明确错误，而不是静默返回错误数据。
pub(all) enum DeltaType {
  Int32Type
  Int64Type
  FloatType
  DoubleType
  BooleanType
  StringType
  BinaryType
  DateType
  TimestampType
  DecimalType(precision~ : Int32, scale~ : Int32)
  NestedType(kind~ : String)
  UnknownType(raw~ : String)
} derive(Eq, Debug)

///|
pub(all) struct DeltaField {
  name : String
  type_ : DeltaType
  nullable : Bool
} derive(Eq, Debug)

///|
pub(all) struct DeltaSchema {
  fields : Array[DeltaField]
  raw_json : String
} derive(Eq, Debug)

///|
pub(all) struct Protocol {
  min_reader_version : Int32
  min_writer_version : Int32
} derive(Eq, Debug)

///|
pub(all) struct TableInfo {
  id : String
  name : String?
  schema : DeltaSchema
  partition_columns : Array[String]
  format_provider : String
  configuration : Array[(String, String)]
} derive(Eq, Debug)

///|
pub(all) struct AddFile {
  path : String
  partition_values : Array[(String, String)]
  size : Int64
  modification_time : Int64
  data_change : Bool
  stats : String?
} derive(Eq, Debug)

///|
pub(all) struct RemoveFile {
  path : String
  deletion_timestamp : Int64?
  data_change : Bool
} derive(Eq, Debug)

///|
pub(all) struct CommitInfo {
  version : Int32
  timestamp : Int64?
  operation : String?
  operation_parameters : Array[(String, String)]
} derive(Eq, Debug)

///|
pub(all) enum Action {
  ProtocolAction(Protocol)
  MetaDataAction(TableInfo)
  AddAction(AddFile)
  RemoveAction(RemoveFile)
  CommitInfoAction(CommitInfo)
  UnknownAction(String)
} derive(Eq, Debug)

///|
pub(all) struct Snapshot {
  version : Int32
  protocol : Protocol
  metadata : TableInfo
  files : Array[AddFile]
  total_rows : Int64
  total_bytes : Int64
} derive(Eq, Debug)

///|
pub(all) struct HistoryEntry {
  version : Int32
  timestamp : Int64?
  operation : String?
} derive(Eq, Debug)

///|
pub(all) struct CheckpointInfo {
  version : Int32
  size : Int32?
  parts : Int32?
} derive(Eq, Debug)

///|
pub fn delta_type_name(ty : DeltaType) -> String {
  match ty {
    Int32Type => "int"
    Int64Type => "long"
    FloatType => "float"
    DoubleType => "double"
    BooleanType => "boolean"
    StringType => "string"
    BinaryType => "binary"
    DateType => "date"
    TimestampType => "timestamp"
    DecimalType(precision~, scale~) => "decimal(\{precision},\{scale})"
    NestedType(kind~) => kind
    UnknownType(raw~) => "unknown(\{raw})"
  }
}

///|
pub(all) suberror DeltaError {
  IoError(String)
  ParseError(String)
  Unsupported(String)
  NotFound(String)
} derive(Eq, Debug)

///|
pub fn describe_error(e : DeltaError) -> String {
  match e {
    IoError(m) => "io error: " + m
    ParseError(m) => "parse error: " + m
    Unsupported(m) => "unsupported: " + m
    NotFound(m) => "not found: " + m
  }
}

///|
pub impl Show for DeltaError with fn output(self, logger) {
  logger.write_string(describe_error(self))
}
