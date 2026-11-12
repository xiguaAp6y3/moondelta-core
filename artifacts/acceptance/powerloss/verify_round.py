#!/usr/bin/env python3
"""Host-side structural verification of a Delta table recovered after a cut.

This runs on the host against a read-only mount of the *copy* of the data image
that a round was cut on. It answers only "is the recovered table self-consistent
and complete?", never "did the power loss behave correctly"; the round-level
invariant (a committed version either fully exists or does not exist at all) is
decided in `run_powerloss.py` from this report plus the previous round's state.

Checks, all of which must pass:
  * `_delta_log` JSON versions are contiguous from 0;
  * every log file parses and contains only parseable Delta actions;
  * every referenced data file exists and matches its declared size;
  * every leftover intent whose target version exists matches that commit's
    digest (so a surviving intent is explainable, not contradictory);
  * `delta inspect` / `delta read` agree with the structural view;
  * optionally, `deltalake` (delta-rs) reads the same version.

Exits 0 only when there are no problems. Absence of problems is not evidence of
power-loss durability by itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

LOG_NAME = re.compile(r"^(\d{20})\.json$")
ADD_PATH = re.compile(r'"path"\s*:\s*"((?:[^"\\]|\\.)*)"')
ADD_SIZE = re.compile(r'"size"\s*:\s*(\d+)')
TXN_ID = re.compile(r'"moondeltaTxnId"\s*:\s*"([0-9a-f]{32})"')


def list_versions(root: Path) -> list[int]:
    log = root / "_delta_log"
    if not log.is_dir():
        return []
    versions = []
    for entry in log.iterdir():
        match = LOG_NAME.match(entry.name)
        if match:
            versions.append(int(match.group(1)))
    return sorted(versions)


def decode_action_path(raw: str) -> str:
    """JSON-unescape the captured string, then apply Delta's percent-encoding."""
    try:
        text = json.loads('"' + raw + '"')
    except json.JSONDecodeError:
        text = raw
    return urllib.parse.unquote(text)


def parse_log(root: Path) -> dict:
    versions = list_versions(root)
    report = {
        "versions": versions,
        "latest": versions[-1] if versions else None,
        "problems": [],
        "commits": {},
        "leftover_intents": [],
        "leftover_temp_files": [],
    }
    if not versions:
        report["problems"].append("no commit JSON files in _delta_log")
        return report
    if versions[0] != 0:
        report["problems"].append(f"log does not start at version 0 (first is {versions[0]})")
    for index, version in enumerate(versions):
        if index and version != versions[index - 1] + 1:
            report["problems"].append(
                f"log is not contiguous: {versions[index - 1]} then {version}"
            )

    for version in versions:
        path = root / "_delta_log" / f"{version:020d}.json"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            report["problems"].append(f"version {version}: cannot read commit: {error}")
            continue
        actions = 0
        adds = []
        txn_ids = []
        for line in text.replace("\r", "").split("\n"):
            if not line.strip():
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as error:
                report["problems"].append(
                    f"version {version}: line is not valid JSON ({error})"
                )
                continue
            actions += 1
            if line.lstrip().startswith('{"add"') or '"add"' in line:
                match = ADD_PATH.search(line)
                if match:
                    size = ADD_SIZE.search(line)
                    adds.append(
                        {
                            "path": decode_action_path(match.group(1)),
                            "size": int(size.group(1)) if size else None,
                        }
                    )
            match = TXN_ID.search(line)
            if match:
                txn_ids.append(match.group(1))
        if actions == 0:
            report["problems"].append(f"version {version}: commit contains no actions")
        report["commits"][version] = {"actions": actions, "adds": adds, "txnIds": txn_ids}
        for add in adds:
            data = root / add["path"]
            if not data.is_file():
                report["problems"].append(
                    f"version {version}: referenced data file is missing: {add['path']}"
                )
                continue
            actual = data.stat().st_size
            if add["size"] is not None and actual != add["size"]:
                report["problems"].append(
                    f"version {version}: {add['path']} is {actual} bytes, log says {add['size']}"
                )

    intents = root / ".moondelta" / "intents"
    if intents.is_dir():
        for entry in sorted(intents.iterdir()):
            if not entry.is_file():
                continue
            name = entry.name
            # An intent only claims a version once it has been *published* as
            # `<txn>.json`. A `*.moondelta-itmp-*` file is a partially written
            # intent that never claimed anything, so it must not be compared
            # against a commit another transaction may have won.
            temporary = ".moondelta-itmp-" in name or ".moondelta-tmp-" in name
            record = {"file": name, "temporary": temporary}
            try:
                payload = json.loads(entry.read_text(encoding="utf-8"))
                record["txnId"] = payload.get("txnId")
                record["version"] = payload.get("version")
                record["kind"] = payload.get("kind")
                record["commitDigest"] = payload.get("commitDigest")
            except (OSError, json.JSONDecodeError) as error:
                record["unreadable"] = str(error)
                if not temporary:
                    report["problems"].append(
                        f"published intent {name} is unreadable: {error}"
                    )
                report["leftover_intents"].append(record)
                continue
            if temporary:
                record["outcome"] = "unpublished-temporary"
                report["leftover_intents"].append(record)
                continue

            record["nameMatchesTxnId"] = (
                isinstance(record.get("txnId"), str) and name == f"{record['txnId']}.json"
            )
            target = record.get("version")
            commit_info = report["commits"].get(target) if isinstance(target, int) else None
            if commit_info is None:
                # The version never became visible: legal residue for recovery.
                record["outcome"] = "version-absent"
            else:
                owner = (commit_info.get("txnIds") or [None])[0]
                commit = root / "_delta_log" / f"{target:020d}.json"
                digest = hashlib.sha256(commit.read_bytes()).hexdigest()
                record["commitDigestMatches"] = digest == record.get("commitDigest")
                if owner == record.get("txnId"):
                    # Same transaction owns the version: the digest must agree.
                    record["outcome"] = (
                        "same-txn-confirmed" if record["commitDigestMatches"]
                        else "same-txn-digest-mismatch"
                    )
                    if not record["commitDigestMatches"]:
                        report["problems"].append(
                            f"published intent {name} owns version {target} but its "
                            "digest does not match that commit"
                        )
                else:
                    # Another transaction won the version. That is a normal
                    # conflict outcome, not corruption.
                    record["outcome"] = "conflict-lost-to-other-txn"
                if not record["nameMatchesTxnId"]:
                    report["problems"].append(
                        f"published intent {name} is not named after its own txnId"
                    )
            report["leftover_intents"].append(record)

    for path in sorted(root.rglob("*")):
        name = path.name
        if ".moondelta-tmp-" in name or ".moondelta-itmp-" in name:
            report["leftover_temp_files"].append(str(path.relative_to(root)))
    return report


