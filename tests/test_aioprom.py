import asyncio
import gc
import os
import re
import socket
import sys

import pytest

import aioprom
from aioprom.aioprom import TIMEOUT_429_INTERVAL, VERSION, __version__


def _run_async(coro):
    return asyncio.run(coro)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _loop_time():
    return asyncio.get_running_loop().time()


async def _get_metrics_body(host: str, port: int) -> bytes:
    reader, writer = await asyncio.open_connection(host, port)
    writer.write(
        b"GET / HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Connection: close\r\n"
        b"\r\n"
    )
    await writer.drain()
    header = await reader.readuntil(b"\r\n\r\n")
    clen = None
    for line in header.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            clen = int(line.split(b":", 1)[1].strip())
            break
    assert clen is not None
    body = await reader.readexactly(clen)
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return body


async def _await_metrics_body(host: str, port: int, timeout: float = 10.0) -> bytes:
    """Wait for the server, then fetch metrics in one HTTP request (avoids 429 from a probe connection)."""
    deadline = _loop_time() + timeout
    while _loop_time() < deadline:
        try:
            return await _get_metrics_body(host, port)
        except (ConnectionRefusedError, OSError):
            await asyncio.sleep(0.02)
    raise RuntimeError("server did not become reachable within %ss" % timeout)


def _assert_aioprom_info_in_metrics(body: bytes) -> None:
    line_pat = re.compile(
        rb'^aioprom_info\{([^}]*)\}\s+1(?:\.0)?\s*$',
        re.MULTILINE,
    )
    m = line_pat.search(body)
    assert m is not None, body.decode("utf-8", errors="replace")
    labels = m.group(1).decode("ascii")
    assert f'major="{VERSION[0]}"' in labels
    assert f'minor="{VERSION[1]}"' in labels
    assert f'patch="{VERSION[2]}"' in labels
    assert f'version="{__version__}"' in labels


def test_import_package():
    assert aioprom.__version__ == __version__
    assert callable(aioprom.start_server)


async def _run_server_once():
    port = _free_port()
    task = asyncio.create_task(aioprom.start_server("127.0.0.1", port))
    try:
        body = await _await_metrics_body("127.0.0.1", port)
        _assert_aioprom_info_in_metrics(body)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def test_start_server_serves_aioprom_info():
    _run_async(_run_server_once())


def test_cancel_start_server_no_fd_leak_linux():
    if sys.platform != "linux":
        pytest.skip("FD counting uses /proc/self/fd (Linux)")

    async def exercise():
        await asyncio.sleep(TIMEOUT_429_INTERVAL + 0.05)
        before = len(os.listdir("/proc/self/fd"))
        for _ in range(5):
            port = _free_port()
            task = asyncio.create_task(aioprom.start_server("127.0.0.1", port))
            try:
                await _await_metrics_body("127.0.0.1", port)
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await asyncio.sleep(TIMEOUT_429_INTERVAL + 0.05)
        gc.collect()
        after = len(os.listdir("/proc/self/fd"))
        assert after == before, "open fd count changed after server start/cancel cycles: %s -> %s" % (
            before,
            after,
        )

    _run_async(exercise())
