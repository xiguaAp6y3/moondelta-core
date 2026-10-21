#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""D3 nested 能力缺口对照：一键复现脚本。

流程：
  1. 用 delta-rs（Python deltalake）写出同一 protocol 配置（reader v1 / writer v2、
     JSON log + Parquet、本地文件系统）的 nested Delta fixture：
       - engine-written/          id long, profile struct, tags array, attrs map（含 null/empty 边界）
       - kind-struct-only/        仅 struct
       - kind-array-only/         仅 array
       - kind-map-only/           仅 map
       - checkpoint-nested/       engine-written 的副本 + delta-rs checkpoint（nested action 内容）
       - checkpoint-only-nested/  仅有 checkpoint + _last_checkpoint，没有 JSON 日志
       - negative-physical-nested/ 手写 flat schema log，指向 fixtures/parquet/list_columns.parquet
                                   （schema 是 flat，Parquet 物理层是 repeated/list）
       - write-attempt-nested/    用本项目 delta create 写入 nested schemaString 得到的空表（补充观察）
  2. oracle：delta-rs 读回（主 oracle，JSON + CSV）与 DuckDB delta_scan（第二 oracle）。
  3. moondelta：对每个表跑 inspect / history / read，保存 stdout/stderr/退出码原文。
  4. 生成 fixture-manifest.json（哈希、schemaString、protocol、边界值覆盖）、comparison.json /
     comparison.md（对比结论、第 3 节七项门槛状态、案例 B 预期核对）、README.md（完整报告）。

运行：
  .tools\\pyenv\\Scripts\\python.exe artifacts\\interop-nested\\run_nested.py

注意：
  - 只写 artifacts/interop-nested/ 目录；
  - fixture 由 delta-rs 现场写出，commitInfo 时间戳 / 文件名 UUID 每次不同，
    因此重跑后数据文件与日志的 SHA-256 会变化；manifest 与 README 每次按当次产物重新生成。
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from importlib.metadata import version as pkg_version
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import deltalake
from deltalake import DeltaTable, write_deltalake

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
RAW = OUT / "raw"
EXE = ROOT / "_build" / "native" / "debug" / "build" / "cmd" / "delta" / "delta.exe"

MAIN = "engine-written"
KIND_TABLES = ["kind-struct-only", "kind-array-only", "kind-map-only"]
CP_NESTED = "checkpoint-nested"
CP_ONLY = "checkpoint-only-nested"
NEG = "negative-physical-nested"
WRITE_ATTEMPT = "write-attempt-nested"

MANAGED_DIRS = [MAIN, *KIND_TABLES, CP_NESTED, CP_ONLY, NEG, WRITE_ATTEMPT, "raw"]
MANAGED_FILES = [
    "oracle-delta-rs.json",
    "oracle-delta-rs.csv",
    "oracle-duckdb.json",
    "fixture-manifest.json",
    "checkpoint-nested.json",
    "negative-physical-nested.json",
    "write-attempt-nested.json",
    "comparison.json",
    "comparison.md",
    "README.md",
]


# --------------------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------------------
def rel(p: Path) -> str:
    return str(Path(p).resolve().relative_to(ROOT)).replace("\\", "/")


def rel_out(p: Path) -> str:
    return str(Path(p).resolve().relative_to(OUT)).replace("\\", "/")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path: Path, obj) -> None:
    write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_rmtree(path: Path) -> None:
    path = path.resolve()
    if path.parent != OUT.resolve():
        raise RuntimeError(f"refusing to remove path outside {OUT}: {path}")
    if path.exists():
        shutil.rmtree(path)


def prepare() -> None:
    if not EXE.exists():
        raise SystemExit(f"moondelta CLI not found: {EXE}")
    for name in MANAGED_DIRS:
        if name != "raw":
            safe_rmtree(OUT / name)
    safe_rmtree(RAW)
    for name in MANAGED_FILES:
        p = OUT / name
        if p.exists():
            p.unlink()
    RAW.mkdir(parents=True, exist_ok=True)


def decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------------------
# fixture 定义与写出
# --------------------------------------------------------------------------------------
MAIN_SCHEMA = pa.schema(
    [
        pa.field("id", pa.int64(), nullable=True),
        pa.field(
            "profile",
            pa.struct(
                [
                    pa.field("name", pa.string(), nullable=True),
                    pa.field("age", pa.int32(), nullable=True),
                ]
            ),
            nullable=True,
        ),
        pa.field("tags", pa.list_(pa.string()), nullable=True),
        pa.field("attrs", pa.map_(pa.string(), pa.string()), nullable=True),
    ]
)

MAIN_ROWS = [
    {
        "id": 1,
        "profile": {"name": "alice", "age": 30},
        "tags": ["red", "green"],
        "attrs": [("k1", "v1"), ("k2", "v2")],
    },
    {"id": 2, "profile": None, "tags": [], "attrs": []},
    {"id": 3, "profile": {"name": None, "age": None}, "tags": None, "attrs": [("k", "v")]},
    {"id": 4, "profile": {"name": "bob", "age": None}, "tags": ["blue", None], "attrs": []},
    {"id": 5, "profile": {"name": None, "age": 42}, "tags": [], "attrs": None},
]

KIND_SPECS = {
    "kind-struct-only": {
        "schema": pa.schema(
            [
                pa.field("id", pa.int64(), nullable=True),
                pa.field(
                    "profile",
                    pa.struct(
                        [
                            pa.field("name", pa.string(), nullable=True),
                            pa.field("age", pa.int32(), nullable=True),
                        ]
                    ),
                    nullable=True,
                ),
            ]
        ),
        "rows": [
            {"id": 1, "profile": {"name": "alice", "age": 30}},
            {"id": 2, "profile": None},
            {"id": 3, "profile": {"name": None, "age": None}},
            {"id": 4, "profile": {"name": "carol", "age": None}},
        ],
    },
    "kind-array-only": {
        "schema": pa.schema(
            [
                pa.field("id", pa.int64(), nullable=True),
                pa.field("tags", pa.list_(pa.string()), nullable=True),
            ]
        ),
        "rows": [
            {"id": 1, "tags": ["a", "b"]},
            {"id": 2, "tags": []},
            {"id": 3, "tags": None},
            {"id": 4, "tags": ["c", None]},
        ],
    },
    "kind-map-only": {
        "schema": pa.schema(
            [
                pa.field("id", pa.int64(), nullable=True),
                pa.field("attrs", pa.map_(pa.string(), pa.string()), nullable=True),
            ]
        ),
        "rows": [
            {"id": 1, "attrs": [("k1", "v1")]},
            {"id": 2, "attrs": []},
            {"id": 3, "attrs": None},
            {"id": 4, "attrs": [("k2", None)]},
        ],
    },
}


def write_fixture(name: str, schema: pa.Schema, rows) -> Path:
    path = OUT / name
    table = pa.Table.from_pylist(rows, schema=schema)
    write_deltalake(str(path), table, mode="overwrite")
    return path


def load_log(path: Path) -> dict:
    log_dir = path / "_delta_log"
    json_files = sorted(log_dir.glob("*.json"))
    raw_actions = []
    for f in json_files:
        text = f.read_text(encoding="utf-8").replace("\r", "")
        for line in text.split("\n"):
            if line.strip():
                raw_actions.append({"file": f.name, "action": json.loads(line)})
    protocol = None
    metadata = None
    adds = []
    commit_infos = []
    for item in raw_actions:
        action = item["action"]
        if "protocol" in action:
            protocol = action["protocol"]
        elif "metaData" in action:
            metadata = action["metaData"]
        elif "add" in action:
            adds.append(action["add"])
        elif "commitInfo" in action:
            commit_infos.append(action["commitInfo"])
    return {
        "log_files": [f.name for f in json_files],
        "raw_actions": raw_actions,
        "protocol": protocol,
        "metadata": metadata,
        "adds": adds,
        "commit_infos": commit_infos,
    }


def fixture_descriptor(name: str, path: Path, writer: str = None) -> dict:
    log = load_log(path)
    metadata = log["metadata"] or {}
    data_files = sorted(p.name for p in path.glob("*.parquet"))
    adds = []
    for add in log["adds"]:
        stats_json = None
        if add.get("stats"):
            try:
                stats_json = json.loads(add["stats"])
            except Exception:
                stats_json = None
        adds.append(
            {
                "path": add.get("path"),
                "size": add.get("size"),
                "modificationTime": add.get("modificationTime"),
                "stats_raw": add.get("stats"),
                "stats_json": stats_json,
            }
        )
    return {
        "name": name,
        "path": rel(path),
        "writer": writer or "delta-rs (Python deltalake.write_deltalake)",
        "version": len(log["log_files"]) - 1 if log["log_files"] else None,
        "log_files": log["log_files"],
        "protocol": log["protocol"],
        "schemaString": metadata.get("schemaString"),
        "partitionColumns": metadata.get("partitionColumns"),
        "format": metadata.get("format"),
        "adds": adds,
        "data_files": data_files,
        "commit_info": log["commit_infos"][0] if log["commit_infos"] else None,
    }


# --------------------------------------------------------------------------------------
# Arrow 值 / schema 编码（null、空集合、struct 子字段均可区分）
# --------------------------------------------------------------------------------------
def encode_value(value, t):
    if value is None:
        return {"kind": "null"}
    if pa.types.is_struct(t):
        fields = {}
        for f in t:
            child = value.get(f.name) if isinstance(value, dict) else None
            fields[f.name] = encode_value(child, f.type)
        return {"kind": "struct", "fields": fields}
    if pa.types.is_list(t) or pa.types.is_large_list(t):
        return {"kind": "list", "items": [encode_value(x, t.value_type) for x in value]}
    if pa.types.is_map(t):
        entries = []
        for k, v in value:
            entries.append(
                [encode_value(k, t.key_type), encode_value(v, t.item_type)]
            )
        entries.sort(key=lambda e: json.dumps(e[0], sort_keys=True))
        return {"kind": "map", "entries": entries}
    if pa.types.is_boolean(t):
        return {"kind": "bool", "value": bool(value)}
    if pa.types.is_integer(t):
        return {"kind": str(t), "value": int(value)}
    if pa.types.is_floating(t):
        return {"kind": str(t), "value": float(value)}
    if pa.types.is_string(t):
        return {"kind": "string", "value": value}
    if pa.types.is_binary(t):
        import base64

        return {"kind": "binary", "base64": base64.b64encode(value).decode("ascii")}
    return {"kind": str(t), "value": str(value)}


def encode_rows(table: pa.Table) -> list:
    out = []
    for row in table.to_pylist():
        out.append({f.name: encode_value(row[f.name], f.type) for f in table.schema})
    return out


def schema_tree(t) -> dict:
    if isinstance(t, pa.Schema):
        return {
            "kind": "struct",
            "fields": [
                {"name": f.name, "nullable": f.nullable, "type": schema_tree(f.type)}
                for f in t
            ],
        }
    if pa.types.is_struct(t):
        return {
            "kind": "struct",
            "fields": [
                {"name": f.name, "nullable": f.nullable, "type": schema_tree(f.type)}
                for f in t
            ],
        }
    if pa.types.is_list(t) or pa.types.is_large_list(t):
        return {
            "kind": "list",
            "element_nullable": t.value_field.nullable,
            "element": schema_tree(t.value_type),
        }
    if pa.types.is_map(t):
        return {
            "kind": "map",
            "key": schema_tree(t.key_type),
            "value": schema_tree(t.item_type),
            "value_nullable": t.item_field.nullable,
            "keys_sorted": t.keys_sorted,
        }
    return {"kind": "primitive", "arrow_type": str(t)}


