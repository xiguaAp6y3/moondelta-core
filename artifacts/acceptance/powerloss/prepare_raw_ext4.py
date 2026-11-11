#!/usr/bin/env python3
"""Create a disposable raw ext4 data disk for the QEMU S5 experiment."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--size-mib", type=int, default=2048)
    parser.add_argument("--label", default="MOONDELTA")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    image = args.image.expanduser().resolve()
    if not image.is_absolute() or str(image).startswith("/mnt/"):
        parser.error("image must be on a Linux-local filesystem, outside /mnt")
    if args.size_mib < 256:
        parser.error("data disk must be at least 256 MiB")
    if image.exists() and not args.overwrite:
        parser.error("image exists; pass --overwrite only for a disposable target")
    if not shutil.which("mkfs.ext4"):
        parser.error("mkfs.ext4 is required")

    image.parent.mkdir(parents=True, exist_ok=True)
    with image.open("wb") as stream:
        stream.truncate(args.size_mib * 1024 * 1024)
        stream.flush()
        os.fsync(stream.fileno())
    subprocess.run([
        "mkfs.ext4", "-F", "-L", args.label,
        "-E", "lazy_itable_init=0,lazy_journal_init=0", str(image),
    ], check=True)
    fd = os.open(image, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    print(f"CREATED disposable raw ext4 image: {image} ({args.size_mib} MiB)")
    print("Keep this backing image outside the guest; never mount it on the host during a run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
