#!/usr/bin/env python
"""生成 D1 协议审计用的自造 Delta fixture（可重放）。

用法（仓库根目录）：
  .tools\\pyenv\\Scripts\\python.exe artifacts\\protocol-audit\\gen_fixtures.py

设计原则：
- 每个 fixture 一个独立目录，_delta_log 下的 JSON 日志全部手写（一行一个 action），
  不使用 delta-rs/deltalake 生成日志，便于精确控制协议字段；
- 数据文件用 pyarrow 写（SNAPPY/UNCOMPRESSED/ZSTD/GZIP/BROTLI/LZ4、INT96、decimal、uuid、嵌套）；
- 覆盖“闭集之外”的输入，用于观察 moondelta-core 是明确拒绝、静默接受还是静默忽略；
- 重复运行会先清空 artifacts/protocol-audit/fixtures/ 再重建（只删本目录）。
"""

import hashlib
import json
import shutil
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

BASE = Path(__file__).resolve().parent          # artifacts/protocol-audit
REPO = BASE.parent.parent                        # 仓库根
FIX = BASE / "fixtures"
TABLE_A_LOG = REPO / "fixtures" / "delta" / "table_a" / "_delta_log"


# ---------------------------------------------------------------- 基础工具
def reset_fixtures() -> None:
    if FIX.exists():
        shutil.rmtree(FIX)
    FIX.mkdir(parents=True)


def write_text(path: Path, text: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    path.write_bytes(data)
    return len(data)


def write_log(root: Path, versions: dict) -> None:
    """versions: {version:int -> [action:dict,...]}，写 20 位零填充 JSON 日志。"""
    log = root / "_delta_log"
    log.mkdir(parents=True, exist_ok=True)
    for version, actions in versions.items():
        lines = [json.dumps(a, ensure_ascii=False, separators=(",", ":")) for a in actions]
        write_text(log / f"{version:020d}.json", "\n".join(lines) + "\n")


def write_raw_log_name(root: Path, name: str, actions: list) -> None:
    log = root / "_delta_log"
    log.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(a, ensure_ascii=False, separators=(",", ":")) for a in actions]
    write_text(log / name, "\n".join(lines) + "\n")


def flat_schema(fields) -> dict:
    """fields: [(name, delta_type_str_or_dict, nullable?)]"""
    out = []
    for item in fields:
        name, ty = item[0], item[1]
        nullable = item[2] if len(item) > 2 else True
        out.append(
            {"name": name, "type": ty, "nullable": nullable, "metadata": {}}
        )
    return {"type": "struct", "fields": out}


def schema_json(fields) -> str:
    return json.dumps(flat_schema(fields), ensure_ascii=False, separators=(",", ":"))


def protocol(min_reader=1, min_writer=1, reader_features=None, writer_features=None) -> dict:
    p = {"minReaderVersion": min_reader, "minWriterVersion": min_writer}
    if reader_features is not None:
        p["readerFeatures"] = reader_features
    if writer_features is not None:
        p["writerFeatures"] = writer_features
    return {"protocol": p}


def metadata(schema_str: str, partition_columns=None, configuration=None,
             table_id="fixture", physical_names=None) -> dict:
    fields = json.loads(schema_str)["fields"]
    if physical_names:
        for f in fields:
            name = f["name"]
            if name in physical_names:
                f["metadata"] = {
                    "delta.columnMapping.id": physical_names[name][0],
                    "delta.columnMapping.physicalName": physical_names[name][1],
                }
    m = {
        "id": table_id,
        "format": {"provider": "parquet", "options": {}},
        "schemaString": json.dumps(
            {"type": "struct", "fields": fields},
            ensure_ascii=False, separators=(",", ":"),
        ),
        "partitionColumns": partition_columns or [],
        "configuration": configuration or {},
        "createdTime": 0,
    }
    return {"metaData": m}