def display_value(value, t) -> str:
    """仅用于 CSV / README 展示：NULL、[]、{}、{...} 可肉眼区分。"""
    if value is None:
        return "NULL"
    if pa.types.is_struct(t):
        parts = []
        for f in t:
            child = value.get(f.name) if isinstance(value, dict) else None
            parts.append(f"{f.name}: {display_value(child, f.type)}")
        return "{" + ", ".join(parts) + "}"
    if pa.types.is_list(t) or pa.types.is_large_list(t):
        return "[" + ", ".join(display_value(x, t.value_type) for x in value) + "]"
    if pa.types.is_map(t):
        return (
            "{"
            + ", ".join(
                f"{display_value(k, t.key_type)}: {display_value(v, t.item_type)}"
                for k, v in value
            )
            + "}"
        )
    if pa.types.is_boolean(t):
        return "true" if value else "false"
    return str(value)


def display_rows(table: pa.Table) -> list:
    rows = []
    for row in table.to_pylist():
        rows.append(
            {f.name: display_value(row[f.name], f.type) for f in table.schema}
        )
    return rows


def expected_display(spec_rows, schema, fields) -> list:
    """按 fixture 输入（Python 值）生成期望展示值，用于自检。"""
    rows = pa.Table.from_pylist(spec_rows, schema=schema).to_pylist()
    out = []
    for row in rows:
        out.append(
            {
                f: display_value(row[f], schema.field(f).type)
                for f in fields
            }
        )
    return out


# --------------------------------------------------------------------------------------
# oracle
# --------------------------------------------------------------------------------------
def delta_rs_open(path: Path) -> DeltaTable:
    return DeltaTable(str(path))


def oracle_delta_rs(name: str, descriptor: dict) -> dict:
    path = OUT / name
    dt = delta_rs_open(path)
    table = dt.to_pyarrow_table()
    schema_json = dt.schema().to_json()
    proto = dt.protocol()
    log_schema = descriptor.get("schemaString")
    schema_equals_log = None
    if log_schema:
        schema_equals_log = json.loads(schema_json) == json.loads(log_schema)
    return {
        "oracle": "delta-rs (Python deltalake)",
        "engine_version": deltalake.__version__,
        "table": descriptor["path"],
        "version": dt.version(),
        "protocol": {
            "minReaderVersion": proto.min_reader_version,
            "minWriterVersion": proto.min_writer_version,
            "readerFeatures": proto.reader_features,
            "writerFeatures": proto.writer_features,
        },
        "schema_json_from_delta_rs": json.loads(schema_json),
        "schema_json_equals_log_schemaString": schema_equals_log,
        "schema_tree": schema_tree(table.schema),
        "row_count": table.num_rows,
        "rows": encode_rows(table),
        "rows_display": display_rows(table),
    }


def init_duckdb():
    con = duckdb.connect()
    info = {"load_ok": False, "install_needed": False, "extension_version": None, "error": None}
    try:
        con.execute("LOAD delta")
    except Exception:
        try:
            con.execute("INSTALL delta")
            con.execute("LOAD delta")
            info["install_needed"] = True
        except Exception as exc:  # pragma: no cover - 环境已装好时不会走到
            info["error"] = f"{type(exc).__name__}: {exc}"
            return con, info
    try:
        row = con.execute(
            "select extension_version from duckdb_extensions() where extension_name='delta'"
        ).fetchone()
        info["extension_version"] = row[0] if row else None
        info["load_ok"] = True
    except Exception as exc:  # pragma: no cover
        info["error"] = f"{type(exc).__name__}: {exc}"
    return con, info


def duckdb_arrow(con, path: Path) -> pa.Table:
    uri = str(path.resolve()).replace("\\", "/")
    reader = con.execute("SELECT * FROM delta_scan(?)", [uri]).arrow()
    if hasattr(reader, "read_all"):
        return reader.read_all()
    return reader


def duckdb_parquet_fallback(con, path: Path) -> pa.Table:
    files = sorted(path.glob("*.parquet"))
    parts = []
    for p in files:
        reader = con.execute("SELECT * FROM read_parquet(?)", [str(p.resolve()).replace("\\", "/")]).arrow()
        parts.append(reader.read_all() if hasattr(reader, "read_all") else reader)
    return pa.concat_tables(parts)


def oracle_duckdb(name: str, descriptor: dict, con, info: dict) -> dict:
    path = OUT / name
    entry = {
        "engine": "DuckDB",
        "engine_version": duckdb.__version__,
        "delta_extension": info,
        "table": descriptor["path"],
        "delta_level": True,
        "status": "ok",
        "error": None,
        "fallback_note": None,
        "schema_tree": None,
        "row_count": None,
        "rows": None,
        "rows_display": None,
    }
    if not info["load_ok"]:
        entry["status"] = "extension_unavailable"
        entry["error"] = info["error"]
    else:
        try:
            table = duckdb_arrow(con, path)
            entry["schema_tree"] = schema_tree(table.schema)
            entry["row_count"] = table.num_rows
            entry["rows"] = encode_rows(table)
            entry["rows_display"] = display_rows(table)
            return entry
        except Exception as exc:
            entry["status"] = "delta_scan_failed"
            entry["error"] = f"{type(exc).__name__}: {exc}"
    try:
        table = duckdb_parquet_fallback(con, path)
        entry["delta_level"] = False
        entry["fallback_note"] = (
            "delta_scan 不可用，改用 read_parquet 直接读数据文件；该结果仅证明 Parquet 文件可读，"
            "不是 Delta 表级读取，不能替代 delta_scan 对照。"
        )
        entry["schema_tree"] = schema_tree(table.schema)
        entry["row_count"] = table.num_rows
        entry["rows"] = encode_rows(table)
        entry["rows_display"] = display_rows(table)
    except Exception as exc:
        entry["status"] = "failed"
        entry["fallback_note"] = f"{type(exc).__name__}: {exc}"
    return entry


def row_index(rows, id_field="id"):
    out = {}
    for row in rows:
        key = json.dumps(row.get(id_field), sort_keys=True)
        out[key] = json.dumps(row, sort_keys=True, ensure_ascii=False)
    return out


def compare_oracles(a_rows, b_rows, id_field="id") -> dict:
    a = row_index(a_rows, id_field)
    b = row_index(b_rows, id_field)
    same_ids = set(a) == set(b)
    diffs = sorted(k for k in set(a) & set(b) if a[k] != b[k])
    return {
        "row_count_equal": len(a_rows) == len(b_rows),
        "id_sets_equal": same_ids,
        "rows_equal": same_ids and not diffs,
        "differing_ids": [json.loads(k) for k in diffs],
    }


