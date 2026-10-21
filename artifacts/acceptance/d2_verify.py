# -*- coding: utf-8 -*-
"""D5 独立复核：D2 flat 互操作证据重算（只读，不改动 artifacts/interop-flat）。"""
import hashlib
import json
import os
import sys
from pathlib import Path

BASE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
FLAT = os.path.join(BASE, "artifacts", "interop-flat")
out_lines = []
results = {"PASS": 0, "FAIL": 0, "DETAILS": []}


def check(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    results[status] += 1
    line = "[%s] %s%s" % (status, name, (" :: " + detail) if detail else "")
    out_lines.append(line)
    print(line)


def read_json(rel):
    with open(os.path.join(BASE, rel), encoding="utf-8") as f:
        return json.load(f)


print("== 0. results.json 计数 ==")
res = read_json("artifacts/interop-flat/results.json")
v = res["verdict"]
checks = res["checks"]
check("results.verdict 与 README 一致 (pass=49)", v == {"pass": 49, "fail": 0, "diff": 2, "info": 5, "blocked": 0}, json.dumps(v))
check("checks 总数=56 且状态计数一致", len(checks) == 56 and
      sum(1 for c in checks if c["status"] == "PASS") == 49 and
      sum(1 for c in checks if c["status"] == "FAIL") == 0 and
      sum(1 for c in checks if c["status"] == "DIFF") == 2 and
      sum(1 for c in checks if c["status"] == "INFO") == 5,
      "len=%d" % len(checks))

readme = open(os.path.join(FLAT, "README.md"), encoding="utf-8").read()
check("README 写 49 PASS / 0 FAIL / 2 DIFF / 5 INFO",
      "49 PASS" in readme and "0 FAIL" in readme and "2 DIFF" in readme and "5 INFO" in readme)

print("== 1. delta-rs 独立读 moondelta-written（C 方向） ==")
from deltalake import DeltaTable  # noqa: E402

EXPECTED = [(1, "alice", True), (2, None, False), (3, "bob", None), (4, "carol", True), (5, None, None)]

mo = DeltaTable(os.path.join(FLAT, "moondelta-written"))
mo_proto = mo.protocol()
check("moondelta-written protocol reader=1 writer=2",
      mo_proto.min_reader_version == 1 and mo_proto.min_writer_version == 2,
      "reader=%s writer=%s" % (mo_proto.min_reader_version, mo_proto.min_writer_version))
check("moondelta-written version=2 (3 commits)", mo.version() == 2, "version=%s" % mo.version())
mo_tbl = mo.to_pyarrow_table()
mo_rows = [(r['id'], r['name'], r['active']) for r in mo_tbl.to_pylist()]
check("delta-rs 读 moondelta-written 5 行且逐值一致（多重集/按 id 排序比较）", sorted(mo_rows, key=lambda r: r[0]) == EXPECTED, str(mo_rows))
schema_names = [(f.name, str(f.type), f.nullable) for f in mo.schema().fields]
check("delta-rs 读 moondelta-written schema = id long NOT NULL/name string/active boolean",
      [n for (n, _, _) in schema_names] == ["id", "name", "active"] and schema_names[0][2] is False,
      str(schema_names))

print("== 2. delta-rs 独立读 engine-written（B 方向 oracle） ==")
en = DeltaTable(os.path.join(FLAT, "engine-written"))
en_rows = [(r['id'], r['name'], r['active']) for r in en.to_pyarrow_table().to_pylist()]
check("delta-rs 读 engine-written 5 行且逐值一致（多重集/按 id 排序比较）", sorted(en_rows, key=lambda r: r[0]) == EXPECTED, str(en_rows))
check("engine-written protocol reader=1 writer=2", en.protocol().min_reader_version == 1 and en.protocol().min_writer_version == 2)

print("== 3. DuckDB delta_scan 独立读两表 ==")
import duckdb  # noqa: E402

con = duckdb.connect()
try:
    try:
        con.execute("LOAD delta")
    except Exception:
        con.execute("INSTALL delta")
        con.execute("LOAD delta")
    for label, path in (("engine-written", os.path.join(FLAT, "engine-written")),
                        ("moondelta-written", os.path.join(FLAT, "moondelta-written"))):
        rows = con.execute("SELECT * FROM delta_scan(?) ORDER BY id", [path.replace("\\", "/")]).fetchall()
        rows = [tuple(r) for r in rows]
        check("DuckDB delta_scan(%s) 5 行逐值一致" % label, rows == EXPECTED, str(rows))
    ext = con.execute("SELECT extension_version FROM duckdb_extensions() WHERE extension_name='delta'").fetchall()
    check("DuckDB delta 扩展可用", bool(ext), str(ext))
except Exception as e:  # noqa: BLE001
    check("DuckDB delta_scan 两表", False, "exception: %r" % (e,))

print("== 4. 日志事实：protocol / 提交数 / stats ==")
def logjson(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]

mw0 = logjson(os.path.join(FLAT, "moondelta-written", "_delta_log", "00000000000000000000.json"))
proto = [a["protocol"] for a in mw0 if "protocol" in a]
check("moondelta-written v0 protocol 原文 = {1,2}",
      proto and proto[0] == {"minReaderVersion": 1, "minWriterVersion": 2}, str(proto))
mw1 = logjson(os.path.join(FLAT, "moondelta-written", "_delta_log", "00000000000000000001.json"))
stats = [json.loads(a["add"]["stats"]) for a in mw1 if "add" in a]
check("moondelta-written v1 add.stats 仅 numRecords+id min/max（与 README E-DIFF 一致）",
      len(stats) == 1 and set(stats[0].keys()) == {"numRecords", "minValues", "maxValues"} and
      set(stats[0]["minValues"].keys()) == {"id"}, str(stats))
ew0 = logjson(os.path.join(FLAT, "engine-written", "_delta_log", "00000000000000000000.json"))
ew_proto = [a["protocol"] for a in ew0 if "protocol" in a]
check("engine-written v0 protocol 原文 = {1,2}", ew_proto and ew_proto[0] == {"minReaderVersion": 1, "minWriterVersion": 2}, str(ew_proto))

print("== 5. hashes.txt 重算（全部 79 项 + 前 10 抽样） ==")
manifest_path = Path(FLAT) / "hashes.txt"
hash_lines = [l.strip() for l in manifest_path.read_text(encoding="utf-8").splitlines() if l.strip()]
check("hashes.txt 条目数 = 79", len(hash_lines) == 79, "n=%d" % len(hash_lines))
bad = []
base_real = Path(BASE).resolve()
for i, line in enumerate(hash_lines):
    h, rel = line.split("  ", 1)
    p = (base_real / rel).resolve()
    if p != base_real and base_real not in p.parents:
        bad.append((rel, "outside-base"))
        continue
    if not p.is_file():
        bad.append((rel, "missing"))
        continue
    actual = hashlib.sha256(p.read_bytes()).hexdigest()
    if actual != h:
        bad.append((rel, "hash-mismatch"))
    if i < 10:
        print("  sample[%d] %s %s" % (i, "OK" if actual == h else "MISMATCH", rel))
check("hashes.txt 全部 79 项可重算一致", not bad, str(bad[:5]))

print("\n== 汇总 ==")
print(json.dumps(results["PASS" if False else "DETAILS"]))
print("PASS=%d FAIL=%d" % (results["PASS"], results["FAIL"]))
out_path = Path("artifacts/acceptance/d2-verify.out")
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
sys.exit(0 if results["FAIL"] == 0 else 1)


