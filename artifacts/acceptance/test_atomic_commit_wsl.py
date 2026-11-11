#!/usr/bin/env python3
"""Process-level atomic commit checks for Linux local filesystems."""
from __future__ import annotations

import os
import argparse
import errno
import fcntl
import hashlib
import json
import platform
import re
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import tempfile
import time
import threading
import sys

import deltalake
import pyarrow
import pyarrow.parquet as pq
from deltalake import DeltaTable

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "_build/native/debug/build/examples/atomic_commit_worker/atomic_commit_worker.exe"
GATE_SOURCE = ROOT / "artifacts/acceptance/atomic_write_gate.c"


def run(table: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(WORKER), *args], cwd=table.parent, text=True, capture_output=True, env=env, timeout=30)


def versions(table: Path) -> list[int]:
    paths = sorted((table / "_delta_log").glob("[0-9]*.json"))
    return [int(path.stem) for path in paths]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def read_rows(table: Path, version: int | None = None) -> list[dict]:
    return DeltaTable(str(table), version=version).to_pyarrow_dataset().to_table(use_threads=False).to_pylist()


def init(table: Path) -> None:
    result = run(table, "init", str(table), "1")
    require("created version 0" in result.stdout and versions(table) == [0], f"init failed: {result}")


def check_row(table: Path, value: int, version: int = 1) -> None:
    require(read_rows(table, version) == [{"id": value, "payload": "x" * value}], "row/schema/nullability readback changed")
    result = run(table, "read", str(table), str(version))
    require(result.stdout.splitlines() == [f"version={version} rows=1", str(value)], f"native readback failed: {result}")


def fault_cases(base: Path, library: Path) -> list[dict]:
    # expected = absent, data-published, commit-unknown, or committed.
    # Temporary names are pinned by the persisted intent: intent publication uses
    # `.moondelta-itmp-`, data/commit staging uses `.moondelta-tmp-`, so each
    # phase can be failed independently.
    cases = [
        ("intent-write-eio", "write", ".moondelta-itmp-", False, 1, errno.EIO, "absent"),
        ("intent-write-enospc", "write", ".moondelta-itmp-", False, 1, errno.ENOSPC, "absent"),
        ("intent-file-sync", "fsync", ".moondelta-itmp-", False, 1, errno.EIO, "absent"),
        ("intent-link", "link", ".moondelta/intents/", False, 1, errno.EIO, "absent"),
        ("intent-dir-sync", "fsync", "INTENT_DIR", True, 1, errno.EIO, "absent"),
        ("data-write-eio", "write", ".parquet.moondelta-tmp-", False, 1, errno.EIO, "absent"),
        ("data-write-enospc", "write", ".parquet.moondelta-tmp-", False, 1, errno.ENOSPC, "absent"),
        ("data-file-sync", "fsync", ".parquet.moondelta-tmp-", False, 1, errno.EIO, "absent"),
        ("data-link", "link", ".parquet", False, 1, errno.EIO, "absent"),
        ("data-dir-sync", "fsync", "DATA_DIR", True, 1, errno.EIO, "data-published"),
        ("log-write", "write", ".json.moondelta-tmp-", False, 1, errno.EIO, "absent"),
        ("log-file-sync", "fsync", ".json.moondelta-tmp-", False, 1, errno.EIO, "absent"),
        ("log-link", "link", "_delta_log/00000000000000000001.json", False, 1, errno.EIO, "absent"),
        ("log-dir-sync", "fsync", "LOG_DIR", True, 1, errno.EIO, "commit-unknown"),
        ("log-dir-enospc", "fsync", "LOG_DIR", True, 1, errno.ENOSPC, "commit-unknown"),
        ("log-cleanup-sync", "fsync", "LOG_DIR", True, 2, errno.EIO, "committed"),
        ("data-temp-unlink", "unlink", ".parquet.moondelta-tmp-", False, 1, errno.EACCES, "committed"),
        ("log-temp-unlink", "unlink", ".json.moondelta-tmp-", False, 1, errno.EACCES, "committed"),
        ("data-sync-eintr", "fsync", ".parquet.moondelta-tmp-", False, 1, errno.EINTR, "committed"),
        ("log-dir-eintr", "fsync", "LOG_DIR", True, 1, errno.EINTR, "committed"),
    ]
    results = []
    for name, op, match, exact, nth, error, expected in cases:
        table = base / name
        init(table)
        env = os.environ.copy()
        trace = base / f"{name}.trace"
        env.update(LD_PRELOAD=str(library), MOONDELTA_FAIL_OP=op,
                   MOONDELTA_TRACE=str(trace),
                   MOONDELTA_FAIL_MATCH=str(table) if match == "DATA_DIR" else str(table / "_delta_log") if match == "LOG_DIR" else str(table / ".moondelta/intents") if match == "INTENT_DIR" else match,
                   MOONDELTA_FAIL_NTH=str(nth), MOONDELTA_FAIL_ERRNO=str(error))
        if exact:
            env["MOONDELTA_FAIL_EXACT"] = "1"
        result = run(table, "append", str(table), "17", env=env)
        events = trace.read_text().splitlines()
        require(sum(event.startswith(f"fail-{op} ") for event in events) == 1, f"{name}: fault injection was not exercised exactly once")
        committed = expected in ("committed", "commit-unknown")
        require(result.returncode == 0, f"{name}: worker crashed: {result}")
        require(versions(table) == ([0, 1] if committed else [0]), f"{name}: invalid versions")
        for path in (table / "_delta_log").glob("*.json"):
            actions = [json.loads(line) for line in path.read_text().splitlines()]
            require(bool(actions), f"{name}: empty formal commit")
        if committed:
            check_row(table, 17)
            if expected == "commit-unknown":
                require("commit outcome unknown:" in result.stdout and "appended version" not in result.stdout, f"{name}: false success or false safe failure: {result.stdout}")
            else:
                require("appended version 1" in result.stdout, f"{name}: cleanup invalidated committed append: {result.stdout}")
        else:
            require(read_rows(table) == [], f"{name}: changed old snapshot")
            require("appended version" not in result.stdout, f"{name}: false success")
            if expected == "data-published":
                require("publish outcome unknown:" in result.stdout, f"{name}: lost publication phase")
            retry = run(table, "append", str(table), "18")
            require("appended version 1" in retry.stdout, f"{name}: retry failed: {retry}")
            check_row(table, 18)
        results.append({"name": name, "status": "PASS", "expected": expected, "stdout": result.stdout.strip(), "syscalls": events})
    print(f"PASS: {len(results)} syscall/intent failure, uncertain-outcome and cleanup cases")
    return results


