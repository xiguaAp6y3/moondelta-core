#!/usr/bin/env python3
"""Build the minimal guest for the S5 power-loss harness.

The guest is a kernel + initramfs: no disk rootfs, no network, no service
manager. It only needs to mount the volatile-NBD data disk, run the project's
own writer, and report over a serial line.

This script must run on the same Linux host (and with the same kernel package)
that will boot the guest, because it lifts the running kernel's modules.

    python3 mk_initramfs.py \
        --kernel /boot/vmlinuz-$(uname -r) \
        --worker _build/native/debug/build/examples/atomic_commit_worker/atomic_commit_worker.exe \
        --out /var/tmp/moondelta-s5/initramfs.cpio.gz

Refuses to build silently-degraded images: a missing busybox, a missing kernel
module, or an unreadable writer binary is an error, not a warning.
"""
from __future__ import annotations

import argparse
import gzip
import os
import shutil
import subprocess
import sys
from pathlib import Path

APPLETS = [
    "sh", "ash", "mount", "umount", "mkdir", "rmdir", "cat", "sed", "head", "tail",
    "tr", "echo", "printf", "sleep", "sync", "modprobe", "insmod", "lsmod", "ls",
    "grep", "awk", "cut", "wc", "cp", "mv", "rm", "chmod", "readlink", "dirname",
    "basename", "dmesg", "mdev", "poweroff", "halt", "reboot", "env", "true",
    "false", "test", "expr", "seq", "dd", "stat", "df", "find", "xargs", "touch",
]
MODULES = ["virtio", "virtio_ring", "virtio_pci", "virtio_blk", "ext4", "jbd2"]
COMPRESSED = {".zst": ["unzstd", "zstd -d"], ".xz": ["unxz", "xz -d"], ".gz": ["gunzip", "gzip -d"]}


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def kernel_release(kernel: Path, override: str | None) -> str:
    if override:
        return override
    name = kernel.name
    for prefix in ("vmlinuz-", "bzImage-", "Image-"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return subprocess.run(
        ["uname", "-r"], capture_output=True, text=True, check=True
    ).stdout.strip()


def decompress_module(source: Path, destination: Path) -> None:
    suffix = source.suffix
    destination.parent.mkdir(parents=True, exist_ok=True)
    if suffix not in COMPRESSED:
        shutil.copy2(source, destination)
        return
    for candidate in COMPRESSED[suffix]:
        parts = candidate.split()
        if shutil.which(parts[0]) is not None:
            completed = subprocess.run(
                [*parts, str(source)], capture_output=True
            )
            if completed.returncode == 0:
                destination.write_bytes(completed.stdout)
                return
    fail(
        f"cannot decompress {source}: install the matching decompressor "
        f"({COMPRESSED[suffix][0]}) so the guest's busybox insmod gets a plain .ko"
    )


def copy_shared_libraries(binary: Path, root: Path) -> list[str]:
    try:
        completed = subprocess.run(
            ["ldd", str(binary)], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError) as error:
        fail(f"ldd failed on {binary}: {error}")
    copied = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if "=>" in line:
            target = line.split("=>", 1)[1].strip().split(" ")[0]
        elif line.startswith("/"):
            target = line.split(" ")[0]
        else:
            continue
        if not target.startswith("/"):
            continue
        source = Path(target)
        if not source.is_file():
            continue
        destination = root / source.relative_to("/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(str(source))
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--worker", type=Path, required=True,
                        help="atomic_commit_worker built for linux native")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--init", type=Path,
                        default=Path(__file__).resolve().parent / "guest" / "init")
    parser.add_argument("--busybox", default=None)
    parser.add_argument("--module-dir", type=Path, default=None)
    parser.add_argument("--release", default=None, help="override kernel release")
    parser.add_argument("--staging", type=Path, default=None,
                        help="keep the unpacked tree here instead of a temp dir")
    parser.add_argument("--allow-missing-modules", action="store_true",
                        help="build anyway when the kernel is expected to be built in")
    args = parser.parse_args()

    for tool in ("cpio",):
        if shutil.which(tool) is None:
            fail(f"{tool} is required to pack the initramfs")
    if not args.kernel.is_file():
        fail(f"kernel image not found: {args.kernel}")
    if not args.worker.is_file():
        fail(
            f"writer binary not found: {args.worker}\n"
            "build it first: moon build --target native"
        )
    if not args.init.is_file():
        fail(f"guest init script not found: {args.init}")

    version = kernel_release(args.kernel, args.release)
    staging = args.staging or Path(
        subprocess.run(
            ["mktemp", "-d", "-t", "moondelta-initramfs-XXXXXX"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    )
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    busybox_path = args.busybox or shutil.which("busybox")
    if busybox_path is None:
        fail("busybox not found; install busybox-static (it must be a static binary)")
    busybox = Path(busybox_path)
    probe = subprocess.run(["ldd", str(busybox)], capture_output=True, text=True)
    if "not a dynamic executable" not in probe.stdout + probe.stderr:
        fail(f"{busybox} is dynamically linked; the initramfs needs a static busybox")
    bin_dir = staging / "bin"
    bin_dir.mkdir(parents=True)
    shutil.copy2(busybox, bin_dir / "busybox")
    os.chmod(bin_dir / "busybox", 0o755)
    for applet in APPLETS:
        link = bin_dir / applet
        if link.exists():
            continue
        link.symlink_to("busybox")

    init_target = staging / "init"
    shutil.copy2(args.init, init_target)
    os.chmod(init_target, 0o755)

    shutil.copy2(args.worker, bin_dir / "atomic_commit_worker")
    os.chmod(bin_dir / "atomic_commit_worker", 0o755)
    libraries = copy_shared_libraries(args.worker, staging)

    module_dir = args.module_dir or Path(f"/lib/modules/{version}")
    module_files: list[Path] = []
    missing: list[str] = []
    builtin: list[str] = []
    if module_dir.is_dir():
        for name in MODULES:
            completed = subprocess.run(
                ["modprobe", "--show-depends", "-S", version, name],
                capture_output=True, text=True,
            )
            if completed.returncode != 0:
                missing.append(name)
                continue
            resolved = False
            for line in completed.stdout.splitlines():
                line = line.strip()
                if line.startswith("insmod "):
                    source = Path(line.split(" ", 1)[1])
                    if source not in module_files:
                        module_files.append(source)
                    resolved = True
                elif line.startswith("builtin "):
                    # The kernel already has this driver: nothing to copy, and
                    # nothing is missing. Treating this as "no modules found"
                    # was a real bug — ext4 is builtin on Ubuntu generic.
                    builtin.append(line.split(" ", 1)[1])
                    resolved = True
            if not resolved:
                missing.append(name)
    else:
        missing = list(MODULES)

    if module_files:
        for source in module_files:
            relative = source.relative_to(module_dir)
            plain = relative.with_suffix("") if source.suffix in COMPRESSED else relative
            if plain.suffix != ".ko":
                plain = plain.with_suffix(".ko")
            decompress_module(source, staging / "lib" / "modules" / version / plain)
        if shutil.which("depmod") is None:
            fail("depmod not found; install kmod so the guest can resolve module deps")
        subprocess.run(
            ["depmod", "-b", str(staging), version], check=True,
            capture_output=True, text=True,
        )
    elif missing or builtin:
        if missing and not args.allow_missing_modules:
            fail(
                f"cannot resolve guest modules for {version}: {', '.join(missing)}; "
                "install the kernel's modules package or pass --allow-missing-modules "
                "if this kernel really has ext4 and virtio_blk built in"
            )
    else:
        if not args.allow_missing_modules:
            fail(
                f"no loadable modules found for {version}; "
                "install the kernel's modules package or pass --allow-missing-modules "
                "if this kernel really has ext4 and virtio_blk built in"
            )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    # cpio -o reads its file list from stdin, and both cpio and the compressor
    # are byte streams: never let text mode near them.
    listing = subprocess.run(
        ["find", ".", "-print"], cwd=staging, capture_output=True, text=True, check=True,
    )
    pack = subprocess.run(
        ["cpio", "-o", "-H", "newc", "--quiet"],
        cwd=staging, input=listing.stdout.encode("utf-8"), capture_output=True,
    )
    if pack.returncode != 0:
        fail(f"cpio failed: {pack.stderr.decode('utf-8', 'replace').strip()}")
    if len(pack.stdout) < 1024:
        fail(
            f"cpio produced only {len(pack.stdout)} bytes; the staging tree at {staging} "
            "is empty, so the guest would not boot"
        )
    args.out.write_bytes(gzip.compress(pack.stdout, 9))

    print(f"kernel release : {version}")
    print(f"busybox        : {busybox}")
    print(f"libraries      : {len(libraries)}")
    print(f"modules        : {len(module_files)}"
          + (f" (built into the kernel: {', '.join(builtin)})" if builtin else ""))
    print(f"initramfs      : {args.out} ({args.out.stat().st_size} bytes)")
    if not args.staging:
        shutil.rmtree(staging, ignore_errors=True)
    else:
        print(f"staging kept   : {staging}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
