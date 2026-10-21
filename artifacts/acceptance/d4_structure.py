# -*- coding: utf-8 -*-
"""D5 独立复核：D4 patch 应用后 Markdown 结构 / 链接 / 声明措辞检查（只读仓库）。"""
import os
import re
import sys

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
APPLY = os.path.join(BASE, "artifacts", "acceptance", "apply-check")
FILES = {
    "申报书.md": os.path.join(APPLY, "申报书.md"),
    "README.md": os.path.join(APPLY, "README.md"),
    "docs/ROADMAP.md": os.path.join(APPLY, "docs", "ROADMAP.md"),
}
out = []
stats = {"PASS": 0, "FAIL": 0}


def check(name, ok, detail=""):
    stats["PASS" if ok else "FAIL"] += 1
    line = "[%s] %s%s" % ("PASS" if ok else "FAIL", name, (" :: " + detail) if detail else "")
    out.append(line)
    print(line)


def resolve_link(src_rel, target):
    if target.startswith(("http://", "https://", "mailto:", "#")):
        return None
    target = target.split("#")[0].strip()
    if not target:
        return None
    if target.startswith("/"):
        return os.path.join(BASE, target.lstrip("/"))
    return os.path.normpath(os.path.join(BASE, os.path.dirname(src_rel), target))


print("== 1. 代码围栏 / 表格结构 ==")
for rel, path in FILES.items():
    text = open(path, encoding="utf-8").read()
    fences = re.findall(r"^```", text, re.M)
    check("%s 代码围栏成对（%d 个）" % (rel, len(fences)), len(fences) % 2 == 0)
    tildes = len(re.findall(r"^~~~", text, re.M))
    check("%s 无未闭合 ~~~ 围栏" % rel, tildes % 2 == 0)
    lines = text.splitlines()
    blocks, cur = [], []
    for i, ln in enumerate(lines):
        if "|" in ln and ln.strip().startswith("|"):
            cur.append((i + 1, ln))
        else:
            if cur:
                blocks.append(cur)
                cur = []
    if cur:
        blocks.append(cur)
    bad = []
    for b in blocks:
        if len(b) < 2:
            continue
        sep = b[1][1]
        if not re.match(r"^\|[\s:|\-]+\|?$", sep.strip()):
            continue
        ncol = b[0][1].count("|")
        for lineno, row in b:
            if row.count("|") != ncol:
                bad.append((lineno, row.count("|"), ncol))
    check("%s 表格列数一致（%d 个表格块）" % (rel, len(blocks)), not bad, str(bad[:3]))

print("== 2. 相对链接可解析（以仓库根为准） ==")
for rel, path in FILES.items():
    text = open(path, encoding="utf-8").read()
    links = re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", text)
    broken = []
    for t in links:
        p = resolve_link(rel, t)
        if p and not os.path.exists(p):
            broken.append(t)
    check("%s 相对链接全部存在（%d 个链接）" % (rel, len(links)), not broken, str(broken))

print("== 3. 关键措辞：未检测/缺口不得写成“明确拒绝/已实现” ==")
all_text = {rel: open(p, encoding="utf-8").read() for rel, p in FILES.items()}


def lines_with(rel, pat):
    return [(i + 1, l) for i, l in enumerate(all_text[rel].splitlines()) if re.search(pat, l)]


for rel in FILES:
    for pat, label in [
        (r"删除向量", "删除向量"),
        (r"reader\s*v?2|reader v2|reader≥2|reader v2/v3", "reader v2/v3"),
        (r"截断", "截断日志"),
        (r"退出码", "CLI 退出码"),
        (r"stats", "stats"),
    ]:
        for lineno, line in lines_with(rel, pat):
            bad = re.search(r"(明确拒绝|已明确拒绝|会拒绝|拒绝删除向量)", line) and not re.search(r"(未检测|不校验|不拒绝|不构成|并非|尚未|待修复|已知缺口|不能)", line)
            if bad:
                check("%s:%d 未把 %s 写成明确拒绝" % (rel, lineno, label), False, line.strip()[:160])
                break

print("== 4. nested 门槛 1/7 表述 ==")
for rel in FILES:
    hits = lines_with(rel, r"nested|嵌套")
    joined = " ".join(l for _, l in hits)
    if "门槛" in joined:
        ok = ("1/7" in joined or "1 项" in joined or "已完成 1" in joined) if rel != "docs/ROADMAP.md" else ("门槛" in joined and "不构成 nested" in joined)
        check("%s nested 门槛表述正确" % rel, ok, joined[:200])
    for lineno, line in hits:
        if "不构成" in line or "不构成嵌套" in line:
            check("%s:%d 明确“不构成 nested 互操作”" % (rel, lineno), True, line.strip()[:120])
            break

print("== 5. 未新增未证实声明 ==")
readme_txt = all_text["README.md"]
sb_txt = all_text["申报书.md"]
check("Spark/Databricks 明确标注未验证（README）", "Spark / Databricks 未验证" in readme_txt or "Spark / Databricks 未在本环境验证" in readme_txt)
check("Spark/Databricks 明确标注未验证（申报书）", "Spark / Databricks 未在本环境验证" in sb_txt)
check("申报书保留“moon package 通过”原文（未新增证据，见残留未验证项）", "moon package` 通过" in sb_txt or "moon package 通过" in sb_txt.replace("`", ""))
check("申报书保留 GitHub Actions 声明（未新增证据，见残留未验证项）", "GitHub Actions" in sb_txt)
check("版本 0.1.0 vs 0.1.1 已说明（README）", "0.1.0" in readme_txt and "0.1.1" in readme_txt and "待统一" in readme_txt)
check("已发布 mooncakes 声明未被删除", "mooncakes.io" in sb_txt)

print("== 6. 范围未扩大：无“所有 Delta/任意标准表/完整 nested 互通”类新增 ==")
for rel in FILES:
    bad = [(i + 1, l) for i, l in enumerate(all_text[rel].splitlines())
           if re.search(r"(所有 Delta|任意标准 Delta|完整 nested|nested.*互操作通过|可直接互通)", l)
           and not re.search(r"(不[能是]|未|缺|不构成|不得|不写)", l)]
    check("%s 无无证据的互通/完整声明" % rel, not bad, str(bad)[:300])

print("\nPASS=%d FAIL=%d" % (stats["PASS"], stats["FAIL"]))
from pathlib import Path  # noqa: E402
out_path = Path("artifacts/acceptance/d4-structure.out")
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text("\n".join(out) + "\n", encoding="utf-8")
sys.exit(0 if stats["FAIL"] == 0 else 1)