def commit(operation="WRITE", ts=0, params=None) -> dict:
    return {
        "commitInfo": {
            "timestamp": ts,
            "operation": operation,
            "operationParameters": params or {},
            "engineInfo": "protocol-audit-fixture",
        }
    }


def add(path: str, size: int, num_records=None, min_values=None, max_values=None,
        null_count=None, partition_values=None, stats_object=None,
        extra=None) -> dict:
    a = {
        "path": path,
        "partitionValues": partition_values or {},
        "size": size,
        "modificationTime": 0,
        "dataChange": True,
    }
    if stats_object is not None:
        a["stats"] = stats_object
    elif num_records is not None:
        stats = {"numRecords": num_records}
        if min_values is not None:
            stats["minValues"] = min_values
        if max_values is not None:
            stats["maxValues"] = max_values
        if null_count is not None:
            stats["nullCount"] = null_count
        a["stats"] = json.dumps(stats, ensure_ascii=False, separators=(",", ":"))
    if extra:
        a.update(extra)
    return {"add": a}


def remove(path: str, extra=None) -> dict:
    r = {"path": path, "deletionTimestamp": 0, "dataChange": True}
    if extra:
        r.update(extra)
    return {"remove": r}


def write_parquet(root: Path, name: str, table: pa.Table, compression="snappy", **kw) -> int:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, compression=compression, **kw)
    return path.stat().st_size


def simple_data(root: Path, name="data.parquet", compression="snappy") -> int:
    t = pa.table({"id": pa.array([1, 2, 3], pa.int64())})
    return write_parquet(root, name, t, compression)


def one_file_table(root: Path, schema_fields, data_builder, protocol_kw=None,
                   config=None, protocol_action=True, commit_info=True) -> None:
    """v0 = [commitInfo] [+protocol] + metaData + add(data.parquet)"""
    root.mkdir(parents=True, exist_ok=True)
    size = data_builder(root)
    actions = []
    if commit_info:
        actions.append(commit("CREATE TABLE", 0))
    if protocol_action:
        actions.append(protocol(**(protocol_kw or {})))
    actions.append(metadata(schema_json(schema_fields), configuration=config))
    actions.append(add("data.parquet", size, num_records=3,
                       min_values={"id": 1}, max_values={"id": 3}, null_count={"id": 0}))
    write_log(root, {0: actions})


# ---------------------------------------------------------------- fixtures
def build_baseline() -> None:
    # 供 CLI 正向命令复用的最简 flat 表（snappy）
    root = FIX / "baseline-flat"
    one_file_table(root, [("id", "long")], simple_data)


