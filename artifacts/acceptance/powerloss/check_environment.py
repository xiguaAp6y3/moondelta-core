#!/usr/bin/env python3
"""Precondition gate for the S5 power-loss harness.

This script NEVER certifies S5 and never prints PASS. It only reports whether the
prerequisites for a valid power-loss experiment exist. Without every precondition
it exits with status BLOCKED (exit code 2) so that "no experiment" can never be
mistaken for "experiment succeeded".

KVM is reported as *advisory*: hardware acceleration changes how long the matrix
takes, not what it proves. The validity of the result comes from the volatile NBD
device model (unflushed writes are discarded, completed flushes survive), so a
TCG-only host can still produce valid evidence — it just needs more wall clock.
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
from pathlib import Path


def kvm_status() -> dict:
    device = Path("/dev/kvm")
    exists = device.exists()
    usable = False
    detail = "absent"
    if exists:
        try:
            with open(device, "rb"):
                usable = True
            detail = "openable"
        except OSError as error:
            detail = f"present but not openable by this user ({error.strerror})"
    return {
        "devicePresent": exists,
        "usable": usable,
        "detail": detail,
        "acceleration": "kvm" if usable else "tcg",
        "note": "advisory only: TCG keeps the same device-cache semantics but is much slower",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", default="qemu-system-x86_64")
    parser.add_argument("--image", type=Path, help="disposable raw ext4 data disk")
    parser.add_argument("--kernel", type=Path, help="guest kernel image used for the run")
    args = parser.parse_args()

    checks: dict[str, bool] = {}
    checks["qemu_present"] = shutil.which(args.qemu) is not None
    checks["image_is_regular_file"] = bool(args.image and args.image.is_file())
    checks["image_outside_wsl_mounts"] = bool(
        args.image and not str(args.image.resolve()).startswith("/mnt/")
    )
    checks["guest_kernel_file_recorded"] = bool(args.kernel and args.kernel.is_file())

    mounted = False
    if args.image:
        try:
            probe = subprocess.run(
                ["findmnt", "-n", "-S", str(args.image)],
                capture_output=True, text=True, timeout=10,
            )
            mounted = probe.returncode == 0 and bool(probe.stdout.strip())
        except (FileNotFoundError, subprocess.SubprocessError):
            mounted = False
    checks["image_not_mounted"] = not mounted

    # A WSL2 host keeps dirty pages in the Windows host: killing the guest does
    # not model device-cache loss, so it can never be accepted as evidence.
    checks["host_is_not_wsl"] = "microsoft" not in platform.release().lower()

    met = all(checks.values())
    kvm = kvm_status()
    report = {
        "status": "PRECONDITIONS_MET" if met else "BLOCKED",
        "host": platform.platform(),
        "kernel": platform.release(),
        "checks": checks,
        "kvm": kvm,
        "note": "This gate never certifies S5; power-loss evidence requires the documented matrix.",
    }
    print(json.dumps(report, indent=2))
    if met:
        print(
            "PRECONDITIONS_MET: run the documented positive-control and 100-cut matrix, "
            f"then record the evidence (acceleration={kvm['acceleration']}). "
            "S5 remains NOT_PASSED until that evidence is reviewed."
        )
        return 0
    print("BLOCKED: S5 power-loss evidence cannot be produced in this environment; no PASS is implied.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