# --------------------------------------------------------------------------------------
# moondelta CLI
# --------------------------------------------------------------------------------------
def cli(table_name: str, sub: str, extra_args=None) -> dict:
    table_path = rel(OUT / table_name)
    args = [sub, table_path] + list(extra_args or [])
    argv = [str(EXE)] + args
    proc = subprocess.run(
        argv, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    base = f"{table_name}__{sub}"
    if extra_args:
        base += "_" + re.sub(r"[^0-9a-zA-Z]+", "_", "_".join(map(str, extra_args))).strip("_")
    stdout_path = RAW / f"{base}.stdout.txt"
    stderr_path = RAW / f"{base}.stderr.txt"
    exit_path = RAW / f"{base}.exit.txt"
    cmd_path = RAW / f"{base}.cmd.txt"
    stdout_path.write_bytes(proc.stdout)
    stderr_path.write_bytes(proc.stderr)
    exit_path.write_text(str(proc.returncode) + "\n", encoding="utf-8", newline="\n")
    cmd_path.write_text("delta.exe " + " ".join(args) + "\n", encoding="utf-8", newline="\n")
    return {
        "table": table_name,
        "subcommand": sub,
        "args": args,
        "command": "delta.exe " + " ".join(args),
        "exit_code": proc.returncode,
        "stdout": decode(proc.stdout),
        "stderr": decode(proc.stderr),
        "stdout_file": rel_out(stdout_path),
        "stderr_file": rel_out(stderr_path),
        "exit_file": rel_out(exit_path),
        "cmd_file": rel_out(cmd_path),
    }


def parse_inspect(stdout: str) -> dict:
    lines = stdout.splitlines()
    info = {
        "schema_display": [],
        "note_lines": [],
        "last_checkpoint": None,
        "current_version": None,
        "log_versions": None,
        "files": None,
        "rows": None,
        "protocol": None,
    }
    in_schema = False
    for line in lines:
        if line.startswith("schema:"):
            in_schema = True
            continue
        if in_schema:
            if line.startswith("  "):
                info["schema_display"].append(line.strip())
                continue
            in_schema = False
        m = re.match(r"current version\s*:\s*(\d+)\s*\(log versions:\s*(\d+)\)", line)
        if m:
            info["current_version"] = int(m.group(1))
            info["log_versions"] = int(m.group(2))
            continue
        m = re.match(r"files\s*:\s*(\d+)\s+rows:\s*(\d+)\s+bytes:\s*(\d+)", line)
        if m:
            info["files"] = int(m.group(1))
            info["rows"] = int(m.group(2))
            continue
        m = re.match(r"protocol\s*:\s*reader=(\d+)\s+writer=(\d+)", line)
        if m:
            info["protocol"] = {
                "minReaderVersion": int(m.group(1)),
                "minWriterVersion": int(m.group(2)),
            }
            continue
        m = re.match(r"last checkpoint\s*:\s*version=(\d+)", line)
        if m:
            info["last_checkpoint"] = int(m.group(1))
            continue
        m = re.match(r"note\s*:\s*(.+)", line)
        if m:
            info["note_lines"].append(m.group(1))
    return info


def parse_read(stdout: str) -> dict:
    lines = stdout.splitlines()
    error_line = next((ln for ln in lines if ln.startswith("error: ")), None)
    success_line = next((ln for ln in lines if ln.startswith("read ")), None)
    out = {
        "error_text": error_line[len("error: "):] if error_line else None,
        "success_line": success_line,
        "fake_success": success_line is not None,
        "files_read": None,
        "row_count": None,
        "version": None,
        "returns_zero_rows": False,
        "data_rows_printed": 0,
    }
    if success_line:
        m = re.match(r"read (\d+) file\(s\), (\d+) row\(s\), version (\d+)", success_line)
        if m:
            out["files_read"] = int(m.group(1))
            out["row_count"] = int(m.group(2))
            out["version"] = int(m.group(3))
            out["returns_zero_rows"] = out["row_count"] == 0
        idx = lines.index(success_line)
        for line in lines[idx + 1:]:
            if line.startswith("... ("):
                continue
            out["data_rows_printed"] += 1
    return out


def parse_scan(stdout: str) -> dict:
    info = {"selected_files": None, "skipped_partition": None, "skipped_stats": None,
            "matched_rows": None, "matched_bytes": None}
    for line in stdout.splitlines():
        m = re.match(r"selected files\s*:\s*(\d+)", line)
        if m:
            info["selected_files"] = int(m.group(1))
        m = re.match(r"skipped partition:\s*(\d+)", line)
        if m:
            info["skipped_partition"] = int(m.group(1))
        m = re.match(r"skipped stats\s*:\s*(\d+)", line)
        if m:
            info["skipped_stats"] = int(m.group(1))
        m = re.match(r"matched rows\s*:\s*(\d+)", line)
        if m:
            info["matched_rows"] = int(m.group(1))
        m = re.match(r"matched bytes\s*:\s*(\d+)", line)
        if m:
            info["matched_bytes"] = int(m.group(1))
    return info


# --------------------------------------------------------------------------------------
# checkpoint / 负向 fixture
# --------------------------------------------------------------------------------------
def make_checkpoint_fixtures() -> None:
    src = OUT / MAIN
    nested = OUT / CP_NESTED
    shutil.copytree(src, nested)
    dt = DeltaTable(str(nested))
    dt.create_checkpoint()

    only = OUT / CP_ONLY
    shutil.copytree(nested, only)
    for json_file in (only / "_delta_log").glob("*.json"):
        json_file.unlink()


def checkpoint_probe() -> dict:
    path = OUT / CP_NESTED
    log_dir = path / "_delta_log"
    cp_files = sorted(log_dir.glob("*.checkpoint.parquet"))
    last_cp = log_dir / "_last_checkpoint"
    result = {
        "table": rel(path),
        "checkpoint_files": [f.name for f in cp_files],
        "last_checkpoint": json.loads(last_cp.read_text(encoding="utf-8")) if last_cp.exists() else None,
        "checkpoint_columns": [],
        "checkpoint_schema_arrow": None,
        "metaData_schemaString_in_checkpoint": None,
        "add_stats_in_checkpoint": None,
        "add_stats_in_checkpoint_json": None,
        "checkpoint_has_nested_action_schema": None,
        "observations": [],
    }
    if cp_files:
        cp = cp_files[0]
        pf = pq.ParquetFile(str(cp))
        result["checkpoint_schema_arrow"] = str(pf.schema_arrow)
        table = pq.read_table(str(cp))
        result["checkpoint_columns"] = table.column_names
        for name in ("metaData", "add"):
            if name not in table.column_names:
                continue
            values = table.column(name).to_pylist()
            non_null = next((v for v in values if v is not None), None)
            if name == "metaData" and non_null:
                result["metaData_schemaString_in_checkpoint"] = non_null.get("schemaString")
            if name == "add" and non_null:
                result["add_stats_in_checkpoint"] = non_null.get("stats")
                if non_null.get("stats"):
                    try:
                        result["add_stats_in_checkpoint_json"] = json.loads(non_null["stats"])
                    except Exception:
                        pass
        nested_markers = []
        schema_text = result["checkpoint_schema_arrow"] or ""
        for marker in ("partitionValues: map", "tags: map", "deletionVector: struct",
                       "metaData: struct", "protocol: struct"):
            nested_markers.append({"marker": marker, "present": marker in schema_text})
        result["checkpoint_nested_action_fields"] = nested_markers
        result["checkpoint_has_nested_action_schema"] = (
            result["metaData_schemaString_in_checkpoint"] is not None
            and result["add_stats_in_checkpoint_json"] is not None
            and bool(result["add_stats_in_checkpoint_json"].get("minValues", {}).get("profile"))
        )
        result["observations"].append(
            "checkpoint 的 add.stats 以 JSON 字符串保存，字符串内部含嵌套 minValues/nullCount；"
            "checkpoint 本身是 struct<add, remove, metaData, protocol, ...> 的 action 行式快照。"
        )
    return result


def make_negative_fixture() -> dict:
    path = OUT / NEG
    (path / "_delta_log").mkdir(parents=True, exist_ok=True)
    (path / "data").mkdir(parents=True, exist_ok=True)
    src_parquet = ROOT / "fixtures" / "parquet" / "list_columns.parquet"
    dst_parquet = path / "data" / "list_columns.parquet"
    shutil.copy2(src_parquet, dst_parquet)
    size = dst_parquet.stat().st_size
    schema_string = json.dumps(
        {
            "type": "struct",
            "fields": [
                {"name": "int64_list", "type": "long", "nullable": True, "metadata": {}},
                {"name": "utf8_list", "type": "string", "nullable": True, "metadata": {}},
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    actions = [
        {
            "commitInfo": {
                "timestamp": 0,
                "operation": "WRITE",
                "operationParameters": {"mode": "Append"},
                "engineInfo": "handwritten-negative-fixture",
            }
        },
        {"protocol": {"minReaderVersion": 1, "minWriterVersion": 2}},
        {
            "metaData": {
                "id": "00000000-0000-0000-0000-000000000001",
                "format": {"provider": "parquet", "options": {}},
                "schemaString": schema_string,
                "partitionColumns": [],
                "configuration": {},
                "createdTime": 0,
            }
        },
        {
            "add": {
                "path": "data/list_columns.parquet",
                "partitionValues": {},
                "size": size,
                "modificationTime": 0,
                "dataChange": True,
                "stats": json.dumps({"numRecords": 3}, separators=(",", ":")),
            }
        },
    ]
    content = "\n".join(json.dumps(a, ensure_ascii=False, separators=(",", ":")) for a in actions) + "\n"
    write_text(path / "_delta_log" / "00000000000000000000.json", content)
    return {
        "name": NEG,
        "path": rel(path),
        "purpose": "Delta schema 声明为 flat（int64_list long / utf8_list string），"
                   "但 add.path 指向的 Parquet 物理层是 standard 3-level LIST（repeated group）。"
                   "用于验证“schema 是 flat 但 Parquet 物理嵌套”的路径必须结构化拒绝。",
        "source_parquet": rel(src_parquet),
        "source_parquet_sha256": sha256_file(src_parquet),
        "source_parquet_bytes": src_parquet.stat().st_size,
        "copied_parquet": rel(dst_parquet),
        "schemaString": schema_string,
        "protocol": {"minReaderVersion": 1, "minWriterVersion": 2},
        "log_files": ["00000000000000000000.json"],
    }


def make_write_attempt_fixture() -> dict:
    """补充观察：delta create 接受 nested schemaString，但不校验能否写出（无 append CLI）。"""
    path = OUT / WRITE_ATTEMPT
    schema_string = json.dumps(
        {
            "type": "struct",
            "fields": [
                {"name": "id", "type": "long", "nullable": True, "metadata": {}},
                {
                    "name": "profile",
                    "type": {
                        "type": "struct",
                        "fields": [
                            {"name": "name", "type": "string", "nullable": True, "metadata": {}},
                            {"name": "age", "type": "integer", "nullable": True, "metadata": {}},
                        ],
                    },
                    "nullable": True,
                    "metadata": {},
                },
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    proc = subprocess.run(
        [str(EXE), "create", rel(path), schema_string],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base = "write-attempt-nested__create"
    (RAW / f"{base}.stdout.txt").write_bytes(proc.stdout)
    (RAW / f"{base}.stderr.txt").write_bytes(proc.stderr)
    (RAW / f"{base}.exit.txt").write_text(str(proc.returncode) + "\n", encoding="utf-8", newline="\n")
    # create 的 schemaString 很长，单独落一个文件保存原始参数
    (RAW / f"{base}.schemaString.txt").write_text(schema_string + "\n", encoding="utf-8", newline="\n")
    return {
        "name": WRITE_ATTEMPT,
        "path": rel(path),
        "purpose": "补充观察：本项目 `delta create` 对 nested schemaString 不做可写性校验，"
                   "会生成一张 metadata 声称 nested、但写入路径实际无法 append 的空表；"
                   "该表自身也无法被本项目的 read 读取。这不属于互操作通过。",
        "schemaString": schema_string,
        "create_stdout": decode(proc.stdout),
        "create_stderr": decode(proc.stderr),
        "create_exit_code": proc.returncode,
        "create_stdout_file": f"raw/{base}.stdout.txt",
        "create_stderr_file": f"raw/{base}.stderr.txt",
        "create_exit_file": f"raw/{base}.exit.txt",
        "create_schema_arg_file": f"raw/{base}.schemaString.txt",
        "created": path.exists(),
    }


# --------------------------------------------------------------------------------------
# 哈希 / 边界覆盖
# --------------------------------------------------------------------------------------
def manifest_hashes() -> list:
    rows = []
    for name in [MAIN, *KIND_TABLES, CP_NESTED, CP_ONLY, NEG, WRITE_ATTEMPT]:
        base = OUT / name
        files = sorted(p for p in base.rglob("*") if p.is_file())
        for f in files:
            rows.append(
                {
                    "fixture": name,
                    "file": rel(f),
                    "bytes": f.stat().st_size,
                    "sha256": sha256_file(f),
                }
            )
    src = ROOT / "fixtures" / "parquet" / "list_columns.parquet"
    rows.append(
        {
            "fixture": "(source) fixtures/parquet/list_columns.parquet",
            "file": rel(src),
            "bytes": src.stat().st_size,
            "sha256": sha256_file(src),
        }
    )
    return rows


def boundary_coverage(rows) -> dict:
    coverage = {
        "profile_null": [],
        "profile_all_children_null": [],
        "profile_name_set_age_null": [],
        "tags_empty_list": [],
        "tags_null": [],
        "tags_null_element": [],
        "attrs_empty_map": [],
        "attrs_null": [],
    }
    for row in rows:
        rid = row["id"]["value"]
        profile = row["profile"]
        tags = row["tags"]
        attrs = row["attrs"]
        if profile["kind"] == "null":
            coverage["profile_null"].append(rid)
        elif profile["kind"] == "struct":
            name_kind = profile["fields"]["name"]["kind"]
            age_kind = profile["fields"]["age"]["kind"]
            if name_kind == "null" and age_kind == "null":
                coverage["profile_all_children_null"].append(rid)
            if name_kind != "null" and age_kind == "null":
                coverage["profile_name_set_age_null"].append(rid)
        if tags["kind"] == "null":
            coverage["tags_null"].append(rid)
        elif tags["kind"] == "list":
            if not tags["items"]:
                coverage["tags_empty_list"].append(rid)
            if any(item["kind"] == "null" for item in tags["items"]):
                coverage["tags_null_element"].append(rid)
        if attrs["kind"] == "null":
            coverage["attrs_null"].append(rid)
        elif attrs["kind"] == "map" and not attrs["entries"]:
            coverage["attrs_empty_map"].append(rid)
    return coverage


REQUIRED_COVERAGE = [
    "profile_null",
    "profile_all_children_null",
    "profile_name_set_age_null",
    "tags_empty_list",
    "tags_null",
    "tags_null_element",
    "attrs_empty_map",
    "attrs_null",
]


def assert_coverage(coverage: dict) -> None:
    missing = [k for k in REQUIRED_COVERAGE if not coverage.get(k)]
    if missing:
        raise SystemExit(f"nested 边界覆盖不足，缺少: {missing}; coverage={coverage}")


# --------------------------------------------------------------------------------------
# checks / 门槛 / 案例 B
# --------------------------------------------------------------------------------------
def build_checks(descriptor, moon, external_ok, duckdb_ok, kind_expect=None) -> dict:
    inspect_info = parse_inspect(moon["inspect"]["stdout"])
    read_info = parse_read(moon["read"]["stdout"])
    error = read_info["error_text"] or ""
    field_match = re.search(r"nested column '([^']+)' \(type=(\w+)\)", error)
    parquet_layer_match = re.search(r"cannot read '([^']+)': (.+)", error)
    schema_kinds = [ln.split(": ", 1)[1] for ln in inspect_info["schema_display"] if ": " in ln]
    nested_expected = kind_expect or ["struct", "array", "map"]
    return {
        "external_delta_rs_ok": external_ok,
        "external_duckdb_ok": duckdb_ok,
        "inspect_reports_nested_types": all(k in schema_kinds for k in nested_expected),
        "inspect_schema_display": inspect_info["schema_display"],
        "inspect_does_not_expand_struct_subfields": (
            all("." not in line for line in inspect_info["schema_display"])
        ),
        "inspect_detects_checkpoint": inspect_info["last_checkpoint"] is not None
        or bool(inspect_info["note_lines"]),
        "read_structured_unsupported": bool(field_match or parquet_layer_match),
        "read_error_field_path": field_match.group(1) if field_match else None,
        "read_error_nested_kind": field_match.group(2) if field_match else None,
        "read_error_parquet_file": parquet_layer_match.group(1) if parquet_layer_match else None,
        "read_error_parquet_reason": parquet_layer_match.group(2) if parquet_layer_match else None,
        "read_error_has_deep_nested_path": bool(re.search(r"nested column '[^']+\.", error)),
        "fake_success": read_info["fake_success"],
        "returns_zero_rows": read_info["returns_zero_rows"],
        "data_rows_printed": read_info["data_rows_printed"],
        "cli_exit_code": moon["read"]["exit_code"],
        "cli_exit_nonzero_on_failure": moon["read"]["exit_code"] != 0,
        "error_text": read_info["error_text"],
        "read_stdout_file": moon["read"]["stdout_file"],
        "read_stderr_empty": moon["read"]["stderr"] == "",
    }


def build_case_b_checklist(main_checks: dict, main_moon: dict) -> list:
    return [
        {
            "expectation": "能读取并报告 Delta schema 中的 nested 类型",
            "result": "通过（但不完整）",
            "evidence": "`delta inspect` 输出 `profile: struct`、`tags: array`、`attrs: map`；"
                        "未显示 struct 子字段 name/age。",
            "status": "partial",
        },
        {
            "expectation": "在进入 Parquet 数据读取前返回 Unsupported，错误包含字段路径和 nested kind",
            "result": "通过（字段路径仅为顶层列名）",
            "evidence": f"`{main_checks['error_text']}`（文件 {main_checks['read_stdout_file']}）；"
                        "错误发生在打开 Parquet 之前（ensure_readable_schema），但路径只有 `profile`，"
                        "没有 `profile.age` 这类深层路径。",
            "status": "partial",
        },
        {
            "expectation": "不得返回 0 行",
            "result": "通过",
            "evidence": "read 未打印任何 `read N file(s), M row(s)` 成功行，也没有数据行；"
                        "只输出 error。",
            "status": "pass",
        },
        {
            "expectation": "不得把 array 当 string",
            "result": "通过（拒绝）",
            "evidence": "schema 阶段直接拒绝，未进入列值解码；`kind-array-only` 用例给出 "
                        "`nested column 'tags' (type=array)`。",
            "status": "pass",
        },
        {
            "expectation": "不得丢弃 struct 子字段",
            "result": "数据层通过（拒绝），识别层已丢失",
            "evidence": "数据读取被拒绝；但 `src/schema.mbt` 的 `NestedType(kind~)` 只保留 kind，"
                        "`DeltaField` 不保存 struct 子字段，`inspect` 无法打印 name/age。",
            "status": "partial",
        },
        {
            "expectation": "不得静默忽略 map",
            "result": "通过（拒绝）",
            "evidence": "`kind-map-only` 用例给出 `nested column 'attrs' (type=map)`。",
            "status": "pass",
        },
        {
            "expectation": "CLI 失败时退出码非 0（任务补充检查）",
            "result": "不通过",
            "evidence": f"read 失败的退出码为 {main_checks['cli_exit_code']}；`fail()` 只打印 "
                        "`error: ...` 后正常返回，`main` 无失败退出路径。",
            "status": "fail",
        },
        {
            "expectation": "结构化错误写到 stderr（任务补充检查）",
            "result": "不通过",
            "evidence": f"read 的 stderr 为空（{main_moon['read']['stderr_file']}），错误在 stdout。",
            "status": "fail",
        },
    ]


GATES = [
    {
        "id": 1,
        "title": "Parquet definition/repetition level，正确处理 struct、list（标准 3-level LIST）和 map 的 null、empty、重复元素与嵌套组合",
        "status": "未完成",
        "evidence": [
            "`raw/engine-written__read.stdout.txt`：本项目在 schema 校验阶段就拒绝，根本没有进入 Parquet 解码；",
            "`raw/negative-physical-nested__read.stdout.txt`：当 Delta schema 伪装成 flat、物理层是 repeated LIST 时，"
            "底层 `mizchi/parquet` 报 `Repeated columns are not supported yet`，说明连 list 的 repetition level 都不支持；",
            "`src/vendor/parquet/` 为移植的 flat reader，无 Dremel definition/repetition level 解码。",
        ],
        "work": "替换/扩展 Parquet 读取层，实现 definition/repetition level、3-level LIST / MAP 的 null-empty-空集合语义，"
                "并用 `engine-written`、`kind-*`、`negative-physical-nested` 做正负回归。",
    },
    {
        "id": 2,
        "title": "Delta schema JSON 与 Parquet schema 双向映射，保留 nullable、字段顺序、字段名和嵌套路径",
        "status": "未完成",
        "evidence": [
            "`src/schema.mbt:36-73`：`parse_type_node` 对 struct/array/map 只返回 `NestedType(kind)`，"
            "丢弃 `fields`/`elementType`/`keyType`/`valueType`；`DeltaField`（`src/types.mbt:23-27`）没有子字段；",
            "`raw/engine-written__inspect.stdout.txt` 仅打印 `profile: struct`、`tags: array`、`attrs: map`，"
            "无子字段/嵌套路径；",
            "fixture `engine-written/_delta_log/00000000000000000000.json` 的 `metaData.schemaString` 保留完整嵌套 JSON，"
            "但解析后信息丢失；写端 `is_writable_type`（`src/schema.mbt:135-145`）排除 nested，无 Parquet schema 生成映射。",
        ],
        "work": "把 Delta 类型建模为可递归结构（struct fields / array element / map key-value），并实现 Delta↔Arrow/Parquet "
                "schema 双向转换（nullable、字段顺序、嵌套路径、map valueContainsNull、list containsNull）。",
    },
    {
        "id": 3,
        "title": "add.stats 对嵌套字段的缺失、null 和 min/max 不能被当作 flat 值误用；无法安全剪枝时必须放弃剪枝",
        "status": "未完成",
        "evidence": [
            "`fixture-manifest.json` 的 `engine-written.adds[0].stats_json` 含嵌套 `minValues.profile`、"
            "`nullCount.profile.age/name`，delta-rs 对 tags/attrs 完全省略统计；",
            "`src/snapshot.mbt:59-70` 只读取 `numRecords`；`src/scan.mbt:54-94` 的 min/max 只接受标量（`json_as_double`），"
            "遇到嵌套对象返回 None → 不剪枝；",
            "`raw/engine-written__scan_where_profile_1.stdout.txt` 显示 `skipped stats: 0`、选中 1 个文件（未误用为浮点）；"
            "但也没有任何嵌套字段的语义化剪枝能力；`nullCount` 被整体忽略。",
        ],
        "work": "定义嵌套 stats 的读取模型（按字段路径解析 min/max/nullCount），实现“无法安全剪枝就放弃”的显式判定，"
                "并补 stats 缺失/部分缺失/嵌套 null 的测试；不能把 struct 对象当标量比较。",
    },
    {
        "id": 4,
        "title": "checkpoint 中的嵌套 action schema 可解析，能从 checkpoint 加载快照，并与 JSON 回放快照逐字段一致",
        "status": "未完成",
        "evidence": [
            "`checkpoint-nested.json`：delta-rs 生成的 `00000000000000000000.checkpoint.parquet` 中，"
            "`metaData.schemaString` 内含完整 nested schema，`add.stats` 字符串内含嵌套 `minValues`/`nullCount`，"
            "checkpoint 本身是 struct<add, remove, metaData, protocol, txn, ...> 的嵌套 action 行式快照；",
            "`src/log.mbt:99-129`：只解析 `_last_checkpoint` 的 version/size/parts 并检测 `.checkpoint.parquet` 是否存在，"
            "不解析 checkpoint Parquet；",
            "`oracle-delta-rs.json` / `comparison.json` 的 `checkpoint_only_cross_check`：delta-rs 能从 `checkpoint-only-nested/`"
            "（已删除全部 JSON 日志）读回与主表逐字段一致的 5 行，证明 checkpoint 本身完整可用；"
            "而本项目同一张表直接 `not found`（`raw/checkpoint-only-nested__inspect.stdout.txt`）。",
        ],
        "work": "实现 checkpoint Parquet 读取（nested action struct schema）、按版本合并 checkpoint + 后续 JSON，"
                "并对 nested 表做 checkpoint 快照与 JSON 回放逐字段比对。",
    },
    {
        "id": 5,
        "title": "外部引擎写入和读取对拍：Spark/Databricks、delta-rs 或 DuckDB 至少两种，分别覆盖 flat 与 nested 表",
        "status": "未完成",
        "evidence": [
            "nested 侧外部引擎已完成两种：delta-rs 1.6.6（写 + 读回）与 DuckDB 1.5.6 + delta 扩展"
            f"（版本见 `oracle-duckdb.json`），两种引擎对 `engine-written` 的 5 行逐字段编码一致"
            "（见 `oracle-delta-rs.json` / `oracle-duckdb.json` 与 `comparison.json` 的 `oracle_cross_check`）；",
            "但“对拍”的本项目一侧缺失：本项目读不出任何一行嵌套数据（只返回 Unsupported）；",
            "flat 对拍属于任务 D2（`artifacts/interop-flat/`），不在本目录结论内。",
        ],
        "work": "在本项目实现 nested 读取后，加入 delta-rs + DuckDB 双向对拍（写后读回、读后逐字段 diff）；"
                "flat 部分沿用 D2。",
    },
    {
        "id": 6,
        "title": "对 nested struct、array/list、map 各提供至少一个正向 fixture、一个 null/empty 边界用例、一个外部引擎结果对照",
        "status": "已完成",
        "evidence": [
            "`engine-written/` 同时包含 struct/array/map，另有 `kind-struct-only/`、`kind-array-only/`、`kind-map-only/` "
            "三个单一 kind 的合法 nested fixture（均为 delta-rs 写出的真实嵌套 Delta 表）；",
            "`fixture-manifest.json` 的 `boundary_coverage` 证明覆盖 profile=NULL、struct 子字段全 null/age null、"
            "tags=[]/NULL/含 null 元素、attrs={}/NULL；",
            "外部引擎对照：delta-rs 与 DuckDB 均成功读回并给出逐字段编码（oracle 文件），且两引擎结果一致；",
            "注意：本门槛是“fixture/证据”门槛，已完成仅表示证据已交付；它不表示本项目可读 nested——"
            "本项目读取仍被门槛 1/2/7 阻塞（见负向对照）。",
        ],
        "work": "fixture/边界/外部引擎对照已交付，本条无剩余证据工作；待本项目实现 nested 读取后，"
                "把本目录从“拒绝对照”升级为“项目↔外部引擎正向断言”（属于门槛 5/7 的验收扩展）。",
    },
    {
        "id": 7,
        "title": "写入端生成的 nested Parquet 必须能被外部引擎读回；外部引擎生成的 nested Delta 必须能被本项目读回（只做“识别后报错”不算完成）",
        "status": "未完成",
        "evidence": [
            "读方向：外部引擎生成的 `engine-written` 被本项目 `read` 拒绝（`raw/engine-written__read.stdout.txt`）；",
            "写方向：`src/schema.mbt:135-145` 的 `is_writable_type` 排除 nested；`src/write.mbt:45-58` 的 "
            "`parquet_type_of` 只映射 int/long/string/binary/boolean/double，append 无法写 nested；",
            "`write-attempt-nested.json`（补充观察）：`delta create` 甚至不校验 schema 可写性，会生成一张"
            "metadata 声称 nested、实际无法 append 且自身无法 read 的空表——这也不等于“写入支持”。",
        ],
        "work": "实现 nested 写入（Parquet writer 的 struct/list/map 编码、Delta schema 生成、add.stats），"
                "写出后交给 delta-rs/DuckDB 读回逐字段对拍；同时让 `create_table` 对不可写 schema 明确拒绝。",
    },
]


# --------------------------------------------------------------------------------------
# markdown 渲染
# --------------------------------------------------------------------------------------
def md_table(headers, rows) -> str:
    def cell(text):
        return str(text).replace("|", "\\|").replace("\n", "<br>")

    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(cell(c) for c in row) + " |")
    return "\n".join(lines)


def build_readme(ctx: dict) -> str:
    env = ctx["environment"]
    manifests = ctx["manifest"]["fixtures"]
    main_desc = manifests[MAIN]
    oracle_rs = ctx["oracles"]["delta_rs"][MAIN]
    oracle_ddb = ctx["oracles"]["duckdb"][MAIN]
    moon = ctx["moondelta"]
    checks = ctx["checks"]
    main_checks = checks[MAIN]
    gate_counts = ctx["gate_counts"]
    err = main_checks["error_text"]
    scan = ctx["scan"]
    cp = ctx["checkpoint"]
    neg = ctx["negative"]

    def moon_block(name, subs):
        parts = []
        for sub in subs:
            run = moon[name][f"{sub}_run"] if f"{sub}_run" in moon[name] else moon[name][sub]
            parts.append(f"- `{run['command']}` → exit={run['exit_code']}")
        return "\n".join(parts)

    fixture_rows = []
    for name in [MAIN, *KIND_TABLES, CP_NESTED, CP_ONLY, NEG, WRITE_ATTEMPT]:
        desc = manifests[name]
        proto = desc.get("protocol") or {}
        version = desc.get("version")
        fixture_rows.append(
            [
                f"`{name}/`",
                desc.get("writer", "-"),
                f"reader={proto.get('minReaderVersion', '-')} writer={proto.get('minWriterVersion', '-')}",
                "-" if version is None else version,
                len(desc.get("data_files", [])),
            ]
        )

    hash_rows = []
    for item in ctx["manifest"]["hashes"]:
        hash_rows.append(
            [f"`{item['file']}`", item["bytes"], f"`{item['sha256']}`"]
        )

    main_row_rows = []
    for display in oracle_rs["rows_display"]:
        main_row_rows.append(
            [display["id"], display["profile"], display["tags"], display["attrs"]]
        )

    checklist_rows = [
        [item["expectation"], item["result"], item["evidence"]]
        for item in ctx["case_b_checklist"]
    ]

    gate_sections = []
    for gate in GATES:
        evidence = "\n".join(f"  - {e}" for e in gate["evidence"])
        gate_sections.append(
            f"### 门槛 {gate['id']}：{gate['title']}\n\n"
            f"**当前状态**：{gate['status']}\n\n"
            f"**证据**：\n{evidence}\n\n"
            f"**完成所需工作**：{gate['work']}\n"
        )

    kind_error_lines = []
    for name, expect in [
        ("kind-struct-only", "struct"),
        ("kind-array-only", "array"),
        ("kind-map-only", "map"),
    ]:
        c = checks[name]
        kind_error_lines.append(
            f"- `{name}`（{expect}）：`{c['error_text']}`（exit={c['cli_exit_code']}）"
        )

    raw_files = sorted(p.name for p in RAW.glob("*"))
    oracle_cross = ctx["oracle_cross_check"]
    ddb_status = (
        "成功" if oracle_ddb["status"] == "ok" else f"失败（{oracle_ddb['status']}）"
    )
    ddb_ext = oracle_ddb.get("delta_extension") or {}

    lines = []
    lines.append("# D3：nested 能力缺口对照（struct / array / map）")
    lines.append("")
    lines.append("> 本目录是**能力缺口对照**，不是互操作通过证明。")
    lines.append("> **外部引擎成功**：delta-rs 与 DuckDB 都能读取本目录生成的 nested Delta 表；")
    lines.append("> **本项目当前拒绝**：`moondelta-core 0.1.0` 在 schema 校验阶段返回结构化 `Unsupported`，没有读出任何一行嵌套数据。")
    lines.append("> 任何把本目录的负向用例描述成“nested 互操作通过”的写法都是错误的。")
    lines.append("")
    lines.append("## 0. 结论摘要")
    lines.append("")
    lines.append(
        f"- 主 fixture `{MAIN}/`：schema 为 `id: long, profile: struct<name:string, age:int>, "
        "tags: array<string>, attrs: map<string,string>`，共 "
        + str(oracle_rs["row_count"])
        + " 行（含 `profile=NULL`、struct 子字段 null、`tags=[]`、`tags` 含 null 元素、"
        "`attrs={}`、`tags/attrs=NULL`）。"
    )
    lines.append(
        f"- protocol：`reader v1 / writer v2`，与文档第 4 节案例 B 的目标配置一致；delta-rs 未启用 reader/writer features。"
    )
    lines.append(
        f"- **外部引擎（成功）**：delta-rs {oracle_rs['engine_version']} 读回 {oracle_rs['row_count']} 行；"
        f"DuckDB {oracle_ddb['engine_version']} + delta 扩展 {ddb_ext.get('extension_version', '?')} {ddb_status}；"
        f"两种引擎逐字段编码一致：`{oracle_cross['rows_equal']}`（见 `comparison.json` 的 `oracle_cross_check`）。"
    )
    lines.append(
        f"- **本项目（拒绝）**：`delta inspect` 能列出 `profile: struct` / `tags: array` / `attrs: map`；"
        f"`delta read` 返回 `unsupported: nested column 'profile' (type=struct) is not supported by the parquet reader; "
        "only flat schemas are readable`，且在打开 Parquet 之前发生。"
    )
    lines.append(
        f"- **错误策略问题**：`delta read` 失败时 CLI 退出码仍为 `{main_checks['cli_exit_code']}`（应为非 0），"
        "错误写 stdout 而非 stderr。"
    )
    lines.append(
        f"- **七项门槛统计**：已完成 {gate_counts['done']} 项 / 部分完成 {gate_counts['partial']} 项 / "
        f"未完成 {gate_counts['missing']} 项。"
    )
    lines.append("")
    lines.append("## 1. 方法与判定口径")
    lines.append("")
    lines.append("1. 用 delta-rs `write_deltalake` 按 reader v1 / writer v2、JSON log + Parquet、本地文件系统写出 nested fixture；")
    lines.append("2. 主 oracle 用 delta-rs `to_pyarrow_table()` 读回，导出完整 schema（含递归类型树）与逐字段值；")
    lines.append("   第二 oracle 用 DuckDB `INSTALL/LOAD delta` + `delta_scan` 读同一张表；")
    lines.append("3. 用 `delta.exe inspect/history/read` 对照本项目，保存 stdout/stderr/退出码原文；")
    lines.append("4. 额外构造“Delta schema 是 flat、Parquet 物理层是 LIST”的负向表，以及 checkpoint / checkpoint-only 变体；")
    lines.append("5. 逐条核对文档第 3 节七项门槛与第 4 节案例 B 的预期。")
    lines.append("")
    lines.append("判定口径：")
    lines.append("")
    lines.append("- **外部引擎成功** 只证明 fixture 与 oracle 成立，不证明本项目兼容；")
    lines.append("- **本项目拒绝** 只要不是“返回 0 行/部分错误数据/静默忽略”，就属于“安全失败”；")
    lines.append("  但按文档要求，只做“识别后报错”**不算完成**第 3 节门槛 7，也不得写成互操作通过；")
    lines.append("- CSV 只用于展示，嵌套值用 `{}`/`[]`/`NULL` 书写，不能替代 JSON 里的逐字段断言。")
    lines.append("")
    lines.append("## 2. 环境与运行")
    lines.append("")
    lines.append("```text")
    lines.append(f"OS            : {env['os']}")
    lines.append(f"Python        : {env['python']}")
    lines.append(f"deltalake     : {env['deltalake']}")
    lines.append(f"pyarrow       : {env['pyarrow']}")
    lines.append(f"duckdb        : {env['duckdb']} (delta extension: {ddb_ext.get('extension_version', '?')})")
    lines.append(f"pandas        : {env['pandas']}")
    lines.append(f"moondelta CLI : {env['moondelta_cli']} ({env['moondelta_version']})")
    lines.append("```")
    lines.append("")
    lines.append("一键复现（在仓库根目录执行）：")
    lines.append("")
    lines.append("```powershell")
    lines.append(r".tools\pyenv\Scripts\python.exe artifacts\interop-nested\run_nested.py")
    lines.append("```")
    lines.append("")
    lines.append("脚本会重建本目录所有 fixture、oracle、`raw/` 输出，并重新生成本 README、`comparison.md`、"
                 "`fixture-manifest.json`。单独运行某条 CLI 对照的原始命令见 `raw/*.cmd.txt`，例如：")
    lines.append("")
    lines.append("```powershell")
    lines.append(r"_build\native\debug\build\cmd\delta\delta.exe inspect artifacts\interop-nested\engine-written")
    lines.append(r"_build\native\debug\build\cmd\delta\delta.exe history artifacts\interop-nested\engine-written")
    lines.append(r"_build\native\debug\build\cmd\delta\delta.exe read    artifacts\interop-nested\engine-written")
    lines.append(r"_build\native\debug\build\cmd\delta\delta.exe scan    artifacts\interop-nested\engine-written --where id>=99")
    lines.append(r"_build\native\debug\build\cmd\delta\delta.exe scan    artifacts\interop-nested\engine-written --where profile>=1")
    lines.append("```")
    lines.append("")
    lines.append("DuckDB delta 扩展首次运行可能需要网络执行 `INSTALL delta`；本机已安装（版本见上），脚本优先 `LOAD delta`，"
                 "失败才回退 `INSTALL`。若扩展不可用，脚本会记录原始错误并用 `read_parquet` 兜底，"
                 "同时明确标注“非 Delta 级”，不会伪装成 `delta_scan` 成功。")
    lines.append("")
    lines.append("> 可重放性说明：fixture 是现场写出的，`add.path` 含 UUID、commitInfo 含墙钟时间，"
                 "因此每次重跑后数据文件/日志的 SHA-256 会变化；表中的哈希是**本次运行产物**的哈希，"
                 "重跑后 manifest 与 README 自动更新。schema、protocol、行值、错误文本是稳定可复现的。")
    lines.append("")
    lines.append("## 3. Fixture 清单与协议配置")
    lines.append("")
    lines.append(md_table(
        ["fixture", "写入方", "protocol", "version", "数据文件数"], fixture_rows
    ))
    lines.append("")
    lines.append("### 3.1 主 fixture `engine-written/`")
    lines.append("")
    lines.append("`metaData.schemaString` 原文（来自 `_delta_log/00000000000000000000.json`）：")
    lines.append("")
    lines.append("```json")
    lines.append(main_desc["schemaString"])
    lines.append("```")
    lines.append("")
    lines.append("protocol（同一日志）：")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(main_desc["protocol"], ensure_ascii=False))
    lines.append("```")
    lines.append("")
    lines.append(f"- 数据文件：`{main_desc['adds'][0]['path']}`（{main_desc['adds'][0]['size']} B）")
    lines.append(f"- `add.stats` 原文：`{main_desc['adds'][0]['stats_raw']}`")
    lines.append("")
    lines.append("行值（来自 delta-rs oracle；CSV 同内容，仅展示）：")
    lines.append("")
    lines.append(md_table(["id", "profile", "tags", "attrs"], main_row_rows))
    lines.append("")
    lines.append("边界覆盖（由 oracle 逐字段编码判定，脚本启动时断言，不满足会直接失败）：")
    lines.append("")
    cov = ctx["manifest"]["boundary_coverage"]
    cov_rows = [
        ["`profile = NULL`", cov["profile_null"]],
        ["`profile` 非 null、name/age 均 null", cov["profile_all_children_null"]],
        ["`profile` 非 null、name 有值 age null", cov["profile_name_set_age_null"]],
        ["`tags = []`", cov["tags_empty_list"]],
        ["`tags = NULL`", cov["tags_null"]],
        ["`tags` 含 null 元素", cov["tags_null_element"]],
        ["`attrs = {}`", cov["attrs_empty_map"]],
        ["`attrs = NULL`", cov["attrs_null"]],
    ]
    lines.append(md_table(["边界", "行 id"], [[a, ", ".join(map(str, b))] for a, b in cov_rows]))
    lines.append("")
    lines.append("### 3.2 单 kind fixture 与 checkpoint / 负向 fixture")
    lines.append("")
    for name in KIND_TABLES:
        desc = manifests[name]
        lines.append(f"- `{name}/`：{desc['writer']}；schemaString=`{desc['schemaString']}`；"
                     f"{len(desc['adds'])} 个 add。")
    lines.append(f"- `{CP_NESTED}/`：`{MAIN}/` 的副本 + delta-rs `create_checkpoint()`；"
                 f"checkpoint 文件 `{', '.join(cp['checkpoint_files'])}`，`_last_checkpoint`={cp['last_checkpoint']}。")
    lines.append(f"- `{CP_ONLY}/`：只保留 checkpoint 与 `_last_checkpoint`、删除全部 JSON 日志，用于验证 checkpoint-only 路径。")
    lines.append(f"- `{NEG}/`：手写 flat schema 的 JSON 日志，指向 `{neg['fixture']['source_parquet']}`"
                 f"（SHA-256 `{neg['fixture']['source_parquet_sha256']}`，Apache-2.0，来自 `apache/parquet-testing`）。")
    lines.append(f"- `{WRITE_ATTEMPT}/`：用本项目 `delta create` 写入 nested schemaString 的空表（补充观察，见门槛 7）。")
    lines.append("")
    lines.append("## 4. Oracle：外部引擎结果")
    lines.append("")
    lines.append("### 4.1 主 oracle：delta-rs")
    lines.append("")
    lines.append(f"- 引擎：delta-rs (Python `deltalake`) {oracle_rs['engine_version']}，`DeltaTable.to_pyarrow_table()`；")
    lines.append(f"- 表：`{oracle_rs['table']}`，version={oracle_rs['version']}，行数={oracle_rs['row_count']}；")
    lines.append(f"- protocol：reader={oracle_rs['protocol']['minReaderVersion']} / writer={oracle_rs['protocol']['minWriterVersion']}，"
                 f"readerFeatures={oracle_rs['protocol']['readerFeatures']}，writerFeatures={oracle_rs['protocol']['writerFeatures']}；")
    lines.append(f"- `delta_rs` 导出的 schema JSON 与日志 `metaData.schemaString` 等价："
                 f"`{oracle_rs['schema_json_equals_log_schemaString']}`；")
    lines.append("- 全量 schema 树、逐行逐字段编码（null 单独编码为 `{\"kind\":\"null\"}`，"
                 "空 list 为 `{\"kind\":\"list\",\"items\":[]}`，空 map 为 `{\"kind\":\"map\",\"entries\":[]}`）"
                 "见 `oracle-delta-rs.json`；展示版 CSV 见 `oracle-delta-rs.csv`。")
    lines.append("")
    lines.append("### 4.2 第二 oracle：DuckDB")
    lines.append("")
    lines.append(f"- DuckDB {oracle_ddb['engine_version']}，delta 扩展 {ddb_ext.get('extension_version', '?')}，"
                 f"`delta_scan` 状态：**{ddb_status}**；")
    if oracle_ddb["status"] == "ok":
        lines.append(f"- 行数={oracle_ddb['row_count']}；全量 schema 树与逐字段值见 `oracle-duckdb.json`；")
        lines.append(f"- 与 delta-rs 对拍：row_count_equal=`{oracle_cross['row_count_equal']}`，"
                     f"id_sets_equal=`{oracle_cross['id_sets_equal']}`，rows_equal=`{oracle_cross['rows_equal']}`，"
                     f"differing_ids={oracle_cross['differing_ids']}。")
    else:
        lines.append(f"- 原始错误：`{oracle_ddb['error']}`")
        if oracle_ddb.get("fallback_note"):
            lines.append(f"- 兜底说明：{oracle_ddb['fallback_note']}")
    lines.append("")
    lines.append("## 5. moondelta 对照（原始输出与退出码）")
    lines.append("")
    lines.append("每个命令的 stdout / stderr / 退出码分别保存在独立文件（`raw/<table>__<subcmd>.stdout.txt` 等）。"
                 "命令本身也写进 `raw/*.cmd.txt`。")
    lines.append("")
    lines.append("### 5.1 主 fixture")
    lines.append("")
    lines.append("`inspect`（exit={}）：".format(moon[MAIN]["inspect"]["exit_code"]))
    lines.append("")
    lines.append("```text")
    lines.append(moon[MAIN]["inspect"]["stdout"].rstrip())
    lines.append("```")
    lines.append("")
    lines.append("`read`（exit={}，stdout；stderr 为空：{}）：".format(
        moon[MAIN]["read"]["exit_code"], moon[MAIN]["read"]["stderr"] == ""))
    lines.append("")
    lines.append("```text")
    lines.append(moon[MAIN]["read"]["stdout"].rstrip())
    lines.append("```")
    lines.append("")
    lines.append(f"- 结构化错误原文：`{err}`")
    lines.append(f"- 错误包含的字段路径：`{main_checks['read_error_field_path']}`；nested kind：`{main_checks['read_error_nested_kind']}`；"
                 f"是否含深层路径：`{main_checks['read_error_has_deep_nested_path']}`")
    lines.append(f"- 是否返回假成功/0 行：fake_success=`{main_checks['fake_success']}`，returns_zero_rows=`{main_checks['returns_zero_rows']}`，"
                 f"打印数据行数=`{main_checks['data_rows_printed']}`")
    lines.append(f"- CLI 失败退出码非 0：`{main_checks['cli_exit_nonzero_on_failure']}`（实际 {main_checks['cli_exit_code']}）")
    lines.append("")
    lines.append("`history`（exit={}）：".format(moon[MAIN]["history"]["exit_code"]))
    lines.append("")
    lines.append("```text")
    lines.append(moon[MAIN]["history"]["stdout"].rstrip())
    lines.append("```")
    lines.append("")
    lines.append("`scan` 补充观察（只做文件级规划，不读数据）：")
    lines.append("")
    lines.append(md_table(
        ["命令", "selected files", "skipped stats", "matched rows", "结论"],
        [
            ["`scan`（无过滤）", scan["scan"]["selected_files"], scan["scan"]["skipped_stats"],
             scan["scan"]["matched_rows"], "计划层可用，不代表数据可读"],
            ["`scan --where id>=99`", scan["scan_where_id_ge_99"]["selected_files"],
             scan["scan_where_id_ge_99"]["skipped_stats"], scan["scan_where_id_ge_99"]["matched_rows"],
             "flat 列 min/max 剪枝生效（全部剪掉）"],
            ["`scan --where profile>=1`", scan["scan_where_profile_ge_1"]["selected_files"],
             scan["scan_where_profile_ge_1"]["skipped_stats"], scan["scan_where_profile_ge_1"]["matched_rows"],
             "嵌套 stats 是对象，`json_as_double` 返回 None → 不剪枝（安全，但没有任何嵌套剪枝能力）"],
        ],
    ))
    lines.append("")
    lines.append("注意：`scan` 只输出文件级计划，不读取任何行；不能把 `scan` 成功当作 nested 读取成功。")
    lines.append("")
    lines.append("### 5.2 单 kind fixture：逐类错误")
    lines.append("")
    lines.append("\n".join(kind_error_lines))
    lines.append("")
    lines.append("这证明三类 nested kind 都被识别并拒绝；因为主 fixture 的拒绝是 fail-fast（第一个嵌套列即返回），"
                 "单 kind fixture 用于分别取得 struct / array / map 的原始错误文本。")
    lines.append("")
    lines.append("### 5.3 checkpoint 变体")
    lines.append("")
    cp_rs = ctx["oracles"]["delta_rs"].get(CP_NESTED) or {}
    cp_only_rs = ctx["oracles"]["delta_rs"].get(CP_ONLY) or {}
    cp_only_ddb = ctx["oracles"]["duckdb"].get(CP_ONLY) or {}
    cp_cross = ctx.get("checkpoint_only_cross_check")
    lines.append(f"- `{CP_NESTED}/`：`inspect` exit={moon[CP_NESTED]['inspect']['exit_code']}，"
                 f"能识别 checkpoint（`last checkpoint`/note），但 `read` 仍因 nested schema 拒绝"
                 f"（`{checks[CP_NESTED]['error_text']}`）。"
                 f"delta-rs 对该表读回 {cp_rs.get('row_count')} 行（status={cp_rs.get('status', 'n/a')}）。")
    lines.append(f"- `{CP_ONLY}/`：**外部引擎可从 checkpoint 重建快照**：delta-rs status={cp_only_rs.get('status')}、"
                 f"读回 {cp_only_rs.get('row_count')} 行；DuckDB delta_scan status={cp_only_ddb.get('status')}。")
    if cp_cross:
        lines.append(f"  - delta-rs 从 checkpoint-only 变体读回的行与主表逐字段一致：rows_equal=`{cp_cross['rows_equal']}`"
                     f"（见 `comparison.json` 的 `checkpoint_only_cross_check`）。")
    lines.append(f"  - 本项目却无法读取：`inspect` exit={moon[CP_ONLY]['inspect']['exit_code']}，"
                 f"原始输出 `{moon[CP_ONLY]['inspect']['stdout'].strip()}`；"
                 "根因是不解析 checkpoint Parquet，只把 checkpoint 当提示。")
    lines.append("")
    lines.append("### 5.4 负向用例：Delta schema 是 flat、Parquet 物理层是 LIST")
    lines.append("")
    lines.append(f"- `{NEG}/` 的 `delta inspect` 把列报告为 `int64_list: long`、`utf8_list: string`（schema 是 flat）；")
    lines.append(f"- `delta read` 原始错误：`{checks[NEG]['error_text']}`（exit={checks[NEG]['cli_exit_code']}）；")
    lines.append(f"- 错误来自 Parquet 读取层（`{checks[NEG]['read_error_parquet_file']}` → "
                 f"`{checks[NEG]['read_error_parquet_reason']}`），不是 schema 阶段；")
    if ctx["negative"]["delta_rs_attempt"]["ok"]:
        lines.append(f"- 外部引擎 delta-rs 对该负向表的读取结果：成功（{ctx['negative']['delta_rs_attempt']['row_count']} 行），"
                     "这是因为 delta-rs 按物理 Parquet schema 读取，而日志 schema 与数据不一致属于伪造表；"
                     "**该表不用于互操作结论**，只用于验证本项目“flat schema + 物理嵌套”的错误路径。")
    else:
        lines.append(f"- 外部引擎 delta-rs 对该负向表的读取同样失败：`{ctx['negative']['delta_rs_attempt']['error']}`；"
                     "**该表是伪造/不自洽的表，不用于互操作结论**。")
    neg_ddb_status = ctx["negative"]["duckdb_attempt"].get("status")
    neg_ddb_err = (ctx["negative"]["duckdb_attempt"].get("error") or "").splitlines()
    neg_ddb_err_first = neg_ddb_err[0] if neg_ddb_err else ""
    lines.append(f"- DuckDB 对该负向表的 `delta_scan` 状态：`{neg_ddb_status}`"
                 + (f"（首行错误：`{neg_ddb_err_first}`；完整错误见 `negative-physical-nested.json`）"
                    if neg_ddb_err_first else "")
                 + "；同样不作为互操作基线。")
    lines.append("")
    lines.append("## 6. 文档第 4 节案例 B 预期核对")
    lines.append("")
    lines.append(md_table(["文档预期", "结果", "证据"], checklist_rows))
    lines.append("")
    lines.append("结论：本项目**没有**返回 0 行、没有把 array 当 string、没有静默忽略 map、也没有读出部分错误数据；"
                 "拒绝是安全的。但“识别后报错”按文档第 3 节门槛 7 明确**不算完成**，且退出码/错误通道存在问题。")
    lines.append("")
    lines.append("## 7. 第 3 节七项门槛：缺口清单")
    lines.append("")
    lines.append(f"统计：**已完成 {gate_counts['done']} 项 / 部分完成 {gate_counts['partial']} 项 / "
                 f"未完成 {gate_counts['missing']} 项**。")
    lines.append("")
    done_ids = [str(g["id"]) for g in GATES if g["status"] == "已完成"]
    partial_ids = [str(g["id"]) for g in GATES if g["status"] == "部分完成"]
    missing_ids = [str(g["id"]) for g in GATES if g["status"] == "未完成"]
    lines.append(f"- 已完成（证据/对照门槛）：门槛 {', '.join(done_ids) if done_ids else '无'}；"
                 "注意门槛 6 是“fixture/边界/外部引擎对照”证据门槛，完成它**不代表**本项目能读 nested。")
    lines.append(f"- 部分完成：{('门槛 ' + ', '.join(partial_ids)) if partial_ids else '无'}；")
    lines.append(f"- 未完成（实现门槛）：门槛 {', '.join(missing_ids) if missing_ids else '无'}。")
    lines.append("")
    lines.append("\n".join(gate_sections))
    lines.append("## 8. 补充发现（不属于新增门槛，但影响错误策略/验收口径）")
    lines.append("")
    lines.append("1. **CLI 失败退出码为 0**：`src/cmd/delta/main.mbt:44-46` 的 `fail()` 只 `println(\"error: ...\")` 后返回，"
                 "`main` 没有失败退出路径；脚本对所有 `read` 失败的 exit code 都有原文记录。")
    lines.append("2. **错误写 stdout**：`fail()` 用 `println`，stderr 为空；脚本/CI 不能只靠 stderr 判失败。")
    lines.append("3. **`delta create` 接受 nested schema**：`src/write.mbt:165-185` 不校验 schema 可写性，"
                 "`write-attempt-nested/` 就是这样一张“metadata 声称 nested、实际无法 append、自己也读不了”的表；"
                 "建议 create 阶段就按 `is_writable_type` 拒绝。")
    lines.append("4. **`scan` 在 nested 表上会成功**：它是计划层 API（只读 add/metadata），不读数据、不校验 schema；"
                 "这本身不是错误，但验收时不能拿 `scan` 成功冒充读取成功。")
    lines.append("5. **inspect 丢弃子字段**：`inspect` 只打印 `profile: struct`，无法显示 `name/age`，"
                 "根因是 `NestedType(kind)` 不保存子结构（门槛 2）。")
    lines.append("")
    lines.append("## 9. 证据索引与完整性")
    lines.append("")
    lines.append("### 9.1 文件索引")
    lines.append("")
    lines.append("- `run_nested.py`：一键复现脚本（建 fixture → oracle → moondelta → 生成结论）；")
    lines.append("- `oracle-delta-rs.json`：主 oracle，完整 schema 树 + 逐行逐字段编码；")
    lines.append("- `oracle-delta-rs.csv`：主 oracle 展示版（仅展示，不替代 JSON 断言）；")
    lines.append("- `oracle-duckdb.json`：DuckDB 第二 oracle（含扩展版本、失败原文/兜底标注）；")
    lines.append("- `checkpoint-nested.json`：checkpoint 内部 action schema 与嵌套 stats 探针；")
    lines.append("- `negative-physical-nested.json`：负向 fixture 说明与外部引擎尝试记录；")
    lines.append("- `write-attempt-nested.json`：本项目 `delta create` 写入 nested schema 的补充观察；")
    lines.append("- `fixture-manifest.json`：fixture 描述、边界覆盖、全部文件 SHA-256；")
    lines.append("- `comparison.json` / `comparison.md`：机器可读/人读对比结论；")
    lines.append(f"- `raw/`：{len(raw_files)} 个原始文件（每条命令的 stdout、stderr、exit、cmd）；")
    lines.append("- README.md：本报告。")
    lines.append("")
    lines.append("### 9.2 SHA-256（本次运行产物）")
    lines.append("")
    lines.append(md_table(["文件", "字节", "SHA-256"], hash_rows))
    lines.append("")
    lines.append("## 10. 结论")
    lines.append("")
    lines.append("1. **fixture 成立**：同一 reader v1 / writer v2 配置下，delta-rs 写出了包含 struct/array/map 的 nested Delta 表，"
                 "覆盖文档案例 B 要求的 null/empty 边界；两种外部引擎都能读取，且逐字段一致。")
    lines.append("2. **外部引擎成功 ≠ 本项目兼容**：`moondelta-core 0.1.0` 对同一张表 `read` 返回结构化 `Unsupported`，"
                 "没有读出任何嵌套数据；按文档第 3 节门槛 7，这**不是**互操作通过。")
    lines.append("3. **拒绝是安全的，但不完整**：没有 0 行假成功、没有类型混淆、没有静默忽略；"
                 "但 inspect 丢子字段、错误路径只有顶层列名、CLI 失败退出码为 0、错误写 stdout。")
    lines.append(f"4. **门槛状态**：第 3 节七项门槛当前已完成 {gate_counts['done']} 项"
                 f"（{', '.join(str(g['id']) for g in GATES if g['status'] == '已完成') or '无'}）、"
                 f"部分完成 {gate_counts['partial']} 项"
                 f"（{', '.join(str(g['id']) for g in GATES if g['status'] == '部分完成') or '无'}）、"
                 f"未完成 {gate_counts['missing']} 项"
                 f"（{', '.join(str(g['id']) for g in GATES if g['status'] == '未完成') or '无'}）；"
                 "其中已完成的门槛 6 只是 fixture/证据门槛，**不改变**“本项目拒绝 nested”的结论；"
                 "在完成嵌套读写与 checkpoint 恢复前，只能使用“flat 子集兼容、nested 明确拒绝”的表述。")
    lines.append("")
    return "\n".join(lines) + "\n"


def build_comparison_md(ctx: dict) -> str:
    verdicts = {
        MAIN: "能力缺口：外部成功、本项目拒绝",
        "kind-struct-only": "能力缺口：外部成功、本项目拒绝",
        "kind-array-only": "能力缺口：外部成功、本项目拒绝",
        "kind-map-only": "能力缺口：外部成功、本项目拒绝",
        CP_NESTED: "同主表；checkpoint 存在不改变 read 拒绝",
        CP_ONLY: "checkpoint-only：外部引擎可读、本项目 not found",
        NEG: "负向用例：Parquet 层拒绝（不计通过）",
    }
    rows = []
    for name in [MAIN, *KIND_TABLES, CP_NESTED, CP_ONLY, NEG]:
        desc = ctx["manifest"]["fixtures"][name]
        rs = ctx["oracles"]["delta_rs"].get(name)
        ddb = ctx["oracles"]["duckdb"].get(name)
        c = ctx["checks"][name]
        rs_cell = (
            "ok"
            if rs and rs.get("status", "ok") == "ok" and rs.get("row_count") is not None
            else ("error" if rs and rs.get("status") == "error" else "n/a")
        )
        ddb_cell = (
            "ok"
            if ddb and ddb.get("status") == "ok"
            else (ddb.get("status", "n/a") if ddb else "n/a")
        )
        rows.append(
            [
                f"`{name}/`",
                rs_cell,
                ddb_cell,
                " / ".join(c["inspect_schema_display"]) or "-",
                c["error_text"] or "-",
                c["cli_exit_code"],
                verdicts[name],
            ]
        )
    gate_rows = [[g["id"], g["title"], g["status"]] for g in GATES]
    lines = [
        "# nested 对照速览（自动生成）",
        "",
        "> 自动生成，完整报告见 `README.md`；机器可读结论见 `comparison.json`。",
        "",
        "## 表级对照",
        "",
        md_table(["表", "delta-rs", "DuckDB", "moondelta inspect schema", "moondelta read 错误", "exit", "判定"], rows),
        "",
        "## 七项门槛",
        "",
        md_table(["#", "门槛", "状态"], gate_rows),
        "",
        f"统计：已完成 {ctx['gate_counts']['done']} / 部分完成 {ctx['gate_counts']['partial']} / "
        f"未完成 {ctx['gate_counts']['missing']}。",
        "",
        "## 结论",
        "",
        "- 外部引擎（delta-rs、DuckDB）读取 nested fixture 成功，oracle 逐字段一致；",
        "- 本项目 `read` 在 schema 校验阶段结构化拒绝，未读出任何嵌套行；",
        "- 因此这是**能力缺口对照**，不是互操作通过；不要引用为 nested 兼容证据。",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------
def main() -> int:
    prepare()
    print(f"[1/9] 环境: Python {sys.version.split()[0]}, deltalake {deltalake.__version__}, "
          f"pyarrow {pa.__version__}, duckdb {duckdb.__version__}")

    # ---- fixture ----
    write_fixture(MAIN, MAIN_SCHEMA, MAIN_ROWS)
    for name, spec in KIND_SPECS.items():
        write_fixture(name, spec["schema"], spec["rows"])
    make_checkpoint_fixtures()
    negative = make_negative_fixture()
    write_attempt = make_write_attempt_fixture()

    manifests = {}
    writers = {
        CP_NESTED: "delta-rs write_deltalake + delta-rs create_checkpoint()",
        CP_ONLY: "checkpoint-nested 的副本，删除了全部 JSON 日志（checkpoint-only 变体）",
        NEG: "手写 JSON 日志 + 复制 fixtures/parquet/list_columns.parquet",
        WRITE_ATTEMPT: "moondelta-core `delta create`（nested schemaString，无数据）",
    }
    for name in [MAIN, *KIND_TABLES, CP_NESTED, CP_ONLY, NEG, WRITE_ATTEMPT]:
        manifests[name] = fixture_descriptor(name, OUT / name, writers.get(name))
    manifests[NEG]["purpose"] = negative["purpose"]
    manifests[WRITE_ATTEMPT]["purpose"] = write_attempt["purpose"]
    manifests[NEG]["source_parquet"] = negative["source_parquet"]
    manifests[NEG]["source_parquet_sha256"] = negative["source_parquet_sha256"]
    print("[2/9] fixture 已写出: " + ", ".join(manifests))

    # ---- oracle ----
    oracle_tables = [MAIN, *KIND_TABLES, CP_NESTED, CP_ONLY]
    oracles_rs = {}
    for name in oracle_tables:
        try:
            oracles_rs[name] = oracle_delta_rs(name, manifests[name])
            oracles_rs[name]["status"] = "ok"
        except Exception as exc:
            oracles_rs[name] = {
                "oracle": "delta-rs (Python deltalake)",
                "engine_version": deltalake.__version__,
                "table": manifests[name]["path"],
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "row_count": None,
                "rows": [],
                "rows_display": [],
            }
    con, ddb_info = init_duckdb()
    oracles_ddb = {name: oracle_duckdb(name, manifests[name], con, ddb_info) for name in oracle_tables}
    main_oracle = oracles_rs[MAIN]
    if main_oracle["status"] != "ok":
        raise SystemExit(f"主 oracle delta-rs 读取失败: {main_oracle.get('error')}")

    boundary = boundary_coverage(main_oracle["rows"])
    assert_coverage(boundary)

    oracle_cross = compare_oracles(
        oracles_rs[MAIN]["rows"], oracles_ddb[MAIN]["rows"]
    ) if oracles_ddb[MAIN]["status"] == "ok" else {
        "row_count_equal": False,
        "id_sets_equal": False,
        "rows_equal": False,
        "differing_ids": [],
        "note": f"duckdb 状态 {oracles_ddb[MAIN]['status']}",
    }
    checkpoint_only_cross = None
    if (
        oracles_rs.get(CP_ONLY, {}).get("status") == "ok"
        and oracles_rs[MAIN]["rows"]
        and oracles_rs[CP_ONLY]["rows"]
    ):
        checkpoint_only_cross = compare_oracles(
            oracles_rs[MAIN]["rows"], oracles_rs[CP_ONLY]["rows"]
        )

    # 期望行值自检：fixture 输入 → 展示值，必须与 delta-rs 读回一致
    expected_main_display = expected_display(MAIN_ROWS, MAIN_SCHEMA, ["id", "profile", "tags", "attrs"])
    readback_ok = expected_main_display == main_oracle["rows_display"]
    if not readback_ok:
        raise SystemExit(
            "fixture 读回值与输入不一致（oracle 自检失败）: "
            f"expected={expected_main_display} got={main_oracle['rows_display']}"
        )

    # checkpoint 探针
    checkpoint = checkpoint_probe()

    # 负向表的外部引擎尝试（如实记录，不用于互操作结论）
    neg_path = OUT / NEG
    try:
        neg_table = DeltaTable(str(neg_path)).to_pyarrow_table()
        neg_rs = {
            "ok": True,
            "row_count": neg_table.num_rows,
            "schema": str(neg_table.schema),
            "rows": encode_rows(neg_table),
        }
    except Exception as exc:
        neg_rs = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    neg_ddb = oracle_duckdb(NEG, manifests[NEG], con, ddb_info)

    write_json(
        OUT / "oracle-delta-rs.json",
        {
            "primary_oracle": "delta-rs (Python deltalake)",
            "engine_version": deltalake.__version__,
            "note": "嵌套值编码：null -> {\"kind\":\"null\"}；空 list -> {\"kind\":\"list\",\"items\":[]}；"
                    "空 map -> {\"kind\":\"map\",\"entries\":[]}；struct -> {\"kind\":\"struct\",\"fields\":{...}}；"
                    "map 条目按 key 排序以消除无序差异。",
            "tables": {name: oracles_rs[name] for name in oracle_tables},
        },
    )
    # CSV 仅展示主表
    csv_path = OUT / "oracle-delta-rs.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["id", "profile", "tags", "attrs"])
        for row in main_oracle["rows_display"]:
            writer.writerow([row["id"], row["profile"], row["tags"], row["attrs"]])

    write_json(
        OUT / "oracle-duckdb.json",
        {
            "secondary_oracle": "DuckDB delta_scan",
            "engine_version": duckdb.__version__,
            "delta_extension": ddb_info,
            "note": "delta_scan 读取结果；若 status != ok，不得把 read_parquet 兜底当作 Delta 级读取。",
            "tables": {name: oracles_ddb[name] for name in oracle_tables},
            "negative_physical_nested_attempt": neg_ddb,
        },
    )
    write_json(OUT / "checkpoint-nested.json", checkpoint)
    write_json(
        OUT / "negative-physical-nested.json",
        {
            "fixture": negative,
            "delta_rs_attempt": neg_rs,
            "duckdb_attempt": neg_ddb,
            "caution": "该表的日志 schema 与 Parquet 物理 schema 不一致，属于人为构造的负向表；"
                       "外部引擎行为不作为互操作基线，只用于验证本项目的错误路径。",
        },
    )
    print(f"[3/9] oracle 完成: delta-rs rows={main_oracle['row_count']}, "
          f"duckdb={oracles_ddb[MAIN]['status']}, cross={oracle_cross['rows_equal']}")

    # ---- moondelta ----
    moon = {}
    for name in [MAIN, *KIND_TABLES, CP_NESTED, CP_ONLY, NEG, WRITE_ATTEMPT]:
        moon[name] = {
            "inspect": cli(name, "inspect"),
            "history": cli(name, "history"),
            "read": cli(name, "read"),
        }
    scan_runs = {
        "scan": cli(MAIN, "scan"),
        "scan_where_id_ge_99": cli(MAIN, "scan", ["--where", "id>=99"]),
        "scan_where_profile_ge_1": cli(MAIN, "scan", ["--where", "profile>=1"]),
    }
    scan_parsed = {key: parse_scan(run["stdout"]) for key, run in scan_runs.items()}
    version_run = subprocess.run([str(EXE), "version"], cwd=str(ROOT),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    moon_version = decode(version_run.stdout).strip()
    (RAW / "moondelta_version.stdout.txt").write_bytes(version_run.stdout)
    (RAW / "moondelta_version.exit.txt").write_text(
        str(version_run.returncode) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"[4/9] moondelta CLI 完成: {len(moon)} 张表 × 3 命令 + {len(scan_runs)} 个 scan")

    write_json(
        OUT / "write-attempt-nested.json",
        {
            "fixture": write_attempt,
            "inspect": {
                "command": moon[WRITE_ATTEMPT]["inspect"]["command"],
                "exit_code": moon[WRITE_ATTEMPT]["inspect"]["exit_code"],
                "stdout_file": moon[WRITE_ATTEMPT]["inspect"]["stdout_file"],
                "stdout": moon[WRITE_ATTEMPT]["inspect"]["stdout"],
            },
            "read": {
                "command": moon[WRITE_ATTEMPT]["read"]["command"],
                "exit_code": moon[WRITE_ATTEMPT]["read"]["exit_code"],
                "stdout_file": moon[WRITE_ATTEMPT]["read"]["stdout_file"],
                "error_text": parse_read(moon[WRITE_ATTEMPT]["read"]["stdout"])["error_text"],
            },
            "note": "`delta create` 不校验 schema 可写性，会产出 metadata 声称 nested、"
                    "但 append 无法写出、自身 read 也拒绝的空表。这不等于写入支持。",
        },
    )

    # ---- checks ----
    checks = {}
    for name in [MAIN, *KIND_TABLES, CP_NESTED, CP_ONLY, NEG, WRITE_ATTEMPT]:
        expect = None
        if name == "kind-struct-only":
            expect = ["struct"]
        elif name == "kind-array-only":
            expect = ["array"]
        elif name == "kind-map-only":
            expect = ["map"]
        checks[name] = build_checks(
            manifests[name],
            moon[name],
            oracles_rs.get(name, {}).get("row_count") is not None,
            oracles_ddb.get(name, {}).get("status") == "ok",
            expect,
        )
    case_b = build_case_b_checklist(checks[MAIN], moon[MAIN])
    gate_counts = {
        "done": sum(1 for g in GATES if g["status"] == "已完成"),
        "partial": sum(1 for g in GATES if g["status"] == "部分完成"),
        "missing": sum(1 for g in GATES if g["status"] == "未完成"),
    }

    # ---- manifest / hashes ----
    manifest = {
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "fixture_source": "本项目用 delta-rs 现场写出（除 negative-physical-nested 复制自 fixtures/parquet/list_columns.parquet）",
        "environment": {
            "os": platform.platform(),
            "python": sys.version.split()[0],
            "deltalake": deltalake.__version__,
            "pyarrow": pa.__version__,
            "duckdb": duckdb.__version__,
            "pandas": pkg_version("pandas"),
            "moondelta_cli": rel(EXE),
            "moondelta_version": moon_version,
            "duckdb_delta_extension": ddb_info,
        },
        "fixtures": manifests,
        "boundary_coverage": boundary,
        "hashes": manifest_hashes(),
    }
    write_json(OUT / "fixture-manifest.json", manifest)
    print("[5/9] fixture-manifest.json 完成（含 SHA-256）")

    # ---- comparison ----
    comparison = {
        "generated_at": manifest["generated_at"],
        "case": "docs/协议兼容性与互操作范围.md 第 4 节案例 B / 第 6 节任务 D3",
        "environment": manifest["environment"],
        "fixture_schema": {
            "table": MAIN,
            "schemaString": manifests[MAIN]["schemaString"],
            "protocol": manifests[MAIN]["protocol"],
            "row_count": main_oracle["row_count"],
            "boundary_coverage": boundary,
        },
        "oracles": {
            "delta_rs": {
                "ok": True,
                "engine_version": deltalake.__version__,
                "row_count": main_oracle["row_count"],
            },
            "duckdb": {
                "status": oracles_ddb[MAIN]["status"],
                "engine_version": duckdb.__version__,
                "delta_extension": ddb_info,
                "row_count": oracles_ddb[MAIN]["row_count"],
            },
            "oracle_cross_check": oracle_cross,
            "checkpoint_only_cross_check": checkpoint_only_cross,
            "tables": {
                name: {
                    "delta_rs_status": oracles_rs[name].get("status", "ok"),
                    "duckdb_status": oracles_ddb[name].get("status"),
                    "row_count": oracles_rs[name].get("row_count")
                    if oracles_rs[name].get("status") == "ok"
                    else oracles_ddb[name].get("row_count"),
                }
                for name in oracle_tables
            },
        },
        "moondelta": {
            name: {
                "inspect": {
                    "command": moon[name]["inspect"]["command"],
                    "exit_code": moon[name]["inspect"]["exit_code"],
                    "stdout_file": moon[name]["inspect"]["stdout_file"],
                    "stderr_file": moon[name]["inspect"]["stderr_file"],
                    "parsed": parse_inspect(moon[name]["inspect"]["stdout"]),
                },
                "history": {
                    "command": moon[name]["history"]["command"],
                    "exit_code": moon[name]["history"]["exit_code"],
                    "stdout_file": moon[name]["history"]["stdout_file"],
                },
                "read": {
                    "command": moon[name]["read"]["command"],
                    "exit_code": moon[name]["read"]["exit_code"],
                    "stdout_file": moon[name]["read"]["stdout_file"],
                    "stderr_file": moon[name]["read"]["stderr_file"],
                    "stdout": moon[name]["read"]["stdout"],
                    "stderr": moon[name]["read"]["stderr"],
                    "parsed": parse_read(moon[name]["read"]["stdout"]),
                },
            }
            for name in [MAIN, *KIND_TABLES, CP_NESTED, CP_ONLY, NEG, WRITE_ATTEMPT]
        },
        "scan_on_nested_table": {
            "note": "scan 只做文件级规划，不读数据；成功不代表 nested 读取成功。",
            "runs": {
                key: {
                    "command": scan_runs[key]["command"],
                    "exit_code": scan_runs[key]["exit_code"],
                    "parsed": scan_parsed[key],
                }
                for key in scan_runs
            },
        },
        "checks": checks,
        "case_b_checklist": case_b,
        "gates": GATES,
        "gate_counts": gate_counts,
        "conclusion": [
            "外部引擎（delta-rs、DuckDB）读取 nested fixture 成功，oracle 逐字段一致；",
            "本项目 read 在进入 Parquet 前结构化拒绝，未读出任何嵌套行；",
            "本项目未返回 0 行假成功、未类型混淆、未静默忽略 map；",
            "但按第 3 节门槛 7，“识别后报错”不算完成，且 CLI 失败退出码为 0、错误写 stdout；",
            f"七项门槛：已完成 {gate_counts['done']} / 部分完成 {gate_counts['partial']} / 未完成 {gate_counts['missing']}。",
            "结论：能力缺口对照，不是互操作通过。",
        ],
    }
    write_json(OUT / "comparison.json", comparison)

    # ---- README ----
    ctx = {
        "environment": manifest["environment"],
        "manifest": manifest,
        "oracles": {"delta_rs": oracles_rs, "duckdb": oracles_ddb},
        "moondelta": moon,
        "scan": scan_parsed,
        "oracle_cross_check": oracle_cross,
        "checkpoint_only_cross_check": checkpoint_only_cross,
        "checks": checks,
        "case_b_checklist": case_b,
        "checkpoint": checkpoint,
        "negative": {"fixture": negative, "delta_rs_attempt": neg_rs, "duckdb_attempt": neg_ddb},
        "gate_counts": gate_counts,
    }
    write_text(OUT / "README.md", build_readme(ctx))
    write_text(OUT / "comparison.md", build_comparison_md(ctx))
    write_json(
        OUT / "negative-physical-nested.json",
        {
            "fixture": negative,
            "delta_rs_attempt": neg_rs,
            "duckdb_attempt": neg_ddb,
            "caution": "该表的日志 schema 与 Parquet 物理 schema 不一致，属于人为构造的负向表；"
                       "外部引擎行为不作为互操作基线，只用于验证本项目的错误路径。",
        },
    )
    print("[6/9] comparison.json / comparison.md / README.md 完成")
    print("[7/9] 门槛统计: " + json.dumps(gate_counts, ensure_ascii=False))
    print("[8/9] 主表 read 退出码: " + str(checks[MAIN]["cli_exit_code"]) +
          "；错误: " + str(checks[MAIN]["error_text"]))
    print("[9/9] 全部完成。输出目录: " + str(OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