def build_protocol() -> None:
    # reader v2：文档要求拒绝（>reader v1），实际是否接受？
    root = FIX / "protocol-reader-v2"
    def data(r):
        return write_parquet(r, "data.parquet", pa.table({"id": pa.array([1, 2, 3], pa.int64())}))
    root.mkdir(parents=True, exist_ok=True)
    size = data(root)
    write_log(root, {0: [
        commit("CREATE TABLE"), protocol(2, 5, writer_features=["appendOnly", "invariants"]),
        metadata(schema_json([("id", "long")])),
        add("data.parquet", size, num_records=3),
    ]})

    # reader v3 + readerFeatures（含未知必需 feature）：文档要求拒绝
    root = FIX / "protocol-reader-v3-features"
    root.mkdir(parents=True, exist_ok=True)
    size = write_parquet(root, "data.parquet", pa.table({"id": pa.array([1, 2, 3], pa.int64())}))
    write_log(root, {0: [
        commit("CREATE TABLE"),
        protocol(3, 7,
                 reader_features=["deletionVectors", "columnMapping", "unknownRequiredFeature"],
                 writer_features=["appendOnly", "invariants", "unknownWriterFeature"]),
        metadata(schema_json([("id", "long")])),
        add("data.parquet", size, num_records=3),
    ]})

    # 没有 protocol action 的表：Delta 规范要求每个快照必须有 protocol，实际？
    root = FIX / "no-protocol"
    root.mkdir(parents=True, exist_ok=True)
    size = simple_data(root)
    write_log(root, {0: [
        metadata(schema_json([("id", "long")])),
        add("data.parquet", size, num_records=3),
    ]})

    # protocol 只有 minWriterVersion，缺 minReaderVersion
    root = FIX / "protocol-missing-reader"
    root.mkdir(parents=True, exist_ok=True)
    size = simple_data(root)
    write_log(root, {0: [
        protocol(1, 4), metadata(schema_json([("id", "long")])),
        add("data.parquet", size, num_records=3),
    ]})
    # 手动移除 minReaderVersion
    log = root / "_delta_log" / f"{0:020d}.json"
    lines = []
    for line in log.read_text(encoding="utf-8").splitlines():
        obj = json.loads(line)
        if "protocol" in obj:
            obj["protocol"].pop("minReaderVersion", None)
        lines.append(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    write_text(log, "\n".join(lines) + "\n")

    # protocol 版本号写成字符串（类型不对）
    root = FIX / "protocol-string-version"
    root.mkdir(parents=True, exist_ok=True)
    size = simple_data(root)
    write_log(root, {0: [
        {"protocol": {"minReaderVersion": "2", "minWriterVersion": "5"}},
        metadata(schema_json([("id", "long")])),
        add("data.parquet", size, num_records=3),
    ]})

    # 写端版本：minWriterVersion=5 + writerFeatures（本项目未实现）
    root = FIX / "writer-v5-features"
    root.mkdir(parents=True, exist_ok=True)
    size = simple_data(root)
    write_log(root, {0: [
        commit("CREATE TABLE"),
        protocol(1, 5, writer_features=["appendOnly", "invariants", "changeDataFeed"]),
        metadata(schema_json([("id", "long")])),
        add("data.parquet", size, num_records=3),
    ]})

    # 空日志文件（0 字节）
    root = FIX / "empty-log"
    (root / "_delta_log").mkdir(parents=True, exist_ok=True)
    write_text(root / "_delta_log" / f"{0:020d}.json", "")


def build_unknown_actions() -> None:
    root = FIX / "unknown-actions"
    root.mkdir(parents=True, exist_ok=True)
    size = simple_data(root)
    write_log(root, {
        0: [commit("CREATE TABLE"), protocol(1, 1),
            metadata(schema_json([("id", "long")])),
            add("data.parquet", size, num_records=3)],
        1: [
            commit("UNKNOWN"),
            {"cdc": {"path": "cdc-0001.parquet", "partitionValues": {}, "size": 100,
                     "dataChange": False}},
            {"txn": {"appId": "audit-app", "version": 1, "lastUpdated": 0}},
            {"domainMetadata": {"domain": "auditDomain", "configuration": "{}", "removed": False}},
            {"sidecar": {"path": "_delta_log/_sidecars/00000000000000000001.parquet",
                         "sizeInBytes": 10, "modificationTime": 0}},
        ],
    })

    # add 带 deletionVector（reader v3 + deletionVectors feature）：应拒绝，还是静默读全量？
    root = FIX / "deletion-vector-add"
    root.mkdir(parents=True, exist_ok=True)
    size = write_parquet(root, "data.parquet", pa.table({"id": pa.array([1, 2, 3], pa.int64())}))
    write_log(root, {0: [
        commit("CREATE TABLE"),
        protocol(3, 7, reader_features=["deletionVectors"],
                 writer_features=["deletionVectors", "appendOnly"]),
        metadata(schema_json([("id", "long")])),
        add("data.parquet", size, num_records=3,
            extra={"deletionVector": {"storageType": "u", "pathOrInlineDv": "AbCdEf12",
                                      "offset": 1, "sizeInBytes": 36, "cardinality": 1}}),
    ]})

    # column mapping（delta.columnMapping.mode=name）：元数据与物理列名不同
    root = FIX / "column-mapping"
    root.mkdir(parents=True, exist_ok=True)
    size = write_parquet(root, "data.parquet",
                         pa.table({"col-5f0c": pa.array([1, 2, 3], pa.int64())}))
    write_log(root, {0: [
        commit("CREATE TABLE"),
        protocol(2, 5, reader_features=["columnMapping"], writer_features=["columnMapping"]),
        metadata(schema_json([("id", "long")]),
                 configuration={"delta.columnMapping.mode": "name",
                                "delta.columnMapping.maxColumnId": "1"},
                 physical_names={"id": (1, "col-5f0c")}),
        add("data.parquet", size, num_records=3),
    ]})

    # 打开 CDF 的表（delta.enableChangeDataFeed=true）：configuration 里声明了未实现语义
    root = FIX / "cdf-enabled"
    root.mkdir(parents=True, exist_ok=True)
    size = simple_data(root)
    write_log(root, {0: [
        commit("CREATE TABLE"), protocol(1, 4),
        metadata(schema_json([("id", "long")]),
                 configuration={"delta.enableChangeDataFeed": "true"}),
        add("data.parquet", size, num_records=3),
    ]})


def build_log_structure() -> None:
    # 日志缺版本：v0、v2 存在，v1 缺失
    root = FIX / "log-gap"
    root.mkdir(parents=True, exist_ok=True)
    size = simple_data(root)
    base = [protocol(1, 1), metadata(schema_json([("id", "long")]))]
    write_log(root, {
        0: [commit("CREATE TABLE")] + base + [add("data.parquet", size, num_records=3)],
        2: [commit("WRITE")] + [add("data.parquet", size, num_records=3)],
    })

    # 日志从 v1 开始（没有 v0）
    root = FIX / "log-start-v1"
    root.mkdir(parents=True, exist_ok=True)
    size = simple_data(root)
    write_log(root, {1: [
        commit("CREATE TABLE"), protocol(1, 1),
        metadata(schema_json([("id", "long")])),
        add("data.parquet", size, num_records=3),
    ]})

    # 非 20 位日志文件名（0.json）
    root = FIX / "nonstandard-log-name"
    root.mkdir(parents=True, exist_ok=True)
    size = simple_data(root)
    write_raw_log_name(root, "0.json", [
        commit("CREATE TABLE"), protocol(1, 1),
        metadata(schema_json([("id", "long")])),
        add("data.parquet", size, num_records=3),
    ])

    # 非法 JSON 行
    root = FIX / "malformed-json"
    (root / "_delta_log").mkdir(parents=True, exist_ok=True)
    write_text(root / "_delta_log" / f"{0:020d}.json",
               '{"protocol":{"minReaderVersion":1,"minWriterVersion":1}}\n{not json}\n')

    # add 缺 path
    root = FIX / "add-missing-path"
    (root / "_delta_log").mkdir(parents=True, exist_ok=True)
    write_text(root / "_delta_log" / f"{0:020d}.json",
               json.dumps(protocol(1, 1)) + "\n" +
               json.dumps(metadata(schema_json([("id", "long")]))) + "\n" +
               json.dumps({"add": {"size": 10, "dataChange": True}}) + "\n")

    # 没有 commitInfo 的表
    root = FIX / "no-commitinfo"
    root.mkdir(parents=True, exist_ok=True)
    size = simple_data(root)
    write_log(root, {0: [
        protocol(1, 1), metadata(schema_json([("id", "long")])),
        add("data.parquet", size, num_records=3),
    ]})


def build_checkpoint() -> None:
    cp_src = TABLE_A_LOG / f"{10:020d}.checkpoint.parquet"
    last_cp_src = TABLE_A_LOG / "_last_checkpoint"

    # checkpoint-only：只有 checkpoint 与 _last_checkpoint，没有任何 JSON 日志
    root = FIX / "checkpoint-only"
    log = root / "_delta_log"
    log.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cp_src, log / f"{10:020d}.checkpoint.parquet")
    shutil.copyfile(last_cp_src, log / "_last_checkpoint")

    # 日志被截断：JSON v0..v9 + checkpoint v10 + _last_checkpoint(v10)，缺 v10..v12
    root = FIX / "checkpoint-truncated"
    log = root / "_delta_log"
    log.mkdir(parents=True, exist_ok=True)
    for v in range(0, 10):
        shutil.copyfile(TABLE_A_LOG / f"{v:020d}.json", log / f"{v:020d}.json")
    shutil.copyfile(cp_src, log / f"{10:020d}.checkpoint.parquet")
    shutil.copyfile(last_cp_src, log / "_last_checkpoint")

    # 有 _last_checkpoint 但内容非法 JSON
    root = FIX / "bad-last-checkpoint"
    root.mkdir(parents=True, exist_ok=True)
    size = simple_data(root)
    write_log(root, {0: [
        commit("CREATE TABLE"), protocol(1, 1),
        metadata(schema_json([("id", "long")])),
        add("data.parquet", size, num_records=3),
    ]})
    write_text(root / "_delta_log" / "_last_checkpoint", "{this is not json")


