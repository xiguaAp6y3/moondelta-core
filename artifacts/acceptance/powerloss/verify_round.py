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

Structure alone is not content: a same-length edit inside a Parquet payload
keeps every structural check green. When the caller supplies `expected` (the
state the *host* recorded before the cut, plus the one row the target append
must contribute), the verifier additionally requires that

  * the recovered schema equals the expected schema,
  * the project reader and delta-rs return the same row count, the same
    per-row content digests and the same schema, and
  * that shared content equals the expected content, row for row,
  * every data file that already existed still has its recorded digest.

Exits 0 only when there are no problems. Absence of problems is not evidence of
power-loss durability by itself.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from decimal import Decimal
from pathlib import Path

LOG_NAME = re.compile(r"^(\d{20})\.json$")
ADD_PATH = re.compile(r'"path"\s*:\s*"((?:[^"\\]|\\.)*)"')
ADD_SIZE = re.compile(r'"size"\s*:\s*(\d+)')
TXN_ID = re.compile(r'"moondeltaTxnId"\s*:\s*"([0-9a-f]{32})"')
READ_SUMMARY = re.compile(r"read\s+(\d+)\s+file\(s\),\s+(\d+)\s+row\(s\),\s+version\s+(-?\d+)")
COLUMN_HEADER = re.compile(r"^(.*) \(([^()]*)\)$")

# Content comparison reads the whole table through both engines. The cap is a
# guard against a pathological table, not a tolerance: hitting it is a failure,
# because a truncated read cannot prove content equality.
MAX_CONTENT_ROWS = 1_000_000
# Cell types whose printed form can be mapped back to an exact value. Anything
# else is refused rather than compared approximately.
DISPLAY_TYPES = {
    "long": "int",
    "int": "int",
    "short": "int",
    "byte": "int",
    "boolean": "bool",
    "double": "float",
    "float": "float",
    "string": "str",
}


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
        "schemaString": None,
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
                action = json.loads(line)
            except json.JSONDecodeError as error:
                report["problems"].append(
                    f"version {version}: line is not valid JSON ({error})"
                )
                continue
            actions += 1
            metadata = action.get("metaData") if isinstance(action, dict) else None
            if isinstance(metadata, dict):
                schema = metadata.get("schemaString")
                if isinstance(schema, str):
                    report["schemaString"] = schema
                else:
                    report["problems"].append(
                        f"version {version}: metaData carries no schemaString"
                    )
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


# --------------------------------------------------------------------------
# content manifests: what the table *contains*, compared across two engines
#
# Structure checks cannot see a same-length edit inside a Parquet payload: the
# length, the version and the row count all stay identical. The manifest below
# is the part that notices, and it is built so that both read engines have to
# produce the *same* canonical rows that the host predicted before the cut.


class ContentError(Exception):
    """A value has no exact comparable form; comparing it would be a guess."""


