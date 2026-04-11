import asyncio
import sys
import time

from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, Gauge

# StreamWriter.wait_closed() is 3.7+; package requires 3.9+ for builds (setuptools>=77 / license-files).
if sys.version_info < (3, 9):
    raise RuntimeError("Python 3.9 or higher is required")

MAX_HEADER_BYTES = 16 * 1024
READ_TIMEOUT = 2.0
TIMEOUT_429_INTERVAL = 1  # Minimum seconds between requests per IP
CACHE_TIMEOUT = TIMEOUT_429_INTERVAL * 4
VERSION: tuple[int, int, int] = (1, 0, 5)
__version__: str = ".".join(map(str, VERSION))
ALLOWED_VERSIONS = {b'HTTP/1.0', b'HTTP/1.1'}
SHARED_HEADERS: bytes = (
    b"Server: aioprom/" + __version__.encode('ascii') + b"\r\n" +
    b"Cache-Control: no-store, no-cache, must-revalidate, proxy-revalidate\r\n" +
    b"Pragma: no-cache\r\n" +
    b"Expires: 0\r\n" +
    b"Connection: close\r\n"
)
CRLF = b"\r\n"
HDR_END = CRLF + CRLF
KNOWN_METHODS = {b'GET', b'HEAD', b'OPTIONS', b'POST', b'PUT', b'DELETE', b'PATCH', b'TRACE', b'CONNECT'} # set of all http methods for 501/405 handling
ALLOWED_METHODS = {b'GET', b'HEAD'}
aioprom_info = Gauge("aioprom_info", "Information about the aioprom server", labelnames=['major', 'minor', 'patch', 'version'])
aioprom_info.labels(major=VERSION[0], minor=VERSION[1], patch=VERSION[2], version=__version__).set(1) # standard _info gauge

# Track last request time per IP address
_last_seen: dict[str, float] = {}

def itoa(value: int) -> bytes:
    return str(value).encode('ascii')

# Pre-computed error responses at module level
_ERROR_RESPONSES: dict[int, bytes] = {}
retry_after = max(int(TIMEOUT_429_INTERVAL), 1)
for status_code, reason, extra_headers in [
    (400, b"Bad Request", b""),
    (405, b"Method Not Allowed", b"Allow: " + b", ".join(ALLOWED_METHODS) + CRLF),
    (408, b"Request Timeout", b""),
    (429, b"Too Many Requests", b"Retry-After: " + itoa(retry_after) + CRLF),
    (431, b"Request Header Fields Too Large", b""),
    (500, b"Internal Server Error", b""),
    (501, b"Not Implemented", b""),
    (505, b"HTTP Version Not Supported", b"")
]:
    body: bytes = reason + CRLF
    headers: bytes = (
        b"HTTP/1.1 " + itoa(status_code) + b" " + reason + CRLF +
        b"Content-Length: " + itoa(len(body)) + CRLF +
        b"Content-Type: text/plain\r\n" +
        SHARED_HEADERS +
        extra_headers +
        HDR_END
    )
    _ERROR_RESPONSES[status_code] = headers + body


FALLBACK_ERROR_RESPONSE: bytes = _ERROR_RESPONSES[500]

HDR_200_PREFIX: bytes = (
    b"HTTP/1.1 200 OK\r\n" +
    SHARED_HEADERS +
    b"Content-Type: " + CONTENT_TYPE_LATEST.encode('ascii') + CRLF +
    b"Content-Length: " # we don't know the length yet, but we can just append it and the HDR_END during the handler
)

def check_host(data: bytes) -> bool:
    i = 0
    n = len(data)
    host_count = 0

    # skip request line
    while i < n-1:
        if data[i] == 13 and data[i+1] == 10:
            i += 2
            break
        i += 1
    
    while i < n-1:
        if data[i] == 13 and data[i+1] == 10:
            return host_count == 1
        
        if (i + 5 <= n and
            data[i]   | 0x20 == 104 and  # h
            data[i+1] | 0x20 == 111 and  # o
            data[i+2] | 0x20 == 115 and  # s
            data[i+3] | 0x20 == 116 and  # t
            data[i+4]         == 58      # :
        ):
            # host header found, but we need to check that there is a value
            i += 5
            while i < n-1 and (data[i] == 32 or data[i] == 9):
                i += 1
            if i >= n or (data[i] == 13 and i + 1 < n and data[i+1] == 10):
                return False # empty or OWS
            host_count += 1
        
        # skip to next CRLF
        while i < n - 1:
            if data[i] == 13 and data[i+1] == 10:
                i += 2
                break
            i += 1

    return False
        

async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        # Rate limiting: 1 request per TIMEOUT_429_INTERVAL seconds per IP
        peername = writer.get_extra_info('peername')
        if peername and isinstance(peername, tuple):
            ip,*_ = peername # handle ipv6 by consuming all args after the first
            if ip:
                now = time.monotonic()
                last_time = _last_seen.get(ip, 0)
                if now - last_time < TIMEOUT_429_INTERVAL:
                    await send_simple(writer, 429)
                    return
                _last_seen[ip] = now

        try:
            data = await asyncio.wait_for(reader.readuntil(HDR_END), timeout=READ_TIMEOUT)
        except asyncio.LimitOverrunError:
            await send_simple(writer, 431)
            return
        except asyncio.TimeoutError:
            await send_simple(writer, 408)
            return
        except asyncio.IncompleteReadError:
            return
        if len(data) > MAX_HEADER_BYTES:
            await send_simple(writer, 431)
            return

        try:
            request_line = data.split(CRLF, 1)[0]
            method, path, version = request_line.split(b' ')
        except ValueError:
            await send_simple(writer, 400)
            return

        if version not in ALLOWED_VERSIONS:
            await send_simple(writer, 505)
            return

        if version == b'HTTP/1.1' and not check_host(data):
            await send_simple(writer, 400) # missing host header or multiple host headers
            return

        if method not in ALLOWED_METHODS:
            if method in KNOWN_METHODS:
                await send_simple(writer, 405)
            else:
                await send_simple(writer, 501)
            return

        # we don't actually look at the path, we just always server metrics
        payload = generate_latest()

        headers = (
            HDR_200_PREFIX +
            itoa(len(payload)) +
            HDR_END
        )
        writer.write(headers)
        if method != b'HEAD':
            writer.write(payload)
        try:
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            return
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        if len(_last_seen) > 3: # size gate to reduce churn
            now = time.monotonic()
            old = []
            for peer, last_time in _last_seen.items():
                if now - last_time > CACHE_TIMEOUT:
                    old.append(peer)
            for peer in old:
                del _last_seen[peer]

async def send_simple(writer: asyncio.StreamWriter, status_code: int):
    response = _ERROR_RESPONSES.get(status_code, FALLBACK_ERROR_RESPONSE)

    writer.write(response)
    try:
        await writer.drain()
    except (ConnectionResetError, BrokenPipeError):
        return
    # the finally will close the writer

async def start_server(host: str, port: int) -> None:
    """Starts the metrics server on the given host and port.
    Runs forever until cancelled"""
    server = await asyncio.start_server(handle_client, host, port, limit=MAX_HEADER_BYTES)
    await server.serve_forever()