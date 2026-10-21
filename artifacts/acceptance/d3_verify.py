# -*- coding: utf-8 -*-
"""D5 独立复核：D3 nested 证据重算（只读，不改动 artifacts/interop-nested）。"""
import json
import os
import subprocess
import sys

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
NEST = os.path.join(BASE, "artifacts", "interop-nested")
DELTA = os.path.join(BASE, "_build", "native", "debug", "build", "cmd", "delta", "delta.exe")
out_lines = []
stats = {"PASS": 0, "FAIL": 0}


def check(name, ok, detail=""):
    stats["PASS" if ok else "FAIL"] += 1
    line = "[%s] %s%s" % ("PASS" if ok else "FAIL", name, (" :: " + detail) if detail else "")
    out_lines.append(line)
    print(line)


def load(rel):
    with open(os.path.join(BASE, rel), encoding="utf-8") as f:
        return json.load(f)


def decode(v):
    k = v["kind"]
    if k == "null":
        return None
    if k in ("int64", "int32", "string", "bool", "double", "float"):
        return v["value"]
    if k == "struct":
        return {name: decode(x) for name, x in v["fields"].items()}
    if k == "list":
        return [decode(x) for x in v["items"]]
    if k == "map":
        return {decode(k2): decode(v2) for k2, v2 in v["entries"]}
    raise ValueError("unknown kind " + str(k))


def norm_arrow(v):
    """把 pyarrow to_pylist 的 map（list of tuples）规范成 dict，便于与 oracle 比较。"""
    if isinstance(v, list) and len(v) > 0 and all(isinstance(i, tuple) and len(i) == 2 for i in v):
        return {norm_arrow(k): norm_arrow(x) for k, x in v}
    if isinstance(v, dict):
        return {k: norm_arrow(x) for k, x in v.items()}
    if isinstance(v, list):
        return [norm_arrow(x) for x in v]
    return v


print("== 0. fixture 目录/文件清单 ==")
fixtures = ["engine-written", "kind-struct-only", "kind-array-only", "kind-map-only",
            "checkpoint-nested", "checkpoint-only-nested", "negative-physical-nested", "write-attempt-nested"]
for f in fixtures:
    p = os.path.join(NEST, f)
    check("fixture 存在: %s" % f, os.path.isdir(p))
check("fixture 数量 = 8", sum(1 for f in fixtures if os.path.isdir(os.path.join(NEST, f))) == 8)

print("== 1. 主 fixture schema：struct / 3-level list / map 与 null/empty ==")
log = [json.loads(l) for l in open(os.path.join(NEST, "engine-written", "_delta_log", "00000000000000000000.json"), encoding="utf-8") if l.strip()]
meta = [a["metaData"] for a in log if "metaData" in a][0]
schema = json.loads(meta["schemaString"])
types = {f["name"]: f["type"] for f in schema["fields"]}
check("engine-written schema 含 struct 字段 profile", isinstance(types.get("profile"), dict) and types["profile"]["type"] == "struct")
check("engine-written schema 含 array 字段 tags（containsNull）", isinstance(types.get("tags"), dict) and types["tags"]["type"] == "array" and types["tags"].get("containsNull") is True)
check("engine-written schema 含 map 字段 attrs（valueContainsNull）", isinstance(types.get("attrs"), dict) and types["attrs"]["type"] == "map" and types["attrs"].get("valueContainsNull") is True)
proto = [a["protocol"] for a in log if "protocol" in a][0]
check("engine-written protocol = reader 1 / writer 2", proto.get("minReaderVersion") == 1 and proto.get("minWriterVersion") == 2, str(proto))