def run_delta_cli(cli: Path, args: list[str], timeout: int = 120) -> dict:
    try:
        completed = subprocess.run(
            [str(cli), *args], capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {"ok": False, "returncode": None, "error": str(error), "stdout": "", "stderr": ""}
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "error": "",
    }


def parse_inspect(output: str) -> dict:
    parsed = {}
    for line in output.splitlines():
        if line.startswith("current version"):
            match = re.search(r"current version\s*:\s*(-?\d+)", line)
            if match:
                parsed["version"] = int(match.group(1))
        elif line.startswith("files"):
            match = re.search(r"files\s*:\s*(\d+)\s+rows:\s*(\d+)", line)
            if match:
                parsed["files"] = int(match.group(1))
                parsed["rows"] = int(match.group(2))
    return parsed


def parse_read(output: str) -> dict:
    match = re.search(r"read\s+(\d+)\s+file\(s\),\s+(\d+)\s+row\(s\),\s+version\s+(-?\d+)", output)
    if not match:
        return {}
    return {
        "files": int(match.group(1)),
        "rows": int(match.group(2)),
        "version": int(match.group(3)),
    }


def verify_table(root: Path, delta_cli: Path | None) -> dict:
    report = parse_log(root)
    report["root"] = str(root)
    report["checks"] = {}
    checks = report["checks"]

    if delta_cli is not None:
        inspect = run_delta_cli(delta_cli, ["inspect", str(root)])
        report["inspect"] = inspect
        checks["delta_inspect"] = inspect["ok"]
        if inspect["ok"]:
            parsed = parse_inspect(inspect["stdout"])
            report["inspectParsed"] = parsed
            if "version" in parsed and parsed["version"] != report["latest"]:
                report["problems"].append(
                    f"delta inspect reports version {parsed['version']} "
                    f"but the log's latest is {report['latest']}"
                )
        else:
            report["problems"].append(
                f"delta inspect failed: {inspect.get('error') or inspect['stderr'].strip()[:400]}"
            )

        read = run_delta_cli(delta_cli, ["read", str(root), "--limit", "5"])
        report["read"] = read
        checks["delta_read"] = read["ok"]
        if read["ok"]:
            parsed = parse_read(read["stdout"])
            report["readParsed"] = parsed
            if parsed and report["latest"] is not None and parsed["version"] != report["latest"]:
                report["problems"].append(
                    f"delta read reports version {parsed['version']} "
                    f"but the log's latest is {report['latest']}"
                )
        else:
            report["problems"].append(
                f"delta read failed: {read.get('error') or read['stderr'].strip()[:400]}"
            )

        recover = run_delta_cli(delta_cli, ["recover", str(root)])
        report["recover"] = recover
        checks["delta_recover_dry_run"] = recover["ok"]

    else:
        checks["delta_inspect"] = None
        checks["delta_read"] = None
        checks["delta_recover_dry_run"] = None

    delta_rs = run_delta_rs(root)
    report["deltaRs"] = delta_rs
    checks["delta_rs"] = delta_rs["status"]

    report["ok"] = not report["problems"]
    return report


def run_delta_rs(root: Path) -> dict:
    """Optional cross-engine read. Recorded, never silently treated as passed."""
    script = (
        "import json,sys\n"
        "from deltalake import DeltaTable\n"
        "t=DeltaTable(sys.argv[1])\n"
        "rows=t.to_pyarrow_table().num_rows\n"
        "print(json.dumps({'version':t.version(),'rows':rows}))\n"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, str(root)],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {"status": "ERROR", "error": str(error)}
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip().splitlines()
        detail = message[-1] if message else "unknown failure"
        if "ModuleNotFoundError" in completed.stderr or "No module named" in detail:
            return {"status": "SKIPPED", "detail": "deltalake is not installed on this host"}
        return {"status": "FAILED", "detail": detail[:400]}
    try:
        return {"status": "PASSED", **json.loads(completed.stdout.strip().splitlines()[-1])}
    except (ValueError, IndexError) as error:
        return {"status": "ERROR", "error": str(error)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="table root inside the mount")
    parser.add_argument("--delta-cli", type=Path, default=None)
    parser.add_argument("--json", type=Path, default=None, help="write the report here")
    args = parser.parse_args()

    report = verify_table(args.root, args.delta_cli)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.json:
        args.json.write_text(text + "\n", encoding="utf-8")
    print(text)
    if not report["ok"]:
        print("VERIFY: FAILED")
        return 1
    print("VERIFY: structurally consistent (this is not S5 evidence by itself)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
