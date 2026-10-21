# -*- coding: utf-8 -*-
"""D5：验收报告自身的结构/链接自检。"""
import os
import re

p = "artifacts/acceptance-report.md"
t = open(p, encoding="utf-8").read()
fences = len(re.findall(r"^```", t, re.M))
print("lines=", len(t.splitlines()), "fences=", fences, "even=", fences % 2 == 0)
t2 = re.sub(r"`[^`\n]*`", "", t)
links = re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", t2)
broken = []
for x in links:
    if x.startswith(("http://", "https://", "mailto:", "#")):
        continue
    q = os.path.normpath(os.path.join("artifacts", x.split("#")[0]))
    if not os.path.exists(q):
        broken.append(x)
print("links=", len(links), "broken=", broken)
# 表格列数一致性
bad = []
lines = t.splitlines()
block = []
for i, ln in enumerate(lines):
    if ln.strip().startswith("|"):
        block.append((i + 1, ln))
    else:
        if len(block) >= 2 and re.match(r"^\|[\s:|\-]+\|?$", block[1][1].strip()):
            n = block[0][1].count("|")
            for ln_no, row in block:
                if row.count("|") != n:
                    bad.append((ln_no, row.count("|"), n))
        block = []
print("table_bad_rows=", bad[:5])
