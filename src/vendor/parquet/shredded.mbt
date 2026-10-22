///|
/// 嵌套读取（shredded 输出）。对每个叶列返回"值 + definition level + repetition
/// level"逐槽对齐的三元组：`values[i]` 非空当且仅当 `def_levels[i] ==
/// max_definition_level`，`rep_levels[i] == 0` 表示第 i 槽开启一条新记录。
/// 记录（行 / list 条目 / map 条目）的装配由调用方按 schema 树完成，
/// 本文件只负责把页级编码正确展开成槽位流。

///|
pub(all) struct LeafColumn {
  /// 根到叶的路径（原始 schema 名，未规范化）
  path : Array[String]
  column_type : ColumnType
  max_definition_level : Int32
  max_repetition_level : Int32
  values : Array[Value]
  def_levels : Array[Int32]
  rep_levels : Array[Int32]
}

///|
fn count_levels_eq(levels : Array[Int32], target : Int32) -> Int32 {
  let mut n = 0
  for l in levels {
    if l == target {
      n = n + 1
    }
  }
  n
}

///|
/// 把密集非空值按 def level 回填成槽位流（def == max_def 取值，否则 Null）
fn pair_slots_shredded(
  slots : Array[Value],
  defs_out : Array[Int32],
  reps_out : Array[Int32],
  defs : Array[Int32],
  reps : Array[Int32],
  dense : Array[Value],
  max_definition_level : Int32,
  count : Int32,
) -> Unit raise ParquetError {
  if defs.length() != count || reps.length() != count {
    invalid_data(
      "Level count mismatch: def=\{defs.length()} rep=\{reps.length()} expected \{count}",
    )
  }
  let mut vi = 0
  for i in 0..<count {
    if defs[i] == max_definition_level {
      if vi >= dense.length() {
        invalid_data("Not enough dense values for def levels")
      }
      slots.push(dense[vi])
      vi = vi + 1
    } else {
      slots.push(Null)
    }
    defs_out.push(defs[i])
    reps_out.push(reps[i])
  }
  if vi != dense.length() {
    invalid_data(
      "Dense value count mismatch: got \{dense.length()} expected \{vi}",
    )
  }
}

///|
fn decode_data_page_v1_shredded(
  slots : Array[Value],
  defs_out : Array[Int32],
  reps_out : Array[Int32],
  data : Bytes,
  payload_offset : Int32,
  header : DataPageHeaderMeta,
  column : LeafColumnMeta,
  dictionary : Array[Value]?,
) -> Unit raise ParquetError {
  // v1 页布局：[rep levels（有 4 字节长度前缀，仅 max_rep>0 时存在）]
  //           [def levels（同上，仅 max_def>0 时存在）] [密集值]
  let mut offset = payload_offset
  let reps : Array[Int32] = if column.max_repetition_level > 0 {
    let (r, consumed) = decode_levels_v1(
      data, offset, column.max_repetition_level, header.num_values,
    )
    offset = offset + consumed
    r
  } else {
    Array::make(header.num_values, 0)
  }
  let defs : Array[Int32] = if column.max_definition_level > 0 {
    let (d, consumed) = decode_levels_v1(
      data, offset, column.max_definition_level, header.num_values,
    )
    offset = offset + consumed
    d
  } else {
    Array::make(header.num_values, 0)
  }
  let non_null = count_levels_eq(defs, column.max_definition_level)
  let (dense, _) = decode_non_null_values(
    data, offset, header.encoding, column, non_null, dictionary,
  )
  pair_slots_shredded(
    slots, defs_out, reps_out, defs, reps, dense,
    column.max_definition_level, header.num_values,
  )
}

///|
fn decode_data_page_v2_shredded(
  slots : Array[Value],
  defs_out : Array[Int32],
  reps_out : Array[Int32],
  data : Bytes,
  payload_offset : Int32,
  header : DataPageHeaderV2Meta,
  column : LeafColumnMeta,
  dictionary : Array[Value]?,
  value_data : Bytes,
  value_offset : Int32,
) -> Unit raise ParquetError {
  // v2 页布局：levels 无长度前缀，字节数在页头；值段可能单独压缩（已在调用方解压）
  let reps : Array[Int32] = if column.max_repetition_level > 0 &&
    header.repetition_levels_byte_length > 0 {
    decode_rle_hybrid(
      data,
      payload_offset,
      bit_width(column.max_repetition_level),
      header.num_values,
      false,
    ).0
  } else {
    Array::make(header.num_values, 0)
  }
  let defs : Array[Int32] = if column.max_definition_level > 0 &&
    header.definition_levels_byte_length > 0 {
    decode_rle_hybrid(
      data,
      payload_offset + header.repetition_levels_byte_length,
      bit_width(column.max_definition_level),
      header.num_values,
      false,
    ).0
  } else {
    Array::make(header.num_values, 0)
  }
  let non_null = count_levels_eq(defs, column.max_definition_level)
  let (dense, _) = decode_non_null_values(
    value_data, value_offset, header.encoding, column, non_null, dictionary,
  )
  pair_slots_shredded(
    slots, defs_out, reps_out, defs, reps, dense,
    column.max_definition_level, header.num_values,
  )
}