def build_schema_cases() -> None:
    # decimal schema + decimal parquet
    root = FIX / "decimal-schema"
    root.mkdir(parents=True, exist_ok=True)
    tbl = pa.table({
        "id": pa.array([1, 2, 3], pa.int64()),
        "price": pa.array([Decimal("12.34"), Decimal("0.01"), Decimal("-5.00")],
                          pa.decimal128(10, 2)),
    })
    size = write_parquet(root, "data.parquet", tbl)
    write_log(root, {0: [
        commit("CREATE TABLE"), protocol(1, 1),
        metadata(schema_json([("id", "long"), ("price", "decimal(10,2)")])),
        add("data.parquet", size, num_records=3),
    ]})
    # decimal 且数据文件缺失：用于证明 schema 拒绝发生在打开 parquet 之前
    root = FIX / "decimal-missing-file"
    (root / "_delta_log").mkdir(parents=True, exist_ok=True)
    write_log(root, {0: [
        commit("CREATE TABLE"), protocol(1, 1),
        metadata(schema_json([("id", "long"), ("price", "decimal(10,2)")])),
        add("missing.parquet", 0, num_records=3),
    ]})

    # uuid：Delta schema 里写 "uuid"（规范没有该原始类型）
    root = FIX / "uuid-schema-unknown"
    root.mkdir(parents=True, exist_ok=True)
    size = simple_data(root)
    write_log(root, {0: [
        commit("CREATE TABLE"), protocol(1, 1),
        metadata(schema_json([("u", "uuid")])),
        add("data.parquet", size, num_records=3),
    ]})

    # uuid：Delta schema 写 string，但 parquet 物理层是 UUID logical type
    root = FIX / "uuid-parquet-logical"
    root.mkdir(parents=True, exist_ok=True)
    u = uuid.UUID(bytes=b"0123456789abcdef")
    tbl = pa.table({"u": pa.array([u, u, u], pa.uuid())})
    size = write_parquet(root, "data.parquet", tbl)
    write_log(root, {0: [
        commit("CREATE TABLE"), protocol(1, 1),
        metadata(schema_json([("u", "string")])),
        add("data.parquet", size, num_records=3),
    ]})

    # 嵌套结构：struct / list / map 各一个 fixture（Delta schema 层）
    nested_cases = {
        "nested-struct": (
            [("id", "long"), ("profile", {"type": "struct", "fields": [
                {"name": "name", "type": "string", "nullable": True, "metadata": {}},
                {"name": "age", "type": "integer", "nullable": True, "metadata": {}},
            ]})],
            pa.table({
                "id": pa.array([1, 2], pa.int64()),
                "profile": pa.array([{"name": "a", "age": 30}, None],
                                    type=pa.struct([("name", pa.string()), ("age", pa.int32())])),
            }),
        ),
        "nested-list": (
            [("id", "long"), ("tags", {"type": "array", "elementType": "string",
                                       "containsNull": True})],
            pa.table({
                "id": pa.array([1, 2], pa.int64()),
                "tags": pa.array([["a", "b"], None], type=pa.list_(pa.string())),
            }),
        ),
        "nested-map": (
            [("id", "long"), ("attrs", {"type": "map", "keyType": "string",
                                        "valueType": "string", "valueContainsNull": True})],
            pa.table({
                "id": pa.array([1, 2], pa.int64()),
                "attrs": pa.array([[("k", "v")], None],
                                  type=pa.map_(pa.string(), pa.string())),
            }),
        ),
    }
    for name, (fields, tbl) in nested_cases.items():
        root = FIX / name
        root.mkdir(parents=True, exist_ok=True)
        size = write_parquet(root, "data.parquet", tbl)
        write_log(root, {0: [
            commit("CREATE TABLE"), protocol(1, 1),
            metadata(schema_json(fields)),
            add("data.parquet", size, num_records=2),
        ]})

    # 嵌套 schema 但数据文件缺失：证明拒绝来自 schema 检查而非文件打开
    root = FIX / "nested-missing-file"
    (root / "_delta_log").mkdir(parents=True, exist_ok=True)
    write_log(root, {0: [
        commit("CREATE TABLE"), protocol(1, 1),
        metadata(schema_json([("id", "long"),
                              ("tags", {"type": "array", "elementType": "string",
                                        "containsNull": True})])),
        add("missing.parquet", 0, num_records=2),
    ]})

    # Delta schema 撒谎：把 LIST 列声明成 long，逼读取路径真正打开 parquet（第二层拒绝）
    root = FIX / "nested-parquet-lie"
    root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO / "fixtures" / "parquet" / "list_columns.parquet",
                    root / "data.parquet")
    size = (root / "data.parquet").stat().st_size
    write_log(root, {0: [
        commit("CREATE TABLE"), protocol(1, 1),
        metadata(schema_json([("int64_list", "long")])),
        add("data.parquet", size, num_records=3),
    ]})


