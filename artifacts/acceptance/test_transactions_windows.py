#!/usr/bin/env python3
"""Windows transaction acceptance checks for moondelta-core (S2/S3).

Covers: OS-random transaction ids embedded in data file names, intent publication
and cleanup, recovery policy gating, idempotent dry-run/apply, transaction result
query, unowned-file preservation, and cross-process LockFileEx coordination.

No third-party Python packages are required; run from the repository root:

    python artifacts/acceptance/test_transactions_windows.py --output <path>
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import hashlib
import json
import os
import platform
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "_build/native/debug/build/examples/atomic_commit_worker/atomic_commit_worker.exe"

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_ALWAYS = 4
LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
LOCKFILE_FAIL_IMMEDIATELY = 0x00000001


class Overlapped(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
kernel32.LockFileEx.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    wintypes.DWORD, ctypes.POINTER(Overlapped),
]
kernel32.UnlockFileEx.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    ctypes.POINTER(Overlapped),
]


class TableLock:
    """Exclusive or shared byte-range lock on the fixed .moondelta.lock file."""

    def __init__(self, table: Path, exclusive: bool) -> None:
        self.path = table / ".moondelta.lock"
        self.exclusive = exclusive
        self.handle = None
        self.overlapped = Overlapped()

    def acquire(self) -> None:
        self.handle = kernel32.CreateFileW(
            str(self.path), GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None, OPEN_ALWAYS, 0, None,
        )
        if self.handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        flags = LOCKFILE_EXCLUSIVE_LOCK if self.exclusive else 0
        if not kernel32.LockFileEx(self.handle, flags, 0, 1, 0, ctypes.byref(self.overlapped)):
            raise ctypes.WinError(ctypes.get_last_error())

    def release(self) -> None:
        if self.handle is not None:
            kernel32.UnlockFileEx(self.handle, 0, 1, 0, ctypes.byref(self.overlapped))
            kernel32.CloseHandle(self.handle)
            self.handle = None


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(WORKER), *args], text=True, capture_output=True, timeout=timeout,
    )
    return result


def init_table(table: Path) -> str:
    result = run("init", str(table), "1")
    require(result.returncode == 0, f"init failed: {result.stdout}{result.stderr}")
    require("created version 0" in result.stdout, f"unexpected init output: {result.stdout}")
    txn = result.stdout.split("txn=")[-1].strip()
    require(len(txn) == 32 and all(c in "0123456789abcdef" for c in txn), f"bad txn id: {txn}")
    return txn


def append(table: Path, value: int) -> str:
    result = run("append", str(table), str(value))
    require(result.returncode == 0, f"append failed: {result.stdout}{result.stderr}")
    require("appended version" in result.stdout, f"unexpected append output: {result.stdout}")
    txn = result.stdout.split("txn=")[-1].strip()
    require(len(txn) == 32, f"append did not report a txn id: {result.stdout}")
    return txn


def recover(table: Path, apply: bool) -> tuple[dict, str]:
    result = run("recover", str(table), "1" if apply else "0")
    require(result.returncode == 0, f"recover crashed: {result.stdout}{result.stderr}")
    header = next(line for line in result.stdout.splitlines() if line.startswith("recover "))
    fields = dict(item.split("=", 1) for item in header.split()[1:])
    return fields, result.stdout


def query(table: Path, txn: str) -> str:
    result = run("query", str(table), txn)
    require(result.returncode == 0, f"query failed: {result.stdout}{result.stderr}")
    state = next(
        line.split("=", 1)[1].split()[0]
        for line in result.stdout.splitlines()
        if line.startswith("state=")
    )
    return state


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_intent(table: Path, txn: str, version: int, data_name: str | None,
                 data: bytes, commit_digest: str, kind: str = "append") -> None:
    intents = table / ".moondelta" / "intents"
    intents.mkdir(parents=True, exist_ok=True)
    commit_path = f"_delta_log/{version:020d}.json"
    suffix = f".moondelta-tmp-{txn}"
    intent = {
        "formatVersion": 1,
        "txnId": txn,
        "kind": kind,
        "version": version,
        "dataTempPath": (data_name + suffix) if data_name else None,
        "dataPath": data_name,
        "dataLength": len(data) if data_name else 0,
        "dataDigest": sha256_bytes(data) if data_name else None,
        "commitTempPath": commit_path + suffix,
        "commitPath": commit_path,
        "commitDigest": commit_digest,
    }
    (intents / f"{txn}.json").write_text(
        json.dumps(intent, sort_keys=True), encoding="utf-8",
    )


def relative_files(table: Path) -> list[str]:
    return sorted(str(path.relative_to(table)).replace("\\", "/") for path in table.rglob("*") if path.is_file())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    require(WORKER.exists(), f"missing worker executable: {WORKER}; run moon build --target native")
    results: dict[str, object] = {"platform": platform.platform(), "python": sys.version}

    with tempfile.TemporaryDirectory(prefix="moondelta-win-") as temp:
        base = Path(temp)

        # 1. create/append publish through a persisted intent and clean it up.
        table = base / "basic"
        create_txn = init_table(table)
        require(create_txn not in ("", "0" * 32), "create did not expose a transaction id")
        append_txn = append(table, 71)
        data_files = [name for name in relative_files(table) if name.endswith(".parquet")]
        require(len(data_files) == 1, f"unexpected data files: {data_files}")
        require(append_txn in data_files[0], f"data file name misses the txn id: {data_files[0]}")
        commit = (table / "_delta_log" / "00000000000000000001.json").read_text(encoding="utf-8")
        require(f'"moondeltaTxnId":"{append_txn}"' in commit, "commit misses the txn marker")
        require(list((table / ".moondelta" / "intents").iterdir()) == [], "intent was not cleaned up")
        require(not any(".moondelta-tmp-" in name for name in relative_files(table)), "temporary residue left behind")
        read = run("read", str(table), "1")
        require(read.stdout.splitlines() == ["version=1 rows=1", "71"], f"readback failed: {read.stdout}")
        print("PASS: intent-backed create/append, txn-id file naming and success cleanup")
        results["intent_backed_commit"] = "PASS"

        # 2. residue lifecycle: dry-run, policy gate, apply, idempotency.
        residue = base / "residue"
        init_table(residue)
        txn = secrets.token_hex(16)
        data = b"unreferenced-but-fully-written-parquet-body"
        data_name = f"part-9001-1-{txn}.parquet"
        (residue / data_name).write_bytes(data)
        (residue / (data_name + f".moondelta-tmp-{txn}")).write_bytes(data[:10])
        write_intent(residue, txn, 1, data_name, data, sha256_bytes(b"never-published"))
        preview, _ = recover(residue, apply=False)
        require(preview["deleted"] == "0" and preview["busy"] == "false", f"dry-run changed files: {preview}")
        require((residue / data_name).exists(), "dry-run deleted a candidate")
        blocked, text = recover(residue, apply=True)
        require(blocked["deleted"] == "0", f"unmanaged apply deleted files: {text}")
        require("blocked" in text, "unmanaged apply did not report the policy block")
        require(run("manage", str(residue), "1").returncode == 0, "manage failed")
        applied, text = recover(residue, apply=True)
        require(applied["deleted"] != "0" and int(applied["deleted"]) >= 2, f"apply removed too little: {text}")
        require(not (residue / data_name).exists(), "unreferenced data survived apply")
        require(not (residue / (data_name + f".moondelta-tmp-{txn}")).exists(), "temp name survived apply")
        require(list((residue / ".moondelta" / "intents").iterdir()) == [], "intent survived apply")
        again, _ = recover(residue, apply=True)
        require(again["deleted"] == "0", f"recovery is not idempotent: {again}")
        print("PASS: dry-run preview, managed-policy gate, apply and idempotent recovery")
        results["recovery_policy"] = "PASS"

        # 3. query distinguishes committed / not-committed / conflict / corrupt.
        states = base / "states"
        init_table(states)
        appended = append(states, 72)
        # recreate the already-cleaned intent to ask about the committed transaction
        commit_bytes = (states / "_delta_log" / "00000000000000000001.json").read_bytes()
        write_intent(states, appended, 1, None, b"", sha256_bytes(commit_bytes), kind="create")
        require(query(states, appended) == "committed-unconfirmed", "Windows must not upgrade file sync to directory durability")
        pending = secrets.token_hex(16)
        write_intent(states, pending, 2, None, b"", "0" * 64, kind="create")
        require(query(states, pending) == "not-committed", "pending transaction reported the wrong state")
        conflict = secrets.token_hex(16)
        write_intent(states, conflict, 1, None, b"", "1" * 64, kind="create")
        require(query(states, conflict) == "conflict", "occupied target version reported the wrong state")
        corrupt = secrets.token_hex(16)
        (states / ".moondelta" / "intents" / f"{corrupt}.json").write_text("{not json", encoding="utf-8")
        require(query(states, corrupt) == "unresolved", "corrupt intent was not unresolved")
        print("PASS: transaction query states (committed / not-committed / conflict / unresolved)")
        results["query_states"] = "PASS"

        # 4. unowned legacy names and unknown maintenance files are preserved.
        unknown = base / "unknown"
        init_table(unknown)
        legacy = unknown / "part-legacy.moondelta-tmp-abcdef"
        legacy.write_text("legacy", encoding="utf-8")
        windows_style = unknown / "mdt1234.tmp"
        windows_style.write_text("legacy", encoding="utf-8")
        notes = unknown / ".moondelta" / "intents" / "notes.txt"
        notes.write_text("unknown", encoding="utf-8")
        run("manage", str(unknown), "1")
        report, text = recover(unknown, apply=True)
        require(legacy.exists() and windows_style.exists() and notes.exists(), f"unowned files were deleted: {text}")
        require("unowned" in text, "unowned temporary files were not reported")
        print("PASS: legacy/unknown names preserved and reported, no wildcard deletion")
        results["unowned_preservation"] = "PASS"

        # 5. cross-process LockFileEx: exclusive holder blocks a writer, and a
        #    shared holder makes the non-blocking recoverer report busy.
        holder_table = base / "locks"
        init_table(holder_table)
        exclusive = TableLock(holder_table, exclusive=True)
        exclusive.acquire()
        moved_lock = base / "renamed-lock"
        try:
            os.replace(holder_table / ".moondelta.lock", moved_lock)
            raise AssertionError("fixed lock file was replaceable while locked")
        except PermissionError:
            pass
        writer = subprocess.Popen(
            [str(WORKER), "append", str(holder_table), "73"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        time.sleep(0.5)
        require(writer.poll() is None, "writer did not block on the exclusive maintenance lock")
        exclusive.release()
        stdout, stderr = writer.communicate(timeout=30)
        require(writer.returncode == 0 and "appended version" in stdout, f"writer did not resume: {stdout}{stderr}")
        shared = TableLock(holder_table, exclusive=False)
        shared.acquire()
        busy, text = recover(holder_table, apply=True)
        require(busy["busy"] == "true" and "lock busy" in text, f"recoverer ignored the shared writer lock: {text}")
        shared.release()
        after, _ = recover(holder_table, apply=True)
        require(after["busy"] == "false", "recoverer stayed busy after the writer released")
        print("PASS: cross-process LockFileEx coordination (writer blocked, recoverer busy)")
        results["windows_locks"] = "PASS"

        # 6. concurrent processes with identical payload produce distinct OS-random ids.
        unique_tables = [base / f"txn-unique-{index}" for index in range(8)]
        create_ids = [init_table(table) for table in unique_tables]
        writers = [
            subprocess.Popen(
                [str(WORKER), "append", str(table), "123"],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            for table in unique_tables
        ]
        append_ids = []
        for table, writer in zip(unique_tables, writers):
            stdout, stderr = writer.communicate(timeout=60)
            require(writer.returncode == 0 and "txn=" in stdout, f"append failed: {stdout}{stderr}")
            append_ids.append(stdout.split("txn=")[-1].strip())
        all_ids = create_ids + append_ids
        require(len(set(all_ids)) == len(all_ids), f"transaction ids collided: {all_ids}")
        require(all(len(item) == 32 for item in all_ids), "malformed transaction id")
        print("PASS: concurrent processes with identical payload produce unique OS-random txn ids")
        results["txn_id_uniqueness"] = "PASS"

        # 7. a fully committed table stays readable through the public reader.
        inspect = run("read", str(holder_table), "1")
        require(inspect.returncode == 0 and "rows=1" in inspect.stdout, f"final read failed: {inspect.stdout}")

    results["status"] = "PASS"
    results["scope"] = "windows_transaction_intent_recovery_query_and_locks"
    results["power_loss"] = "NOT_TESTED"
    results["durability_claim"] = "process-crash only; no NTFS power-loss claim"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print("PASS: Windows transaction acceptance matrix")


if __name__ == "__main__":
    main()