def jsonable(value):
    """One canonical, JSON-representable form of a cell value."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return {"$float": "nan"}
        if math.isinf(value):
            return {"$float": "inf" if value > 0 else "-inf"}
        return value
    if isinstance(value, (bytes, bytearray)):
        return {"$binary": bytes(value).hex()}
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return {"$time": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    raise ContentError(f"value of type {type(value).__name__} has no canonical form")


def canonical_row(row: dict) -> str:
    canonical = {name: jsonable(value) for name, value in row.items()}
    return json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def row_digest(row: dict) -> str:
    return hashlib.sha256(canonical_row(row).encode("utf-8")).hexdigest()


def row_digests(rows: list[dict]) -> list[str]:
    """Order-independent multiset of row digests.

    Row order across files is an engine detail, so the comparison is a multiset
    comparison: the same rows in any order are the same content, a missing,
    duplicated or altered row is not.
    """
    return sorted(row_digest(row) for row in rows)


def table_digest(digests: list[str]) -> str:
    return hashlib.sha256("\n".join(digests).encode("utf-8")).hexdigest()


def typed_cell(text: str, declared: str):
    """Turn a printed cell back into a value, using its declared type."""
    kind = DISPLAY_TYPES.get(declared)
    if kind is None:
        raise ContentError(f"column type '{declared}' has no exact comparable form")
    if kind == "int":
        try:
            return int(text)
        except ValueError as error:
            raise ContentError(f"'{text[:40]}' is not an integer ({error})") from error
    if kind == "float":
        try:
            return float(text)
        except ValueError as error:
            raise ContentError(f"'{text[:40]}' is not a number ({error})") from error
    if kind == "bool":
        if text in ("true", "false"):
            return text == "true"
        raise ContentError(f"'{text[:40]}' is not a boolean")
    return text


def read_project_rows(cli: Path, root: Path) -> dict:
    """Read every row through the project's own reader (`delta read`)."""
    completed = run_delta_cli(cli, ["read", str(root), "--limit", str(MAX_CONTENT_ROWS)])
    if not completed["ok"]:
        detail = completed.get("error") or (completed["stderr"] or "").strip()[:400]
        return {"status": "FAILED", "detail": f"delta read failed: {detail}"}
    lines = completed["stdout"].replace("\r", "").split("\n")
    if not lines or not lines[0].strip():
        return {"status": "FAILED", "detail": "delta read printed nothing"}
    summary = READ_SUMMARY.search(lines[0])
    if summary is None:
        return {"status": "FAILED", "detail": f"unparsable reader summary: {lines[0][:200]!r}"}
    declared_rows = int(summary.group(2))
    version = int(summary.group(3))
    if declared_rows >= MAX_CONTENT_ROWS:
        return {
            "status": "FAILED",
            "detail": f"table has at least {MAX_CONTENT_ROWS} rows, above the content-read cap",
        }
    if len(lines) < 2:
        return {"status": "FAILED", "detail": "delta read printed no column header"}
    columns = []
    for part in lines[1].split(" | "):
        match = COLUMN_HEADER.match(part)
        if match is None:
            return {"status": "FAILED", "detail": f"unparsable column header: {part[:80]!r}"}
        columns.append({"name": match.group(1), "type": match.group(2).strip()})
    rows: list[dict] = []
    try:
        for line in lines[2:]:
            if not line.strip():
                continue
            cells = line.split(" | ")
            if len(cells) != len(columns):
                return {
                    "status": "FAILED",
                    "detail": (
                        f"row {len(rows)} printed {len(cells)} cells but the header declares "
                        f"{len(columns)} columns; a cell value contains the separator, so the "
                        "row cannot be reconstructed exactly"
                    ),
                }
            rows.append({column["name"]: typed_cell(text, column["type"])
                         for column, text in zip(columns, cells)})
    except ContentError as error:
        return {"status": "FAILED", "detail": str(error)}
    if len(rows) != declared_rows:
        return {
            "status": "FAILED",
            "detail": f"reader announced {declared_rows} rows but printed {len(rows)}",
        }
    return {"status": "PASSED", "version": version, "rows": rows, "columns": columns}


def parse_schema_fields(schema_string: str | None) -> list[dict] | None:
    """Flat struct schema -> comparable fields; None means "cannot compare"."""
    if not isinstance(schema_string, str):
        return None
    try:
        schema = json.loads(schema_string)
    except json.JSONDecodeError:
        return None
    if not isinstance(schema, dict) or schema.get("type") != "struct":
        return None
    fields = []
    for field in schema.get("fields") or []:
        if not isinstance(field, dict):
            return None
        type_ = field.get("type")
        if not isinstance(type_, str):
            return None  # nested type: not comparable field by field
        fields.append(
            {
                "name": field.get("name"),
                "type": type_,
                "nullable": bool(field.get("nullable", True)),
            }
        )
    return fields


def file_digests(root: Path, report: dict) -> dict:
    """SHA-256 of every data file the log references (the disk-level facts)."""
    digests = {}
    for version in sorted(report["commits"]):
        for add in report["commits"][version]["adds"]:
            path = add["path"]
            data = root / path
            if not data.is_file():
                continue  # already reported as a missing referenced file
            digests[path] = {
                "size": data.stat().st_size,
                "sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
            }
    return digests