def build_parquet_types() -> None:
    # INT96 timestamp
    root = FIX / "int96-timestamp"
    root.mkdir(parents=True, exist_ok=True)
    ts = pa.array([1577836800000000, 1577923200000000], pa.timestamp("us"))
    size = write_parquet(root, "data.parquet",
                         pa.table({"ts": ts}), use_deprecated_int96_timestamps=True)
    write_log(root, {0: [
        commit("CREATE TABLE"), protocol(1, 1),
        metadata(schema_json([("ts", "timestamp")])),
        add("data.parquet", size, num_records=2),
    ]})

    # date（parquet DATE -> int32 days）
    root = FIX / "date-col"
    root.mkdir(parents=True, exist_ok=True)
    d = pa.array([date(2020, 1, 1), date(2020, 1, 2)], pa.date32())
    size = write_parquet(root, "data.parquet", pa.table({"d": d}))
    write_log(root, {0: [
        commit("CREATE TABLE"), protocol(1, 1),
        metadata(schema_json([("d", "date")])),
        add("data.parquet", size, num_records=2),
    ]})

    # float
    root = FIX / "float-col"
    root.mkdir(parents=True, exist_ok=True)
    f = pa.array([1.5, -2.25], pa.float32())
    size = write_parquet(root, "data.parquet", pa.table({"f": f}))
    write_log(root, {0: [
        commit("CREATE TABLE"), protocol(1, 1),
        metadata(schema_json([("f", "float")])),
        add("data.parquet", size, num_records=2),
    ]})

    # 各压缩 codec 的数据文件
    for codec in ["zstd", "gzip", "brotli", "lz4"]:
        root = FIX / f"codec-{codec}"
        root.mkdir(parents=True, exist_ok=True)
        size = write_parquet(root, "data.parquet",
                             pa.table({"id": pa.array([1, 2, 3], pa.int64())}),
                             compression=codec)
        write_log(root, {0: [
            commit("CREATE TABLE"), protocol(1, 1),
            metadata(schema_json([("id", "long")])),
            add("data.parquet", size, num_records=3),
        ]})

    # 损坏的 parquet（截断）
    root = FIX / "corrupt-parquet"
    root.mkdir(parents=True, exist_ok=True)
    size = simple_data(root)
    p = root / "data.parquet"
    raw = p.read_bytes()
    p.write_bytes(raw[: max(20, len(raw) - 40)])
    size = p.stat().st_size
    write_log(root, {0: [
        commit("CREATE TABLE"), protocol(1, 1),
        metadata(schema_json([("id", "long")])),
        add("data.parquet", size, num_records=3),
    ]})

    # 缺失数据文件
    root = FIX / "missing-data-file"
    (root / "_delta_log").mkdir(parents=True, exist_ok=True)
    write_log(root, {0: [
        commit("CREATE TABLE"), protocol(1, 1),
        metadata(schema_json([("id", "long")])),
        add("missing.parquet", 0, num_records=3),
    ]})

    # Delta schema 与 parquet 物理类型不一致（long vs string）
    root = FIX / "type-mismatch"
    root.mkdir(parents=True, exist_ok=True)
    size = write_parquet(root, "data.parquet",
                         pa.table({"id": pa.array(["abc", "def", "ghi"], pa.string())}))
    write_log(root, {0: [
        commit("CREATE TABLE"), protocol(1, 1),
        metadata(schema_json([("id", "long")])),
        add("data.parquet", size, num_records=3),
    ]})

    # Delta schema 有列、parquet 缺列
    root = FIX / "missing-column"
    root.mkdir(parents=True, exist_ok=True)
    size = simple_data(root)  # 只有 id
    write_log(root, {0: [
        commit("CREATE TABLE"), protocol(1, 1),
        metadata(schema_json([("id", "long"), ("name", "string")])),
        add("data.parquet", size, num_records=3),
    ]})