import pyarrow.parquet as pq  # noqa: E402
parq = [os.path.join(NEST, "engine-written", n) for n in os.listdir(os.path.join(NEST, "engine-written")) if n.endswith(".parquet")][0]
pf = pq.ParquetFile(parq)
paths = [pf.schema.column(i).path for i in range(pf.metadata.num_columns)]
check("物理 Parquet 为 3-level LIST（tags -> list -> item）", any("tags.list.item" in p or "tags.list.element" in p for p in paths), str(paths))
check("物理 Parquet 为 MAP（attrs -> entries -> key/value）", any("attrs.entries.key" in p or "attrs.key_value.key" in p for p in paths), str(paths))
check("物理 Parquet 为 struct（profile.name/age）", any("profile.name" in p for p in paths) and any("profile.age" in p for p in paths), str(paths))

print("== 2. 独立用 delta-rs 重读 engine-written 并与 oracle-delta-rs.json 比对 ==")
from deltalake import DeltaTable  # noqa: E402
dt = DeltaTable(os.path.join(NEST, "engine-written"))
oracle = load("artifacts/interop-nested/oracle-delta-rs.json")["tables"]["engine-written"]
tbl = dt.to_pyarrow_table()
rows = [norm_arrow(r) for r in tbl.to_pylist()]
for _r in rows:
    if _r["attrs"] == []:
        _r["attrs"] = {}  # pyarrow 对空 map 返回 []，按 schema 归一为空 map
orows = [{k: decode(v) for k, v in r.items()} for r in oracle["rows"]]
check("独立重读行数 = 5（oracle row_count=5）", len(rows) == 5 == oracle["row_count"], "len=%d oracle=%d" % (len(rows), oracle["row_count"]))
check("独立重读逐行逐字段 = oracle（按 id 排序）", sorted(rows, key=lambda r: r["id"]) == sorted(orows, key=lambda r: r["id"]))
check("oracle 自述 schema 与日志 schemaString 等价", oracle["schema_json_equals_log_schemaString"] is True)
check("delta-rs schema 与日志 schemaString 等价（独立重算）",
      json.loads(dt.schema().to_json()) == json.loads(meta["schemaString"]), "schema mismatch" if json.loads(dt.schema().to_json()) != json.loads(meta["schemaString"]) else "")

expect_rows = {
    1: {"id": 1, "profile": {"name": "alice", "age": 30}, "tags": ["red", "green"], "attrs": {"k1": "v1", "k2": "v2"}},
    2: {"id": 2, "profile": None, "tags": [], "attrs": {}},
    3: {"id": 3, "profile": {"name": None, "age": None}, "tags": None, "attrs": {"k": "v"}},
    4: {"id": 4, "profile": {"name": "bob", "age": None}, "tags": ["blue", None], "attrs": {}},
    5: {"id": 5, "profile": {"name": None, "age": 42}, "tags": [], "attrs": None},
}
by_id = {r["id"]: r for r in rows}
check("边界覆盖：profile=NULL(2) / tags=[](2,5) / tags=NULL(3) / tags 含 null(4) / attrs={}(2,4) / attrs=NULL(5)",
      by_id == expect_rows, str(by_id))
check("边界覆盖与 README 表格一致", by_id[2]["profile"] is None and by_id[3]["tags"] is None and by_id[5]["attrs"] is None)

print("== 3. 用重建后的 CLI 复核 moondelta 原始错误文本/退出码 ==")


def run(args):
    p = subprocess.run([DELTA] + args, capture_output=True)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


rc, so, se = run(["read", "artifacts/interop-nested/engine-written"])
raw_out = open(os.path.join(NEST, "raw", "engine-written__read.stdout.txt"), encoding="utf-8").read()
raw_err = open(os.path.join(NEST, "raw", "engine-written__read.stderr.txt"), encoding="utf-8").read()
raw_exit = open(os.path.join(NEST, "raw", "engine-written__read.exit.txt"), encoding="utf-8").read().strip()
msg = "unsupported: nested column 'profile' (type=struct) is not supported by the parquet reader; only flat schemas are readable"
check("重建 CLI 的 read 错误文本 = README 引用 = raw 原文", msg in so and msg in raw_out, so.strip()[:160])
check("重建 CLI read 退出码 = 0（与 raw 记录一致，README 已记录该缺陷）", rc == 0 and raw_exit == "0", "rc=%d raw_exit=%s" % (rc, raw_exit))
check("错误写 stdout、stderr 为空（README 已记录）", se.strip() == "" and raw_err.strip() == "")