def creation_failures(base: Path, library: Path) -> list[dict]:
    results = []
    for name, match_parent, outcome in [
        ("create-table-dir", "base", "absent"),
        ("create-log-dir", "table", "absent"),
        ("create-v0-sync", "log", "commit-unknown"),
    ]:
        table = base / name
        match = base if match_parent == "base" else table if match_parent == "table" else table / "_delta_log"
        trace = base / f"{name}.trace"
        env = os.environ.copy()
        env.update(LD_PRELOAD=str(library), MOONDELTA_FAIL_OP="fsync", MOONDELTA_FAIL_MATCH=str(match),
                   MOONDELTA_FAIL_EXACT="1", MOONDELTA_TRACE=str(trace))
        result = run(table, "init", str(table), "1", env=env)
        require("created version" not in result.stdout and "create failed:" in result.stdout, f"{name}: false success")
        events = trace.read_text().splitlines()
        require(sum(event.startswith("fail-fsync ") for event in events) == 1, f"{name}: fault was not exercised")
        if outcome == "absent":
            require(not (table / "_delta_log/00000000000000000000.json").exists(), f"{name}: ignored failed ancestor sync")
            init(table)
        else:
            require("commit outcome unknown:" in result.stdout and versions(table) == [0], f"{name}: lost uncertain outcome")
        require(read_rows(table) == [], f"{name}: table recovery failed")
        results.append({"name": name, "status": "PASS", "expected": outcome, "stdout": result.stdout.strip(), "syscalls": events})
    print("PASS: table/log directory creation failure and version-0 uncertain outcome")
    return results


def publication_order(base: Path, library: Path) -> list[str]:
    table = base / "ordering"
    init(table)
    trace = base / "ordering.trace"
    env = os.environ.copy()
    env.update(LD_PRELOAD=str(library), MOONDELTA_TRACE=str(trace))
    result = run(table, "append", str(table), "19", env=env)
    require("appended version 1" in result.stdout, result.stdout)
    events = trace.read_text().splitlines()
    intent_write = next(i for i, event in enumerate(events) if event.startswith("write ") and ".moondelta-itmp-" in event)
    intent_sync = next(i for i, event in enumerate(events) if event.startswith("fsync ") and ".moondelta-itmp-" in event)
    intent_link = next(i for i, event in enumerate(events) if event.startswith("link ") and ".moondelta/intents/" in event)
    intent_dir = events.index(f"fsync {table / '.moondelta/intents'}")
    data_sync = next(i for i, event in enumerate(events) if event.startswith("fsync ") and ".parquet.moondelta-tmp-" in event)
    data_link = next(i for i, event in enumerate(events) if event.startswith("link ") and event.endswith(".parquet"))
    data_dir = events.index(f"fsync {table}")
    log_sync = next(i for i, event in enumerate(events) if event.startswith("fsync ") and ".json.moondelta-tmp-" in event)
    log_link = next(i for i, event in enumerate(events) if event.startswith("link ") and "/_delta_log/" in event and event.endswith(".json"))
    log_dir = events.index(f"fsync {table / '_delta_log'}")
    require(intent_write < intent_sync < intent_link < intent_dir, f"intent was not persisted before managed data: {events}")
    require(intent_dir < data_sync < data_link < data_dir < log_sync < log_link < log_dir, f"wrong durability order: {events}")
    print("PASS: intent sync -> intent link -> intent directory sync -> data-file sync -> data link -> data-directory sync -> log-file sync -> log link -> log-directory sync")
    return events