def build_partitioned() -> None:
    root = FIX / "partition-table"
    root.mkdir(parents=True, exist_ok=True)
    size1 = write_parquet(root, "dt=2026-01-01/part-1.parquet",
                          pa.table({"id": pa.array([1, 2], pa.int64())}))
    size2 = write_parquet(root, "dt=2026-01-02/part-1.parquet",
                          pa.table({"id": pa.array([3], pa.int64())}))
    fields = schema_json([("id", "long"), ("dt", "string")])
    write_log(root, {0: [
        commit("CREATE TABLE"), protocol(1, 2),
        metadata(fields, partition_columns=["dt"]),
        add("dt=2026-01-01/part-1.parquet", size1, num_records=2,
            partition_values={"dt": "2026-01-01"}, min_values={"id": 1}, max_values={"id": 2}),
        add("dt=2026-01-02/part-1.parquet", size2, num_records=1,
            partition_values={"dt": "2026-01-02"}, min_values={"id": 3}, max_values={"id": 3}),
    ]})

    # 分区值为 null（JSON null）：partitionValues 里的 null 会怎样？
    root = FIX / "partition-null-value"
    root.mkdir(parents=True, exist_ok=True)
    size = simple_data(root)
    write_log(root, {0: [
        commit("CREATE TABLE"), protocol(1, 2),
        metadata(schema_json([("id", "long"), ("dt", "string")]), partition_columns=["dt"]),
        add("data.parquet", size, num_records=3, partition_values={"dt": None}),
    ]})