for kind, field in (("struct", "profile"), ("array", "tags"), ("map", "attrs")):
    rc, so, _ = run(["read", "artifacts/interop-nested/kind-%s-only" % kind])
    exp = "nested column '%s' (type=%s)" % (field, kind)
    check("单 kind fixture %s 被结构化拒绝（含字段名+kind）" % kind, exp in so and rc == 0, so.strip()[:150])

print("== 4. checkpoint-only-nested：外部引擎可读、本项目拒绝 ==")
cp = DeltaTable(os.path.join(NEST, "checkpoint-only-nested"))
check("delta-rs 从 checkpoint-only-nested 读回 5 行（证明 checkpoint 完整）", cp.to_pyarrow_table().num_rows == 5, "rows=%d" % cp.to_pyarrow_table().num_rows)
rc, so, _ = run(["inspect", "artifacts/interop-nested/checkpoint-only-nested"])
raw_cp = open(os.path.join(NEST, "raw", "checkpoint-only-nested__inspect.stdout.txt"), encoding="utf-8").read()
check("重建 CLI inspect 对 checkpoint-only 报 not found（= raw/README）",
      "error: not found" in so and "has no log entries" in so and "has no log entries" in raw_cp, so.strip()[:160])

print("== 5. 负向 fixture：物理 repeated 被明确拒绝，且未被算作互操作通过 ==")
rc, so, _ = run(["read", "artifacts/interop-nested/negative-physical-nested"])
check("negative-physical-nested 报 Repeated columns are not supported yet",
      "Repeated columns are not supported yet" in so and rc == 0, so.strip()[:200])
neg = load("artifacts/interop-nested/negative-physical-nested.json")
neg_txt = json.dumps(neg, ensure_ascii=False)
check("negative fixture 记录外部引擎同样失败（不作为互操作基线）",
      ("ArrowNotImplementedError" in neg_txt) and ("failed" in neg_txt.lower()))

print("== 6. 七项门槛统计与“负向不是通过”的表述 ==")
comp = load("artifacts/interop-nested/comparison.json")
gc = comp["gate_counts"]
check("comparison.json gate_counts = {done:1,partial:0,missing:6}", gc == {"done": 1, "partial": 0, "missing": 6}, str(gc))
st = [g["status"] for g in comp["gates"]]
check("gates 数组 7 项状态 = 1 完成 / 0 部分 / 6 未完成", st.count("已完成") == 1 and st.count("部分完成") == 0 and st.count("未完成") == 6, str(st))
readme = open(os.path.join(NEST, "README.md"), encoding="utf-8").read()
check("README 写“已完成 1 项 / 部分完成 0 项 / 未完成 6 项”", "已完成 1 项 / 部分完成 0 项 / 未完成 6 项" in readme)
check("README 明确“不是互操作通过”且无正向互操作结论",
      ("不是互操作通过" in readme or "不是**互操作通过**" in readme) and "nested 互操作通过" not in readme.replace("把本目录的负向用例描述成“nested 互操作通过”的写法都是错误的", ""))
check("门槛 7 明确未完成，未把“识别后报错”写成完成", any(g["id"] == 7 and g["status"] == "未完成" for g in comp["gates"]))
check("comparison.json conclusion 明确“能力缺口对照，不是互操作通过”",
      any("不是互操作通过" in c for c in comp["conclusion"]), str(comp["conclusion"])[-2:])

print("\nPASS=%d FAIL=%d" % (stats["PASS"], stats["FAIL"]))
from pathlib import Path  # noqa: E402
out_path = Path("artifacts/acceptance/d3-verify.out")
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
sys.exit(0 if stats["FAIL"] == 0 else 1)



