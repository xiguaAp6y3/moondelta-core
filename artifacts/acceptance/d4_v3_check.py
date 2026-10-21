# -*- coding: utf-8 -*-
"""D5b：D4 v3 增量复核（apply 后结构、未验证断言残留、三项闭环、9 hunk 映射）。"""
import json
import os
import re
import subprocess
import sys

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
V3 = os.path.join(BASE, "artifacts", "acceptance", "d4-v3-apply-check")
MD = os.path.join(BASE, "artifacts", "claim-alignment.md")
PATCH = os.path.join(BASE, "artifacts", "claim-alignment.patch")
FILES = {
    "申报书.md": os.path.join(V3, "申报书.md"),
    "README.md": os.path.join(V3, "README.md"),
    "docs/ROADMAP.md": os.path.join(V3, "docs", "ROADMAP.md"),
}
out, stats = [], {"PASS": 0, "FAIL": 0}


def check(name, ok, detail=""):
    stats["PASS" if ok else "FAIL"] += 1
    line = "[%s] %s%s" % ("PASS" if ok else "FAIL", name, (" :: " + detail) if detail else "")
    out.append(line)
    print(line)


texts = {rel: open(p, encoding="utf-8").read() for rel, p in FILES.items()}
md = open(MD, encoding="utf-8").read()
patch = open(PATCH, encoding="utf-8").read()

print("== 1. 行数/围栏/表格（lead 声称：申报书52 / README fences10、表格12行 / ROADMAP68） ==")
check("申报书.md 行数=52", len(texts["申报书.md"].splitlines()) == 52, str(len(texts["申报书.md"].splitlines())))
check("README.md 代码围栏=10 且成对", len(re.findall(r"^```", texts["README.md"], re.M)) == 10)
rows = [l for l in texts["README.md"].splitlines() if l.startswith("|")]
check("README.md 表格行（能力边界表）=12", len(rows) == 12, "rows=%d" % len(rows))
check("docs/ROADMAP.md 行数=68", len(texts["docs/ROADMAP.md"].splitlines()) == 68, str(len(texts["docs/ROADMAP.md"].splitlines())))

print("== 2. Markdown 结构（围栏/表格/链接） ==")
for rel, t in texts.items():
    fences = len(re.findall(r"^```", t, re.M))
    check("%s 代码围栏成对" % rel, fences % 2 == 0, "fences=%d" % fences)
    blocks, cur, bad = [], [], []
    for i, ln in enumerate(t.splitlines()):
        if ln.strip().startswith("|"):
            cur.append((i + 1, ln))
        else:
            if cur:
                blocks.append(cur)
                cur = []
    if cur:
        blocks.append(cur)
    for b in blocks:
        if len(b) < 2 or not re.match(r"^\|[\s:|\-]+\|?$", b[1][1].strip()):
            continue
        n = b[0][1].count("|")
        for no, row in b:
            if row.count("|") != n:
                bad.append((no, row.count("|"), n))
    check("%s 表格列数一致" % rel, not bad, str(bad[:3]))
    links = re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", re.sub(r"`[^`\n]*`", "", t))
    broken = []
    for x in links:
        if x.startswith(("http://", "https://", "mailto:", "#")):
            continue
        q = os.path.normpath(os.path.join(BASE, os.path.dirname(rel), x.split("#")[0]))
        if not os.path.exists(q):
            broken.append(x)
    check("%s 相对链接全部存在（%d 个）" % (rel, len(links)), not broken, str(broken))

print("== 3. D4-I1：三项未验证断言已改为待复核（不得残留肯定句） ==")
bad_phrases = [
    r"moon package`?\s*通过",
    r"流水线已通过",
    r"安装并编译验证通过",
    r"已发布[：:]",
    r"warning 全部来自 vendored",
]
for rel, t in texts.items():
    for pat in bad_phrases:
        hits = re.findall(pat, t)
        check("%s 无未验证断言 /%s/" % (rel, pat), not hits, str(hits[:3]))