def compare_content(
    root: Path,
    expected: dict,
    log_report: dict,
    delta_cli: Path | None,
    delta_rs: dict,
) -> dict:
    """Require the recovered table to contain exactly the expected content.

    `expected` is recorded on the host *before* the cut:
        baseVersion, schemaString, rowDigests, files, appendRow
    The recovered version decides which state is required: the baseline alone
    (commit lost) or the baseline plus the one row the target append must add
    (commit survived). Anything else, including any read that cannot be parsed,
    fails closed.
    """
    problems: list[str] = []
    latest = log_report["latest"]
    base_version = expected["baseVersion"]
    baseline_digests = list(expected["rowDigests"])
    if latest == base_version:
        branch = "lost"
        expected_digests = sorted(baseline_digests)
    elif latest == base_version + 1:
        branch = "survived"
        expected_digests = sorted(baseline_digests + [row_digest(expected["appendRow"])])
    else:
        branch = None
        expected_digests = []
        problems.append(
            f"recovered version {latest} is neither {base_version} (commit lost) nor "
            f"{base_version + 1} (commit survived), so there is no expected content for it"
        )

    if log_report.get("schemaString") != expected.get("schemaString"):
        problems.append(
            "schema changed: the recovered log declares a different schema than the host "
            "recorded before the cut"
        )
    expected_fields = parse_schema_fields(expected.get("schemaString"))
    if expected_fields is None:
        problems.append(
            "the recorded baseline schema is not a flat struct, so row content cannot be "
            "compared field by field"
        )

    project = {"status": "SKIPPED"}
    if delta_cli is None:
        problems.append("no project reader was supplied, so content equality was not checked")
    else:
        project = read_project_rows(delta_cli, root)
        if project["status"] != "PASSED":
            problems.append(f"project reader content unavailable: {project.get('detail')}")
        else:
            got = [{"name": c["name"], "type": c["type"]} for c in project["columns"]]
            want = [{"name": f["name"], "type": f["type"]} for f in (expected_fields or [])]
            if expected_fields is not None and got != want:
                problems.append(
                    f"schema mismatch: project reader declares {got} but the recorded "
                    f"baseline declares {want}"
                )
            project_digests = row_digests(project["rows"])
            if branch is not None and (
                len(project["rows"]) != len(expected_digests) or project_digests != expected_digests
            ):
                problems.append(
                    f"content mismatch ({branch}): project reader returned "
                    f"{len(project['rows'])} rows with digest set {table_digest(project_digests)[:16]}, "
                    f"expected {len(expected_digests)} rows with {table_digest(expected_digests)[:16]}"
                )
            project = {
                "status": "PASSED",
                "version": project["version"],
                "rows": len(project["rows"]),
                "rowDigests": project_digests,
                "tableDigest": table_digest(project_digests),
                "columns": project["columns"],
            }

    records = delta_rs.pop("records", None)
    second = {"status": delta_rs.get("status")}
    if delta_rs.get("status") != "PASSED":
        problems.append(
            f"delta-rs cross-read did not pass ({delta_rs.get('status')}), so the second "
            "engine produced no comparable content"
        )
    elif records is None:
        problems.append("delta-rs returned no rows, so its content cannot be compared")
    else:
        # delta-rs reports the Delta schema itself, so both engines' schemas are
        # compared in the same representation the log uses.
        rs_schema = delta_rs.get("schema")
        rs_fields = None
        if rs_schema is not None:
            rs_fields = parse_schema_fields(json.dumps(rs_schema))
        if rs_fields is None:
            problems.append(
                "delta-rs did not report a comparable flat schema, so schema equality "
                "cannot be established"
            )
        elif expected_fields is not None and rs_fields != expected_fields:
            problems.append(
                f"schema mismatch: delta-rs declares {rs_fields} but the recorded baseline "
                f"declares {expected_fields}"
            )
        rs_digests = row_digests(records)
        second = {
            "status": "PASSED",
            "version": delta_rs.get("version"),
            "rows": len(records),
            "rowDigests": rs_digests,
            "tableDigest": table_digest(rs_digests),
            "fields": rs_fields,
        }
        if branch is not None and (
            len(records) != len(expected_digests) or rs_digests != expected_digests
        ):
            problems.append(
                f"content mismatch ({branch}): delta-rs returned {len(records)} rows with "
                f"digest set {table_digest(rs_digests)[:16]}, expected "
                f"{len(expected_digests)} rows with {table_digest(expected_digests)[:16]}"
            )
        if project.get("status") == "PASSED":
            if project["tableDigest"] != second["tableDigest"]:
                problems.append(
                    "the two engines disagree on row content: project reader "
                    f"{project['tableDigest'][:16]} vs delta-rs {second['tableDigest'][:16]}"
                )
            elif project["rows"] != second["rows"]:
                problems.append(
                    f"the two engines disagree on row count: project {project['rows']} vs "
                    f"delta-rs {second['rows']}"
                )
            elif project.get("version") != second.get("version"):
                problems.append(
                    f"the two engines read different versions: project "
                    f"{project.get('version')} vs delta-rs {second.get('version')}"
                )

    files = file_digests(root, log_report)
    for path, recorded in (expected.get("files") or {}).items():
        actual = files.get(path)
        if actual is None:
            continue  # no longer referenced: a removed file, not a damaged one
        if actual["sha256"] != recorded["sha256"]:
            problems.append(
                f"data file changed content after the cut: {path} was "
                f"{recorded['sha256'][:16]} ({recorded['size']} bytes) and is now "
                f"{actual['sha256'][:16]} ({actual['size']} bytes)"
            )

    return {
        "ok": not problems,
        "branch": branch,
        "problems": problems,
        "expectedVersion": latest,
        "expectedRows": len(expected_digests),
        "expectedTableDigest": table_digest(expected_digests) if branch else None,
        "project": project,
        "deltaRs": second,
        "files": files,
    }