def build_stats() -> None:
    # 字符串/布尔统计
    root = FIX / "stats-string-bool"
    root.mkdir(parents=True, exist_ok=True)
    a = pa.table({"id": pa.array([1, 2], pa.int64()),
                  "name": pa.array(["a", "b"], pa.string()),
                  "ok": pa.array([False, True], pa.bool_())})
    b = pa.table({"id": pa.array([10, 11], pa.int64()),
                  "name": pa.array(["x", "z"], pa.string()),
                  "ok": pa.array([True, True], pa.bool_())})
    sa = write_parquet(root, "a.parquet", a)
    sb = write_parquet(root, "b.parquet", b)
    write_log(root, {0: [
        commit("CREATE TABLE"), protocol(1, 1),
        metadata(schema_json([("id", "long"), ("name", "string"), ("ok", "boolean")])),
        add("a.parquet", sa, num_records=2,
            min_values={"id": 1, "name": "a", "ok": False},
            max_values={"id": 2, "name": "b", "ok": True},
            null_count={"id": 0, "name": 0, "ok": 0}),
        add("b.parquet", sb, num_records=2,
            min_values={"id": 10, "name": "x", "ok": True},
            max_values={"id": 11, "name": "z", "ok": True},
            null_count={"id": 0, "name": 0, "ok": 0}),
    ]})

    # min==max 与无 stats
    root = FIX / "stats-min-max-eq"
    root.mkdir(parents=True, exist_ok=True)
    a = write_parquet(root, "a.parquet", pa.table({"id": pa.array([5], pa.int64())}))
    b = write_parquet(root, "b.parquet", pa.table({"id": pa.array([6], pa.int64())}))
    write_log(root, {0: [
        commit("CREATE TABLE"), protocol(1, 1),
        metadata(schema_json([("id", "long")])),
        add("a.parquet", a, num_records=1, min_values={"id": 5}, max_values={"id": 5}),
        add("b.parquet", b, num_records=1),  # 无 stats
    ]})

    # stats 写成 JSON object（规范历史上有 string 与 struct 两种写法）
    root = FIX / "stats-object"
    root.mkdir(parents=True, exist_ok=True)
    size = simple_data(root)
    write_log(root, {0: [
        commit("CREATE TABLE"), protocol(1, 1),
        metadata(schema_json([("id", "long")])),
        add("data.parquet", size, stats_object={"numRecords": 3,
                                               "minValues": {"id": 1},
                                               "maxValues": {"id": 3}}),
    ]})