checks = [
    ("申报书.md", "未在本机复核，发布/申报前需提供", "moon package/CI 已标注待复核"),
    ("申报书.md", "mooncakes.io 发布状态待复核", "mooncakes 发布状态待复核"),
    ("README.md", "发布记录待复核", "README 顶部/安装注释待复核"),
    ("README.md", "远端运行结果未在本机复核", "CI 远端结果待复核"),
    ("docs/ROADMAP.md", "CI 与 mooncakes.io 发布记录待复核", "ROADMAP 发布行待复核"),
]
for rel, needle, name in checks:
    check("%s：%s" % (rel, name), needle in texts[rel], "missing %r" % needle)

print("== 4. D4-I2：warning 统计口径 ==")
needles = ["43 条带文件定位的 warning 中 39 条在项目", "4 条在 vendored parquet",
           "另有 1 条无文件定位的 duplicate alias", "不在这 43 条内"]
for n in needles:
    check("申报书包含口径句：%r" % n, n in texts["申报书.md"])
check("md 记录 D4-I2 修正", "D4-I2" in md and "第 44 条" in md)

print("== 5. D4-I3：121/131 口径 ==")
for n in ["121 份带退出码的 CLI/校验输出", "131 条记录"]:
    check("md 含口径串：%r" % n, n in md)
check("md 记录 D4-I3 修正", "D4-I3" in md)

print("== 6. 未扩大/缩小范围、nested 1/7、不得把未检测写成明确拒绝 ==")
for rel, t in texts.items():
    check("%s 含 nested 1/7 或等效门槛表述" % rel,
          ("1/7" in t) or ("七项门槛" in t) or ("不构成 nested" in t))
    # 删除向量/截断日志/reader>=2 不得被写成“明确拒绝”
    for pat in [r"删除向量[^。；\n]{0,40}明确拒绝", r"截断[^。；\n]{0,40}明确拒绝", r"reader\s*v?2[^。；\n]{0,40}明确拒绝"]:
        check("%s 无“%s”误写" % (rel, pat), not re.search(pat, t))
    bad = [(i + 1, l) for i, l in enumerate(t.splitlines())
           if re.search(r"(所有 Delta|任意标准 Delta|完整 nested.*通过|可直接互通)", l)
           and not re.search(r"(不|未|缺|不得|不能)", l)]
    check("%s 无无证据互通/完整声明" % rel, not bad, str(bad[:3]))

print("== 7. 9 个 hunk 数量/映射 ==")
hunks = re.findall(r"^@@ .*?@@", patch, re.M)
check("patch hunk 数=9", len(hunks) == 9, str(len(hunks)))
check("patch 行数=172", len(patch.splitlines()) == 172, str(len(patch.splitlines())))
check("patch 大小=22063 B", os.path.getsize(PATCH) == 22063, str(os.path.getsize(PATCH)))
targets = re.findall(r"^\+\+\+ b/(.+)$", patch, re.M)
check("patch 目标文件=申报书/README/ROADMAP", targets == ["申报书.md", "README.md", "docs/ROADMAP.md"], str(targets))
check("md 自述 9 hunk / 22063 B / 172 行 / SHA", ("9 个 hunk" in md) and ("22,063 B" in md) and ("172 行" in md))
check("md 版本戳 v3 / 20:40", ("v3" in md.splitlines()[2] or "第三版" in md.splitlines()[0]) and "20:40" in md)
check("md 记录 D4-I1 修正", "D4-I1" in md and "未在本机复核" in md)

print("== 8. 最终哈希（磁盘实测） ==")
import hashlib  # noqa: E402
from pathlib import Path  # noqa: E402
for name, p, claimed in [
    ("claim-alignment.patch", PATCH, "DBDE5AB7E0578C43828ACD5C601A29EB360A20795DB0C9101F14AE108FDEA3EA"),
    ("claim-alignment.md", MD, "0B4BB22025156F9CE3CCB52376055EEE2385D1E05DF3C3F505D142FE988291A7"),
]:
    h = hashlib.sha256(Path(p).read_bytes()).hexdigest()
    check("%s SHA-256 与 Lead 声称一致" % name, h.upper() == claimed.upper(), h.upper())

print("\nPASS=%d FAIL=%d" % (stats["PASS"], stats["FAIL"]))
out_path = Path("artifacts/acceptance/d4-v3-structure.out")
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text("\n".join(out) + "\n", encoding="utf-8")
sys.exit(0 if stats["FAIL"] == 0 else 1)

