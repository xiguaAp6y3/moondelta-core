#!/usr/bin/env python3
"""Self-test for verify_round.py: does the verifier actually reject damage?

`verify_round.py` is the only thing standing between a corrupted table and a
recorded PASS, so the verifier itself needs negative cases. This builds a real
table with the project writer, checks it verifies clean, then injects four kinds
of damage into copies and asserts each one is reported.

It needs no QEMU, no root and no mount: it operates on table directories.

    python3 test_verifier.py \
        --worker _build/native/debug/build/examples/atomic_commit_worker/atomic_commit_worker.exe \
        --delta-cli _build/native/debug/build/cmd/delta/delta.exe

Exit 0 only when the clean table passes and every damaged copy is rejected.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import verify_round  # noqa: E402

FAILURES: list[str] = []


def report(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(f"{name}{(': ' + detail) if detail else ''}")


def run_worker(worker: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(worker), *args], capture_output=True, text=True, timeout=180
    )


def has(report_dict: dict, needle: str) -> bool:
    return any(needle in problem for problem in report_dict.get("problems", []))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--delta-cli", type=Path, default=None)
    parser.add_argument("--workdir", type=Path, default=None)
    parser.add_argument("--keep", action="store_true", help="keep the scratch tree")
    args = parser.parse_args()

    if not args.worker.is_file():
        print(f"error: worker not found: {args.worker}; run moon build --target native",
              file=sys.stderr)
        return 2
    base = args.workdir or Path(tempfile.mkdtemp(prefix="moondelta-verify-selftest-"))
    if base.exists() and args.workdir:
        shutil.rmtree(base)
    root = base / "table"
    root.parent.mkdir(parents=True, exist_ok=True)

    print("building a real table")
    for command in (["init", str(root), "1"], ["manage", str(root), "1"],
                    ["append", str(root), "4096"], ["append", str(root), "8192"]):
        completed = run_worker(args.worker, command)
        if completed.returncode != 0:
            print(f"error: worker {command[0]} failed: {completed.stdout}{completed.stderr}",
                  file=sys.stderr)
            return 2

    print("positive case")
    clean = verify_round.verify_table(root, args.delta_cli)
    report("clean table verifies", clean["ok"], str(clean["problems"])[:200])
    report("latest version is 2", clean.get("latest") == 2, f"got {clean.get('latest')}")
    parsed = clean.get("inspectParsed") or {}
    report("reader agrees on row count", parsed.get("rows") == 2, f"got {parsed.get('rows')}")
    report(
        "delta-rs cross-read attempted",
        (clean.get("deltaRs") or {}).get("status") in ("PASSED", "SKIPPED"),
        (clean.get("deltaRs") or {}).get("status", "missing"),
    )

    print("negative cases (each must be rejected)")
    cases = {}

    damaged = base / "case-truncated-json"
    shutil.copytree(root, damaged)
    with (damaged / "_delta_log" / f"{2:020d}.json").open("a", encoding="utf-8") as stream:
        stream.write('{"add":{"path":')
    cases["truncated commit JSON"] = (damaged, "not valid JSON")

    damaged = base / "case-missing-data"
    shutil.copytree(root, damaged)
    victim = sorted(damaged.glob("*.parquet"))[0]
    victim.unlink()
    cases["referenced data file deleted"] = (damaged, "referenced data file is missing")

    damaged = base / "case-bad-intent"
    shutil.copytree(root, damaged)
    # The intent must carry the txnId that actually owns version 2, otherwise a
    # mismatch is just "another transaction won that version" (legal) rather than
    # a contradiction. Take the owner from the commit itself.
    commit_text = (damaged / "_delta_log" / f"{2:020d}.json").read_text(encoding="utf-8")
    owner = re.search(r'"moondeltaTxnId"\s*:\s*"([0-9a-f]{32})"', commit_text)
    if owner is None:
        print("error: could not find the commit owner txnId; writer format changed",
              file=sys.stderr)
        return 2
    txn = owner.group(1)
    intent = {
        "formatVersion": 1,
        "txnId": txn,
        "kind": "append",
        "version": 2,
        "dataTempPath": f"x.moondelta-tmp-{txn}",
        "dataPath": "x.parquet",
        "dataLength": 10,
        "dataDigest": "0" * 64,
        "commitTempPath": f"_delta_log/{2:020d}.json.moondelta-tmp-{txn}",
        "commitPath": f"_delta_log/{2:020d}.json",
        "commitDigest": "1" * 64,
    }
    intents = damaged / ".moondelta" / "intents"
    intents.mkdir(parents=True, exist_ok=True)
    (intents / f"{txn}.json").write_text(json.dumps(intent), encoding="utf-8")
    cases["owning intent contradicts its commit"] = (
        damaged, "digest does not match that commit"
    )

    damaged = base / "case-log-gap"
    shutil.copytree(root, damaged)
    (damaged / "_delta_log" / f"{1:020d}.json").unlink()
    cases["log gap"] = (damaged, "not contiguous")

    for name, (path, needle) in cases.items():
        result = verify_round.verify_table(path, args.delta_cli)
        report(name, (not result["ok"]) and has(result, needle),
               f"problems={str(result['problems'])[:160]}")

    # Residue that looks alarming but is legal: an unpublished intent temporary,
    # and a published intent whose version another transaction won. The harness
    # cut rounds mid-flight constantly, so misclassifying these would make every
    # matrix FAIL for the wrong reason.
    print("legal residue (must not be reported as damage)")
    legal = {}
    other = "b" * 32
    template = {
        "formatVersion": 1,
        "txnId": other,
        "kind": "append",
        "version": 2,
        "dataTempPath": f"x.moondelta-tmp-{other}",
        "dataPath": "x.parquet",
        "dataLength": 10,
        "dataDigest": "0" * 64,
        "commitTempPath": f"_delta_log/{2:020d}.json.moondelta-tmp-{other}",
        "commitPath": f"_delta_log/{2:020d}.json",
        "commitDigest": "2" * 64,
    }

    copy = base / "case-intent-temp"
    shutil.copytree(root, copy)
    intents = copy / ".moondelta" / "intents"
    intents.mkdir(parents=True, exist_ok=True)
    (intents / f"{other}.json.moondelta-itmp-{other}").write_text(
        json.dumps(template), encoding="utf-8"
    )
    legal["unpublished intent temporary"] = copy

    copy = base / "case-lost-conflict"
    shutil.copytree(root, copy)
    intents = copy / ".moondelta" / "intents"
    intents.mkdir(parents=True, exist_ok=True)
    (intents / f"{other}.json").write_text(json.dumps(template), encoding="utf-8")
    legal["published intent that lost the version"] = copy

    for name, path in legal.items():
        result = verify_round.verify_table(path, args.delta_cli)
        report(name, result["ok"], f"problems={str(result['problems'])[:200]}")

    if not args.keep and not args.workdir:
        shutil.rmtree(base, ignore_errors=True)

    print()
    if FAILURES:
        print(f"VERIFIER SELFTEST: {len(FAILURES)} failure(s)")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print(
        "VERIFIER SELFTEST: PASS (clean table accepted, 4 damages rejected, "
        "2 legal-residue shapes accepted)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
