#!/usr/bin/env python3
"""A volatile write-cache NBD export for QEMU power-cut experiments.

Writes are held in this process until NBD_CMD_FLUSH. Killing this process
models loss of the device's volatile cache; the backing image contains only
data made durable by completed flushes.
"""
from __future__ import annotations

import argparse
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

NBD_MAGIC = 0x4E42444D41474943
NBD_OPTIONS_MAGIC = 0x49484156454F5054
NBD_REQUEST_MAGIC = 0x25609513
NBD_REPLY_MAGIC = 0x67446698
NBD_OPT_EXPORT_NAME = 1
NBD_CMD_READ = 0
NBD_CMD_WRITE = 1
NBD_CMD_DISC = 2
NBD_CMD_FLUSH = 3
NBD_CMD_TRIM = 4
NBD_CMD_FLAG_FUA = 1
NBD_TRANSMISSION_FLAGS = 1 | 4 | 8 | 32
PAGE = 4096


def sync_fd(fd: int) -> None:
    sync = getattr(os, "fdatasync", os.fsync)
    sync(fd)


def recvall(conn: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        part = conn.recv(length - len(chunks))
        if not part:
            raise EOFError("NBD client disconnected")
        chunks.extend(part)
    return bytes(chunks)


class VolatileExport:
    def __init__(self, image: Path, size: int, trace: Path | None) -> None:
        self.image = image
        self.size = size
        self.fd = os.open(image, os.O_RDWR)
        if os.fstat(self.fd).st_size != size:
            raise ValueError(f"backing size mismatch: expected {size}")
        self.pages: dict[int, bytearray] = {}
        self.trace_path = trace

    def event(self, name: str, offset: int = 0, length: int = 0) -> None:
        if self.trace_path is not None:
            with self.trace_path.open("a", encoding="ascii") as stream:
                stream.write(f"{name} offset={offset} length={length}\n")

    def read(self, offset: int, length: int) -> bytes:
        result = bytearray(os.pread(self.fd, length, offset))
        if len(result) != length:
            raise OSError("short backing read")
        first = offset // PAGE
        last = (offset + length - 1) // PAGE if length else first - 1
        for page_index in range(first, last + 1):
            page = self.pages.get(page_index)
            if page is None:
                continue
            page_start = page_index * PAGE
            start = max(offset, page_start)
            end = min(offset + length, page_start + PAGE)
            result[start - offset:end - offset] = page[start - page_start:end - page_start]
        return bytes(result)

    def write(self, offset: int, data: bytes) -> None:
        cursor = 0
        while cursor < len(data):
            absolute = offset + cursor
            page_index, in_page = divmod(absolute, PAGE)
            amount = min(PAGE - in_page, len(data) - cursor)
            page = self.pages.get(page_index)
            if page is None:
                page = bytearray(os.pread(self.fd, PAGE, page_index * PAGE))
                if len(page) < PAGE:
                    page.extend(bytes(PAGE - len(page)))
                self.pages[page_index] = page
            page[in_page:in_page + amount] = data[cursor:cursor + amount]
            cursor += amount
        self.event("write_cached", offset, len(data))

    def flush(self) -> None:
        for page_index in sorted(self.pages):
            page = self.pages[page_index]
            offset = page_index * PAGE
            amount = min(PAGE, self.size - offset)
            written = os.pwrite(self.fd, page[:amount], offset)
            if written != amount:
                raise OSError("short backing write during FLUSH")
        sync_fd(self.fd)
        flushed_pages = len(self.pages)
        self.pages.clear()
        self.event("flush_durable", 0, flushed_pages * PAGE)

    def close(self) -> None:
        os.close(self.fd)


def serve_client(conn: socket.socket, export: VolatileExport) -> None:
    conn.sendall(struct.pack(">QQH", NBD_MAGIC, NBD_OPTIONS_MAGIC, 3))
    client_flags = struct.unpack(">I", recvall(conn, 4))[0]
    no_zeroes = bool(client_flags & 2)
    while True:
        magic, option, length = struct.unpack(">QII", recvall(conn, 16))
        if magic != NBD_OPTIONS_MAGIC:
            raise ValueError("invalid NBD option magic")
        payload = recvall(conn, length)
        if option == NBD_OPT_EXPORT_NAME:
            if payload not in (b"", b"powerloss"):
                raise ValueError("unknown NBD export name")
            response = struct.pack(">QH", export.size, NBD_TRANSMISSION_FLAGS)
            conn.sendall(response if no_zeroes else response + bytes(124))
            break
        raise ValueError(f"unsupported NBD option {option}; use EXPORT_NAME")

    while True:
        request = recvall(conn, 28)
        magic, flags, command, handle, offset, length = struct.unpack(">IHH8sQI", request)
        if magic != NBD_REQUEST_MAGIC:
            raise ValueError("invalid NBD request magic")
        error = 0
        body = b""
        try:
            if offset + length > export.size:
                raise OSError("request outside export")
            if command == NBD_CMD_READ:
                body = export.read(offset, length)
            elif command == NBD_CMD_WRITE:
                export.write(offset, recvall(conn, length))
                if flags & NBD_CMD_FLAG_FUA:
                    export.flush()
            elif command == NBD_CMD_FLUSH:
                export.flush()
            elif command == NBD_CMD_TRIM:
                export.write(offset, bytes(length))
            elif command == NBD_CMD_DISC:
                return
            else:
                raise OSError(f"unsupported NBD command {command}")
        except OSError:
            error = 5
        conn.sendall(struct.pack(">I8sI", NBD_REPLY_MAGIC, handle, error) + body)


def run_server(args: argparse.Namespace) -> int:
    socket_path = Path(args.socket)
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    export = VolatileExport(Path(args.image), args.size, Path(args.trace) if args.trace else None)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    os.chmod(socket_path, 0o600)
    listener.listen(1)
    print(f"READY {socket_path} size={args.size}", flush=True)
    try:
        conn, _ = listener.accept()
        with conn:
            serve_client(conn, export)
    except (EOFError, ConnectionError):
        pass
    finally:
        listener.close()
        export.close()
        socket_path.unlink(missing_ok=True)
    return 0


def self_test(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    image = directory / "nbd-selftest.raw"
    socket_path = directory / "nbd-selftest.sock"
    size = PAGE * 2
    fd = os.open(image, os.O_CREAT | os.O_TRUNC | os.O_RDWR, 0o600)
    os.ftruncate(fd, size)
    sync_fd(fd)
    os.close(fd)

    def start_server() -> subprocess.Popen[bytes]:
        process = subprocess.Popen([
            sys.executable, str(Path(__file__).resolve()), "--image", str(image),
            "--size", str(size), "--socket", str(socket_path),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(500):
            if socket_path.exists():
                return process
            if process.poll() is not None:
                raise RuntimeError("NBD self-test server exited before listening")
            time.sleep(0.01)
        process.kill()
        raise TimeoutError("NBD self-test server did not start")

    def connect_export() -> socket.socket:
        conn = None
        for _ in range(500):
            candidate = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                candidate.connect(str(socket_path))
                conn = candidate
                break
            except (ConnectionRefusedError, FileNotFoundError):
                candidate.close()
                time.sleep(0.01)
        if conn is None:
            raise TimeoutError("NBD self-test could not connect to export")
        recvall(conn, 18)
        conn.sendall(struct.pack(">I", 3))
        conn.sendall(struct.pack(">QII", NBD_OPTIONS_MAGIC, NBD_OPT_EXPORT_NAME, 0))
        size_wire, _flags = struct.unpack(">QH", recvall(conn, 10))
        assert size_wire == size
        return conn

    def request(conn: socket.socket, command: int, offset: int = 0,
                data: bytes = b"", length: int = 0) -> bytes:
        handle = os.urandom(8)
        amount = len(data) if command == NBD_CMD_WRITE else length
        conn.sendall(struct.pack(">IHH8sQI", NBD_REQUEST_MAGIC, 0, command,
                                handle, offset, amount) + data)
        if command == NBD_CMD_DISC:
            return b""
        magic, response_handle, error = struct.unpack(">I8sI", recvall(conn, 16))
        assert magic == NBD_REPLY_MAGIC and response_handle == handle and error == 0
        return recvall(conn, amount) if command == NBD_CMD_READ else b""

    server = start_server()
    conn = connect_export()
    request(conn, NBD_CMD_WRITE, PAGE, b"flushed")
    request(conn, NBD_CMD_FLUSH)
    request(conn, NBD_CMD_WRITE, 0, b"unflushed")
    request(conn, NBD_CMD_WRITE, PAGE + 64, b"lost-after-cut")
    conn.close()
    server.kill()  # Discard the device process's volatile cache immediately.
    server.wait(timeout=5)

    server = start_server()
    conn = connect_export()
    assert request(conn, NBD_CMD_READ, 0, length=9) == bytes(9)
    page = request(conn, NBD_CMD_READ, PAGE, length=80)
    assert page[:7] == b"flushed"
    assert page[64:78] == bytes(14)
    request(conn, NBD_CMD_DISC)
    conn.close()
    server.wait(timeout=5)
    print("PASS: volatile writes disappear; completed FLUSH survives restart")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--size", type=int)
    parser.add_argument("--socket")
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--self-test", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test(args.self_test)
        return 0
    if not args.image or not args.size or not args.socket:
        parser.error("--image, --size, and --socket are required outside --self-test")
    if not args.image.is_file() or args.image.stat().st_size != args.size:
        parser.error("--image must be an existing raw image with exactly --size bytes")
    return run_server(args)


if __name__ == "__main__":
    raise SystemExit(main())
