///|
/// 比较运算符（用于文件级 min/max 统计剪枝）
pub(all) enum CompareOp {
  EqV
  GtV
  GeV
  LtV
  LeV
} derive(Eq, Debug)

///|
pub(all) struct ColumnPredicate {
  column : String
  op : CompareOp
  value : Double
} derive(Eq, Debug)

///|
/// 扫描过滤条件：分区等值过滤 + 单列统计剪枝
pub(all) struct ScanFilter {
  partitions : Array[(String, String)]
  predicate : ColumnPredicate?
} derive(Eq, Debug)

///|
/// 扫描结果（不读数据文件，只做文件级规划）
pub(all) struct ScanReport {
  files : Array[AddFile]
  skipped_partition : Int32
  skipped_stats : Int32
  matched_rows : Int64
  matched_bytes : Int64
} derive(Eq, Debug)

///|
fn partition_matches(f : AddFile, wanted : Array[(String, String)]) -> Bool {
  for pair in wanted {
    let (key, value) = pair
    let mut found = false
    for pv in f.partition_values {
      let (pk, pval) = pv
      if pk == key && pval == value {
        found = true
      }
    }
    if !found {
      return false
    }
  }
  true
}

///|
fn min_max_for_column(stats : String?, column : String) -> (Double?, Double?) {
  match stats {
    Some(text) => {
      let j = @json.parse(text) catch { _ => return (None, None) }
      let min_v = match json_field(j, "minValues") {
        Some(m) =>
          match json_field(m, column) {
            Some(v) => json_as_double(v)
            None => None
          }
        None => None
      }
      let max_v = match json_field(j, "maxValues") {
        Some(m) =>
          match json_field(m, column) {
            Some(v) => json_as_double(v)
            None => None
          }
        None => None
      }
      (min_v, max_v)
    }
    None => (None, None)
  }
}

///|
fn stats_prunes(f : AddFile, pred : ColumnPredicate) -> Bool {
  let (min_v, max_v) = min_max_for_column(f.stats, pred.column)
  match (min_v, max_v) {
    (Some(mn), Some(mx)) =>
      match pred.op {
        EqV => mx < pred.value || mn > pred.value
        GtV => mx <= pred.value
        GeV => mx < pred.value
        LtV => mn >= pred.value
        LeV => mn > pred.value
      }
    _ => false
  }
}

///|
/// 文件级扫描规划：分区过滤 + min/max 统计剪枝。
/// 不读取数据文件，因此可离线测试、也可用于"改动了多少行"这类审计场景。
/// stats 损坏时显式报错（不静默失真）。
pub fn scan(
  snapshot : Snapshot,
  filter : ScanFilter,
) -> ScanReport raise DeltaError {
  let files : Array[AddFile] = []
  let mut skipped_partition = 0
  let mut skipped_stats = 0
  let mut matched_rows = 0L
  let mut matched_bytes = 0L
  for f in snapshot.files {
    if !partition_matches(f, filter.partitions) {
      skipped_partition = skipped_partition + 1
      continue
    }
    let pruned = match filter.predicate {
      Some(pred) => stats_prunes(f, pred)
      None => false
    }
    if pruned {
      skipped_stats = skipped_stats + 1
      continue
    }
    let rows = stats_num_records(f.stats)
    matched_rows = matched_rows + rows
    matched_bytes = matched_bytes + f.size
    files.push(f)
  }
  { files, skipped_partition, skipped_stats, matched_rows, matched_bytes, }
}