def record_baseline(
    root: Path,
    log_report: dict,
    delta_cli: Path | None,
    delta_rs: dict,
    append_row: dict | None = None,
) -> dict | None:
    """Record the state a later cut must start from, and cross-check it.

    Used once, on the clean setup boot: there is no earlier state to compare
    against, so the content *observed* here becomes the expectation for the next
    round. It is still cross-checked, because a baseline taken from a single
    engine would make every later comparison one-sided: delta-rs has to agree on
    every row and every byte of schema, and file digests are recorded here.
    """
    problems: list[str] = []
    project = read_project_rows(delta_cli, root) if delta_cli is not None else {
        "status": "FAILED",
        "detail": "no project reader was supplied",
    }
    if project["status"] != "PASSED":
        log_report["problems"].append(
            f"baseline cannot be recorded: project reader content unavailable: "
            f"{project.get('detail')}"
        )
        return None
    digests = row_digests(project["rows"])
    baseline = {
        "baseVersion": log_report["latest"],
        "schemaString": log_report.get("schemaString"),
        "rowDigests": digests,
        "files": file_digests(root, log_report),
        "appendRow": append_row if append_row is not None else {},
    }
    # A self-comparison, which is not circular where it matters: it forces the
    # second engine to reproduce the same rows and schema before this baseline is
    # allowed to become the expectation for every following round.
    content = compare_content(root, baseline, log_report, delta_cli, delta_rs)
    if not content["ok"]:
        problems.extend(content["problems"])
        log_report["problems"].extend(problems)
        return None
    baseline["capturedBy"] = {
        "rows": len(project["rows"]),
        "tableDigest": table_digest(digests),
        "projectColumns": project["columns"],
        "deltaRsVersion": content["deltaRs"].get("version"),
    }
    return baseline


def verify_table(
    root: Path,
    delta_cli: Path | None,
    expected: dict | None = None,
    capture_baseline: bool = False,
) -> dict:
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
    report["deltaRs"] = {key: value for key, value in delta_rs.items() if key != "records"}
    checks["delta_rs"] = delta_rs["status"]
    if delta_rs["status"] == "PASSED" and report["latest"] is not None:
        if delta_rs.get("version") != report["latest"]:
            report["problems"].append(
                f"delta-rs reads version {delta_rs.get('version')} but the log's "
                f"latest is {report['latest']}"
            )

    if expected is not None:
        content = compare_content(root, expected, report, delta_cli, delta_rs)
        report["content"] = content
        report["problems"].extend(content["problems"])
    elif capture_baseline:
        baseline = record_baseline(root, report, delta_cli, delta_rs)
        report["baseline"] = baseline
        report["content"] = {
            "ok": baseline is not None,
            "branch": "captured" if baseline is not None else None,
            "problems": [],
        }
        if baseline is None:
            report["problems"].append(
                "the setup boot's baseline could not be recorded, so no later round has "
                "an expectation to compare against"
            )
    else:
        # Structural verification only. Whoever uses this as acceptance evidence
        # must pass `expected`: structure alone cannot detect a content edit.
        report["content"] = {"ok": None, "problems": [], "branch": None}

    report["ok"] = not report["problems"]
    return report