def build_convergence() -> None:
    root = FIX / "dup-add-remove"
    root.mkdir(parents=True, exist_ok=True)
    size = write_parquet(root, "dup.parquet",
                         pa.table({"id": pa.array([1, 2, 3], pa.int64())}))
    write_log(root, {
        0: [commit("CREATE TABLE"), protocol(1, 1),
            metadata(schema_json([("id", "long")])),
            add("dup.parquet", size, num_records=3)],
        1: [commit("WRITE"), add("dup.parquet", size, num_records=3,
                                 extra={"modificationTime": 111})],
        2: [commit("DELETE"), remove("dup.parquet")],
        3: [commit("WRITE"), add("dup.parquet", size, num_records=3,
                                 extra={"modificationTime": 222})],
        4: [commit("DELETE"), remove("never-existed.parquet")],
    })


# ---------------------------------------------------------------- 汇总
def main() -> None:
    reset_fixtures()
    build_baseline()
    build_protocol()
    build_unknown_actions()
    build_log_structure()
    build_checkpoint()
    build_schema_cases()
    build_parquet_types()
    build_partitioned()
    build_stats()
    build_convergence()

    # SHA-256 清单（docs/协议兼容性与互操作范围.md §7 要求）
    rows = []
    for p in sorted(FIX.rglob("*")):
        if p.is_file():
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            rows.append(f"{h}  {p.stat().st_size:>8}  {p.relative_to(FIX).as_posix()}")
    write_text(BASE / "fixtures.sha256", "\n".join(rows) + "\n")

    cases = sorted(p.name for p in FIX.iterdir() if p.is_dir())
    print(f"generated {len(cases)} fixture tables under {FIX}")
    for c in cases:
        print("  -", c)


if __name__ == "__main__":
    main()
