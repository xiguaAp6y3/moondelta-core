#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D2：flat 表双向互操作对拍（delta-rs / DuckDB <-> moondelta-core）。

依据 docs/协议兼容性与互操作范围.md 第 4 节案例 A 与第 6 节任务 D2。

一次运行完成：
  A. 用 delta-rs（Python deltalake）写出 flat Delta 表
     （id long NOT NULL / name string / active boolean，含 null，两次提交 => 两个数据文件），
     记录 protocol / schema / 版本 / 文件清单 / add.stats / 每文件 numRecords。
  B. 用 moondelta-core CLI（delta.exe）inspect/history/scan/read 读取该表，
     保存原始 stdout/stderr/退出码，逐项 diff 列名顺序、类型、nullability、版本、
     文件清单、行数、逐列值与 null、add.stats。
  C. 用 MoonBit 示例 src/examples/interop_flat_writer 写出同类表（3 次 commit，2 个数据文件），
     再用 delta-rs 与 DuckDB 回读并断言 schema / 行数 / 逐列值，并检查 parquet 物理层 nullability。
  D. DuckDB：INSTALL delta / LOAD delta / delta_scan；扩展不可用时明确记录错误文本，
     并附带 read_parquet 级补充（标注“非 Delta 级互操作”）。
  E. 比较两侧 add.stats（numRecords/minValues/maxValues/nullCount 的存在性、类型、值），
     并用 scan --where "id>=4" 观察剪枝行为的实际影响。
  F. 输出 results.json / results/*.csv / raw/ 原始输出 / hashes.txt。

退出码：0 = 全部检查 PASS（允许已记录的 DIFF/INFO 差异）；1 = 存在 FAIL。

用法（仓库根目录）：
  .tools\\pyenv\\Scripts\\python.exe artifacts\\interop-flat\\run_interop.py
  .tools\\pyenv\\Scripts\\python.exe artifacts\\interop-flat\\run_interop.py --build
    --build 会先执行 `moon build --target native`（构建锁被占用时自动重试），
    以重建 writer 示例与 CLI；不传则直接使用 _build 下已有可执行文件。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from collections import Counter
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

ART = Path(__file__).resolve().parent
ROOT = ART.parents[1]
RAW = ART / "raw"
RES = ART / "results"
ENGINE = ART / "engine-written"
MOON = ART / "moondelta-written"
PROBE = ART / "probe"

DELTA_EXE = ROOT / "_build" / "native" / "debug" / "build" / "cmd" / "delta" / "delta.exe"
WRITER_EXE = (
    ROOT
    / "_build"
    / "native"
    / "debug"
    / "build"
    / "examples"
    / "interop_flat_writer"
    / "interop_flat_writer.exe"
)

COMPAT_CONFIG = {"delta.minReaderVersion": "1", "delta.minWriterVersion": "2"}

# 期望的 Delta schema（列名, Delta 类型, nullable）与数据（id => (name, active)）
COLUMNS = [("id", "long", False), ("name", "string", True), ("active", "boolean", True)]
DATA = {
    1: ("alice", True),
    2: (None, False),
    3: ("bob", None),
    4: ("carol", True),
    5: (None, None),
}
EXPECTED_ROWS = [(rid, *DATA[rid]) for rid in sorted(DATA)]

checks: list[dict] = []
state: dict = {}


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------
def relposix(p: Path | str) -> str:
    """仓库根为基准的 posix 相对路径，用于命令行参数与记录。"""
    return Path(p).resolve().relative_to(ROOT).as_posix()


def add_check(group: str, name: str, status: str, detail: str) -> None:
    checks.append({"group": group, "name": name, "status": status, "detail": detail})
    mark = {"PASS": "PASS", "FAIL": "FAIL", "DIFF": "DIFF", "INFO": "INFO", "BLOCKED": "BLOCKED"}[status]
    print(f"  [{mark}] {group}:{name} - {detail}")


def ok(group: str, name: str, cond: bool, detail: str) -> bool:
    add_check(group, name, "PASS" if cond else "FAIL", detail)
    return cond


def diff(group: str, name: str, detail: str) -> None:
    add_check(group, name, "DIFF", detail)


def info(group: str, name: str, detail: str) -> None:
    add_check(group, name, "INFO", detail)


def blocked(group: str, name: str, detail: str) -> None:
    add_check(group, name, "BLOCKED", detail)


def ensure_dirs() -> None:
    for d in (RAW, RES):
        d.mkdir(parents=True, exist_ok=True)


def safe_rmtree(path: Path) -> None:
    """只允许删除 artifacts/interop-flat 下的内容。"""
    p = path.resolve()
    if ART.resolve() not in p.parents:
        raise RuntimeError(f"拒绝删除 artifacts/interop-flat 之外的路径: {p}")
    if p.exists():
        shutil.rmtree(p)


def run(args: list, timeout: int = 600) -> dict:
    try:
        p = subprocess.run(
            [str(a) for a in args],
            cwd=str(ROOT),
            capture_output=True,
            timeout=timeout,
        )
        return {
            "args": [str(a) for a in args],
            "exit": p.returncode,
            "stdout": decode_bytes(p.stdout),
            "stderr": decode_bytes(p.stderr),
        }
    except Exception as exc:  # noqa: BLE001 - 记录失败而不是静默跳过
        return {
            "args": [str(a) for a in args],
            "exit": None,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
        }


def decode_bytes(b: bytes) -> str:
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


def save_run(key: str, res: dict) -> None:
    (RAW / f"{key}.stdout.txt").write_text(res["stdout"], encoding="utf-8", newline="\n")
    (RAW / f"{key}.stderr.txt").write_text(res["stderr"], encoding="utf-8", newline="\n")
    (RAW / f"{key}.exit.txt").write_text(f"{res['exit']}\n", encoding="utf-8", newline="\n")
    (RAW / f"{key}.cmd.txt").write_text(" ".join(res["args"]) + "\n", encoding="utf-8", newline="\n")


def save_json(key: str, obj) -> None:
    (RAW / f"{key}.json").write_text(
        json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 解析 delta 日志 / CLI 文本输出
# ---------------------------------------------------------------------------
def read_delta_log(table_root: Path) -> list[dict]:
    log_dir = table_root / "_delta_log"
    versions = []
    for f in sorted(log_dir.glob("*.json")):
        actions = []
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                actions.append(json.loads(line))
        versions.append({"version": int(f.stem), "actions": actions})
    return versions


def collect_adds(log: list[dict]) -> list[dict]:
    """展开日志中所有 add action；stats 字符串解析为对象。"""
    adds = []
    for ver in log:
        for action in ver["actions"]:
            if "add" not in action:
                continue
            a = action["add"]
            stats_raw = a.get("stats")
            stats = json.loads(stats_raw) if isinstance(stats_raw, str) else stats_raw
            adds.append(
                {
                    "version": ver["version"],
                    "path": a["path"],
                    "size": a.get("size"),
                    "modificationTime": a.get("modificationTime"),
                    "partitionValues": a.get("partitionValues"),
                    "stats_raw": stats_raw,
                    "stats": stats,
                }
            )
    return adds


def schema_fields_from_log(log: list[dict]) -> list[dict]:
    for ver in log:
        for action in ver["actions"]:
            if "metaData" in action:
                schema_string = action["metaData"]["schemaString"]
                return json.loads(schema_string)["fields"]
    raise RuntimeError("日志中没有 metaData action")


def schema_fields_from_table(dt) -> list[dict]:
    return [
        {"name": f.name, "type": f.type, "nullable": f.nullable}
        for f in dt.schema().fields
    ]


def parse_inspect(text: str) -> dict:
    out: dict = {"schema": [], "raw": text}
    in_schema = False
    for line in text.splitlines():
        if re.match(r"^schema\s*:\s*$", line.strip()):
            in_schema = True
            continue
        if in_schema:
            m = re.match(r"^\s{2}([A-Za-z0-9_]+)\s*:\s*(\w+)(\s*\(not null\))?\s*$", line)
            if m:
                out["schema"].append(
                    {"name": m.group(1), "type": m.group(2), "nullable": m.group(3) is None}
                )
                continue
            if line.strip() == "":
                continue
            in_schema = False
        m = re.match(r"^([a-z][a-z ]*?)\s*:\s?(.*)$", line)
        if not m:
            continue
        key, value = m.group(1).strip(), m.group(2).strip()
        out[key] = value
    if "current version" in out:
        m = re.match(r"^(\d+)\s+\(log versions:\s*(\d+)\)", out["current version"])
        if m:
            out["version"] = int(m.group(1))
            out["log_versions"] = int(m.group(2))
    if "files" in out:
        v = out["files"]
        m = re.search(r"(\d+)\s+rows:\s*(\d+)\s+bytes:\s*(\d+)", v)
        if m:
            out["files_count"] = int(m.group(1))
            out["rows"] = int(m.group(2))
            out["bytes"] = int(m.group(3))
    if "protocol" in out:
        m = re.search(r"reader=(\d+)\s+writer=(\d+)", out["protocol"])
        if m:
            out["reader_version"] = int(m.group(1))
            out["writer_version"] = int(m.group(2))
    return out


def parse_history(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) == 3 and parts[0].isdigit():
            rows.append({"version": int(parts[0]), "timestamp": int(parts[1]), "operation": parts[2]})
    return rows


def parse_scan(text: str) -> dict:
    out: dict = {"files": [], "raw": text}
    keys = ("total files", "selected files", "skipped partition", "skipped stats", "matched rows", "matched bytes")
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("- "):
            out["files"].append(s[2:].strip())
            continue
        m = re.match(r"^(" + "|".join(re.escape(k) for k in keys) + r")\s*:\s*(\d+)", s)
        if m:
            out[m.group(1)] = int(m.group(2))
    m = re.search(r"\(version (\d+)\)", text)
    if m:
        out["version"] = int(m.group(1))
    return out


def parse_read(text: str) -> dict:
    lines = text.splitlines()
    out: dict = {"columns": [], "rows": [], "raw": text}
    head = re.search(r"read (\d+) file\(s\), (\d+) row\(s\), version (\d+)", text)
    if head:
        out["files"] = int(head.group(1))
        out["rows_reported"] = int(head.group(2))
        out["version"] = int(head.group(3))
    if len(lines) < 2:
        return out
    for field in lines[1].split(" | "):
        m = re.match(r"^(.*) \((\w+)\)$", field.strip())
        if m:
            out["columns"].append({"name": m.group(1), "type": m.group(2)})
    for line in lines[2:]:
        if not line.strip():
            continue
        cells = [c.strip() for c in line.split(" | ")]
        row = []
        for col, cell in zip(out["columns"], cells):
            if cell == "NULL":
                row.append(None)
            elif col["type"] in ("long", "int", "integer"):
                row.append(int(cell))
            elif col["type"] == "boolean":
                row.append(cell == "true")
            elif col["type"] in ("double", "float"):
                row.append(float(cell))
            else:
                row.append(cell)
        out["rows"].append(row)
    return out


# ---------------------------------------------------------------------------
# 值比较（类型严格）
# ---------------------------------------------------------------------------
def typed_row(row) -> tuple:
    return tuple((type(v).__name__, v) for v in row)


def rows_match(got: list, expected: list) -> tuple[bool, str]:
    g = Counter(typed_row(r) for r in got)
    e = Counter(typed_row(r) for r in expected)
    if g == e:
        return True, f"{len(got)} 行逐列值严格一致（含 null，按多重集比较）"
    missing = list((e - g).elements())[:5]
    extra = list((g - e).elements())[:5]
    return False, f"缺失={missing} 多余={extra}"


def pyarrow_rows(table) -> list[tuple]:
    return [(r["id"], r["name"], r["active"]) for r in table.to_pylist()]


def fields_match(fields: list[dict]) -> tuple[bool, str]:
    if len(fields) != len(COLUMNS):
        return False, f"字段数 {len(fields)} != {len(COLUMNS)}: {fields}"
    problems = []
    for i, (got, (name, typ, nullable)) in enumerate(zip(fields, COLUMNS)):
        if got.get("name") != name:
            problems.append(f"第 {i} 列名 {got.get('name')!r} != {name!r}")
        if got.get("type") != typ:
            problems.append(f"{name} 类型 {got.get('type')!r} != {typ!r}")
        if bool(got.get("nullable")) != nullable:
            problems.append(f"{name} nullable {got.get('nullable')} != {nullable}")
    return (not problems), ("列名/顺序/类型/nullability 完全一致" if not problems else "; ".join(problems))


# ---------------------------------------------------------------------------
# Step A：外部引擎（delta-rs）写出
# ---------------------------------------------------------------------------
def step_engine_write() -> bool:
    print("\n== Step A: delta-rs 写出 flat Delta 表 ==")
    try:
        import pyarrow as pa
        from deltalake import DeltaTable, write_deltalake
    except Exception as exc:  # noqa: BLE001
        blocked("A", "import deltalake/pyarrow", f"{type(exc).__name__}: {exc}")
        return False

    pa_type = {"long": pa.int64(), "string": pa.string(), "boolean": pa.bool_()}
    schema = pa.schema([pa.field(n, pa_type[t], nullable=nl) for n, t, nl in COLUMNS])
    batch1 = pa.table(
        {
            "id": pa.array([1, 2, 3], type=pa.int64()),
            "name": pa.array(["alice", None, "bob"], type=pa.string()),
            "active": pa.array([True, False, None], type=pa.bool_()),
        },
        schema=schema,
    )
    batch2 = pa.table(
        {
            "id": pa.array([4, 5], type=pa.int64()),
            "name": pa.array(["carol", None], type=pa.string()),
            "active": pa.array([True, None], type=pa.bool_()),
        },
        schema=schema,
    )
    table_uri = relposix(ENGINE)
    errors = []
    # 第一次写：overwrite 建表（version 0 同时含 protocol/metaData/add）；第二次 append（version 1）
    for label, batch, mode in (("create", batch1, "overwrite"), ("append", batch2, "append")):
        try:
            write_deltalake(table_uri, batch, mode=mode, configuration=COMPAT_CONFIG)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
    if errors:
        blocked("A", "delta-rs write_deltalake", "; ".join(errors))
        save_json("a_engine_write_error", {"errors": errors})
        return False

    dt = DeltaTable(table_uri)
    proto = dt.protocol()
    log = read_delta_log(ENGINE)
    fields = schema_fields_from_log(log)
    adds = collect_adds(log)
    files = [PurePosixPath(unquote(u)).name for u in dt.file_uris()]
    add_paths = [a["path"] for a in adds]
    phys = parquet_physical(ENGINE, add_paths)
    arrow_table = dt.to_pyarrow_table()
    oracle_rows = pyarrow_rows(arrow_table)
    delta_arrow_fields = [
        {"name": f.name, "type": str(f.type), "nullable": f.nullable} for f in arrow_table.schema
    ]

    state["engine"] = {
        "table": table_uri,
        "engine": "delta-rs (python deltalake)",
        "protocol": {
            "min_reader_version": proto.min_reader_version,
            "min_writer_version": proto.min_writer_version,
            "writer_features": proto.writer_features,
            "reader_features": proto.reader_features,
        },
        "version": dt.version(),
        "log_versions": len(log),
        "files": files,
        "schema_fields": fields,
        "delta_arrow_schema": delta_arrow_fields,
        "oracle_rows": [list(r) for r in oracle_rows],
        "adds": adds,
        "parquet_physical": phys,
        "write_configuration": COMPAT_CONFIG,
    }
    save_json("a_engine_write", state["engine"])

    # A 组断言
    ok(
        "A",
        "protocol = reader v1 / writer v2",
        proto.min_reader_version == 1 and proto.min_writer_version == 2,
        f"reader={proto.min_reader_version} writer={proto.min_writer_version} "
        f"writerFeatures={proto.writer_features} readerFeatures={proto.reader_features}",
    )
    ok("A", "两次提交 => 版本数 2", dt.version() == 1 and len(log) == 2,
       f"version={dt.version()} log_versions={len(log)}（version 0 create+add, version 1 append）")
    ok("A", "数据文件数 = 2", len(files) == 2, f"files={files}")
    pass_schema, schema_detail = fields_match(fields)
    ok("A", "schema 列名/顺序/类型/nullability", pass_schema, schema_detail)
    ok("A", "总行数 = 5", len(oracle_rows) == 5, f"delta-rs read rows={len(oracle_rows)}")
    pass_rows, rows_detail = rows_match(oracle_rows, EXPECTED_ROWS)
    ok("A", "delta-rs 读回的逐列值（含 null）", pass_rows, rows_detail)
    num_records = [a["stats"].get("numRecords") for a in adds if a["stats"]]
    ok(
        "A",
        "每文件 add.stats.numRecords 合计 = 5",
        sum(num_records) == 5 and len(num_records) == 2,
        f"numRecords={num_records}",
    )
    stats_complete = all(
        isinstance(a["stats"], dict)
        and set(a["stats"].get("minValues", {})) == {"id", "name", "active"}
        and set(a["stats"].get("maxValues", {})) == {"id", "name", "active"}
        and set(a["stats"].get("nullCount", {})) == {"id", "name", "active"}
        for a in adds
    )
    ok(
        "A",
        "add.stats 含 3 列 min/max/nullCount",
        stats_complete,
        "delta-rs 为 id/name/active 都写出 minValues、maxValues、nullCount",
    )
    # parquet 物理层对照：delta-rs 对 Delta 声明 NOT NULL 的 id 是否写成 required
    eng_id_required = all(
        any(f["name"] == "id" and not f["nullable"] for f in p["arrow_fields"]) for p in phys
    )
    ok(
        "A",
        "parquet 物理 nullability 对照（delta-rs: id required）",
        eng_id_required,
        f"delta-rs 落盘: {[{f['name']: f['nullable'] for f in p['arrow_fields']} for p in phys]}",
    )
    state["engine"]["ok"] = True
    return True


# ---------------------------------------------------------------------------
# Step B：moondelta CLI 读取外部引擎表
# ---------------------------------------------------------------------------
def step_moondelta_read_engine() -> bool:
    print("\n== Step B: moondelta CLI 读取 delta-rs 写出的表 ==")
    eng = state.get("engine")
    if not eng:
        blocked("B", "依赖 Step A", "delta-rs 表未成功写出")
        return False
    table = eng["table"]
    if not DELTA_EXE.exists():
        blocked("B", "delta.exe 不存在", str(DELTA_EXE))
        return False

    runs = {}
    for key, args in (
        ("b_engine_inspect", ["inspect", table]),
        ("b_engine_history", ["history", table]),
        ("b_engine_scan", ["scan", table]),
        ("b_engine_scan_where_id_ge_4", ["scan", table, "--where", "id>=4"]),
        ("b_engine_read", ["read", table]),
    ):
        res = run([DELTA_EXE, *args])
        save_run(key, res)
        runs[key] = res

    insp = parse_inspect(runs["b_engine_inspect"]["stdout"])
    hist = parse_history(runs["b_engine_history"]["stdout"])
    scan = parse_scan(runs["b_engine_scan"]["stdout"])
    scan_where = parse_scan(runs["b_engine_scan_where_id_ge_4"]["stdout"])
    rd = parse_read(runs["b_engine_read"]["stdout"])
    state["moondelta_read_engine"] = {
        "runs": {k: {"args": v["args"], "exit": v["exit"]} for k, v in runs.items()},
        "inspect": insp,
        "history": hist,
        "scan": scan,
        "scan_where_id_ge_4": scan_where,
        "read": rd,
    }
    save_json("b_moondelta_read_engine", state["moondelta_read_engine"])

    exits_ok = all(v["exit"] == 0 for v in runs.values())
    ok("B", "inspect/history/scan/read 退出码均为 0", exits_ok,
       {k: v["exit"] for k, v in runs.items()})

    ok("B", "inspect 当前版本 = 1", insp.get("version") == 1, f"version={insp.get('version')}")
    ok("B", "inspect 日志版本数 = 2", insp.get("log_versions") == 2, f"log_versions={insp.get('log_versions')}")
    ok("B", "inspect files/rows = 2/5", (insp.get("files_count"), insp.get("rows")) == (2, 5),
       f"files={insp.get('files_count')} rows={insp.get('rows')} bytes={insp.get('bytes')}")
    ok(
        "B",
        "inspect protocol = reader1/writer2",
        (insp.get("reader_version"), insp.get("writer_version")) == (1, 2),
        f"protocol={insp.get('protocol')}",
    )
    pass_schema, schema_detail = fields_match(insp["schema"])
    ok("B", "inspect schema 列名/顺序/类型/nullability", pass_schema, schema_detail)

    expected_history = [{"version": 0, "operation": "WRITE"}, {"version": 1, "operation": "WRITE"}]
    got_history = [{"version": h["version"], "operation": h["operation"]} for h in hist]
    ok("B", "history 版本与操作 = [0:WRITE, 1:WRITE]", got_history == expected_history, f"{got_history}")

    ok(
        "B",
        "scan 全量文件/行数 = 2/5",
        (scan.get("total files"), scan.get("selected files"), scan.get("matched rows"),
         scan.get("skipped stats")) == (2, 2, 5, 0),
        f"total={scan.get('total files')} selected={scan.get('selected files')} "
        f"matched_rows={scan.get('matched rows')} skipped_stats={scan.get('skipped stats')}",
    )
    ok("B", "scan 文件清单 = 日志 add 文件清单", sorted(scan["files"]) == sorted(eng["files"]),
       f"scan={sorted(scan['files'])} log={sorted(eng['files'])}")

    read_cols = [(c["name"], c["type"]) for c in rd["columns"]]
    expected_cols = [(n, t) for n, t, _ in COLUMNS]
    ok("B", "read 表头列名/类型/顺序", read_cols == expected_cols, f"{read_cols}")
    ok("B", "read 报告文件数/行数/版本 = 2/5/1",
       (rd.get("files"), rd.get("rows_reported"), rd.get("version")) == (2, 5, 1),
       f"files={rd.get('files')} rows={rd.get('rows_reported')} version={rd.get('version')}")
    pass_rows, rows_detail = rows_match([tuple(r) for r in rd["rows"]], EXPECTED_ROWS)
    ok("B", "read 逐列值与 null 和 delta-rs oracle 一致", pass_rows, rows_detail)
    pass_rows2, rows_detail2 = rows_match([tuple(r) for r in rd["rows"]], [tuple(r) for r in eng["oracle_rows"]])
    ok("B", "read 逐值 vs delta-rs 读回 oracle（同源对照）", pass_rows2, rows_detail2)

    # add.stats 与 CLI scan 的一致性
    stats = {a["path"]: a["stats"] for a in eng["adds"]}
    expected_pruned_file = [a["path"] for a in eng["adds"] if a["stats"]["minValues"]["id"] >= 4]
    ok(
        "B",
        "scan --where id>=4 用 stats 剪掉 1 个文件并命中 2 行",
        scan_where.get("skipped stats") == 1
        and scan_where.get("selected files") == 1
        and scan_where.get("matched rows") == 2
        and scan_where["files"] == expected_pruned_file,
        f"skipped_stats={scan_where.get('skipped stats')} selected={scan_where.get('selected files')} "
        f"matched_rows={scan_where.get('matched rows')} files={scan_where['files']} "
        f"期望文件={expected_pruned_file}",
    )
    ok("B", "scan matched rows 与 add.stats.numRecords 一致", scan.get("matched rows") == 5,
       f"numRecords={[s.get('numRecords') for s in stats.values()]}")

    # 记录 CLI inspect 的 schema 与 delta-rs delta schema 的 nullability 对照
    ok(
        "B",
        "nullability: Delta schema NOT NULL 被 CLI 识别为 not null",
        [f["nullable"] for f in insp["schema"]] == [f["nullable"] for f in eng["schema_fields"]],
        f"inspect={[f['nullable'] for f in insp['schema']]} delta_schema={[f['nullable'] for f in eng['schema_fields']]}",
    )
    return True


# ---------------------------------------------------------------------------
# Step C：moondelta 写出 + 外部引擎回读
# ---------------------------------------------------------------------------
def parquet_physical(table_root: Path, add_paths: list[str]) -> list[dict]:
    import pyarrow.parquet as pq

    out = []
    for p in add_paths:
        pf = pq.ParquetFile(table_root / p)
        out.append(
            {
                "path": p,
                "num_rows": pf.metadata.num_rows,
                "compression": [
                    pf.metadata.row_group(0).column(i).compression
                    for i in range(pf.metadata.row_group(0).num_columns)
                ],
                "created_by": pf.metadata.created_by,
                "arrow_fields": [
                    {"name": f.name, "nullable": f.nullable, "type": str(f.type)}
                    for f in pf.schema_arrow
                ],
            }
        )
    return out


def step_moondelta_write() -> bool:
    print("\n== Step C: moondelta-core 写出 + delta-rs/DuckDB 回读 ==")
    if not WRITER_EXE.exists():
        blocked("C", "writer 示例可执行文件不存在", f"{WRITER_EXE}（可加 --build 重建）")
        return False
    res = run([WRITER_EXE, relposix(MOON)])
    save_run("c_moon_writer", res)
    state["moon_write_run"] = {"args": res["args"], "exit": res["exit"], "stdout": res["stdout"],
                               "stderr": res["stderr"]}
    stdout = res["stdout"]
    ok("C", "writer 示例退出码 0", res["exit"] == 0, f"exit={res['exit']} stderr={res['stderr'].strip()}")
    ok(
        "C",
        "writer 输出 created/appended/self-check",
        "created version 0" in stdout
        and "appended batch1 => version 1" in stdout
        and "appended batch2 => version 2" in stdout
        and "self-check: version=2 files=2 rows=5" in stdout,
        " / ".join(line.strip() for line in stdout.splitlines() if line.strip()),
    )

    try:
        from deltalake import DeltaTable
    except Exception as exc:  # noqa: BLE001
        blocked("C", "import deltalake", f"{type(exc).__name__}: {exc}")
        return False

    log = read_delta_log(MOON)
    fields = schema_fields_from_log(log)
    adds = collect_adds(log)
    dt = DeltaTable(relposix(MOON))
    proto = dt.protocol()
    arrow_table = dt.to_pyarrow_table()
    rows = pyarrow_rows(arrow_table)
    files = [a["path"] for a in adds]
    phys = parquet_physical(MOON, files)
    state["moon_written"] = {
        "table": relposix(MOON),
        "protocol": {
            "min_reader_version": proto.min_reader_version,
            "min_writer_version": proto.min_writer_version,
            "writer_features": proto.writer_features,
            "reader_features": proto.reader_features,
        },
        "version": dt.version(),
        "log_versions": len(log),
        "files": files,
        "adds": adds,
        "schema_fields": fields,
        "delta_arrow_schema": [
            {"name": f.name, "type": str(f.type), "nullable": f.nullable} for f in arrow_table.schema
        ],
        "deltars_rows": [list(r) for r in rows],
        "parquet_physical": phys,
    }
    save_json("c_moondelta_written_deltars_read", state["moon_written"])

    ok("C", "moondelta 写出 protocol = reader1/writer2",
       proto.min_reader_version == 1 and proto.min_writer_version == 2,
       f"reader={proto.min_reader_version} writer={proto.min_writer_version}")
    ok("C", "moondelta 写出 commit 版本 = 3（create + 2 append）", dt.version() == 2 and len(log) == 3,
       f"version={dt.version()} log_versions={len(log)}")
    ok("C", "moondelta 写出数据文件数 = 2", len(files) == 2, f"files={files}")
    pass_schema, schema_detail = fields_match(fields)
    ok("C", "Delta schema 列名/顺序/类型/nullability", pass_schema, schema_detail)
    ok("C", "delta-rs 回读行数 = 5", len(rows) == 5, f"rows={len(rows)}")
    pass_rows, rows_detail = rows_match(rows, EXPECTED_ROWS)
    ok("C", "delta-rs 回读逐列值（含 null）", pass_rows, rows_detail)
    ok(
        "C",
        "delta-rs 回读按 Delta schema 保留 NOT NULL 语义",
        arrow_table.schema.field("id").nullable is False,
        f"delta-rs arrow schema: {[(f['name'], f['type'], f['nullable']) for f in state['moon_written']['delta_arrow_schema']]}",
    )

    # 物理 parquet nullability：Delta 声明 id NOT NULL，但 MoonBit writer 写成 optional
    phys_id_optional = all(
        any(f["name"] == "id" and f["nullable"] for f in p["arrow_fields"]) for p in phys
    )
    phys_names = [ [f["name"] for f in p["arrow_fields"]] for p in phys ]
    ok("C", "parquet 物理列名/顺序与 Delta schema 一致", all(n == ["id", "name", "active"] for n in phys_names),
       f"{phys_names}")
    ok("C", "parquet 压缩均为 UNCOMPRESSED（受支持）",
       all(c == "UNCOMPRESSED" for p in phys for c in p["compression"]),
       f"{[p['compression'] for p in phys]}")
    if phys_id_optional:
        diff(
            "C",
            "parquet 物理物理 nullability 与 Delta schema 不一致（id NOT NULL -> optional）",
            "Delta schema 声明 id nullable=false，MoonBit writer 落盘为 optional int64；"
            "delta-rs 回读时按 Delta schema 呈现 not null，但 read_parquet/第三方物理校验看不到 NOT NULL 约束。"
            "影响：读值正确，但物理层无法阻止/发现违反 NOT NULL 的写入；外部引擎若仅按 parquet schema 推导会认为 id 可为空。",
        )
    else:
        add_check("C", "parquet 物理 nullability", "PASS", "id 物理列为 required")

    # moondelta CLI 自读（写出后自洽性）
    self_runs = {}
    for key, args in (("c_self_inspect", ["inspect", relposix(MOON)]), ("c_self_read", ["read", relposix(MOON)])):
        r = run([DELTA_EXE, *args])
        save_run(key, r)
        self_runs[key] = r
    self_insp = parse_inspect(self_runs["c_self_inspect"]["stdout"])
    self_read = parse_read(self_runs["c_self_read"]["stdout"])
    state["moon_written"]["moondelta_self_read"] = {"inspect": self_insp, "read": self_read}
    ok("C", "moondelta CLI 自读写出表 inspect/read 退出码 0",
       all(v["exit"] == 0 for v in self_runs.values()), {k: v["exit"] for k, v in self_runs.items()})
    ok("C", "moondelta CLI 自读 schema 与 Delta schema 一致",
       *fields_match(self_insp["schema"])[:1], fields_match(self_insp["schema"])[1])
    pass_self, self_detail = rows_match([tuple(r) for r in self_read["rows"]], EXPECTED_ROWS)
    ok("C", "moondelta CLI 自读逐列值一致", pass_self, self_detail)
    return True


# ---------------------------------------------------------------------------
# Step D：DuckDB（第二外部引擎）
# ---------------------------------------------------------------------------
def step_duckdb() -> bool:
    print("\n== Step D: DuckDB 回读（delta_scan，失败则 read_parquet 补充） ==")
    try:
        import duckdb
    except Exception as exc:  # noqa: BLE001
        blocked("D", "import duckdb", f"{type(exc).__name__}: {exc}")
        return False

    con = duckdb.connect()
    result: dict = {"engine": "duckdb", "version": duckdb.__version__, "tables": {}}
    delta_available = False
    try:
        con.execute("INSTALL delta")
        con.execute("LOAD delta")
        version = con.execute(
            "select extension_version from duckdb_extensions() where extension_name='delta'"
        ).fetchall()[0][0]
        delta_available = True
        result["delta_extension"] = {"available": True, "version": version}
        ok("D", "DuckDB delta 扩展可用", True, f"duckdb={duckdb.__version__} delta extension_version={version}")
    except Exception as exc:  # noqa: BLE001
        result["delta_extension"] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
        blocked("D", "DuckDB delta 扩展不可用", f"{type(exc).__name__}: {exc}（已记录；改用 read_parquet 补充，标注非 Delta 级互操作）")

    for label, root, expected in (
        ("engine-written", ENGINE, EXPECTED_ROWS),
        ("moondelta-written", MOON, EXPECTED_ROWS),
    ):
        entry: dict = {"table": relposix(root)}
        target = root.as_posix()
        if delta_available:
            try:
                rows = con.execute("SELECT * FROM delta_scan(?)", [target]).fetchall()
                desc = con.execute("DESCRIBE SELECT * FROM delta_scan(?)", [target]).fetchall()
                entry["delta_scan"] = {
                    "rows": [list(r) for r in rows],
                    "describe": [
                        {"column": d[0], "type": d[1], "null": d[2]} for d in desc
                    ],
                }
                ok(
                    "D",
                    f"delta_scan({label}) 逐列值一致",
                    *rows_match([tuple(r) for r in rows], expected)[:1],
                    rows_match([tuple(r) for r in rows], expected)[1],
                )
                types = {d[0]: d[1] for d in desc}
                ok(
                    "D",
                    f"delta_scan({label}) 列类型 BIGINT/VARCHAR/BOOLEAN",
                    types == {"id": "BIGINT", "name": "VARCHAR", "active": "BOOLEAN"},
                    f"{types}",
                )
                nulls = {d[0]: d[2] for d in desc}
                if nulls.get("id") == "YES":
                    info(
                        "D",
                        f"delta_scan({label}) DESCRIBE 不反映 Delta NOT NULL",
                        "DuckDB delta 扩展对全部列返回 null=YES（即使 Delta schema 中 id nullable=false）；"
                        "这是 DuckDB 扩展的列级 nullability 展示行为，两侧一致，不影响值读取。",
                    )
            except Exception as exc:  # noqa: BLE001
                entry["delta_scan_error"] = f"{type(exc).__name__}: {exc}"
                blocked("D", f"delta_scan({label}) 失败", f"{type(exc).__name__}: {exc}")
        # read_parquet 补充（永远执行，明确标注非 Delta 级）
        try:
            rows = con.execute("SELECT * FROM read_parquet(?)", [target + "/*.parquet"]).fetchall()
            entry["read_parquet"] = {"rows": [list(r) for r in rows], "note": "非 Delta 级互操作（parquet 级补充）"}
            ok(
                "D",
                f"read_parquet({label}) 逐列值一致（非 Delta 级补充）",
                *rows_match([tuple(r) for r in rows], expected)[:1],
                rows_match([tuple(r) for r in rows], expected)[1],
            )
        except Exception as exc:  # noqa: BLE001
            entry["read_parquet_error"] = f"{type(exc).__name__}: {exc}"
            blocked("D", f"read_parquet({label}) 失败", f"{type(exc).__name__}: {exc}")
        result["tables"][label] = entry
    state["duckdb"] = result
    save_json("d_duckdb", result)
    return True


# ---------------------------------------------------------------------------
# Step E：add.stats 对比与剪枝影响；附带 protocol 覆盖探针
# ---------------------------------------------------------------------------
def stats_signature(adds: list[dict]) -> dict:
    sig = {"files": []}
    for a in adds:
        s = a["stats"] or {}
        sig["files"].append(
            {
                "path": a["path"],
                "version": a["version"],
                "numRecords": s.get("numRecords"),
                "minValues_keys": sorted(s.get("minValues", {}).keys()),
                "maxValues_keys": sorted(s.get("maxValues", {}).keys()),
                "nullCount_keys": sorted(s.get("nullCount", {}).keys()),
                "minValues": s.get("minValues"),
                "maxValues": s.get("maxValues"),
                "nullCount": s.get("nullCount"),
                "stats_raw": a["stats_raw"],
            }
        )
    return sig


def step_stats_and_probe() -> None:
    print("\n== Step E: add.stats 对比与剪枝影响 ==")
    eng = state.get("engine")
    moon = state.get("moon_written")
    if not eng or not moon:
        blocked("E", "依赖 Step A/C", "缺少一侧 add.stats")
        return
    eng_sig = stats_signature(eng["adds"])
    moon_sig = stats_signature(moon["adds"])
    state["stats"] = {"engine-written": eng_sig, "moondelta-written": moon_sig}
    save_json("e_stats_compare", state["stats"])

    eng_all = all(
        f["numRecords"] is not None
        and f["minValues_keys"] == ["active", "id", "name"]
        and f["maxValues_keys"] == ["active", "id", "name"]
        and f["nullCount_keys"] == ["active", "id", "name"]
        for f in eng_sig["files"]
    )
    ok(
        "E",
        "delta-rs add.stats：numRecords + 3 列 min/max/nullCount 齐全",
        eng_all,
        f"{[{k: f[k] for k in ('path', 'numRecords', 'nullCount_keys')} for f in eng_sig['files']]}",
    )
    moon_has_num = all(f["numRecords"] is not None for f in moon_sig["files"])
    moon_only_id = all(
        f["minValues_keys"] == ["id"] and f["maxValues_keys"] == ["id"] for f in moon_sig["files"]
    )
    moon_no_null = all(f["nullCount_keys"] == [] for f in moon_sig["files"])
    ok("E", "moondelta add.stats：每文件都有 numRecords", moon_has_num,
       f"{[f['numRecords'] for f in moon_sig['files']]}")
    if moon_only_id or moon_no_null:
        diff(
            "E",
            "moondelta add.stats 仅含 id 的 min/max、缺 nullCount 与 string/boolean 统计",
            "moondelta 写出 stats = {numRecords, minValues:{id}, maxValues:{id}}；"
            "delta-rs 写出 stats 含 active/name/id 的 min/max 以及 3 列 nullCount。"
            "影响：数值列 id 的 min/max 剪枝可用（下方 scan 验证）；"
            "name/active 谓词剪枝、NULL 相关剪枝（IS NULL/IS NOT NULL）在 moondelta 写出的表上不可用，"
            "只能退化为全文件扫描 —— 少剪枝但不会多读/少读数据（读值已验证一致）。",
        )
    else:
        ok("E", "moondelta add.stats 与 delta-rs 同等完备", True, "两侧 min/max/nullCount 均齐全")

    # 剪枝行为实测：两边各跑 scan --where "id>=4"
    prune = {}
    if DELTA_EXE.exists():
        for label, table in (("engine-written", eng["table"]), ("moondelta-written", moon["table"])):
            r = run([DELTA_EXE, "scan", table, "--where", "id>=4"])
            save_run(f"e_scan_where_id_ge_4_{'engine' if label == 'engine-written' else 'moondelta'}", r)
            parsed = parse_scan(r["stdout"])
            prune[label] = {"exit": r["exit"], "selected": parsed.get("selected files"),
                            "skipped_stats": parsed.get("skipped stats"),
                            "matched_rows": parsed.get("matched rows"), "files": parsed["files"]}
        state["pruning"] = prune
        save_json("e_pruning", prune)
        ok(
            "E",
            "两侧 scan --where id>=4 都剪掉 1 个文件、命中 2 行",
            all(v["exit"] == 0 and v["selected"] == 1 and v["skipped_stats"] == 1 and v["matched_rows"] == 2
                for v in prune.values()),
            f"{prune}",
        )
    else:
        blocked("E", "剪枝实测", "delta.exe 不存在")

    # 附带探针（超出 D2 验收范围，交 D1 协议审计）：delta-rs configuration 可强制更高 protocol
    print("\n-- 附带探针：写 reader v1/writer v2 的配置手段，以及 reader>1 表的 CLI 行为 --")
    probe_result: dict = {"configuration_keys": COMPAT_CONFIG, "note": "超出 D2 验收，交 D1 协议审计复核"}
    try:
        import pyarrow as pa
        from deltalake import DeltaTable, write_deltalake

        probe_uri = relposix(PROBE / "protocol-reader2")
        schema = pa.schema(
            [pa.field(n, {"long": pa.int64(), "string": pa.string(), "boolean": pa.bool_()}[t], nullable=nl)
             for n, t, nl in COLUMNS]
        )
        write_deltalake(
            probe_uri,
            pa.table({"id": pa.array([1], type=pa.int64()), "name": pa.array(["probe"], type=pa.string()),
                      "active": pa.array([True], type=pa.bool_())}, schema=schema),
            mode="overwrite",
            configuration={"delta.minReaderVersion": "2", "delta.minWriterVersion": "5"},
        )
        probe_dt = DeltaTable(probe_uri)
        probe_proto = probe_dt.protocol()
        probe_log = read_delta_log(PROBE / "protocol-reader2")
        probe_protocol_action = [
            a["protocol"] for v in probe_log for a in v["actions"] if "protocol" in a
        ][0]
        probe_result["written_protocol"] = probe_protocol_action
        probe_result["written_protocol_api"] = {
            "min_reader_version": probe_proto.min_reader_version,
            "min_writer_version": probe_proto.min_writer_version,
        }
        # 不传 configuration 时 delta-rs 的默认 protocol（用于支撑“如何写出 reader v1/writer v2”的说明）
        default_uri = relposix(PROBE / "default-protocol")
        write_deltalake(
            default_uri,
            pa.table({"id": pa.array([1], type=pa.int64()), "name": pa.array(["probe"], type=pa.string()),
                      "active": pa.array([True], type=pa.bool_())}, schema=schema),
            mode="overwrite",
        )
        default_proto = DeltaTable(default_uri).protocol()
        default_log = read_delta_log(PROBE / "default-protocol")
        probe_result["default_write_no_configuration"] = {
            "protocol": [a["protocol"] for v in default_log for a in v["actions"] if "protocol" in a][0],
            "api": {
                "min_reader_version": default_proto.min_reader_version,
                "min_writer_version": default_proto.min_writer_version,
            },
        }
        info(
            "E",
            "delta-rs 不传 configuration 时的默认 protocol",
            f"reader={default_proto.min_reader_version} writer={default_proto.min_writer_version}"
            f"（本环境默认即 1/2，故显式配置是“钉死协议”而非必需）",
        )
        # 用 CLI 读取高版本表，记录实际行为
        for key, args in (("f_probe_reader2_inspect", ["inspect", probe_uri]),
                          ("f_probe_reader2_read", ["read", probe_uri])):
            r = run([DELTA_EXE, *args])
            save_run(key, r)
            probe_result[key] = {"exit": r["exit"], "stdout": r["stdout"], "stderr": r["stderr"]}
        probe_result["raw_log_v0"] = (PROBE / "protocol-reader2" / "_delta_log" / "00000000000000000000.json").read_text(
            encoding="utf-8"
        )
        save_json("f_probe_protocol_config", probe_result)
        info(
            "E",
            "探针：write_deltalake(configuration={delta.minReaderVersion,delta.minWriterVersion}) 可控制 protocol",
            f"默认即 reader=1/writer=2；显式配置 1/2 可固定；写成 2/5 时日志 protocol="
            f"{probe_protocol_action}（证明配置键真实生效）。",
        )
        inspect_exit = probe_result["f_probe_reader2_inspect"]["exit"]
        read_exit = probe_result["f_probe_reader2_read"]["exit"]
        if inspect_exit == 0 and read_exit == 0:
            info(
                "E",
                "风险：moondelta 接受 reader=2/writer=5 表而非拒绝（与文档第 1/2 节口径不一致）",
                f"inspect/read 退出码 0；文档要求高于 reader v1 的表必须拒绝。"
                f"该探针表日志 protocol={{minReaderVersion:2,minWriterVersion:5}} 且无 readerFeatures/writerFeatures，"
                f"属 delta-rs 非常规产出，但 CLI 未做版本校验。建议交 D1 协议审计确认；"
                f"详见 raw/f_probe_protocol_config.json。",
            )
        else:
            info(
                "E",
                "探针：moondelta 对 reader=2 表返回非 0",
                f"inspect exit={inspect_exit} read exit={read_exit}（符合文档的拒绝口径）",
            )
    except Exception as exc:  # noqa: BLE001
        probe_result["error"] = f"{type(exc).__name__}: {exc}"
        save_json("f_probe_protocol_config", probe_result)
        blocked("E", "protocol 配置探针", f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Step F：输出 results.json / CSV / hashes.txt
# ---------------------------------------------------------------------------
def write_results() -> dict:
    summary = Counter(c["status"] for c in checks)
    verdict = {
        "pass": summary.get("PASS", 0),
        "fail": summary.get("FAIL", 0),
        "diff": summary.get("DIFF", 0),
        "info": summary.get("INFO", 0),
        "blocked": summary.get("BLOCKED", 0),
    }
    results = {
        "task": "D2：flat 表双向互操作对拍",
        "repo": str(ROOT),
        "environment": state.get("environment", {}),
        "engine_written": state.get("engine"),
        "moondelta_read_engine": state.get("moondelta_read_engine"),
        "moondelta_written": state.get("moon_written"),
        "moon_write_run": state.get("moon_write_run"),
        "duckdb": state.get("duckdb"),
        "stats": state.get("stats"),
        "pruning": state.get("pruning"),
        "checks": checks,
        "verdict": verdict,
    }
    (ART / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )

    with (RES / "checks.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["group", "check", "status", "detail"])
        for c in checks:
            w.writerow([c["group"], c["name"], c["status"], c["detail"]])

    # 展示用 CSV：逐值（断言以 JSON 为准）
    if state.get("engine"):
        with (RES / "values_engine_written.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["source", "id", "name", "active"])
            for r in state["engine"]["oracle_rows"]:
                w.writerow(["delta-rs read (oracle)", *r])
            for r in state["moondelta_read_engine"]["read"]["rows"]:
                w.writerow(["moondelta CLI read", *r])
            for t in (state.get("duckdb") or {}).get("tables", {}).values():
                if "delta_scan" in t:
                    for r in t["delta_scan"]["rows"]:
                        w.writerow([f"DuckDB delta_scan ({t['table']})", *r])
                if "read_parquet" in t:
                    for r in t["read_parquet"]["rows"]:
                        w.writerow([f"DuckDB read_parquet [非 Delta 级] ({t['table']})", *r])

    if state.get("moon_written"):
        with (RES / "values_moondelta_written.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["source", "id", "name", "active"])
            for r in state["moon_written"]["deltars_rows"]:
                w.writerow(["delta-rs read", *r])
            for r in state["moon_written"]["moondelta_self_read"]["read"]["rows"]:
                w.writerow(["moondelta CLI read (self)", *r])
            for t in (state.get("duckdb") or {}).get("tables", {}).values():
                if "delta_scan" in t:
                    for r in t["delta_scan"]["rows"]:
                        w.writerow([f"DuckDB delta_scan ({t['table']})", *r])
                if "read_parquet" in t:
                    for r in t["read_parquet"]["rows"]:
                        w.writerow([f"DuckDB read_parquet [非 Delta 级] ({t['table']})", *r])

    if state.get("stats"):
        with (RES / "stats_compare.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["writer", "file", "version", "numRecords", "minValues_keys",
                        "maxValues_keys", "nullCount_keys", "minValues", "maxValues", "nullCount"])
            for writer, sig in state["stats"].items():
                for file in sig["files"]:
                    w.writerow([writer, file["path"], file["version"], file["numRecords"],
                                ";".join(file["minValues_keys"]), ";".join(file["maxValues_keys"]),
                                ";".join(file["nullCount_keys"]),
                                json.dumps(file["minValues"], ensure_ascii=False),
                                json.dumps(file["maxValues"], ensure_ascii=False),
                                json.dumps(file["nullCount"], ensure_ascii=False)])
    results["verdict"] = verdict
    return results


def write_hashes() -> None:
    skip_names = {"README.md", "hashes.txt"}
    files = []
    for p in sorted(ART.rglob("*")):
        if not p.is_file():
            continue
        if p.name in skip_names:
            continue
        if p.resolve() == Path(__file__).resolve():
            files.append(p)
            continue
        files.append(p)
    lines = []
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        lines.append(f"{sha256_file(p)}  {rel}")
    (ART / "hashes.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"\nhashes.txt: {len(lines)} 个文件")


def collect_environment() -> None:
    import platform

    env = {
        "platform": platform.platform(),
        "python": sys.version,
        "python_executable": sys.executable,
        "cwd": str(ROOT),
        "moon": None,
        "python_packages": {},
    }
    for mod in ("deltalake", "duckdb", "pyarrow", "pandas"):
        try:
            m = __import__(mod)
            env["python_packages"][mod] = getattr(m, "__version__", "unknown")
        except Exception as exc:  # noqa: BLE001
            env["python_packages"][mod] = f"MISSING: {type(exc).__name__}: {exc}"
    moon = run(["moon", "version"])
    env["moon"] = {"exit": moon["exit"], "stdout": moon["stdout"].strip(), "stderr": moon["stderr"].strip()}
    if DELTA_EXE.exists():
        v = run([DELTA_EXE, "version"])
        save_run("00_delta_version", v)
        env["delta_exe_version"] = v["stdout"].strip()
        env["delta_exe"] = relposix(DELTA_EXE)
    state["environment"] = env
    save_json("00_environment", env)


def clean() -> None:
    for d in (RAW, RES, ENGINE, MOON, PROBE):
        safe_rmtree(d)
    ensure_dirs()


def build_with_retry(log: Path) -> bool:
    attempts = 3
    for i in range(1, attempts + 1):
        res = run(["moon", "build", "--target", "native"], timeout=1800)
        text = "$ moon build --target native\n" + res["stdout"] + res["stderr"] + f"\nexit={res['exit']}\n"
        log.write_text(text, encoding="utf-8", newline="\n")
        if res["exit"] == 0:
            print(f"moon build 成功（第 {i} 次尝试）")
            return True
        print(f"moon build 失败（第 {i} 次尝试，可能被其它 agent 的构建锁占用），15s 后重试...")
        time.sleep(15)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="D2 flat 表双向互操作对拍")
    parser.add_argument("--build", action="store_true", help="先运行 moon build --target native 重建可执行文件")
    args = parser.parse_args()

    os.chdir(ROOT)
    print(f"repo root: {ROOT}")
    clean()

    if args.build:
        ensure_dirs()
        build_ok = build_with_retry(RAW / "build_moon.log")
        if not build_ok:
            blocked("0", "moon build --target native", "3 次均失败，见 raw/build_moon.log")
            write_results()
            write_hashes()
            return 1

    collect_environment()

    try:
        step_engine_write()
    except Exception:  # noqa: BLE001
        add_check("A", "未捕获异常", "FAIL", traceback.format_exc())
    try:
        step_moondelta_read_engine()
    except Exception:  # noqa: BLE001
        add_check("B", "未捕获异常", "FAIL", traceback.format_exc())
    try:
        step_moondelta_write()
    except Exception:  # noqa: BLE001
        add_check("C", "未捕获异常", "FAIL", traceback.format_exc())
    try:
        step_duckdb()
    except Exception:  # noqa: BLE001
        add_check("D", "未捕获异常", "FAIL", traceback.format_exc())
    try:
        step_stats_and_probe()
    except Exception:  # noqa: BLE001
        add_check("E", "未捕获异常", "FAIL", traceback.format_exc())

    results = write_results()
    write_hashes()
    verdict = results["verdict"]
    print("\n== 汇总 ==")
    print(
        f"PASS={verdict['pass']} FAIL={verdict['fail']} DIFF={verdict['diff']} "
        f"INFO={verdict['info']} BLOCKED={verdict['blocked']}"
    )
    print(f"结果：{ART / 'results.json'}")
    return 0 if verdict["fail"] == 0 and verdict["blocked"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
