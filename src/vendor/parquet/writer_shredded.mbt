///|
/// 嵌套写入（shredded 输入，读取侧 read_bytes_shredded 的镜像）。
/// 调用方按 schema 树把记录拆成叶级槽位流（值 + def/rep levels），
/// 并提供与前序遍历一致的 WriteNode 树；本文件负责编码：
/// 数据页 = [rep levels][def levels][PLAIN 密集值]（v1 布局，均未压缩），
/// def==max_def 的槽与 values 中的密集值一一对应。

///|
pub let converted_type_list = 3

///|
pub let converted_type_map = 1

///|
pub(all) struct WriteLeaf {
  /// 根到叶的路径（与前序遍历顺序一致）
  path : Array[String]
  column_type : ColumnType
  max_definition_level : Int32
  max_repetition_level : Int32
  /// 密集非空值（def==max_def 的槽按顺序取用）
  values : Array[Value]
  def_levels : Array[Int32]
  rep_levels : Array[Int32]
}

///|
pub(all) struct WriteNode {
  name : String
  repetition : Repetition
  /// None = group（有 children）；Some = 叶
  column_type : ColumnType?
  /// converted_type 注解：UTF8(0) / LIST(3) / MAP(4)；None = 不写
  converted_type : Int32?
  children : Array[WriteNode]
}

///|
fn encode_data_page_shredded(leaf : WriteLeaf) -> Bytes raise ParquetError {
  let count = leaf.def_levels.length()
  if leaf.rep_levels.length() != count || leaf.max_definition_level >= 0 {
    if leaf.rep_levels.length() != count {
      invalid_data("rep level count does not match def level count")
    }
  }
  let rep_bytes = if leaf.max_repetition_level > 0 {
    encode_rle_levels(leaf.rep_levels, bit_width(leaf.max_repetition_level), true)
  } else {
    Default::default()
  }
  let def_bytes = if leaf.max_definition_level > 0 {
    encode_rle_levels(leaf.def_levels, bit_width(leaf.max_definition_level), true)
  } else {
    Default::default()
  }
  let value_bytes = encode_plain_non_null_values(leaf.values, leaf.column_type)
  let payload = rep_bytes + def_bytes + value_bytes
  let writer = CompactWriter::new()
  writer.begin_struct()
  writer.write_i32_field(1, page_type_data_page)
  writer.write_i32_field(2, payload.length())
  writer.write_i32_field(3, payload.length())
  writer.write_field_header(5, thrift_wire_struct)
  writer.begin_struct()
  writer.write_i32_field(1, count)
  writer.write_i32_field(2, encoding_plain)
  writer.write_i32_field(3, encoding_rle)
  writer.write_i32_field(4, encoding_rle)
  writer.end_struct()
  writer.end_struct()
  writer.write_bytes() + payload
}

///|
fn writer_type_code_or_invalid(column_type : ColumnType) -> Int32 raise ParquetError {
  match column_type {
    Int32 => parquet_type_int32
    Int64 => parquet_type_int64
    String => parquet_type_byte_array
    Binary => parquet_type_byte_array
    Boolean => parquet_type_boolean
    Double => parquet_type_double
    _ => {
      unsupported("This column type is not supported by the writer yet")
      0
    }
  }
}

///|
fn count_schema_nodes(nodes : Array[WriteNode]) -> Int {
  let mut n = 0
  for node in nodes {
    n = n + 1
    n = n + count_schema_nodes(node.children)
  }
  n
}

///|
/// 前序遍历写出 SchemaElement 列表（先本节点，再递归子节点）
fn write_schema_elements(
  footer : CompactWriter,
  nodes : Array[WriteNode],
) -> Unit raise ParquetError {
  for node in nodes {
    let element = CompactWriter::new()
    element.begin_struct()
    match node.column_type {
      Some(column_type) => {
        element.write_i32_field(1, writer_type_code_or_invalid(column_type))
        element.write_i32_field(
          3,
          match node.repetition {
            Required => repetition_required
            Optional => repetition_optional
            Repeated => repetition_repeated
          },
        )
        element.write_string_field(4, node.name)
        match node.converted_type {
          Some(ct) => element.write_i32_field(6, ct)
          None => {
            if column_type == String {
              element.write_i32_field(6, converted_type_utf8)
            }
          }
        }
      }
      None => {
        element.write_i32_field(
          3,
          match node.repetition {
            Required => repetition_required
            Optional => repetition_optional
            Repeated => repetition_repeated
          },
        )
        element.write_string_field(4, node.name)
        match node.converted_type {
          Some(ct) => element.write_i32_field(6, ct)
          None => ()
        }
        element.write_i32_field(5, node.children.length())
      }
    }
    element.end_struct()
    footer.sink.push_bytes(element.write_bytes()[:])
    write_schema_elements(footer, node.children)
  }
}

///|
fn collect_write_leaves(
  nodes : Array[WriteNode],
  out : Array[WriteLeaf],
) -> Unit raise ParquetError {
  for node in nodes {
    match node.column_type {
      Some(_) =>
        unsupported("write_bytes_shredded requires leaves at the top array")
      None => collect_write_leaves(node.children, out)
    }
  }
}

