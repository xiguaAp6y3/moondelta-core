///|
priv struct SchemaElementMeta {
  type_code : Int32?
  type_length : Int32?
  repetition_type : Int32?
  name : String
  num_children : Int32?
  converted_type : Int32?
}

///|
priv struct ColumnMetaDataMeta {
  type_code : Int32
  encodings : Array[Int32]
  path_in_schema : Array[String]
  codec : Int32
  num_values : Int64
  total_uncompressed_size : Int64
  total_compressed_size : Int64
  data_page_offset : Int64
  dictionary_page_offset : Int64?
}

///|
priv struct ColumnChunkMeta {
  file_offset : Int64
  meta_data : ColumnMetaDataMeta
}

///|
priv struct RowGroupMeta {
  columns : Array[ColumnChunkMeta]
  total_byte_size : Int64
  num_rows : Int64
  file_offset : Int64?
  total_compressed_size : Int64?
}

///|
priv struct FileMetaDataMeta {
  schema : Array[SchemaElementMeta]
  row_groups : Array[RowGroupMeta]
  created_by : String?
}

///|
priv struct DataPageHeaderMeta {
  num_values : Int32
  encoding : Int32
}

///|
priv struct DataPageHeaderV2Meta {
  num_values : Int32
  encoding : Int32
  definition_levels_byte_length : Int32
  repetition_levels_byte_length : Int32
  is_compressed : Bool
}

///|
priv struct PageHeaderMeta {
  page_type : Int32
  uncompressed_page_size : Int32
  compressed_page_size : Int32
  data_page : DataPageHeaderMeta?
  data_page_v2 : DataPageHeaderV2Meta?
  dictionary_page : DictionaryPageHeaderMeta?
}

///|
priv struct DictionaryPageHeaderMeta {
  num_values : Int32
  encoding : Int32
}

///|
priv struct LeafColumnMeta {
  name : String
  column_type : ColumnType
  repetition : Repetition
  max_definition_level : Int32
  max_repetition_level : Int32
  fixed_length : Int32?
}
