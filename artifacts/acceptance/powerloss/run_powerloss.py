#!/usr/bin/env python3
"""S5 power-loss matrix: cut a QEMU guest on a volatile NBD device, 100 times.

What this runner deliberately does *not* do
------------------------------------------
It never invents durability evidence. It refuses to print PASS unless

  * the volatile-cache positive control passed on this host (unflushed writes
    really do disappear, completed FLUSHes really do survive),
  * every round was actually cut at an announced persistence boundary,
  * every cut image passed `fsck` plus the structural/reader verification, and
  * the recovered version is always either the previous version or exactly one
    newer complete version.

Killing a process, `wsl --terminate`, or a clean shutdown are not substitutes
and are not accepted here.

One round is:

    boot guest (initramfs) -> mount data disk -> clean setup appends
        -> one target append with MOONDELTA_POWERLOSS_AT=<boundary>
        -> the probe announces the boundary and blocks
        -> host SIGKILLs QEMU, then SIGKILLs the NBD server (volatile cache gone)
        -> copy the image, fsck it (this is the reboot), mount read-only,
           verify contiguity / referenced files / rows / readers
        -> record; success rounds keep logs only, failures keep the image

Usage (on a dedicated, non-WSL Linux host, as root):

    python3 run_powerloss.py \
        --image /var/tmp/moondelta-s5/data.raw \
        --kernel /boot/vmlinuz-$(uname -r) \
        --initramfs /var/tmp/moondelta-s5/initramfs.cpio.gz \
        --delta-cli _build/native/debug/build/cmd/delta/delta.exe \
        --workdir /var/tmp/moondelta-s5/run \
        --rounds 100

Before the first 100-round run, use `--trace` on the same QEMU/guest to prove
every boundary is reachable:

    python3 run_powerloss.py ... --trace
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import queue
import random
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import verify_round  # noqa: E402  (same directory)

BOUNDARY_FILE = HERE / "boundaries.json"
NBD_SCRIPT = HERE / "volatile_nbd.py"
ENV_GATE = HERE / "check_environment.py"

BOOT_TIMEOUT = 180.0
TARGET_TIMEOUT = 240.0
HALT_TIMEOUT = 60.0
FSCK_GREEN = {0, 1, 2}
# The order in which one publication must reach the device, per phase.
PHASE_OPS = ["temp-created", "written", "fsynced", "linked", "dir-fsynced"]
# Cutting power at or after this boundary must never lose the commit: the
# _delta_log directory entry was already fsynced before the probe fires. Every
# other boundary may legally end with the commit present or absent, but this one
# is an assertion, not an observation.
DURABLE_BOUNDARIES = {"commit-publish-dir-fsynced"}
# The S5 exit gate is "at least 100 valid power cuts". A green run with fewer
# rounds is a smoke test, not acceptance, so it is downgraded to INVALID.
MINIMUM_ROUNDS = 100
# The target row's id is derived from the payload size and the round number
# alone. The host therefore knows the exact content that a surviving commit must
# contain *before* the cut, and never has to trust the guest's report of it.
# guest/init uses the same formula.
TARGET_ID_OFFSET = 1000
MAX_SETUP_APPENDS = TARGET_ID_OFFSET


def expected_append_row(payload_size: int, round_index: int) -> dict:
    return {"id": payload_size + TARGET_ID_OFFSET + round_index,
            "payload": "x" * payload_size}


# --------------------------------------------------------------------------
# small helpers


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ci_context() -> dict:
    """Record where the evidence came from when this runs inside CI."""
    keys = [
        "GITHUB_ACTIONS", "GITHUB_WORKFLOW", "GITHUB_JOB", "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT", "GITHUB_SHA", "GITHUB_REF_NAME",
        "RUNNER_NAME", "RUNNER_OS", "RUNNER_ARCH", "ImageOS", "ImageVersion",
    ]
    return {key: os.environ[key] for key in keys if key in os.environ}


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def run(args: list[str], timeout: float = 120.0, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, **kwargs)


def read_boundaries() -> tuple[list[str], list[str]]:
    data = json.loads(BOUNDARY_FILE.read_text(encoding="utf-8"))
    names = [entry["name"] for entry in data["boundaries"]]
    required = list(data["required"])
    unknown = [name for name in required if name not in names]
    if unknown:
        fail(f"boundaries.json declares required boundaries that do not exist: {unknown}")
    return names, required


# --------------------------------------------------------------------------
# guest process management


@dataclass
class Guest:
    """One boot: NBD server + QEMU + the ttyS1 event stream."""

    nbd: subprocess.Popen
    qemu: subprocess.Popen
    events: queue.Queue
    lines: list[str] = field(default_factory=list)
    console: Path = Path()
    event_log: Path = Path()
    trace: Path = Path()
    nbd_socket: Path | None = None
    evt_socket: Path | None = None
    _reader: threading.Thread | None = None

    def clean_sockets(self) -> None:
        # Unix sockets cannot be archived, and a leftover one makes the artifact
        # upload fail with "zip creation" errors, so they never survive a round.
        for path in (self.nbd_socket, self.evt_socket):
            if path is not None:
                path.unlink(missing_ok=True)

    def next_line(self, timeout: float) -> str | None:
        try:
            line = self.events.get(timeout=timeout)
        except queue.Empty:
            return None
        if line is None:  # stream closed
            return None
        return line

    def cut(self) -> dict:
        """Kill QEMU first (its cache dies), then NBD (the device cache dies)."""
        stamps = {}
        start = time.monotonic()
        self.qemu.kill()
        stamps["qemuKilledMs"] = round((time.monotonic() - start) * 1000, 3)
        self.nbd.kill()
        stamps["nbdKilledMs"] = round((time.monotonic() - start) * 1000, 3)
        for name, process in (("qemu", self.qemu), ("nbd", self.nbd)):
            try:
                process.wait(timeout=30)
                stamps[f"{name}Exit"] = process.returncode
            except subprocess.TimeoutExpired:
                process.kill()
                stamps[f"{name}Exit"] = "timeout"
        return stamps

    def shutdown(self) -> None:
        for process in (self.qemu, self.nbd):
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    pass
        self.clean_sockets()

    def kill_only_qemu(self) -> None:
        if self.qemu.poll() is None:
            self.qemu.kill()
            try:
                self.qemu.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass


def start_guest(
    args: argparse.Namespace,
    image: Path,
    image_size: int,
    workdir: Path,
    round_index: int,
    mode: str,
    boundary: str,
    setup: int,
) -> Guest:
    stem = f"{round_index:03d}-{mode}"
    nbd_sock = workdir / f"{stem}.nbd.sock"
    evt_sock = workdir / f"{stem}.evt.sock"
    nbd_trace = workdir / f"{stem}.nbd.trace"
    console = workdir / f"{stem}.console.log"
    event_log = workdir / f"{stem}.events.log"
    for path in (nbd_sock, evt_sock):
        path.unlink(missing_ok=True)

    nbd_out = open(workdir / f"{stem}.nbd.log", "w", encoding="utf-8")
    nbd = subprocess.Popen(
        [
            sys.executable, str(NBD_SCRIPT),
            "--image", str(image),
            "--size", str(image_size),
            "--socket", str(nbd_sock),
            "--trace", str(nbd_trace),
        ],
        stdout=nbd_out,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if nbd.poll() is not None:
            nbd_out.close()
            fail(f"NBD server exited before listening (see {stem}.nbd.log)")
        if nbd_sock.exists():
            break
        time.sleep(0.05)
    else:
        nbd.kill()
        nbd_out.close()
        fail("NBD server did not create its socket within 30s")

    console_handle = open(console, "wb")
    cmdline = " ".join(
        [
            "console=ttyS0",
            "rdinit=/init",
            "panic=1",
            "quiet",
            f"moondelta.mode={mode}",
            f"moondelta.disk=/dev/vda",
            f"moondelta.mount=/mnt/data",
            f"moondelta.table=/mnt/data/table",
            f"moondelta.payload={args.payload}",
            f"moondelta.setup={setup}",
            f"moondelta.boundary={boundary or 'none'}",
            f"moondelta.round={round_index}",
        ]
    )
    qemu_args = [
        args.qemu,
        "-machine", "q35,accel=kvm:tcg",
        "-m", str(args.memory_mib),
        "-smp", "1",
        "-kernel", str(args.kernel),
        "-initrd", str(args.initramfs),
        "-append", cmdline,
        "-chardev", f"socket,id=evt,path={evt_sock},server=on,wait=off",
        "-chardev", f"file,id=cons,path={console}",
        "-serial", "chardev:cons",
        "-serial", "chardev:evt",
        "-blockdev",
        f"driver=nbd,server.type=unix,server.path={nbd_sock},export=powerloss,node-name=delta_data",
        "-device", "virtio-blk-pci,drive=delta_data",
        "-display", "none",
        "-no-reboot",
        "-no-shutdown",
    ]
    qemu_log = open(workdir / f"{stem}.qemu.log", "w", encoding="utf-8")
    qemu = subprocess.Popen(qemu_args, stdout=qemu_log, stderr=subprocess.STDOUT)

    events: queue.Queue = queue.Queue()
    event_handle = open(event_log, "w", encoding="utf-8", buffering=1)
    guest = Guest(
        nbd=nbd, qemu=qemu, events=events,
        console=console, event_log=event_log, trace=nbd_trace,
        nbd_socket=nbd_sock, evt_socket=evt_sock,
    )

    def reader() -> None:
        connection = None
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and qemu.poll() is None:
            candidate = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                candidate.connect(str(evt_sock))
                connection = candidate
                break
            except OSError:
                candidate.close()
                time.sleep(0.05)
        if connection is None:
            events.put(None)
            return
        with connection:
            stream = connection.makefile("r", encoding="utf-8", errors="replace")
            for line in stream:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                guest.lines.append(line)
                event_handle.write(line + "\n")
                events.put(line)
        events.put(None)

    guest._reader = threading.Thread(target=reader, daemon=True)
    guest._reader.start()
    return guest


# --------------------------------------------------------------------------
# verification of a cut image


def mount_ro(image: Path, mountpoint: Path) -> None:
    mountpoint.mkdir(parents=True, exist_ok=True)
    completed = run(["mount", "-o", "ro,loop", str(image), str(mountpoint)])
    if completed.returncode != 0:
        raise RuntimeError(f"mount failed: {completed.stderr.strip()}")


def unmount(mountpoint: Path) -> None:
    for attempt in range(3):
        completed = run(["umount", str(mountpoint)])
        if completed.returncode == 0:
            return
        if attempt == 2:
            run(["umount", "-l", str(mountpoint)])
            return
        time.sleep(1)


def verify_image(
    args: argparse.Namespace,
    image: Path,
    workdir: Path,
    round_index: int,
    keep: bool,
    expected: dict | None = None,
    capture_baseline: bool = False,
) -> dict:
    """fsck a copy (the reboot), mount it read-only, verify, then throw it away.

    `expected` is the state the host recorded before this cut; when it is given
    the verification covers content, not just structure. `capture_baseline` is
    for the clean setup boot, which has no earlier state: it records the state
    the following rounds must start from.
    """
    record: dict = {"performed": True}
    scratch = workdir / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    copy = scratch / f"check-{round_index:03d}.raw"
    if copy.exists():
        copy.unlink()
    started = time.monotonic()
    copied = run(["cp", "--reflink=auto", "--sparse=always", str(image), str(copy)], timeout=900)
    if copied.returncode != 0:
        record.update({"performed": False, "error": f"copy failed: {copied.stderr.strip()}"})
        return record
    record["copySeconds"] = round(time.monotonic() - started, 2)

    fsck = run(["fsck.ext4", "-fy", str(copy)], timeout=900)
    record["fsck"] = {
        "returncode": fsck.returncode,
        "output": (fsck.stdout + fsck.stderr).strip()[:4000],
        "accepted": fsck.returncode in FSCK_GREEN,
    }
    if fsck.returncode not in FSCK_GREEN:
        record["problems"] = [f"fsck rejected the image (exit {fsck.returncode})"]
        if not keep:
            copy.unlink(missing_ok=True)
        return record

    mountpoint = scratch / f"mnt-{round_index:03d}"
    try:
        mount_ro(copy, mountpoint)
    except RuntimeError as error:
        record["problems"] = [str(error)]
        if not keep:
            copy.unlink(missing_ok=True)
        return record
    try:
        table = mountpoint / args.table_rel
        if not table.is_dir():
            record["problems"] = [f"table directory missing after recovery: {args.table_rel}"]
            record["report"] = None
        else:
            report = verify_round.verify_table(
                table,
                Path(args.delta_cli) if args.delta_cli else None,
                expected=expected,
                capture_baseline=capture_baseline,
            )
            record["report"] = report
            record["problems"] = list(report["problems"])
            record["observedVersion"] = report["latest"]
            parsed = report.get("inspectParsed") or {}
            record["observedRows"] = parsed.get("rows")
    finally:
        unmount(mountpoint)

    record["keptImage"] = str(copy) if keep else None
    if not keep and not record.get("problems"):
        copy.unlink(missing_ok=True)
    return record


def judge_round(
    record: dict,
    base_version: int | None,
    require_host_verify: bool,
    require_delta_rs: bool = True,
) -> None:
    """Decide a single round's status from the evidence it collected.

    INVALID means "this round proves nothing" (no cut happened, the wrong
    boundary fired, verification could not run). FAILED means "the round ran and
    the recovered state violates an invariant". They must never be merged: a
    matrix with invalid rounds cannot be a pass, and a failed round is a defect.
    """
    invalid: list[str] = []
    failed: list[str] = []

    if record.get("guestError"):
        invalid.append(f"guest reported an error: {record['guestError']}")
    if not record.get("cut"):
        invalid.append("round was not cut at an announced boundary")
    elif record.get("boundaryAnnounced") != record.get("boundaryRequested"):
        invalid.append(
            f"announced boundary {record.get('boundaryAnnounced')!r} is not the requested "
            f"{record.get('boundaryRequested')!r}"
        )

    verification = record.get("verification") or {}
    if require_host_verify:
        if not verification.get("performed"):
            invalid.append(
                "host verification could not run: "
                + str(verification.get("error") or verification.get("problems") or "unknown")
            )
        observed = verification.get("observedVersion")
        if observed is None:
            failed.append("verification found no recoverable version")
        elif base_version is not None and observed not in (base_version, base_version + 1):
            failed.append(
                f"recovered version {observed} is neither {base_version} (commit lost) "
                f"nor {base_version + 1} (commit complete)"
            )
        rows = verification.get("observedRows")
        if observed is not None and rows is not None and rows != observed:
            failed.append(
                f"row count {rows} does not equal the number of surviving appends {observed}"
            )
        report = verification.get("report") or {}
        content = report.get("content") or {}
        if not content.get("branch"):
            failed.append(
                "no expected content was compared for this round, so schema, row values and "
                "data-file digests are unproven (the round has no host-recorded baseline)"
            )
        elif not content.get("ok"):
            reasons = "; ".join(content.get("problems") or ["no detail recorded"])
            failed.append(f"content verification failed: {reasons}")
        else:
            # The two engines, the recorded expectation and the structural view
            # must all report the same thing. Checking only that "delta-rs passed"
            # is what let a forged row count through before.
            project = content.get("project") or {}
            second = content.get("deltaRs") or {}
            counts = {
                "project": project.get("rows"),
                "deltaRs": second.get("rows"),
                "expected": content.get("expectedRows"),
                "inspect": rows,
                "reportedDeltaRs": (report.get("deltaRs") or {}).get("rows"),
            }
            missing = [name for name, value in counts.items() if value is None]
            if missing:
                failed.append(
                    f"row counts are missing for {missing}; content equality cannot be judged"
                )
            elif len(set(counts.values())) != 1:
                failed.append(f"row counts disagree across engines/expectation: {counts}")
            digests = {
                "project": project.get("tableDigest"),
                "deltaRs": second.get("tableDigest"),
                "expected": content.get("expectedTableDigest"),
            }
            if any(value is None for value in digests.values()):
                failed.append(f"content digests are missing: {digests}")
            elif len(set(digests.values())) != 1:
                failed.append(f"content digests disagree across engines/expectation: {digests}")
        if observed is not None and base_version is not None:
            survived = observed == base_version + 1
            record["commitOutcome"] = "survived" if survived else "lost"
            if not survived and record.get("boundaryRequested") in DURABLE_BOUNDARIES:
                failed.append(
                    f"the commit was lost although power was cut at "
                    f"{record['boundaryRequested']}, after the _delta_log directory "
                    "entry had already been synced"
                )
        for problem in verification.get("problems", []):
            if "fsck rejected" in problem or "mount failed" in problem:
                invalid.append(problem)
            else:
                failed.append(problem)
        delta_rs = ((verification.get("report") or {}).get("deltaRs") or {}).get("status")
        record["deltaRs"] = delta_rs
        if delta_rs == "PASSED":
            pass
        elif delta_rs == "SKIPPED":
            # The S5 exit criteria require a second engine to read the recovered
            # tables; a smoke run may skip it, an evidence run may not.
            if require_delta_rs:
                failed.append(
                    "delta-rs cross-read was skipped; an evidence run needs a second "
                    "engine (install deltalake on the interpreter the harness runs under)"
                )
        else:
            failed.append(f"delta-rs cross-read did not pass ({delta_rs})")
        record["observedVersion"] = observed

    if failed:
        record["status"] = "FAILED"
    elif invalid:
        record["status"] = "INVALID"
    else:
        record["status"] = "PASS"
    record["reasons"] = failed + invalid
    record["invalidReasons"] = invalid
    record["failedReasons"] = failed


def baseline_from_report(report: dict) -> dict | None:
    """Turn a passing verification into the expectation for the next round.

    The host records this from its own read of the cut image — the state the
    next boot will replay into — so no later comparison ever trusts the guest.
    """
    content = report.get("content") or {}
    project = content.get("project") or {}
    if not content.get("ok") or not content.get("branch"):
        return None
    if project.get("status") != "PASSED" or not isinstance(project.get("rowDigests"), list):
        return None
    return {
        "baseVersion": report.get("latest"),
        "schemaString": report.get("schemaString"),
        "rowDigests": project["rowDigests"],
        "files": content.get("files") or {},
    }


# --------------------------------------------------------------------------
# phases


def positive_control(workdir: Path) -> dict:
    directory = workdir / "nbd-selftest"
    shutil.rmtree(directory, ignore_errors=True)
    completed = run([sys.executable, str(NBD_SCRIPT), "--self-test", str(directory)], timeout=300)
    output = (completed.stdout + completed.stderr).strip()
    # The self-test directory is throwaway; keep the verdict, not the scratch.
    shutil.rmtree(directory, ignore_errors=True)
    return {
        "ok": completed.returncode == 0 and "PASS" in output,
        "output": output[-2000:],
        "note": "Proves the model discards unflushed writes and keeps completed flushes. "
                "If this fails the whole matrix is INVALID, not merely inconvenient.",
    }


def collect_until(
    guest: Guest,
    predicate,
    timeout: float,
) -> tuple[bool, list[str], float]:
    """Read events until predicate(line) is true or the deadline passes."""
    seen: list[str] = []
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, seen, timeout
        line = guest.next_line(min(remaining, 5.0))
        if line is None:
            if guest.qemu.poll() is not None and guest.events.empty():
                return False, seen, timeout - remaining
            continue
        seen.append(line)
        if predicate(line):
            if line.startswith("GUEST_ERROR"):
                # Give the guest a moment to flush its follow-up diagnostics
                # (mount error text, dmesg tail) before we cut it off.
                time.sleep(1.0)
                while True:
                    try:
                        trailing = guest.events.get(timeout=0.2)
                    except queue.Empty:
                        break
                    if trailing is None:
                        break
                    seen.append(trailing)
                    guest.lines.append(trailing)
            return True, seen, timeout - (deadline - time.monotonic())


def boot_and_wait_ready(guest: Guest) -> tuple[bool, list[str]]:
    ok, seen, _ = collect_until(
        guest,
        lambda line: line.startswith("GUEST_READY") or line.startswith("GUEST_ERROR"),
        BOOT_TIMEOUT,
    )
    return ok, seen


def run_prepare(args: argparse.Namespace, image: Path, size: int, workdir: Path) -> dict:
    print("[prepare] clean setup boot: create the table and commit the baseline versions")
    guest = start_guest(args, image, size, workdir, 0, "prepare", "", args.setup)
    record: dict = {"mode": "prepare", "status": "INVALID", "reasons": []}
    try:
        ready, seen = boot_and_wait_ready(guest)
        record["handshake"] = seen
        if not ready:
            record["reasons"].append("guest never reported GUEST_READY")
            return record
        ok, seen, _ = collect_until(
            guest,
            lambda line: line.startswith("ROUND_COMPLETE") or line.startswith("GUEST_ERROR"),
            TARGET_TIMEOUT,
        )
        record["events"] = seen
        if not ok:
            record["reasons"].append("prepare boot did not complete its clean appends")
            return record
        for line in seen:
            if line.startswith("GUEST_ERROR"):
                record["reasons"].append(line)
        guest.kill_only_qemu()
        guest.shutdown()
    finally:
        guest.shutdown()

    transcript = list(guest.lines)
    record["transcript"] = transcript[-80:]
    # A baseline boot that finds an existing table is a setup mistake, not a
    # storage failure: the version accounting would silently start from the wrong
    # offset. Detect it from the guest's own transcript instead of guessing.
    record["tableCreatedByThisRun"] = any(
        line.startswith("WORKER_INIT") for line in transcript
    )
    if not record["tableCreatedByThisRun"]:
        record["staleDisk"] = True
        record["reasons"].append(
            "the data disk already contained a Delta table: a baseline boot must start "
            "from a freshly formatted image (running --trace or a previous matrix on the "
            "same image breaks the version accounting)"
        )
        record["status"] = "INVALID"
        return record

    verification = verify_image(
        args, image, workdir, 0, keep=not record["reasons"], capture_baseline=True
    )
    record["verification"] = verification
    if verification.get("problems"):
        record["reasons"].extend(verification["problems"])
    observed = verification.get("observedVersion")
    if observed is None:
        record["reasons"].append("prepare verification found no version")
    elif observed != args.setup:
        record["reasons"].append(
            f"prepare expected version {args.setup} (init + {args.setup} clean appends), "
            f"found {observed}"
        )
    baseline = baseline_from_report(verification.get("report") or {})
    record["baseline"] = {
        "version": (baseline or {}).get("baseVersion"),
        "rows": len((baseline or {}).get("rowDigests") or []),
        "tableDigest": (
            verify_round.table_digest(baseline["rowDigests"]) if baseline else None
        ),
    }
    if baseline is None:
        record["reasons"].append(
            "the clean setup state could not be recorded as a baseline, so no round would "
            "have an expectation to compare against"
        )
    record["baseVersion"] = observed
    record["status"] = "PASS" if not record["reasons"] else "FAILED"
    if record["status"] == "PASS":
        record["baselineState"] = baseline
    return record


def run_inject_round(
    args: argparse.Namespace,
    image: Path,
    size: int,
    workdir: Path,
    index: int,
    boundary: str,
    base_version: int | None,
    baseline: dict | None,
) -> dict:
    print(f"[round {index:03d}] cut at {boundary}")
    record: dict = {
        "index": index,
        "boundaryRequested": boundary,
        "baseVersion": base_version,
        "startedAt": now(),
    }
    guest = start_guest(args, image, size, workdir, index, "inject", boundary, 0)
    try:
        ready, handshake = boot_and_wait_ready(guest)
        record["handshake"] = handshake
        if not ready:
            record["cut"] = False
            record["guestError"] = "GUEST_READY never arrived (boot or handshake failure)"
            record["verification"] = {"performed": False}
            judge_round(record, base_version, require_host_verify=False, require_delta_rs=not args.smoke_test)
            return record
        for line in handshake:
            if line.startswith("GUEST_ERROR"):
                record["guestError"] = line

        ok, seen, _ = collect_until(
            guest,
            lambda line: line.startswith("BOUNDARY")
            or line.startswith("TARGET_APPEND")
            or line.startswith("ROUND_COMPLETE")
            or line.startswith("GUEST_ERROR"),
            TARGET_TIMEOUT,
        )
        record["events"] = seen
        announced = None
        for line in seen:
            if line.startswith("BOUNDARY"):
                fields = dict(
                    part.split("=", 1) for part in line.split()[1:] if "=" in part
                )
                announced = fields.get("name")
            if line.startswith("GUEST_ERROR"):
                record["guestError"] = line
        record["boundaryAnnounced"] = announced

        if announced:
            record["cut"] = True
            record["cutTiming"] = guest.cut()
        else:
            # The append returned without hitting the boundary: the guest never
            # simulated anything, so this round is not evidence.
            record["cut"] = False
            record["cutTiming"] = None
            if not ok:
                record["reasons"] = ["no boundary event before the timeout"]
    finally:
        guest.shutdown()

    if not record.get("cut"):
        record["verification"] = {"performed": False}
        judge_round(record, base_version, require_host_verify=False, require_delta_rs=not args.smoke_test)
        return record

    keep = record.get("boundaryAnnounced") != record.get("boundaryRequested")
    expected = None
    if baseline is not None:
        expected = dict(baseline)
        expected["appendRow"] = expected_append_row(args.payload, index)
    verification = verify_image(args, image, workdir, index, keep=keep, expected=expected)
    record["verification"] = verification
    judge_round(record, base_version, require_host_verify=True, require_delta_rs=not args.smoke_test)
    if record["status"] == "PASS":
        carried = baseline_from_report(verification.get("report") or {})
        if carried is None:
            record["status"] = "FAILED"
            record["failedReasons"].append(
                "the verified state could not be recorded as the next round's baseline"
            )
            record["reasons"] = record["failedReasons"] + record.get("invalidReasons", [])
        else:
            record["baseline"] = {
                "version": carried["baseVersion"],
                "rows": len(carried["rowDigests"]),
                "tableDigest": verify_round.table_digest(carried["rowDigests"]),
            }
            record["baselineState"] = carried
    record["finishedAt"] = now()
    return record


def run_trace(args: argparse.Namespace, image: Path, size: int, workdir: Path,
              names: list[str], required: list[str]) -> int:
    """Boot once with the probe in announce-only mode and check boundary coverage."""
    print("[trace] one boot, probe announces every boundary without blocking")
    guest = start_guest(args, image, size, workdir, 0, "trace", "", 0)
    lines: list[str] = []
    try:
        ready, handshake = boot_and_wait_ready(guest)
        lines.extend(handshake)
        if not ready:
            print("trace failed: guest never reported GUEST_READY")
            return 2
        ok, seen, _ = collect_until(
            guest,
            lambda line: line.startswith("ROUND_COMPLETE") or line.startswith("GUEST_ERROR"),
            TARGET_TIMEOUT,
        )
        lines.extend(seen)
        if not ok:
            print("trace failed: the traced append never completed")
            return 2
        guest.kill_only_qemu()
    finally:
        guest.shutdown()

    sequence = []
    for line in lines:
        if line.startswith("BOUNDARY"):
            fields = dict(part.split("=", 1) for part in line.split()[1:] if "=" in part)
            sequence.append(fields.get("name"))
    missing = [name for name in required if name not in sequence]

    # A globally sorted order is the wrong assertion: the cleanup boundary fires
    # once while the intent's own temporary file is removed, long before the data
    # is published. What must hold is the durability ordering per phase, and that
    # publication is separated by directory syncs.
    def first(name: str) -> int | None:
        return sequence.index(name) if name in sequence else None

    order_problems: list[str] = []
    for phase in ("intent-publish", "data-publish", "commit-publish"):
        positions = [first(f"{phase}-{op}") for op in PHASE_OPS]
        if any(position is None for position in positions):
            order_problems.append(f"{phase}: incomplete boundary set")
        elif positions != sorted(positions):
            order_problems.append(f"{phase}: boundaries out of order ({positions})")

    data_synced = first("data-publish-dir-fsynced")
    commit_started = first("commit-publish-temp-created")
    if data_synced is not None and commit_started is not None and commit_started < data_synced:
        order_problems.append(
            "the JSON commit is published before the data directory is synced"
        )
    commit_linked = first("commit-publish-linked")
    cleanup_positions = [index for index, name in enumerate(sequence) if name == "cleanup-unlinked"]
    if commit_linked is not None and not any(index > commit_linked for index in cleanup_positions):
        order_problems.append("no cleanup boundary was announced after the commit became visible")
    if first("intent-publish-dir-fsynced") is not None and data_synced is not None:
        if first("intent-publish-dir-fsynced") > data_synced:
            order_problems.append("data publication started before the intent was durable")

    order_ok = not order_problems
    report = {
        "sequence": sequence,
        "required": required,
        "missing": missing,
        "orderOk": order_ok,
        "orderProblems": order_problems,
        "unknownNames": [name for name in sequence if name not in names],
        "guestLines": lines,
    }
    (workdir / f"boundary-trace-{int(time.time())}.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(f"[trace] announced {len(sequence)} boundaries")
    if missing:
        print(f"[trace] MISSING required boundaries: {missing}")
    if not order_ok:
        print("[trace] required boundaries were announced out of order")
    if missing or not order_ok:
        print("[trace] trace INVALID — do not start the 100-round matrix")
        return 2
    print("[trace] all required boundaries reachable; the matrix may run")
    return 0


# --------------------------------------------------------------------------


def qemu_version(qemu: str) -> list[str]:
    try:
        return run([qemu, "--version"]).stdout.splitlines()[:1]
    except (OSError, subprocess.SubprocessError):
        return []


def preflight(args: argparse.Namespace) -> dict:
    report: dict = {"checks": {}, "problems": []}
    checks = report["checks"]
    checks["hostIsNotWsl"] = "microsoft" not in platform.release().lower()
    checks["qemu"] = shutil.which(args.qemu) is not None
    checks["fsckExt4"] = shutil.which("fsck.ext4") is not None
    checks["image"] = args.image.is_file()
    checks["kernel"] = args.kernel.is_file()
    checks["initramfs"] = args.initramfs.is_file()
    checks["deltaCli"] = bool(args.delta_cli and Path(args.delta_cli).is_file())
    checks["runningAsRoot"] = getattr(os, "geteuid", lambda: -1)() == 0
    if args.image.is_file():
        try:
            probe = run(["findmnt", "-n", "-S", str(args.image)])
            checks["imageNotMounted"] = not (probe.returncode == 0 and probe.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            checks["imageNotMounted"] = False
    else:
        checks["imageNotMounted"] = False

    if not checks["hostIsNotWsl"]:
        if args.smoke_test:
            report["smokeTestWarning"] = (
                "host is WSL: this run exercises the plumbing only and every result "
                "is forced to INVALID; it can never be S5 evidence"
            )
        else:
            report["problems"].append(
                "this host is WSL: dirty pages can survive in the Windows host, so the "
                "cache-loss model cannot be trusted"
            )
    if not checks["runningAsRoot"]:
        report["problems"].append("root is required for loop mounts and fsck in verification")
    for key in ("qemu", "fsckExt4", "image", "kernel", "initramfs", "deltaCli",
                "imageNotMounted"):
        if not checks[key]:
            report["problems"].append(f"precondition failed: {key}")
    # Keep the standalone gate's verdict in the same evidence bundle.
    try:
        gate = run(
            [
                sys.executable, str(ENV_GATE),
                "--qemu", args.qemu,
                "--image", str(args.image),
                "--kernel", str(args.kernel),
            ],
            timeout=120,
        )
        report["environmentGate"] = {
            "returncode": gate.returncode,
            "output": (gate.stdout + gate.stderr).strip()[-4000:],
        }
    except (OSError, subprocess.SubprocessError) as error:
        report["environmentGate"] = {"returncode": None, "output": f"gate failed to run: {error}"}

    report["environment"] = {
        "platform": platform.platform(),
        "kernel": platform.release(),
        "qemu": qemu_version(args.qemu),
        "kvm": Path("/dev/kvm").exists(),
        "acceleration": "kvm" if Path("/dev/kvm").exists() else "tcg",
        "image": str(args.image),
        "imageBytes": args.image.stat().st_size if args.image.is_file() else None,
        "initramfs": str(args.initramfs),
        "kernelImage": str(args.kernel),
        "deltaCli": str(args.delta_cli),
        "python": sys.version.split()[0],
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", type=Path, required=True, help="disposable raw ext4 data disk")
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--initramfs", type=Path, required=True)
    parser.add_argument("--delta-cli", type=Path, default=None,
                        help="host-built delta binary used to read recovered tables")
    parser.add_argument("--workdir", type=Path, default=Path("/var/tmp/moondelta-s5/run"),
                        help="evidence directory (never on the guest disk)")
    parser.add_argument("--rounds", type=int, default=100)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--setup", type=int, default=2, help="clean appends before the cut")
    parser.add_argument("--payload", type=int, default=8192, help="payload bytes per appended row")
    parser.add_argument("--table-rel", default="table", help="table path inside the data disk")
    parser.add_argument("--qemu", default="qemu-system-x86_64")
    parser.add_argument("--memory-mib", type=int, default=512)
    parser.add_argument("--trace", action="store_true",
                        help="announce-only boot that checks boundary coverage, no cut")
    parser.add_argument("--skip-positive-control", action="store_true",
                        help="never use this for evidence runs; it makes the matrix INVALID")
    parser.add_argument("--smoke-test", action="store_true",
                        help="exercise the plumbing on a host the gate rejects (e.g. WSL); "
                             "every result is forced to INVALID and can never be evidence")
    args = parser.parse_args()

    if not 0 <= args.setup < MAX_SETUP_APPENDS:
        fail(
            f"--setup must be between 0 and {MAX_SETUP_APPENDS - 1}: setup row ids come from "
            "the same id space the target rows use"
        )

    names, required = read_boundaries()
    args.workdir.mkdir(parents=True, exist_ok=True)
    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(2**31)
    rng = random.Random(seed)
    size = args.image.stat().st_size if args.image.is_file() else 0

    environment = preflight(args)
    if environment["problems"]:
        print(json.dumps(environment, indent=2))
        print("BLOCKED: preconditions for a valid power-loss experiment are not met")
        return 2

    if args.trace:
        return run_trace(args, args.image, size, args.workdir, names, required)

    control = positive_control(args.workdir)
    print(f"[control] volatile cache model: {'PASS' if control['ok'] else 'INVALID'}")
    if not control["ok"] and not args.skip_positive_control:
        print(json.dumps(control, indent=2))
        print("INVALID: the cache-loss model did not demonstrate its own semantics")
        return 2

    plan: list[str] = []
    while len(plan) < args.rounds:
        block = list(names)
        rng.shuffle(block)
        plan.extend(block)
    plan = plan[: args.rounds]

    prepared = run_prepare(args, args.image, size, args.workdir)
    print(f"[prepare] {prepared['status']} baseVersion={prepared.get('baseVersion')}")
    records: list[dict] = []
    base = prepared.get("baseVersion")
    baseline = prepared.get("baselineState")
    if prepared["status"] != "PASS" or base is None or baseline is None:
        setup_status = "INVALID" if prepared.get("staleDisk") else "FAILED"
        results = {
            "formatVersion": 1,
            "status": setup_status,
            "generatedAt": now(),
            "ci": ci_context(),
            "seed": seed,
            "preflight": environment,
            "environment": environment.get("environment"),
            "positiveControl": control,
            "prepare": prepared,
            "rounds": [],
            "summary": {"note": "the clean setup boot failed; no cut was attempted"},
        }
        (args.workdir / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        if setup_status == "INVALID":
            print("INVALID: start from a freshly formatted data disk, then re-run")
            return 2
        print("FAILED: the setup boot is not sound, so the matrix cannot start")
        return 1

    for index in range(1, args.rounds + 1):
        record = run_inject_round(
            args, args.image, size, args.workdir, index, plan[index - 1], base, baseline
        )
        records.append(record)
        if record.get("observedVersion") is not None:
            base = record["observedVersion"]
        if record["status"] == "PASS" and record.get("baselineState") is not None:
            baseline = record["baselineState"]
        print(
            f"[round {index:03d}] {record['status']:<7} boundary={record.get('boundaryAnnounced')} "
            f"version={record.get('observedVersion')} (base was {record.get('baseVersion')})"
        )
        if record["status"] != "PASS":
            # Never continue after a round that either broke an invariant or did
            # not prove anything: the baseline version would be wrong and every
            # later invariant would be judged against corrupt state.
            print("stopping: fix and explain this round before the matrix continues")
            break

    exercised: dict[str, int] = {}
    outcomes: dict[str, dict[str, int]] = {}
    for record in records:
        name = record.get("boundaryAnnounced")
        if name:
            exercised[name] = exercised.get(name, 0) + 1
            bucket = outcomes.setdefault(name, {"rounds": 0, "commitSurvived": 0, "commitLost": 0})
            bucket["rounds"] += 1
            if record.get("commitOutcome") == "survived":
                bucket["commitSurvived"] += 1
            elif record.get("commitOutcome") == "lost":
                bucket["commitLost"] += 1
    missing = [name for name in required if name not in exercised]
    statuses = [record["status"] for record in records]
    if "FAILED" in statuses:
        status = "FAILED"
    elif "INVALID" in statuses or len(records) < args.rounds or missing:
        status = "INVALID"
    elif all(record["status"] == "PASS" for record in records):
        status = "PASS"
    else:
        status = "INVALID"
    if status == "PASS" and len(records) < MINIMUM_ROUNDS:
        status = "INVALID"
        print(
            f"only {len(records)} rounds ran; the S5 exit gate needs {MINIMUM_ROUNDS} "
            "valid cuts, so this is a smoke test and cannot be recorded as a pass"
        )
    if args.smoke_test and status == "PASS":
        status = "INVALID"
        print("SMOKE TEST: plumbing verified, but this host cannot produce S5 evidence")

    results = {
        "formatVersion": 1,
        "status": status,
        "generatedAt": now(),
        "smokeTest": bool(args.smoke_test),
        "ci": ci_context(),
        "seed": seed,
        "claimScope": (
            "This result, if PASS, certifies only this exact combination of QEMU version, "
            "guest kernel, ext4 feature set and mount options, volatile-NBD device model, "
            "and writer build recorded below. It says nothing about NTFS, macOS, WSL "
            "/mnt/*, NFS/SMB/FUSE, or real hardware caches."
        ),
        "preflight": environment,
        "environment": environment.get("environment"),
        "positiveControl": control,
        "prepare": prepared,
        "boundaries": {
            "requested": plan,
            "exercised": exercised,
            "outcomes": outcomes,
            "durableBoundaries": sorted(DURABLE_BOUNDARIES),
            "required": required,
            "missing": missing,
        },
        "rounds": records,
        "summary": {
            "minimumRounds": MINIMUM_ROUNDS,
            "rounds": len(records),
            "pass": statuses.count("PASS"),
            "failed": statuses.count("FAILED"),
            "invalid": statuses.count("INVALID"),
            "commitSurvived": sum(
                1 for record in records
                if record.get("observedVersion") is not None
                and record.get("baseVersion") is not None
                and record["observedVersion"] == record["baseVersion"] + 1
            ),
            "commitLost": sum(
                1 for record in records
                if record.get("observedVersion") is not None
                and record.get("baseVersion") is not None
                and record["observedVersion"] == record["baseVersion"]
            ),
        },
    }
    output = args.workdir / f"results-{args.rounds}.json"
    output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nstatus : {status}")
    print(f"results: {output}")
    if status != "PASS":
        print("S5 remains NOT_PASSED; the recorded reasons are in the results file")
        return 1 if status == "FAILED" else 2
    print("S5 matrix PASSED for the recorded configuration only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