///|
/// 嵌套写入：nodes 为根的子节点（前序遍历顺序决定列块顺序），
/// leaves 与该前序遍历中的叶顺序一一对应。
pub fn write_bytes_shredded(
  nodes : Array[WriteNode],
  leaves : Array[WriteLeaf],
  num_rows : Int32,
) -> Bytes raise ParquetError {
  if nodes.is_empty() {
    invalid_data("Cannot write parquet file without columns")
  }
  let ordered : Array[WriteLeaf] = []
  // 由 schema 树推导前序叶序列，与调用方提供的 leaves 逐位核对
  fn walk(node : WriteNode, path : Array[String]) -> Unit raise ParquetError {
    let next_path = path + [node.name]
    match node.column_type {
      Some(column_type) => {
        if ordered.length() >= leaves.length() {
          invalid_data("Fewer leaf streams than schema leaves")
        }
        let leaf = leaves[ordered.length()]
        let _ = column_type
        if leaf.path != next_path {
          invalid_data(
            "Leaf stream order does not match schema tree (expected '\{path_text_shredded(next_path)}')",
          )
        }
        if leaf.def_levels.length() == 0 && num_rows > 0 {
          invalid_data("Leaf stream is empty for a non-empty row count")
        }
        ordered.push(leaf)
      }
      None =>
        for child in node.children {
          walk(child, next_path)
        }
    }
  }

  for node in nodes {
    walk(node, [])
  }
  if ordered.length() != leaves.length() {
    invalid_data("More leaf streams than schema leaves")
  }
  let body = ByteSink::new()
  let mut offset = 4
  let column_chunks : Array[ColumnChunkMeta] = []
  for leaf in ordered {
    let page = encode_data_page_shredded(leaf)
    let page_offset = offset
    body.push_bytes(page[:])
    offset += page.length()
    column_chunks.push({
      file_offset: 0L,
      meta_data: {
        type_code: writer_type_code_or_invalid(leaf.column_type),
        encodings: [encoding_plain, encoding_rle],
        path_in_schema: leaf.path,
        codec: codec_uncompressed,
        num_values: leaf.def_levels.length().to_int64(),
        total_uncompressed_size: page.length().to_int64(),
        total_compressed_size: page.length().to_int64(),
        data_page_offset: page_offset.to_int64(),
        dictionary_page_offset: None,
      },
    })
  }
  let row_group = {
    columns: column_chunks,
    total_byte_size: body.length().to_int64(),
    num_rows: num_rows.to_int64(),
    file_offset: Some(4L),
    total_compressed_size: Some(body.length().to_int64()),
  }
  let footer = CompactWriter::new()
  footer.begin_struct()
  footer.write_i32_field(1, 1)
  footer.write_field_header(2, thrift_wire_list)
  footer.write_list_header(count_schema_nodes(nodes) + 1, thrift_wire_struct)
  let root = CompactWriter::new()
  root.begin_struct()
  root.write_string_field(4, "schema")
  root.write_i32_field(5, nodes.length())
  root.end_struct()
  footer.sink.push_bytes(root.write_bytes()[:])
  write_schema_elements(footer, nodes)
  footer.write_i64_field(3, num_rows.to_int64())
  footer.write_field_header(4, thrift_wire_list)
  footer.write_list_header(1, thrift_wire_struct)
  let row_group_writer = CompactWriter::new()
  row_group_writer.begin_struct()
  row_group_writer.write_field_header(1, thrift_wire_list)
  row_group_writer.write_list_header(
    row_group.columns.length(),
    thrift_wire_struct,
  )
  for chunk in row_group.columns {
    let chunk_writer = CompactWriter::new()
    chunk_writer.begin_struct()
    chunk_writer.write_i64_field(2, chunk.file_offset)
    chunk_writer.write_field_header(3, thrift_wire_struct)
    chunk_writer.begin_struct()
    chunk_writer.write_i32_field(1, chunk.meta_data.type_code)
    write_list_of_i32(chunk_writer, 2, chunk.meta_data.encodings)
    write_list_of_string(chunk_writer, 3, chunk.meta_data.path_in_schema)
    chunk_writer.write_i32_field(4, chunk.meta_data.codec)
    chunk_writer.write_i64_field(5, chunk.meta_data.num_values)
    chunk_writer.write_i64_field(6, chunk.meta_data.total_uncompressed_size)
    chunk_writer.write_i64_field(7, chunk.meta_data.total_compressed_size)
    chunk_writer.write_i64_field(9, chunk.meta_data.data_page_offset)
    chunk_writer.end_struct()
    chunk_writer.end_struct()
    row_group_writer.sink.push_bytes(chunk_writer.write_bytes()[:])
  }
  row_group_writer.write_i64_field(2, row_group.total_byte_size)
  row_group_writer.write_i64_field(3, row_group.num_rows)
  row_group_writer.write_i64_field(5, row_group.file_offset.unwrap_or(0L))
  row_group_writer.write_i64_field(
    6,
    row_group.total_compressed_size.unwrap_or(0L),
  )
  row_group_writer.end_struct()
  footer.sink.push_bytes(row_group_writer.write_bytes()[:])
  footer.write_string_field(6, "mizchi/parquet")
  footer.end_struct()
  let footer_bytes = footer.write_bytes()
  let file_bytes = ByteSink::new()
  file_bytes.push_byte(parquet_magic_0)
  file_bytes.push_byte(parquet_magic_1)
  file_bytes.push_byte(parquet_magic_2)
  file_bytes.push_byte(parquet_magic_3)
  file_bytes.push_bytes(body.to_bytes()[:])
  file_bytes.push_bytes(footer_bytes[:])
  file_bytes.push_u32_le(footer_bytes.length())
  file_bytes.push_byte(parquet_magic_0)
  file_bytes.push_byte(parquet_magic_1)
  file_bytes.push_byte(parquet_magic_2)
  file_bytes.push_byte(parquet_magic_3)
  file_bytes.to_bytes()
}

///|
fn path_text_shredded(path : Array[String]) -> String {
  let sb = StringBuilder()
  let mut i = 0
  while i < path.length() {
    if i > 0 {
      sb.write_char('.')
    }
    sb.write_string(path[i])
    i = i + 1
  }
  sb.to_string()
}
