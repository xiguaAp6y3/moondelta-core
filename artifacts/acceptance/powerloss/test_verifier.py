#!/usr/bin/env python3
"""Self-test for verify_round.py: does the verifier actually reject damage?

`verify_round.py` is the only thing standing between a corrupted table and a
recorded PASS, so the verifier itself needs negative cases. This builds a real
table with the project writer, checks it verifies clean, then injects damage
into copies and asserts each case is reported.

Two classes of damage are covered, because they fail differently:

  * structural damage (truncated commit, missing data file, contradicting
    intent, log gap) — visible in the log;
  * content damage (a same-length edit inside a Parquet payload, a changed
    schema, a tampered data file) — invisible in the log, so it is only caught
    by comparing against the state the host recorded before the cut.

It also re-judges a recorded round with forged numbers, because the round
verdict — not the table verifier — is what the matrix trusts.

It needs no QEMU, no root and no mount: it operates on table directories.

    python3 test_verifier.py \
        --worker _build/native/debug/build/examples/atomic_commit_worker/atomic_commit_worker.exe \
        --delta-cli _build/native/debug/build/cmd/delta/delta.exe

Exit 0 only when the clean table passes and every damaged copy is rejected.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_powerloss  # noqa: E402
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


def as_round(report_dict: dict) -> dict:
    """Shape a verification report the way run_powerloss records a cut round."""
    parsed = report_dict.get("inspectParsed") or {}
    return {
        "cut": True,
        "boundaryAnnounced": "commit-publish-linked",
        "boundaryRequested": "commit-publish-linked",
        "verification": {
            "performed": True,
            "report": report_dict,
            "problems": list(report_dict.get("problems") or []),
            "observedVersion": report_dict.get("latest"),
            "observedRows": parsed.get("rows"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
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

    # Two appends with different payload sizes and, crucially, different ids: a
    # multiset of identical rows cannot tell "one row lost, one row duplicated"
    # from "nothing happened".
    print("building a real table")
    for command in (["init", str(root), "1"], ["manage", str(root), "1"],
                    ["append", str(root), "4096", "1000"],
                    ["append", str(root), "8192", "1001"]):
        completed = run_worker(args.worker, command)
        if completed.returncode != 0:
            print(f"error: worker {command[0]} failed: {completed.stdout}{completed.stderr}",
                  file=sys.stderr)
            return 2

    print("positive case (structure only)")
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
    report(
        "structure-only verification does not claim content",
        (clean.get("content") or {}).get("ok") is None,
        f"got {(clean.get('content') or {}).get('ok')!r}",
    )

    print("baseline capture (the state a cut must start from)")
    before_cut = base / "table-v1"
    shutil.copytree(root, before_cut)
    added = (before_cut / "_delta_log" / f"{2:020d}.json").read_text(encoding="utf-8")
    v2_files = [add["path"] for add in
                verify_round.parse_log(before_cut)["commits"][2]["adds"]]
    (before_cut / "_delta_log" / f"{2:020d}.json").unlink()
    for name in v2_files:
        (before_cut / name).unlink()
    captured = verify_round.verify_table(before_cut, args.delta_cli, capture_baseline=True)
    baseline = captured.get("baseline")
    report("baseline is recorded and cross-checked", baseline is not None,
           str(captured["problems"])[:200])
    report("baseline version is 1", (baseline or {}).get("baseVersion") == 1,
           f"got {(baseline or {}).get('baseVersion')}")

    expected = dict(baseline or {})
    expected["appendRow"] = {"id": 1001, "payload": "x" * 8192}

    print("positive case (content, commit survived)")
    with_content = verify_round.verify_table(root, args.delta_cli, expected=expected)
    report("clean table matches the recorded expectation", with_content["ok"],
           str(with_content["problems"])[:240])
    content = with_content.get("content") or {}
    report("content branch is 'survived'", content.get("branch") == "survived",
           f"got {content.get('branch')!r}")
    project = content.get("project") or {}
    second = content.get("deltaRs") or {}
    report(
        "both engines report the same row content",
        project.get("tableDigest") is not None
        and project.get("tableDigest") == second.get("tableDigest"),
        f"project {project.get('tableDigest')} vs delta-rs {second.get('tableDigest')}",
    )

    print("negative cases (each must be rejected)")
    cases: dict[str, tuple[Path, str, dict | None]] = {}

    damaged = base / "case-truncated-json"
    shutil.copytree(root, damaged)
    with (damaged / "_delta_log" / f"{2:020d}.json").open("a", encoding="utf-8") as stream:
        stream.write('{"add":{"path":')
    cases["truncated commit JSON"] = (damaged, "not valid JSON", None)

    damaged = base / "case-missing-data"
    shutil.copytree(root, damaged)
    victim = sorted(damaged.glob("*.parquet"))[0]
    victim.unlink()
    cases["referenced data file deleted"] = (damaged, "referenced data file is missing", None)

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
        damaged, "digest does not match that commit", None
    )

    damaged = base / "case-log-gap"
    shutil.copytree(root, damaged)
    (damaged / "_delta_log" / f"{1:020d}.json").unlink()
    cases["log gap"] = (damaged, "not contiguous", None)

    # Content damage: the payload byte changes, the length and the row count do
    # not. This is the case that passed every structural check before.
    damaged = base / "case-same-size-payload"
    shutil.copytree(root, damaged)
    data = sorted(damaged.glob("*.parquet"))[0]
    raw = data.read_bytes()
    offset = raw.index(b"x" * 64)
    data.write_bytes(raw[:offset] + b"y" + raw[offset + 1:])
    cases["same-length payload edit"] = (damaged, "content mismatch", expected)

    # Schema damage: the log declares one more column than the data does.
    damaged = base / "case-schema-change"
    shutil.copytree(root, damaged)
    zero = damaged / "_delta_log" / f"{0:020d}.json"
    text = zero.read_text(encoding="utf-8")
    tampered = text.replace(
        '"schemaString":"',
        '"schemaString":"{\\"type\\":\\"struct\\",\\"fields\\":['
        '{\\"name\\":\\"id\\",\\"type\\":\\"long\\",\\"nullable\\":false,\\"metadata\\":{}},'
        '{\\"name\\":\\"payload\\",\\"type\\":\\"string\\",\\"nullable\\":true,\\"metadata\\":{}},'
        '{\\"name\\":\\"extra\\",\\"type\\":\\"string\\",\\"nullable\\":true,\\"metadata\\":{}}]}"'
        ',"ignoredSchemaString":"',
        1,
    )
    if tampered == text:
        print("error: could not rewrite the schema string; writer format changed",
              file=sys.stderr)
        return 2
    zero.write_text(tampered, encoding="utf-8")
    cases["schema changed after the cut"] = (damaged, "schema", expected)

    # The data-file digest rule: the recorded digest no longer matches the file.
    damaged = base / "case-file-digest"
    shutil.copytree(root, damaged)
    wrong = dict(expected)
    first_path = sorted((expected.get("files") or {}))[0]
    wrong["files"] = dict(expected["files"])
    wrong["files"][first_path] = {
        "size": expected["files"][first_path]["size"],
        "sha256": "0" * 64,
    }
    cases["data file digest no longer matches"] = (
        damaged, "data file changed content", wrong
    )

    for name, (path, needle, case_expected) in cases.items():
        result = verify_round.verify_table(path, args.delta_cli, expected=case_expected)
        report(name, (not result["ok"]) and has(result, needle),
               f"problems={str(result['problems'])[:200]}")

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

    copy_tree = base / "case-intent-temp"
    shutil.copytree(root, copy_tree)
    intents = copy_tree / ".moondelta" / "intents"
    intents.mkdir(parents=True, exist_ok=True)
    (intents / f"{other}.json.moondelta-itmp-{other}").write_text(
        json.dumps(template), encoding="utf-8"
    )
    legal["unpublished intent temporary"] = copy_tree

    copy_tree = base / "case-lost-conflict"
    shutil.copytree(root, copy_tree)
    intents = copy_tree / ".moondelta" / "intents"
    intents.mkdir(parents=True, exist_ok=True)
    (intents / f"{other}.json").write_text(json.dumps(template), encoding="utf-8")
    legal["published intent that lost the version"] = copy_tree

    for name, path in legal.items():
        result = verify_round.verify_table(path, args.delta_cli, expected=expected)
        report(name, result["ok"], f"problems={str(result['problems'])[:200]}")

    print("round verdict (run_powerloss.judge_round)")
    record = as_round(with_content)
    run_powerloss.judge_round(record, base_version=1, require_host_verify=True)
    report("a sound round with matching content passes", record["status"] == "PASS",
           f"{record['status']}: {record.get('reasons')}")

    forged = as_round(copy.deepcopy(with_content))
    forged["verification"]["report"]["deltaRs"]["rows"] = 999999
    run_powerloss.judge_round(forged, base_version=1, require_host_verify=True)
    report(
        "a forged delta-rs row count is rejected",
        forged["status"] == "FAILED"
        and any("row counts disagree" in reason for reason in forged["reasons"]),
        f"{forged['status']}: {forged.get('reasons')}",
    )

    stripped = as_round(copy.deepcopy(with_content))
    stripped["verification"]["report"]["content"] = {
        "ok": None, "problems": [], "branch": None,
    }
    run_powerloss.judge_round(stripped, base_version=1, require_host_verify=True)
    report(
        "a round with no content comparison is rejected",
        stripped["status"] == "FAILED"
        and any("no expected content" in reason for reason in stripped["reasons"]),
        f"{stripped['status']}: {stripped.get('reasons')}",
    )

    if not args.keep and not args.workdir:
        shutil.rmtree(base, ignore_errors=True)

    print()
    if FAILURES:
        print(f"VERIFIER SELFTEST: {len(FAILURES)} failure(s)")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print(
        "VERIFIER SELFTEST: PASS (clean table accepted with and without an "
        f"expectation, {len(cases)} damages rejected, {len(legal)} legal-residue "
        "shapes accepted, forged round verdicts rejected)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
