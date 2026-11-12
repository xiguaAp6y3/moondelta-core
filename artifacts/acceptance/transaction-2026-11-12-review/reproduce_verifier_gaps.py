"""Acceptance counterexamples; exit 0 means the reported verifier gaps reproduced.

Run under Linux with the acceptance Python environment after building worker/CLI.
Only disposable tables are modified. This is a diagnostic, not a passing S5 test.
"""
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "artifacts/acceptance/powerloss"))
import run_powerloss
import verify_round
import pyarrow.parquet as pq


def main():
    worker = ROOT / "_build/native/debug/build/examples/atomic_commit_worker/atomic_commit_worker.exe"
    cli = ROOT / "_build/native/debug/build/cmd/delta/delta.exe"
    evidence = ROOT / "artifacts/acceptance/powerloss/ci-2026-11-12/results-100.json"
    original = json.loads(evidence.read_text())["rounds"][0]
    forged = copy.deepcopy(original)
    forged["verification"]["report"]["deltaRs"]["rows"] = 999999
    run_powerloss.judge_round(forged, forged["baseVersion"], True)
    result = {"second_reader_wrong_rows": {
        "injectedRows": 999999, "verdict": forged["status"],
        "reasons": forged["reasons"],
    }}
    with tempfile.TemporaryDirectory(prefix="moondelta-review-") as raw:
        table = Path(raw) / "table"
        for args in (["init", str(table), "1"], ["append", str(table), "4096"]):
            completed = subprocess.run([str(worker), *args], capture_output=True, text=True, check=True)
            assert "failed" not in completed.stdout, completed.stdout
        data = next(table.glob("*.parquet"))
        before = pq.read_table(data).to_pylist()
        content = data.read_bytes()
        offset = content.index(b"x" * 64)
        data.write_bytes(content[:offset] + b"y" + content[offset + 1:])
        after = pq.read_table(data).to_pylist()
        assert before != after and len(content) == data.stat().st_size
        verified = verify_round.verify_table(table, cli)
        result["same_size_changed_payload"] = {
            "bytes": len(content), "changedOffset": offset,
            "expectedPayloadPrefix": before[0]["payload"][:16],
            "actualPayloadPrefix": after[0]["payload"][:16],
            "verifierOk": verified["ok"], "problems": verified["problems"],
            "deltaRs": verified["deltaRs"],
        }
        assert verified["ok"] and verified["deltaRs"]["status"] == "PASSED"
    assert forged["status"] == "PASS"
    target = Path(__file__).with_name("verifier-gap-results.json")
    target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
