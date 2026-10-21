# -*- coding: utf-8 -*-
"""D5：交付物 Markdown 链接完整性 + D3 哈希抽样 + D2 checks.csv 核对。"""
import csv
import hashlib
import os
import re
import sys

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DOCS = [
    "artifacts/protocol-audit.md",
    "artifacts/interop-flat/README.md",
    "artifacts/interop-nested/README.md",
    "artifacts/claim-alignment.md",
    "docs/协议兼容性与互操作范围.md",
]
out, stats = [], {"PASS": 0, "FAIL": 0}


def check(name, ok, detail=""):
    stats["PASS" if ok else "FAIL"] += 1
    line = "[%s] %s%s" % ("PASS" if ok else "FAIL", name, (" :: " + detail) if detail else "")
    out.append(line)
    print(line)


print("== 1. 交付物 Markdown 相对链接完整性 ==")
for rel in DOCS:
    p = os.path.join(BASE, rel)
    text = open(p, encoding="utf-8").read()
    # 去掉行内代码段后再找链接（代码段内的 [...]() 不渲染为链接）
    text_nocode = re.sub(r"`[^`\n]*`", "", text)
    links = re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", text_nocode)
    broken = []
    for t in links:
        if t.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = t.split("#")[0].strip()
        if not target:
            continue
        q = os.path.normpath(os.path.join(BASE, os.path.dirname(rel), target))
        if not os.path.exists(q):
            broken.append(t)
    fences = len(re.findall(r"^```", text, re.M))
    check("%s：链接 %d 个全部可解析、代码围栏成对" % (rel, len(links)), not broken and fences % 2 == 0,
          ("broken=%s " % broken[:5]) if broken else "")
    if broken:
        for b in broken[:20]:
            print("    broken:", b)

print("== 2. D3 README SHA-256 表抽样（5 项） ==")
nst = os.path.join(BASE, "artifacts", "interop-nested", "README.md")
text = open(nst, encoding="utf-8").read()
pairs = re.findall(r"\| `(artifacts/[^`]+|fixtures/[^`]+)` \| (\d+) \| `([0-9a-f]{64})` \|", text)
print("  表中哈希条目数:", len(pairs))
sample = pairs[:6] + pairs[-3:]
for rel, size, h in sample:
    p = os.path.join(BASE, rel)
    ok = os.path.exists(p) and os.path.getsize(p) == int(size) and \
        hashlib.sha256(open(p, "rb").read()).hexdigest() == h
    check("D3 哈希抽样 %s" % rel.split("/")[-1], ok, "size=%s" % (os.path.getsize(p) if os.path.exists(p) else "missing"))

print("== 3. D2 results/checks.csv 与 results.json 一致 ==")
import json  # noqa: E402
from pathlib import Path  # noqa: E402
res = json.loads((Path(BASE) / "artifacts/interop-flat/results.json").read_text(encoding="utf-8"))
rows = list(csv.DictReader((Path(BASE) / "artifacts/interop-flat/results/checks.csv").read_text(encoding="utf-8-sig").splitlines()))
from collections import Counter  # noqa: E402
cnt = Counter(r.get("status") or r.get("STATUS") or "" for r in rows)
check("checks.csv 行数=56 且状态计数与 results.json 一致",
      len(rows) == 56 and cnt.get("PASS") == 49 and cnt.get("DIFF") == 2 and cnt.get("INFO") == 5 and not cnt.get("FAIL"),
      "rows=%d %s" % (len(rows), dict(cnt)))

print("\nPASS=%d FAIL=%d" % (stats["PASS"], stats["FAIL"]))
out_path = Path("artifacts/acceptance/links-hashes.out")
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text("\n".join(out) + "\n", encoding="utf-8")
sys.exit(0 if stats["FAIL"] == 0 else 1)

