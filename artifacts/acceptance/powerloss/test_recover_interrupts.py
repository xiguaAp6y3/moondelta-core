#!/usr/bin/env python3
"""S3 matrix: kill the *recoverer itself* at each unlink/directory-sync boundary.

The S1-S5 roadmap requires, for S3:

    "在恢复每个 unlink/fsync 边界再次 SIGKILL，重启重复 recover"

What already existed was "writer interrupted, then recovery runs". That is a
different question: it never interrupts `recover --apply` itself, so the
deletion half of the recovery contract — the part that actually removes files —
was unproven. This test interrupts the recoverer exactly where it deletes:

  * `cleanup-unlinked`    the file is out of the namespace, the directory entry
                          is not synced yet (a real crash window);
  * `cleanup-dir-fsynced` the deletion is durable, the intent is not yet removed.

The boundary is not raced: `src/powerloss_probe.c` announces it on a signal file
and blocks, and this host kills the process there. After one or two such kills
the test re-runs `recover --apply` until it converges and then asserts

  * only the interrupted transaction's own garbage disappeared,
  * every file that existed before the interruption still has the same digest
    (so every historical version is byte-identical),
  * both read engines still see the same content,
  * another `--apply` deletes nothing (idempotent),
  * a writer can append again afterwards.

POSIX only: on Windows the probe is a documented no-op and NTFS gives no
unlink/sync ordering evidence, so the matrix refuses to run there rather than
report a meaningless pass.

    python3 test_recover_interrupts.py \
        --worker _build/native/debug/build/examples/atomic_commit_worker/atomic_commit_worker.exe \
        --delta-cli _build/native/debug/build/cmd/delta/delta.exe
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import verify_round  # noqa: E402

BOUNDARIES = ["cleanup-unlinked", "cleanup-dir-fsynced"]
FAILURES: list[str] = []


def report(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(f"{name}{(': ' + detail) if detail else ''}")


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=300, **kwargs)


def file_map(root: Path) -> dict:
    """digest of every file under the table, excluding transaction residue."""
    digests = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = str(path.relative_to(root)).replace("\\", "/")
        if ".moondelta-tmp-" in path.name or ".moondelta-itmp-" in path.name:
            continue
        if relative.startswith(".moondelta/intents/"):
            continue
        digests[relative] = {
            "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return digests


def craft_residue(root: Path) -> dict:
    """Lay down a crash from a transaction that never committed.

    Version 99 has no commit, so the recovery policy may delete exactly these
    paths: the pinned data temporary, the pinned commit temporary, and the
    intent. Everything else must survive.
    """
    txn = "c" * 32
    data_path = f"part-1-{txn}.parquet"
    data_temp = data_path + f".moondelta-tmp-{txn}"
    commit_path = f"_delta_log/{99:020d}.json"
    commit_temp = commit_path + f".moondelta-tmp-{txn}"
    payload = b"residue" * 64
    (root / data_temp).write_bytes(payload)
    (root / commit_temp).write_text("{\"unfinished\":true}\n", encoding="utf-8")
    intent = {
        "formatVersion": 1,
        "txnId": txn,
        "kind": "append",
        "version": 99,
        "dataTempPath": data_temp,
        "dataPath": data_path,
        "dataLength": len(payload),
        "dataDigest": hashlib.sha256(payload).hexdigest(),
        "commitTempPath": commit_temp,
        "commitPath": commit_path,
        "commitDigest": "d" * 64,
    }
    intents = root / ".moondelta" / "intents"
    intents.mkdir(parents=True, exist_ok=True)
    (intents / f"{txn}.json").write_text(json.dumps(intent), encoding="utf-8")
    return {"txn": txn, "paths": [data_temp, commit_temp], "intent": f"{txn}.json"}


def recover(worker: Path, root: Path, apply: bool, env: dict | None = None):
    arguments = [str(worker), "recover", str(root), "1" if apply else "0"]
    environment = dict(os.environ)
    for key in ("MOONDELTA_POWERLOSS_AT", "MOONDELTA_POWERLOSS_SIGNAL",
                "MOONDELTA_POWERLOSS_PAUSE"):
        environment.pop(key, None)
    if env:
        environment.update(env)
    return arguments, environment


def recover_counts(output: str) -> dict:
    """Parse the worker's one-line recovery summary (`deleted=3 kept=0 ...`)."""
    for line in output.splitlines():
        if line.startswith("recover mode="):
            counts = {}
            for part in line.split()[1:]:
                if "=" in part:
                    key, value = part.split("=", 1)
                    counts[key] = int(value) if value.isdigit() else value
            return counts
    return {}