def run_delta_rs(root: Path) -> dict:
    """Second-engine read via delta-rs.

    delta-rs/pyarrow can abort during interpreter teardown ("terminate called
    without an active exception") *after* the read has succeeded, which makes a
    naive returncode check report a false failure. The result is therefore
    handed over through a file and the child exits with os._exit() to skip
    finalisation.
    """
    handle, raw_path = tempfile.mkstemp(prefix="moondelta-delta-rs-", suffix=".json")
    os.close(handle)
    result_path = Path(raw_path)
    script = (
        "import json, os, sys\n"
        "out = sys.argv[2]\n"
        "try:\n"
        "    from deltalake import DeltaTable\n"
        "except ModuleNotFoundError:\n"
        "    open(out, 'w').write(json.dumps({'status': 'SKIPPED', "
        "'detail': 'deltalake is not installed'}))\n"
        "    os._exit(0)\n"
        "import datetime, decimal\n"
        "\n"
        "def enc(value):\n"
        "    if value is None or isinstance(value, (bool, int, str)):\n"
        "        return value\n"
        "    if isinstance(value, float):\n"
        "        if value != value:\n"
        "            return {'$float': 'nan'}\n"
        "        if value in (float('inf'), float('-inf')):\n"
        "            return {'$float': 'inf' if value > 0 else '-inf'}\n"
        "        return value\n"
        "    if isinstance(value, (bytes, bytearray)):\n"
        "        return {'$binary': bytes(value).hex()}\n"
        "    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):\n"
        "        return {'$time': value.isoformat()}\n"
        "    if isinstance(value, decimal.Decimal):\n"
        "        return {'$decimal': str(value)}\n"
        "    if isinstance(value, (list, tuple)):\n"
        "        return [enc(item) for item in value]\n"
        "    if isinstance(value, dict):\n"
        "        return {str(k): enc(v) for k, v in value.items()}\n"
        "    raise TypeError('no canonical form for ' + type(value).__name__)\n"
        "\n"
        "try:\n"
        "    table = DeltaTable(sys.argv[1])\n"
        "    arrow = table.to_pyarrow_table()\n"
        "    records = [{k: enc(v) for k, v in row.items()} for row in arrow.to_pylist()]\n"
        "    payload = {\n"
        "        'version': table.version(),\n"
        "        'rows': len(records),\n"
        "        'records': records,\n"
        "        'schema': table.schema().json(),\n"
        "    }\n"
        "    open(out, 'w').write(json.dumps(payload))\n"
        "    os._exit(0)\n"
        "except Exception as error:\n"
        "    open(out, 'w').write(json.dumps({'error': str(error)[:400]}))\n"
        "    os._exit(0)\n"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, str(root), str(result_path)],
            capture_output=True, text=True, timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as error:
        result_path.unlink(missing_ok=True)
        return {"status": "ERROR", "error": str(error)}
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "status": "ERROR",
            "detail": (completed.stderr or completed.stdout).strip()[-400:],
            "returncode": completed.returncode,
        }
    finally:
        result_path.unlink(missing_ok=True)
    if "error" in payload:
        return {"status": "FAILED", "detail": payload["error"],
                "returncode": completed.returncode}
    if payload.get("status") == "SKIPPED":
        return payload
    return {"status": "PASSED", "returncode": completed.returncode, **payload}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="table root inside the mount")
    parser.add_argument("--delta-cli", type=Path, default=None)
    parser.add_argument("--json", type=Path, default=None, help="write the report here")
    parser.add_argument(
        "--expected",
        type=Path,
        default=None,
        help="host-recorded expectation (baseline + the row the cut append must add); "
             "without it only structure is checked, never content",
    )
    args = parser.parse_args()

    expected = None
    if args.expected is not None:
        expected = json.loads(args.expected.read_text(encoding="utf-8"))
    report = verify_table(args.root, args.delta_cli, expected)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.json:
        args.json.write_text(text + "\n", encoding="utf-8")
    print(text)
    if not report["ok"]:
        print("VERIFY: FAILED")
        return 1
    if expected is None:
        print("VERIFY: structurally consistent (content was NOT compared)")
    else:
        print(
            "VERIFY: structure and content match the recorded expectation "
            f"(branch {report['content']['branch']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