def maintenance_lock_blocks_writer(base: Path) -> None:
    table = base / "maintenance-lock"
    init(table)
    lock_path = table / ".moondelta.lock"
    fd = os.open(lock_path, os.O_RDWR)
    child = None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        child = subprocess.Popen([str(WORKER), "append", str(table), "27"], cwd=base,
                                 text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(0.2)
        require(child.poll() is None, "writer did not wait for exclusive maintenance lock")
        require(versions(table) == [0] and read_rows(table) == [], "locked writer changed the snapshot")
        fcntl.flock(fd, fcntl.LOCK_UN)
        stdout, stderr = child.communicate(timeout=30)
        require(child.returncode == 0 and "appended version 1" in stdout,
                f"writer did not resume after maintenance lock: {stdout}{stderr}")
        check_row(table, 27)
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        if child is not None and child.poll() is None:
            child.kill()
            child.wait()
    print("PASS: append waits while the fixed table maintenance lock is held exclusively")


def maintenance_lock_rejects_symlink(base: Path) -> None:
    table = base / "maintenance-lock-symlink"
    init(table)
    lock_path = table / ".moondelta.lock"
    lock_path.unlink()
    external = base / "lock-target"
    external.write_text("preserve")
    lock_path.symlink_to(external)
    result = run(table, "append", str(table), "28")
    require(result.returncode == 0 and "cannot acquire table maintenance lock" in result.stdout,
            f"symlink lock was not rejected: {result}")
    require(versions(table) == [0] and read_rows(table) == [], "rejected lock changed table data")
    require(external.read_text() == "preserve", "symlink lock target was modified")
    print("PASS: symlink maintenance locks are rejected without touching their target")


def deterministic_race(base: Path, library: Path, iteration: int) -> None:
    table = base / f"race-{iteration}"
    init(table)
    writers = []
    releases = []
    ready_files = []
    errors = []
    observed = set()
    stop = threading.Event()

    def reader() -> None:
        try:
            while not stop.is_set():
                dt = DeltaTable(str(table))
                version = dt.version()
                rows = dt.to_pyarrow_dataset().to_table(use_threads=False).to_pylist()
                require((version == 0 and rows == []) or (version == 1 and rows == [{"id": 31, "payload": "x" * 31}]), f"partial snapshot: v{version}, {rows}")
                observed.add(version)
                time.sleep(0.001)
        except BaseException as error:
            errors.append(str(error))

    thread = threading.Thread(target=reader)
    try:
        for index, value in enumerate((31, 32)):
            ready = base / f"ready-{iteration}-{index}"
            release = base / f"release-{iteration}-{index}"
            ready_files.append(ready)
            releases.append(release)
            env = os.environ.copy()
            env.update(LD_PRELOAD=str(library), MOONDELTA_LINK_MATCH="00000000000000000001.json",
                       MOONDELTA_LINK_READY=str(ready), MOONDELTA_LINK_RELEASE=str(release))
            writers.append(subprocess.Popen([str(WORKER), "append", str(table), str(value)], cwd=base, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env))
        deadline = time.monotonic() + 30
        while not all(path.exists() for path in ready_files) and all(writer.poll() is None for writer in writers) and time.monotonic() < deadline:
            time.sleep(0.001)
        require(all(path.exists() for path in ready_files), "writers did not both reach version-1 link barrier")
        require(read_rows(table) == [] and versions(table) == [0], "staged writers exposed a version")
        thread.start()
        # Hold the reader on the old snapshot, then prove it sees the new one.
        deadline = time.monotonic() + 5
        while 0 not in observed and not errors and time.monotonic() < deadline:
            time.sleep(0.001)
        releases[0].touch()
        winner = writers[0].communicate(timeout=30)
        require(writers[0].returncode == 0 and "appended version 1" in winner[0], f"winner failed: {winner}")
        commit = table / "_delta_log/00000000000000000001.json"
        original = commit.read_bytes()
        releases[1].touch()
        loser = writers[1].communicate(timeout=30)
        require(writers[1].returncode == 0 and "commit conflict:" in loser[0], f"expected conflict: {loser}")
        require(commit.read_bytes() == original, "loser overwrote an immutable commit")
        deadline = time.monotonic() + 5
        while 1 not in observed and not errors and time.monotonic() < deadline:
            time.sleep(0.001)
        stop.set()
        thread.join(timeout=10)
        require(not thread.is_alive() and not errors and observed == {0, 1}, f"reader saw inconsistent snapshots: {errors}, {observed}")
        check_row(table, 31)
        retry = run(table, "append", str(table), "32")
        require("appended version 2" in retry.stdout and versions(table) == [0, 1, 2], f"conflict retry failed: {retry}")
        require(sorted(read_rows(table), key=lambda row: row["id"]) == [{"id": 31, "payload": "x" * 31}, {"id": 32, "payload": "x" * 32}], "retry duplicated or lost rows")
        check_row(table, 31, version=1)
    finally:
        stop.set()
        if thread.ident is not None:
            thread.join(timeout=10)
        for writer in writers:
            if writer.poll() is None:
                writer.kill()
            writer.communicate(timeout=10)


def interrupt_during_write(table: Path, gate_library: Path, kind: str, size: int) -> None:
    ready = table.parent / f"ready-{kind}-{table.name}"
    env = os.environ.copy()
    env.update(
        LD_PRELOAD=str(gate_library),
        MOONDELTA_BLOCK_MATCH=f".{kind}.moondelta-tmp-",
        MOONDELTA_BLOCK_READY=str(ready),
    )
    child = subprocess.Popen(
        [str(WORKER), "append", table.name, str(size)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=table.parent,
    )
    deadline = time.monotonic() + 30
    try:
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        require(ready.exists(), f"worker did not reach {kind} write gate")
        temp_files = list(table.rglob(f"*.{kind}.moondelta-tmp-*"))
        require(len(temp_files) == 1, f"expected one staged {kind} file, found {temp_files}")
        staged_size = temp_files[0].stat().st_size
        require(staged_size > 0, "fault gate did not write a partial temp file")
        require(staged_size == 4096 if kind == "parquet" else staged_size < 4096, "fault gate did not stop at a partial write")
        os.kill(child.pid, signal.SIGKILL)
        child.wait(timeout=10)
        require(versions(table) == [0], f"interrupted {kind} write exposed a commit")
        data_files = list(table.glob("part-*.parquet"))
        if kind == "parquet":
            require(not data_files, "interrupted Parquet staging published a data file")
        else:
            require(len(data_files) == 1, "commit interruption should leave one unreferenced complete Parquet file")
        require(read_rows(table) == [], "interruption changed the readable snapshot")
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def write_intent(table: Path, txn: str, version: int, data_name: str | None,
                 data: bytes, commit_digest: str, kind: str = "append") -> None:
    """Persist a syntactically valid intent that simulates a crashed transaction."""
    intents = table / ".moondelta/intents"
    intents.mkdir(parents=True, exist_ok=True)
    suffix = f".moondelta-tmp-{txn}"
    commit_path = f"_delta_log/{version:020d}.json"
    intent = {
        "formatVersion": 1,
        "txnId": txn,
        "kind": kind,
        "version": version,
        "dataTempPath": (data_name + suffix) if data_name else None,
        "dataPath": data_name,
        "dataLength": len(data) if data_name else 0,
        "dataDigest": hashlib.sha256(data).hexdigest() if data_name else None,
        "commitTempPath": commit_path + suffix,
        "commitPath": commit_path,
        "commitDigest": commit_digest,
    }
    (intents / f"{txn}.json").write_text(json.dumps(intent), encoding="utf-8")


def recover(table: Path, apply: bool) -> tuple[dict[str, str], str]:
    result = run(table, "recover", str(table), "1" if apply else "0")
    require(result.returncode == 0, f"recover crashed: {result.stdout}{result.stderr}")
    header = next(line for line in result.stdout.splitlines() if line.startswith("recover "))
    return dict(item.split("=", 1) for item in header.split()[1:]), result.stdout


def query_state(table: Path, txn: str) -> str:
    result = run(table, "query", str(table), txn)
    require(result.returncode == 0, f"query failed: {result.stdout}{result.stderr}")
    return next(
        line.split("=", 1)[1].split()[0]
        for line in result.stdout.splitlines()
        if line.startswith("state=")
    )


def recovery_lifecycle(base: Path) -> dict:
    """dry-run preview -> managed-policy gate -> apply -> idempotent re-run."""
    table = base / "recovery-lifecycle"
    init(table)
    txn = secrets.token_hex(16)
    data = b"unreferenced-but-fully-written-parquet-body"
    name = f"part-9001-1-{txn}.parquet"
    (table / name).write_bytes(data)
    (table / (name + f".moondelta-tmp-{txn}")).write_bytes(data[:8])
    write_intent(table, txn, 1, name, data, "0" * 64)

    preview, _ = recover(table, apply=False)
    require(preview["deleted"] == "0" and preview["busy"] == "false", f"dry-run did not stay read-only: {preview}")
    require((table / name).exists(), "dry-run deleted a candidate")

    blocked, text = recover(table, apply=True)
    require(blocked["deleted"] == "0" and "blocked" in text, f"unmanaged apply was not blocked: {text}")
    require((table / name).exists(), "unmanaged apply deleted a candidate")

    require("managed=true" in run(table, "manage", str(table), "1").stdout, "cannot enable managed policy")
    applied, text = recover(table, apply=True)
    require(int(applied["deleted"]) >= 2, f"apply removed too little: {text}")
    require(not (table / name).exists(), "unreferenced data survived apply")
    require(not (table / (name + f".moondelta-tmp-{txn}")).exists(), "pinned temp name survived apply")
    require(list((table / ".moondelta/intents").iterdir()) == [], "intent survived apply")
    require(read_rows(table) == [], "recovery changed the readable snapshot")

    again, _ = recover(table, apply=True)
    require(again["deleted"] == "0", f"recovery is not idempotent: {again}")
    print("PASS: recovery dry-run, managed-policy gate, apply deletion order and idempotency")
    return {"status": "PASS", "applied_deleted": applied["deleted"]}


def query_states(base: Path) -> dict:
    table = base / "query-states"
    init(table)
    appended = run(table, "append", str(table), "81")
    require("appended version 1" in appended.stdout, appended.stdout)
    txn = appended.stdout.split("txn=")[-1].strip()
    commit_bytes = (table / "_delta_log/00000000000000000001.json").read_bytes()
    write_intent(table, txn, 1, None, b"", hashlib.sha256(commit_bytes).hexdigest(), kind="create")
    require(query_state(table, txn) == "committed", "committed transaction reported the wrong state")

    pending = secrets.token_hex(16)
    write_intent(table, pending, 2, None, b"", "0" * 64, kind="create")
    require(query_state(table, pending) == "not-committed", "missing commit reported the wrong state")

    conflict = secrets.token_hex(16)
    write_intent(table, conflict, 1, None, b"", "1" * 64, kind="create")
    require(query_state(table, conflict) == "conflict", "occupied target version reported the wrong state")

    corrupt = secrets.token_hex(16)
    (table / ".moondelta/intents" / f"{corrupt}.json").write_text("{not json", encoding="utf-8")
    require(query_state(table, corrupt) == "unresolved", "corrupt intent was not unresolved")
    print("PASS: transaction query states (committed / not-committed / conflict / unresolved)")
    return {"status": "PASS"}


def recovery_respects_writer_lock(base: Path) -> dict:
    table = base / "recovery-lock-busy"
    init(table)
    txn = secrets.token_hex(16)
    data = b"candidate-while-a-writer-is-active"
    name = f"part-9101-1-{txn}.parquet"
    (table / name).write_bytes(data)
    write_intent(table, txn, 1, name, data, "4" * 64)
    run(table, "manage", str(table), "1")
    fd = os.open(table / ".moondelta.lock", os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        busy, text = recover(table, apply=True)
        require(busy["busy"] == "true" and "lock busy" in text, f"recoverer ignored the writer lock: {text}")
        require((table / name).exists(), "busy recoverer deleted a file")
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    after, text = recover(table, apply=True)
    require(after["busy"] == "false" and int(after["deleted"]) >= 1, f"recovery did not resume: {text}")
    require(not (table / name).exists(), "resumed recovery left the candidate")
    print("PASS: non-blocking recoverer reports busy under an active writer and resumes after release")
    return {"status": "PASS"}


def sigkill_recovery(base: Path, library: Path) -> dict:
    table = base / "sigkill-recovery"
    init(table)
    interrupt_during_write(table, library, "parquet", 64 * 1024 * 1024)
    intents = list((table / ".moondelta/intents").iterdir())
    require(len(intents) == 1, f"interrupted write should retain exactly one intent, found {intents}")
    run(table, "manage", str(table), "1")
    applied, text = recover(table, apply=True)
    require(int(applied["deleted"]) >= 2, f"recovery removed too little: {text}")
    require(list((table / ".moondelta/intents").iterdir()) == [], "intent survived recovery")
    require(not list(table.glob("*.parquet.moondelta-tmp-*")), "staged Parquet temp name survived recovery")
    require(versions(table) == [0] and read_rows(table) == [], "recovery changed the interrupted snapshot")
    retry = run(table, "append", str(table), "301")
    require("appended version 1" in retry.stdout, f"append after recovery failed: {retry}")
    check_row(table, 301)
    print("PASS: SIGKILL residue is recovered from its intent, then append succeeds unchanged")
    return {"status": "PASS"}


def history_protection(base: Path) -> dict:
    table = base / "history-protection"
    init(table)
    require("appended version 1" in run(table, "append", str(table), "91").stdout, "append v1 failed")
    require("appended version 2" in run(table, "append", str(table), "92").stdout, "append v2 failed")
    first = sorted(table.glob("part-91-1-*.parquet"))
    require(len(first) == 1, f"cannot identify the version-1 data file: {first}")
    first = first[0]
    txn = secrets.token_hex(16)
    write_intent(table, txn, 3, first.name, first.read_bytes(), "5" * 64)
    run(table, "manage", str(table), "1")
    applied, text = recover(table, apply=True)
    require(first.exists(), "a file referenced by reachable history was deleted")
    require("protected" in text, f"history protection was not reported: {text}")
    require(sorted(read_rows(table, 1), key=lambda row: row["id"]) == [{"id": 91, "payload": "x" * 91}], "version 1 readback changed")
    require(sorted(read_rows(table, 2), key=lambda row: row["id"]) == [{"id": 91, "payload": "x" * 91}, {"id": 92, "payload": "x" * 92}], "version 2 readback changed")

    (table / "_delta_log/00000000000000000001.json").unlink()
    junk = b"junk-candidate-body"
    junk_name = "part-junk.parquet"
    (table / junk_name).write_bytes(junk)
    gap_txn = secrets.token_hex(16)
    write_intent(table, gap_txn, 4, junk_name, junk, "6" * 64)
    gap, text = recover(table, apply=True)
    require(gap["logComplete"] == "false", f"log gap was not detected: {gap}")
    require((table / junk_name).exists(), "a candidate was deleted from an incomplete log")
    require((table / ".moondelta/intents" / f"{gap_txn}.json").exists(), "evidence intent was deleted despite a log gap")
    print("PASS: reachable history/checkpoint references are protected and log gaps block deletion")
    return {"status": "PASS"}


def unowned_preserved(base: Path) -> dict:
    table = base / "unowned-files"
    init(table)
    legacy = table / "part-legacy.moondelta-tmp-abcdef"
    legacy.write_text("legacy", encoding="utf-8")
    windows_style = table / "mdt1234.tmp"
    windows_style.write_text("legacy", encoding="utf-8")
    notes = table / ".moondelta/intents/notes.txt"
    notes.write_text("unknown", encoding="utf-8")
    run(table, "manage", str(table), "1")
    report, text = recover(table, apply=True)
    require(legacy.exists() and windows_style.exists() and notes.exists(), f"unowned files were deleted: {text}")
    require("unowned" in text, "unowned files were not reported")
    print("PASS: unowned legacy names/unknown files are preserved and reported (no wildcard deletion)")
    return {"status": "PASS"}


def txn_id_uniqueness(base: Path) -> dict:
    """Concurrent processes, identical payload/timestamp, distinct OS-random ids."""
    tables = [base / f"txn-unique-{index}" for index in range(8)]
    create_ids = []
    for table in tables:
        result = run(table, "init", str(table), "1")
        require(result.returncode == 0 and "txn=" in result.stdout, f"init failed: {result.stdout}")
        create_ids.append(result.stdout.split("txn=")[-1].strip())
    writers = [
        subprocess.Popen(
            [str(WORKER), "append", str(table), "123"], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        for table in tables
    ]
    append_ids = []
    for table, writer in zip(tables, writers):
        stdout, stderr = writer.communicate(timeout=60)
        require(writer.returncode == 0 and "txn=" in stdout, f"append failed: {stdout}{stderr}")
        txn = stdout.split("txn=")[-1].strip()
        require(re.fullmatch(r"[0-9a-f]{32}", txn) is not None, f"not a 32-hex id: {txn}")
        require(txn in " ".join(path.name for path in table.glob("part-*.parquet")), "data file name misses its txn id")
        append_ids.append(txn)
    all_ids = create_ids + append_ids
    require(len(set(all_ids)) == len(all_ids), f"transaction ids collided: {all_ids}")
    print("PASS: concurrent processes with identical payload/timestamp produce unique OS-random txn ids")
    return {"status": "PASS", "ids": len(all_ids)}


def recoverer_sees_paused_writer(base: Path, library: Path) -> dict:
    """A writer paused between data and JSON publication keeps the lock exclusive to itself."""
    table = base / "paused-writer"
    init(table)
    ready = base / "ready-paused-writer"
    release = base / "release-paused-writer"
    env = os.environ.copy()
    env.update(LD_PRELOAD=str(library), MOONDELTA_LINK_MATCH="00000000000000000001.json",
               MOONDELTA_LINK_READY=str(ready), MOONDELTA_LINK_RELEASE=str(release))
    writer = subprocess.Popen(
        [str(WORKER), "append", str(table), "45"], cwd=table.parent,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    try:
        deadline = time.monotonic() + 30
        while not ready.exists() and writer.poll() is None and time.monotonic() < deadline:
            time.sleep(0.005)
        require(ready.exists(), "writer did not pause before the version-1 JSON link")
        intents = list((table / ".moondelta/intents").iterdir())
        data_files = list(table.glob("part-*.parquet"))
        require(len(intents) == 1, f"paused writer should hold one intent, found {intents}")
        require(len(data_files) == 1, f"paused writer should have published data, found {data_files}")
        busy, text = recover(table, apply=True)
        require(busy["busy"] == "true" and "lock busy" in text, f"recoverer entered an active writer: {text}")
        require((table / ".moondelta/intents" / intents[0].name).exists(), "recoverer deleted an active intent")
        require(data_files[0].exists(), "recoverer deleted an active data file")
        require(versions(table) == [0], "a paused writer exposed a version")
        release.touch()
        stdout, stderr = writer.communicate(timeout=30)
        require(writer.returncode == 0 and "appended version 1" in stdout, f"paused writer failed to resume: {stdout}{stderr}")
        check_row(table, 45)
        require(list((table / ".moondelta/intents").iterdir()) == [], "successful resume left an intent")
    finally:
        if writer.poll() is None:
            writer.kill()
            writer.wait()
    print("PASS: recoverer is busy while a writer is paused between data and JSON publication")
    return {"status": "PASS"}


def recovery_unlink_failure_retries(base: Path, library: Path) -> dict:
    """A failed unlink keeps the intent and the next recovery run finishes the job."""
    table = base / "recovery-unlink-retry"
    init(table)
    txn = secrets.token_hex(16)
    data = b"candidate-with-unlink-failure"
    name = f"part-9201-1-{txn}.parquet"
    (table / name).write_bytes(data)
    write_intent(table, txn, 1, name, data, "7" * 64)
    run(table, "manage", str(table), "1")
    env = os.environ.copy()
    env.update(LD_PRELOAD=str(library), MOONDELTA_FAIL_OP="unlink",
               MOONDELTA_FAIL_MATCH=name, MOONDELTA_FAIL_NTH="1",
               MOONDELTA_FAIL_ERRNO=str(errno.EACCES))
    first = run(table, "recover", str(table), "1", env=env)
    require("recover mode=apply" in first.stdout, f"faulted recovery crashed: {first.stdout}{first.stderr}")
    require((table / ".moondelta/intents" / f"{txn}.json").exists(), "failed cleanup dropped the intent evidence")
    second, text = recover(table, apply=True)
    require(int(second["deleted"]) >= 1, f"repeated recovery did not finish: {text}")
    require(not (table / ".moondelta/intents" / f"{txn}.json").exists(), "intent survived the retry")
    require(not (table / name).exists(), "unreferenced data survived the retry")
    print("PASS: failed unlink keeps the intent; a repeated recovery converges")
    return {"status": "PASS"}


def unknown_outcome_query(base: Path, library: Path) -> dict:
    """A real post-publication sync failure is queried back as committed, then settled."""
    table = base / "unknown-outcome-query"
    init(table)
    trace = base / "unknown-outcome-query.trace"
    env = os.environ.copy()
    env.update(LD_PRELOAD=str(library), MOONDELTA_FAIL_OP="fsync",
               MOONDELTA_FAIL_MATCH=str(table / "_delta_log"), MOONDELTA_FAIL_EXACT="1",
               MOONDELTA_FAIL_NTH="1", MOONDELTA_FAIL_ERRNO=str(errno.EIO),
               MOONDELTA_TRACE=str(trace))
    result = run(table, "append", str(table), "88", env=env)
    require("commit outcome unknown:" in result.stdout, f"expected an uncertain commit: {result.stdout}")
    match = re.search(r"txn ([0-9a-f]{32})", result.stdout)
    require(match is not None, f"uncertain outcome did not expose a txn id: {result.stdout}")
    txn = match.group(1)
    require(versions(table) == [0, 1], "uncertain commit did not become visible")
    query = run(table, "query", str(table), txn)
    require("state=committed" in query.stdout, f"visible commit was not queried as committed: {query.stdout}")
    run(table, "manage", str(table), "1")
    applied, text = recover(table, apply=True)
    require(int(applied["deleted"]) >= 1, f"recovery did not settle the residue: {text}")
    require(list((table / ".moondelta/intents").iterdir()) == [], "intent survived recovery")
    require(read_rows(table) == [{"id": 88, "payload": "x" * 88}], "committed row changed after recovery")
    print("PASS: real commit-unknown outcome queries as committed, then recovery keeps the commit")
    return {"status": "PASS"}


def remove_history_protection(base: Path) -> dict:
    """A real remove action must keep the data readable by older versions."""
    table = base / "remove-history"
    init(table)
    require("appended version 1" in run(table, "append", str(table), "91").stdout, "append v1 failed")
    require("appended version 2" in run(table, "append", str(table), "92").stdout, "append v2 failed")
    first = sorted(table.glob("part-91-1-*.parquet"))
    require(len(first) == 1, f"cannot identify the version-1 data file: {first}")
    first = first[0]
    remove_log = (
        '{"commitInfo":{"timestamp":2000,"operation":"DELETE","operationParameters":{}}}\n'
        f'{{"remove":{{"path":"{first.name}","deletionTimestamp":2000,"dataChange":true}}}}\n'
    )
    (table / "_delta_log/00000000000000000003.json").write_text(remove_log, encoding="utf-8")
    require([row["id"] for row in read_rows(table, 1)] == [91], "version 1 readback changed")
    require(sorted(row["id"] for row in read_rows(table, 2)) == [91, 92], "version 2 readback changed")
    require([row["id"] for row in read_rows(table, 3)] == [92], "removed file is still visible at version 3")
    txn = secrets.token_hex(16)
    write_intent(table, txn, 4, first.name, first.read_bytes(), "a" * 64)
    run(table, "manage", str(table), "1")
    applied, text = recover(table, apply=True)
    require(first.exists(), "a file referenced by a remove action in history was deleted")
    require("protected" in text, f"remove-history protection was not reported: {text}")
    require([row["id"] for row in read_rows(table, 1)] == [91], "time travel to version 1 changed")
    require([row["id"] for row in read_rows(table, 3)] == [92], "version 3 readback changed")
    print("PASS: a remove action still protects its data for older versions (delta-rs time travel)")
    return {"status": "PASS"}


def checkpoint_protection(base: Path) -> dict:
    """A reference from a supported single-file checkpoint protects its data file."""
    table = base / "checkpoint-protection"
    shutil.copytree(ROOT / "fixtures/delta/table_a", table)
    checkpoint = table / "_delta_log/00000000000000000010.checkpoint.parquet"
    require(checkpoint.exists(), "table_a checkpoint fixture is missing")
    rows = pq.read_table(checkpoint, columns=["add"]).column("add").to_pylist()
    paths = [row["path"] for row in rows if row and row.get("path")]
    require(bool(paths), "checkpoint contains no add paths")
    relative = paths[0]
    data = b"checkpoint-protected-candidate"
    (table / relative).write_bytes(data)
    txn = secrets.token_hex(16)
    write_intent(table, txn, 13, relative, data, "b" * 64)
    run(table, "manage", str(table), "1")
    applied, text = recover(table, apply=True)
    require((table / relative).exists(), "a checkpoint-referenced data file was deleted")
    require("protected" in text, f"checkpoint protection was not reported: {text}")
    print("PASS: a single-file checkpoint reference protects its data file from cleanup")
    return {"status": "PASS"}


def checkpoint_only_blocked(base: Path) -> dict:
    """Checkpoint-only history is not enough evidence to delete anything."""
    table = base / "checkpoint-only"
    shutil.copytree(ROOT / "fixtures/delta/table_a", table)
    for path in (table / "_delta_log").glob("[0-9]*.json"):
        path.unlink()
    junk = table / "part-junk.parquet"
    junk.write_bytes(b"junk-candidate-body")
    txn = secrets.token_hex(16)
    write_intent(table, txn, 11, junk.name, junk.read_bytes(), "c" * 64)
    run(table, "manage", str(table), "1")
    applied, text = recover(table, apply=True)
    require(applied["logComplete"] == "false", f"checkpoint-only table was treated as complete: {applied}")
    require(junk.exists(), "checkpoint-only table allowed a deletion")
    require((table / ".moondelta/intents" / f"{txn}.json").exists(), "evidence intent was deleted")
    print("PASS: checkpoint-only history disables deletion and preserves evidence")
    return {"status": "PASS"}


def symlink_preserved(base: Path) -> dict:
    """A symlink claiming ownership is preserved and reported without following it."""
    table = base / "symlink-protection"
    init(table)
    target = base / "outside-target.bin"
    target.write_bytes(b"outside-target-content")
    link = table / "part-linked.parquet"
    link.symlink_to(target)
    txn = secrets.token_hex(16)
    write_intent(table, txn, 1, link.name, target.read_bytes(), "d" * 64)
    run(table, "manage", str(table), "1")
    applied, text = recover(table, apply=True)
    require(link.is_symlink(), "symbolic link was removed")
    require(target.read_bytes() == b"outside-target-content", "symlink target was modified")
    require("symbolic link" in text, f"symlink preservation was not reported: {text}")
    require((table / ".moondelta/intents" / f"{txn}.json").exists(), "symlink intent was deleted")
    print("PASS: symlink candidates are preserved, never followed, and reported")
    return {"status": "PASS"}


def nested_symlink_preserved(base: Path) -> dict:
    """A symlink in a parent component cannot redirect cleanup outside the table."""
    table = base / "nested-symlink-protection"
    init(table)
    outside = base / "nested-outside"
    outside.mkdir()
    target = outside / "part-target.parquet"
    target.write_bytes(b"outside-target-content")
    (table / "redirect").symlink_to(outside, target_is_directory=True)
    txn = secrets.token_hex(16)
    write_intent(table, txn, 1, "redirect/part-target.parquet", target.read_bytes(), "d" * 64)
    run(table, "manage", str(table), "1")
    _, text = recover(table, apply=True)
    require(target.read_bytes() == b"outside-target-content", "parent symlink target was modified")
    require((table / "redirect").is_symlink(), "parent symlink was removed")
    require((table / ".moondelta/intents" / f"{txn}.json").exists(), "nested symlink intent was deleted")
    require("symbolic link" in text, f"nested symlink was not reported: {text}")
    print("PASS: parent symlink components are preserved and never followed")
    return {"status": "PASS"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    require(args.iterations > 0, "iterations must be positive")
    require(WORKER.exists(), f"missing worker executable: {WORKER}; run moon build --target native")
    with tempfile.TemporaryDirectory(prefix="moondelta-atomic-") as temp:
        base = Path(temp)
        table = base / "table"
        result = run(table, "init", table.name, "1")
        require(result.returncode == 0 and versions(table) == [0], f"init failed: {result.stdout}{result.stderr}")

        library = base / "atomic_write_gate.so"
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-O2", str(GATE_SOURCE), "-ldl", "-o", str(library)],
            check=True,
        )
        interrupt_during_write(table, library, "parquet", 64 * 1024 * 1024)
        print("PASS: SIGKILL during staged Parquet write leaves version 0 and no published data")

        # A successful retry advances exactly one version despite the abandoned temp file.
        retry = run(table, "append", table.name, "101")
        require(retry.returncode == 0 and versions(table) == [0, 1], f"data retry failed: {retry.stdout}{retry.stderr}")
        check_row(table, 101)

        table2 = base / "commit-table"
        result = run(table2, "init", table2.name, "1")
        require(result.returncode == 0, f"second init failed: {result.stdout}{result.stderr}")
        interrupt_during_write(table2, library, "json", 1024 * 1024)
        require(versions(table2) == [0], "interrupted JSON commit changed version 0")
        retry = run(table2, "append", table2.name, "202")
        require(retry.returncode == 0 and versions(table2) == [0, 1], "retry after interrupted commit failed")
        check_row(table2, 202)
        print("PASS: SIGKILL during staged JSON commit preserves the previous snapshot and permits retry")

        results = fault_cases(base, library) + creation_failures(base, library)
        order = publication_order(base, library)
        maintenance_lock_blocks_writer(base)
        maintenance_lock_rejects_symlink(base)
        recovery_results = {
            "recovery_lifecycle": recovery_lifecycle(base),
            "query_states": query_states(base),
            "recover_lock_busy": recovery_respects_writer_lock(base),
            "sigkill_recovery": sigkill_recovery(base, library),
            "history_protection": history_protection(base),
            "unowned_preservation": unowned_preserved(base),
            "txn_id_uniqueness": txn_id_uniqueness(base),
            "recoverer_sees_paused_writer": recoverer_sees_paused_writer(base, library),
            "recovery_unlink_retry": recovery_unlink_failure_retries(base, library),
            "unknown_outcome_query": unknown_outcome_query(base, library),
            "remove_history_protection": remove_history_protection(base),
            "checkpoint_protection": checkpoint_protection(base),
            "checkpoint_only_blocked": checkpoint_only_blocked(base),
            "symlink_preserved": symlink_preserved(base),
            "nested_symlink_preserved": nested_symlink_preserved(base),
        }
        empty = base / "empty"
        init(empty)
        before = sorted(str(path.relative_to(empty)) for path in empty.rglob("*"))
        rejected = run(empty, "empty", str(empty), "1")
        require("empty batches" in rejected.stdout, "empty batch was not rejected")
        require(before == sorted(str(path.relative_to(empty)) for path in empty.rglob("*")), "empty batch wrote a file")
        print("PASS: absolute-path initialization and empty append without files")
        for iteration in range(args.iterations):
            deterministic_race(base, library, iteration)
        print(f"PASS: {args.iterations} deterministic version-publication races, continuous readers, immutable commits and conflict retries")
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps({"status": "PASS", "scope": "process_crash_io_faults_intent_recovery_publication_races_and_maintenance_locks",
                                              "overall_transaction_acceptance": "NOT_PASSED",
                                              "platform": platform.platform(), "race_iterations": args.iterations,
                                              "python": sys.version, "deltalake": deltalake.__version__, "pyarrow": pyarrow.__version__,
                                              "filesystem": subprocess.check_output(["findmnt", "-T", str(base), "-n", "-o", "FSTYPE"], text=True).strip(),
                                              "fault_cases": results, "partial_write_sigkill": "PASS", "publication_order": order,
                                              "maintenance_lock_blocks_writer": "PASS",
                                              "maintenance_lock_rejects_symlink": "PASS",
                                              "recovery": recovery_results,
                                              "native_and_delta_rs_readback": "PASS", "power_loss": "NOT_TESTED",
                                              "automatic_cleanup": "IMPLEMENTED_MANAGED_TABLES_ONLY"}, indent=2) + "\n")


if __name__ == "__main__":
    main()