def interrupt_recover(worker: Path, root: Path, boundary: str, signal_path: Path) -> dict:
    """Run `recover --apply` armed at one boundary and kill it there."""
    signal_path.write_text("", encoding="utf-8")
    arguments, environment = recover(
        worker,
        root,
        True,
        {
            "MOONDELTA_POWERLOSS_AT": boundary,
            "MOONDELTA_POWERLOSS_SIGNAL": str(signal_path),
        },
    )
    process = subprocess.Popen(arguments, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, env=environment)
    announced = None
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            text = signal_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for line in text.splitlines():
            if line.startswith("BOUNDARY"):
                fields = dict(part.split("=", 1) for part in line.split()[1:] if "=" in part)
                announced = fields.get("name")
                break
        if announced:
            break
        time.sleep(0.02)
    killed = False
    if announced:
        process.kill()  # the probe is blocked; this is the crash
        killed = True
    try:
        output = process.communicate(timeout=30)[0] or ""
    except subprocess.TimeoutExpired:
        process.kill()
        output = ""
    return {
        "boundaryRequested": boundary,
        "boundaryAnnounced": announced,
        "killed": killed,
        "output": output[-600:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--delta-cli", type=Path, required=True)
    parser.add_argument("--kills", type=int, default=2,
                        help="interruptions before the converging run (>=1)")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    if os.name == "nt" or sys.platform.startswith("win"):
        print("error: the boundary probe is a no-op on Windows; this matrix is POSIX only",
              file=sys.stderr)
        return 2
    if not args.worker.is_file() or not args.delta_cli.is_file():
        print("error: build the worker and the CLI first (moon build --target native)",
              file=sys.stderr)
        return 2

    base = Path(tempfile.mkdtemp(prefix="moondelta-recover-interrupt-"))
    results: dict = {"boundaries": BOUNDARIES, "kills": args.kills, "scenarios": []}
    try:
        pristine = base / "pristine"
        for command in (["init", str(pristine), "1"], ["manage", str(pristine), "1"],
                        ["append", str(pristine), "4096", "1000"],
                        ["append", str(pristine), "8192", "1001"]):
            completed = run([str(args.worker), *command])
            if completed.returncode != 0:
                print(f"error: {command}: {completed.stdout}{completed.stderr}", file=sys.stderr)
                return 2
        reference = verify_round.verify_table(pristine, args.delta_cli, capture_baseline=True)
        if not reference["ok"]:
            print(f"error: the pristine table does not verify: {reference['problems']}",
                  file=sys.stderr)
            return 2
        reference_digest = reference["baseline"]["capturedBy"]["tableDigest"]
        reference_version = reference["latest"]

        for index, boundary in enumerate(BOUNDARIES, start=1):
            print(f"[{boundary}] interrupt recover --apply {args.kills}x, then converge")
            root = base / f"case-{index}"
            shutil.copytree(pristine, root)
            residue = craft_residue(root)
            before = file_map(root)

            interruptions = []
            for attempt in range(args.kills):
                signal_path = base / f"signal-{index}-{attempt}.txt"
                interruptions.append(
                    interrupt_recover(args.worker, root, boundary, signal_path)
                )
            announced = [item["boundaryAnnounced"] for item in interruptions]
            report(
                f"{boundary}: the recoverer was interrupted at the announced boundary",
                all(name == boundary for name in announced) and all(
                    item["killed"] for item in interruptions),
                f"announced={announced}",
            )

            converge_arguments, converge_environment = recover(args.worker, root, True)
            converged = run(converge_arguments, env=converge_environment)
            report(f"{boundary}: the restarting recoverer succeeds",
                   converged.returncode == 0,
                   (converged.stdout + converged.stderr).strip()[-300:])

            left = [path for path in residue["paths"] if (root / path).exists()]
            intent_left = (root / ".moondelta" / "intents" / residue["intent"]).exists()
            report(f"{boundary}: the interrupted transaction's garbage is gone",
                   not left and not intent_left,
                   f"files={left} intent={intent_left}")

            after = file_map(root)
            report(
                f"{boundary}: every pre-existing file is byte-identical",
                before == after,
                "changed or missing: " +
                str(sorted(set(before) ^ set(after))[:5] or
                    [name for name in before if before[name] != after.get(name)][:5]),
            )

            again_arguments, again_environment = recover(args.worker, root, True)
            again = run(again_arguments, env=again_environment)
            counts = recover_counts(again.stdout)
            report(f"{boundary}: a second apply deletes nothing",
                   again.returncode == 0 and counts.get("deleted") == 0,
                   f"counts={counts} rc={again.returncode}")

            verification = verify_round.verify_table(root, args.delta_cli, capture_baseline=True)
            observed_digest = ((verification.get("baseline") or {}).get("capturedBy") or {}).get(
                "tableDigest")
            report(
                f"{boundary}: both engines still read the same content",
                verification["ok"] and observed_digest == reference_digest,
                f"ok={verification['ok']} version={verification['latest']} "
                f"digest={observed_digest} expected={reference_digest}",
            )

            appended = run([str(args.worker), "append", str(root), "1024", "2000"])
            after_append = verify_round.verify_table(root, args.delta_cli, capture_baseline=True)
            report(
                f"{boundary}: a writer can append after recovery",
                appended.returncode == 0
                and after_append["ok"]
                and after_append["latest"] == reference_version + 1,
                f"rc={appended.returncode} version={after_append['latest']} "
                f"problems={after_append['problems'][:2]}",
            )
            results["scenarios"].append(
                {
                    "boundary": boundary,
                    "interruptions": interruptions,
                    "residue": residue["paths"],
                    "referenceDigest": reference_digest,
                    "referenceVersion": reference_version,
                }
            )
    finally:
        shutil.rmtree(base, ignore_errors=True)

    payload = {
        "formatVersion": 1,
        "harness": "recover-interrupt-matrix",
        "platform": sys.platform,
        "note": (
            "Process-crash evidence for the recovery deletion paths. It is NOT power-loss "
            "evidence: killing the recoverer does not drop a device cache."
        ),
        "kills": args.kills,
        "failures": FAILURES,
        "status": "PASS" if not FAILURES else "FAILED",
        **results,
    }
    if args.json is not None:
        args.json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print()
    if FAILURES:
        print(f"RECOVER INTERRUPT MATRIX: {len(FAILURES)} failure(s)")
        return 1
    print(f"RECOVER INTERRUPT MATRIX: PASS ({len(BOUNDARIES)} boundaries, "
          f"{args.kills} interruption(s) each)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