///|
/// 解码单个列块（可含 repeated 层级），返回槽位流与对齐的 def/rep levels。
fn decode_column_chunk_shredded(
  data : Bytes,
  chunk : ColumnChunkMeta,
  column : LeafColumnMeta,
) -> (Array[Value], Array[Int32], Array[Int32]) raise ParquetError {
  let mut offset = chunk.meta_data.dictionary_page_offset
    .unwrap_or(chunk.meta_data.data_page_offset)
    .to_int()
  let target_values = chunk.meta_data.num_values.to_int()
  let slots : Array[Value] = Array(capacity=target_values)
  let defs : Array[Int32] = []
  let reps : Array[Int32] = []
  let mut dictionary : Array[Value]? = None
  while slots.length() < target_values {
    let (page_header, page_header_len) = read_page_header(data, offset)
    let payload_offset = offset + page_header_len
    if page_header.page_type == page_type_data_page {
      let payload = if chunk.meta_data.codec == codec_uncompressed {
        data
      } else {
        decompress_page_payload(
          data,
          payload_offset,
          page_header.compressed_page_size,
          page_header.uncompressed_page_size,
          chunk.meta_data.codec,
        )
      }
      let page_data_offset = if chunk.meta_data.codec == codec_uncompressed {
        payload_offset
      } else {
        0
      }
      match page_header.data_page {
        Some(data_page) =>
          decode_data_page_v1_shredded(
            slots, defs, reps, payload, page_data_offset, data_page, column,
            dictionary,
          )
        None => invalid_data("DATA_PAGE without data_page_header")
      }
    } else if page_header.page_type == page_type_data_page_v2 {
      match page_header.data_page_v2 {
        Some(data_page_v2) => {
          let value_offset = payload_offset +
            data_page_v2.repetition_levels_byte_length +
            data_page_v2.definition_levels_byte_length
          let compressed_value_len = page_header.compressed_page_size -
            data_page_v2.repetition_levels_byte_length -
            data_page_v2.definition_levels_byte_length
          let uncompressed_value_len = page_header.uncompressed_page_size -
            data_page_v2.repetition_levels_byte_length -
            data_page_v2.definition_levels_byte_length
          let (value_data, value_data_offset) = if data_page_v2.is_compressed &&
            chunk.meta_data.codec != codec_uncompressed {
            if compressed_value_len == 0 {
              (Default::default(), 0)
            } else {
              (
                decompress_page_payload(
                  data,
                  value_offset,
                  compressed_value_len,
                  uncompressed_value_len,
                  chunk.meta_data.codec,
                ),
                0,
              )
            }
          } else {
            (data, value_offset)
          }
          decode_data_page_v2_shredded(
            slots, defs, reps, data, payload_offset, data_page_v2, column,
            dictionary, value_data, value_data_offset,
          )
        }
        None => invalid_data("DATA_PAGE_V2 without data_page_header_v2")
      }
    } else if page_header.page_type == page_type_dictionary_page {
      let payload = if chunk.meta_data.codec == codec_uncompressed {
        data
      } else {
        decompress_page_payload(
          data,
          payload_offset,
          page_header.compressed_page_size,
          page_header.uncompressed_page_size,
          chunk.meta_data.codec,
        )
      }
      let page_data_offset = if chunk.meta_data.codec == codec_uncompressed {
        payload_offset
      } else {
        0
      }
      match page_header.dictionary_page {
        Some(dictionary_header) =>
          dictionary = Some(
            decode_dictionary_page(
              payload, page_data_offset, dictionary_header, column,
            ),
          )
        None => invalid_data("DICTIONARY_PAGE without dictionary_page_header")
      }
    } else {
      unsupported("Unsupported page type: \{page_header.page_type}")
    }
    offset = payload_offset + page_header.compressed_page_size
  }
  if slots.length() != target_values {
    invalid_data("Decoded slot count does not match column metadata")
  }
  (slots, defs, reps)
}

///|
/// 全文件 shredded 读取：返回每个叶列的槽位流与总记录数（跨 row group 拼接，
/// 记录边界由 rep==0 标记）。
pub fn read_bytes_shredded(
  data : Bytes,
) -> (Array[LeafColumn], Int32) raise ParquetError {
  check_magic(data)
  let footer_len = read_u32_le(data, data.length() - 8)
  let footer_start = data.length() - 8 - footer_len
  if footer_start < 4 {
    invalid_data("Invalid parquet footer length")
  }
  let metadata = read_file_metadata(data, footer_start, footer_len)
  let leaf_columns = build_leaf_columns(metadata.schema)
  let column_count = leaf_columns.length()
  let values : Array[Array[Value]] = Array::makei(column_count, fn(_) { [] })
  let defs : Array[Array[Int32]] = Array::makei(column_count, fn(_) { [] })
  let reps : Array[Array[Int32]] = Array::makei(column_count, fn(_) { [] })
  let mut total_rows = 0
  for row_group in metadata.row_groups {
    if row_group.columns.length() != column_count {
      invalid_data("RowGroup column count does not match schema leaf count")
    }
    total_rows = total_rows + row_group.num_rows.to_int()
    for column_index in 0..<column_count {
      let (s, d, r) = decode_column_chunk_shredded(
        data,
        row_group.columns[column_index],
        leaf_columns[column_index],
      )
      values[column_index].append(s)
      defs[column_index].append(d)
      reps[column_index].append(r)
    }
  }
  let out : Array[LeafColumn] = Array::makei(column_count, fn(i) {
    let leaf = leaf_columns[i]
    {
      path: leaf.path,
      column_type: leaf.column_type,
      max_definition_level: leaf.max_definition_level,
      max_repetition_level: leaf.max_repetition_level,
      values: values[i],
      def_levels: defs[i],
      rep_levels: reps[i],
    }
  })
  (out, total_rows)
}
