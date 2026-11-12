"""Closure check for the two verifier gaps reported in this directory.

`reproduce_verifier_gaps.py` (and `verifier-gap-results.json`) are the record of
the *original* finding: at commit `fa4c2eb` the verifier accepted a same-length
payload edit and a round with a forged delta-rs row count. They are kept
unchanged, and they now fail by design — their assertions say "the gap is still
open".

This script asserts the opposite direction, so the finding has a regression that
keeps failing if the gaps ever come back:

  A. a same-length edit inside a Parquet payload must be rejected once the round
     is verified against the state the host recorded before the cut (and it is
     demonstrably invisible to structure-only verification, which is why the
     expectation is mandatory for a verdict);
  B. a recorded round whose delta-rs row count disagrees with the project
     reader, the recorded expectation and the structural view must be FAILED.

Exit 0 only when both gaps are closed. It needs no QEMU and no root.

    python3 check_verifier_gaps_closed.py \
        --worker _build/native/debug/build/examples/atomic_commit_worker/atomic_commit_worker.exe \
        --delta-cli _build/native/debug/build/cmd/delta/delta.exe
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
POWERLOSS = HERE.parent / "powerloss"
sys.path.insert(0, str(POWERLOSS))

import run_powerloss  # noqa: E402
import verify_round  # noqa: E402


def run_worker(worker: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([str(worker), *args], capture_output=True, text=True, timeout=180)


def as_round(report_dict: dict) -> dict:
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
    parser.add_argument("--delta-cli", type=Path, required=True)
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="moondelta-gap-closure-"))
    result: dict = {"gapsClosed": False, "probes": {}}
    try:
        table = work / "table"
        for command in (["init", str(table), "1"], ["append", str(table), "4096", "1000"],
                        ["append", str(table), "8192", "1001"]):
            completed = run_worker(args.worker, command)
            if completed.returncode != 0:
                print(f"error: {command}: {completed.stdout}{completed.stderr}", file=sys.stderr)
                return 2

        # The pre-cut state: version 1 without version 2's commit and data file.
        before_cut = work / "table-v1"
        shutil.copytree(table, before_cut)
        v2_files = [add["path"] for add in
                    verify_round.parse_log(before_cut)["commits"][2]["adds"]]
        (before_cut / "_delta_log" / f"{2:020d}.json").unlink()
        for name in v2_files:
            (before_cut / name).unlink()
        captured = verify_round.verify_table(before_cut, args.delta_cli, capture_baseline=True)
        baseline = captured.get("baseline")
        if baseline is None:
            print(f"error: baseline capture failed: {captured['problems']}", file=sys.stderr)
            return 2
        expected = dict(baseline)
        expected["appendRow"] = {"id": 1001, "payload": "x" * 8192}

        # A: same-length payload edit.
        tampered = work / "tampered"
        shutil.copytree(table, tampered)
        data = sorted(tampered.glob("*.parquet"))[0]
        raw = data.read_bytes()
        offset = raw.index(b"x" * 64)
        data.write_bytes(raw[:offset] + b"y" + raw[offset + 1:])
        structural = verify_round.verify_table(tampered, args.delta_cli)
        content = verify_round.verify_table(tampered, args.delta_cli, expected=expected)
        probe_a = {
            "bytes": len(raw),
            "changedOffset": offset,
            "structuralOnlyOk": structural["ok"],
            "contentVerificationOk": content["ok"],
            "problems": content["problems"],
            "closed": (not content["ok"])
            and any("content mismatch" in problem for problem in content["problems"]),
        }
        result["probes"]["sameSizePayloadEdit"] = probe_a

        # B: forged delta-rs row count on an otherwise sound round.
        sound = as_round(verify_round.verify_table(table, args.delta_cli, expected=expected))
        run_powerloss.judge_round(sound, base_version=1, require_host_verify=True)
        forged = as_round(copy.deepcopy(sound["verification"]["report"]))
        forged["verification"]["report"]["deltaRs"]["rows"] = 999999
        run_powerloss.judge_round(forged, base_version=1, require_host_verify=True)
        result["probes"]["forgedDeltaRsRows"] = {
            "injectedRows": 999999,
            "soundVerdict": sound["status"],
            "forgedVerdict": forged["status"],
            "forgedReasons": forged["reasons"],
            "closed": sound["status"] == "PASS"
            and forged["status"] == "FAILED"
            and any("row counts disagree" in reason for reason in forged["reasons"]),
        }

        result["gapsClosed"] = all(
            probe.get("closed") for probe in result["probes"].values()
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)

    target = HERE / "verifier-gap-closure.json"
    target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["gapsClosed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
