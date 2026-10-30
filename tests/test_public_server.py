import asyncio
import json
import logging
import os
import secrets
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from astrbot_plugin_teamtodo.services import public_server
from astrbot_plugin_teamtodo.services.public_server import PublicServer
from multidict import CIMultiDict

BOARD_DIR = Path(__file__).resolve().parents[1] / "views" / "board"
TOKEN = secrets.token_urlsafe(24)
OTHER = secrets.token_urlsafe(24)
BOARD = {"space": {"name": "研发"}, "tasks": [], "updated_at": 1, "timezone": "UTC"}
NOT_FOUND = {"error": "链接无效或已关闭"}
COMMON = {
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "server": "TeamTodo",
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def fetch(port, target, method="GET", headers=()):
    """Send one raw HTTP/1.1 request so the target reaches the server as-is
    (HTTP clients normalize ``..`` and encodings before sending)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    lines = [f"{method} {target} HTTP/1.1", "Host: 127.0.0.1", "Connection: close"]
    lines += [f"{name}: {value}" for name, value in headers]
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
    data = await reader.read()
    writer.close()
    await writer.wait_closed()
    head, _, body = data.partition(b"\r\n\r\n")
    status_line, *header_lines = head.decode("latin-1").split("\r\n")
    response_headers = {}
    for line in header_lines:
        name, _, value = line.partition(":")
        response_headers[name.strip().lower()] = value.strip()
    return SimpleNamespace(
        status=int(status_line.split()[1]), headers=response_headers, body=body
    )


class Lookup:
    """Board callback that records every token it is asked about."""

    def __init__(self, boards=None, error=None):
        self.boards = {TOKEN: BOARD} if boards is None else boards
        self.error = error
        self.calls = []

    def __call__(self, token):
        self.calls.append(token)
        if self.error is not None:
            raise self.error
        return self.boards.get(token)


@pytest_asyncio.fixture
async def make_server():
    """Factory starting a PublicServer on a free port; all are stopped after."""
    started = []

    async def factory(lookup=None, static_dir=BOARD_DIR, **options):
        options = {"rate_limit_per_minute": 60, "trust_proxy": False} | options
        server = PublicServer(
            lookup or Lookup(),
            static_dir,
            host="127.0.0.1",
            port=free_port(),
            **options,
        )
        await server.start()
        started.append(server)
        return server

    yield factory
    for server in started:
        await server.stop()


def assert_common(response):
    for name, value in COMMON.items():
        assert response.headers.get(name) == value, name
    assert "content-security-policy" in response.headers


def assert_not_found(response):
    assert response.status == 404
    assert json.loads(response.body) == NOT_FOUND
    assert_common(response)


@pytest.mark.asyncio
async def test_redirect_is_relative(make_server):
    server = await make_server()
    response = await fetch(server._port, f"/b/{TOKEN}")
    assert response.status == 302
    assert response.headers["location"] == f"{TOKEN}/"
    assert_common(response)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token",
    ["short", "a" * 129, "https:evil.example", "%2F%2Fevil.example", "a.b" * 8],
)
async def test_redirect_rejects_malformed_token(make_server, token):
    server = await make_server()
    response = await fetch(server._port, f"/b/{token}")
    assert_not_found(response)
    assert "location" not in response.headers


@pytest.mark.asyncio
async def test_page_served_for_any_token_without_lookup(make_server):
    lookup = Lookup()
    server = await make_server(lookup)
    expected = (BOARD_DIR / "public.html").read_bytes()
    for token in (TOKEN, secrets.token_urlsafe(24), "x" * 16):
        response = await fetch(server._port, f"/b/{token}/")
        assert response.status == 200
        assert response.body == expected
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert response.headers["x-frame-options"] == "DENY"
        csp = response.headers["content-security-policy"]
        for directive in (
            "default-src 'none'",
            "script-src 'self' 'unsafe-eval'",
            "style-src 'self'",
            "img-src 'self' data:",
            "connect-src 'self'",
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "form-action 'none'",
            "object-src 'none'",
        ):
            assert directive in csp
        assert "unsafe-inline" not in csp
        assert_common(response)
    assert lookup.calls == []


@pytest.mark.asyncio
async def test_board_data(make_server):
    lookup = Lookup()
    server = await make_server(lookup)
    response = await fetch(server._port, f"/b/{TOKEN}/api/board")
    assert response.status == 200
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert json.loads(response.body) == BOARD
    assert "x-frame-options" not in response.headers
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    assert_common(response)
    assert lookup.calls == [TOKEN]


@pytest.mark.asyncio
async def test_unknown_and_malformed_tokens_get_identical_404(make_server):
    lookup = Lookup()
    server = await make_server(lookup)
    tokens = [OTHER, "short", "a" * 129, "a.b.c.d.e.f.g.h.i", "%41" * 20, ""]
    bodies = set()
    for token in tokens:
        response = await fetch(server._port, f"/b/{token}/api/board")
        assert_not_found(response)
        bodies.add(response.body)
    assert len(bodies) == 1
    # Only the well-formed token reached the lookup.
    assert lookup.calls == [OTHER]


@pytest.mark.asyncio
async def test_lookup_failure_is_500_without_details_or_token(make_server, log_records):
    records = log_records
    server = await make_server(Lookup(error=RuntimeError(f"db broke on {TOKEN}")))
    response = await fetch(server._port, f"/b/{TOKEN}/api/board")
    assert response.status == 500
    assert json.loads(response.body) == {"error": "服务暂时不可用"}
    assert TOKEN.encode() not in response.body
    assert_common(response)
    logged = "\n".join(records)
    assert "db broke on" in logged
    assert f"{TOKEN[:4]}…" in logged
    assert TOKEN not in logged


@pytest.mark.asyncio
async def test_unserializable_board_is_500(make_server):
    server = await make_server(Lookup({TOKEN: {"bad": object()}}))
    response = await fetch(server._port, f"/b/{TOKEN}/api/board")
    assert response.status == 500
    assert_common(response)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("public-app.js", "text/javascript; charset=utf-8"),
        ("theme.js", "text/javascript; charset=utf-8"),
        ("vue.js", "text/javascript; charset=utf-8"),
        ("vendor/vue.esm-browser.prod.js", "text/javascript; charset=utf-8"),
        ("components/board.js", "text/javascript; charset=utf-8"),
        ("css/base.css", "text/css; charset=utf-8"),
        ("css/public.css", "text/css; charset=utf-8"),
    ],
)
async def test_static_files(make_server, path, content_type):
    lookup = Lookup()
    server = await make_server(lookup)
    response = await fetch(server._port, f"/b/{secrets.token_urlsafe(24)}/{path}")
    assert response.status == 200
    assert response.headers["content-type"] == content_type
    assert response.body == (BOARD_DIR / path).read_bytes()
    assert_common(response)
    assert lookup.calls == []


TRAVERSALS = [
    f"/b/{TOKEN}/../../main.py",
    f"/b/{TOKEN}/../../../../../../Windows/win.ini",
    f"/b/{TOKEN}/%2e%2e/%2e%2e/main.py",
    f"/b/{TOKEN}/%2E%2E/%2E%2E/core/store.py",
    f"/b/{TOKEN}/..%2F..%2Fmain.py",
    f"/b/{TOKEN}/css%2Fbase.css",
    f"/b/{TOKEN}/css%2fbase.css",
    f"/b/{TOKEN}/%5c..%5c..%5cmain.py",
    f"/b/{TOKEN}/css%5Cbase.css",
    f"/b/{TOKEN}/%252e%252e/main.py",
    f"/b/{TOKEN}//etc/passwd.js",
    f"/b/{TOKEN}/C:/Windows/win.js",
    f"/b/{TOKEN}/C:%5CWindows%5Cwin.js",
    f"/b/{TOKEN}/./public-app.js",
    f"/b/{TOKEN}/css/../theme.js",
    f"/b/{TOKEN}/index.html",
    f"/b/{TOKEN}/public.html",
    f"/b/{TOKEN}/vendor/VENDOR.md",
    f"/b/{TOKEN}/mock-data.json",
    f"/b/{TOKEN}/css",
    f"/b/{TOKEN}/css/",
    f"/b/{TOKEN}/missing.js",
    f"/b/{TOKEN}/theme.js.",
    f"/b/{TOKEN}/theme.js%20",
    f"/b/{TOKEN}/THEME.JS",
    f"/b/{TOKEN}/theme.js::$DATA",
    f"/b/{TOKEN}/theme.js%00.md",
    f"/b/{TOKEN}/a/b/c/d/e.js",
    f"/b/{TOKEN}/{'a' * 101}.js",
    f"/b/{TOKEN}/api/board/",
    f"/b/{TOKEN}/api",
    "/b/short/theme.js",
    "/b/short/",
    "/",
    "/b",
    "/b/",
    "/favicon.ico",
    "/public.html",
    "/theme.js",
]


@pytest.mark.asyncio
async def test_traversal_and_non_asset_paths_are_404(make_server):
    lookup = Lookup()
    server = await make_server(lookup)
    for target in TRAVERSALS:
        response = await fetch(server._port, target)
        assert response.status == 404, target
        assert json.loads(response.body) == NOT_FOUND, target
        assert_common(response)
    assert lookup.calls == []


@pytest.mark.asyncio
async def test_raw_backslash_never_served(make_server):
    server = await make_server()
    response = await fetch(server._port, f"/b/{TOKEN}/..\\..\\main.py")
    # The HTTP parser may reject the raw byte (400) before the app sees it;
    # either way nothing is served.
    assert response.status in (400, 404)
    assert b"PublicServer" not in response.body


@pytest.fixture
def private_dir(tmp_path):
    root = tmp_path / "board"
    root.mkdir()
    (root / "public.html").write_text("<p>page</p>", encoding="utf-8")
    (root / "ok.js").write_text("export {};", encoding="utf-8")
    (root / ".secret.js").write_text("secret", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "x.js").write_text("secret", encoding="utf-8")
    (root / "dir.js").mkdir()
    (tmp_path / "outside.js").write_text("outside", encoding="utf-8")
    (root / "notes.md").write_text("notes", encoding="utf-8")
    return root


@pytest.mark.asyncio
async def test_dotfiles_and_directories_are_404(make_server, private_dir):
    server = await make_server(static_dir=private_dir)
    assert (await fetch(server._port, f"/b/{TOKEN}/ok.js")).status == 200
    for path in (".secret.js", ".git/x.js", "dir.js", "%2esecret.js"):
        assert_not_found(await fetch(server._port, f"/b/{TOKEN}/{path}"))


@pytest.mark.asyncio
async def test_symlink_escape_is_404(make_server, private_dir):
    try:
        os.symlink(private_dir.parent / "outside.js", private_dir / "link.js")
        os.symlink(private_dir / "notes.md", private_dir / "notes.js")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    server = await make_server(static_dir=private_dir)
    for path in ("link.js", "notes.js"):
        assert_not_found(await fetch(server._port, f"/b/{TOKEN}/{path}"))


@pytest.mark.asyncio
async def test_resolved_name_must_keep_the_requested_type(make_server, private_dir):
    # On a case-insensitive filesystem ``upper.js`` resolves to ``upper.JS``;
    # elsewhere it does not exist. Either way it is not served.
    (private_dir / "upper.JS").write_text("x", encoding="utf-8")
    server = await make_server(static_dir=private_dir)
    for path in ("upper.js", "upper.JS"):
        assert_not_found(await fetch(server._port, f"/b/{TOKEN}/{path}"))


@pytest.mark.asyncio
async def test_missing_page_is_404(make_server, private_dir):
    (private_dir / "public.html").unlink()
    server = await make_server(static_dir=private_dir)
    assert_not_found(await fetch(server._port, f"/b/{TOKEN}/"))


@pytest.mark.asyncio
async def test_head_requests(make_server):
    server = await make_server()
    page = await fetch(server._port, f"/b/{TOKEN}/", method="HEAD")
    assert page.status == 200
    assert page.body == b""
    assert page.headers["content-type"] == "text/html; charset=utf-8"
    assert page.headers["x-frame-options"] == "DENY"
    assert_common(page)
    data = await fetch(server._port, f"/b/{TOKEN}/api/board", method="HEAD")
    assert data.status == 200
    assert data.body == b""
    assert_common(data)
    asset = await fetch(server._port, f"/b/{TOKEN}/theme.js", method="HEAD")
    assert asset.status == 200
    assert asset.headers["content-type"] == "text/javascript; charset=utf-8"
    missing = await fetch(server._port, f"/b/{OTHER}/api/board", method="HEAD")
    assert missing.status == 404
    assert_common(missing)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def test_other_methods_are_405(make_server, method):
    lookup = Lookup()
    server = await make_server(lookup)
    for target in (f"/b/{TOKEN}/api/board", f"/b/{TOKEN}/", "/nowhere"):
        response = await fetch(
            server._port, target, method=method, headers=[("Content-Length", "0")]
        )
        assert response.status == 405
        assert response.headers["allow"] == "GET, HEAD"
        assert TOKEN.encode() not in response.body
        assert_common(response)
    assert lookup.calls == []


@pytest.mark.asyncio
async def test_rate_limit(make_server):
    server = await make_server(rate_limit_per_minute=3)
    data = f"/b/{TOKEN}/api/board"
    for _ in range(3):
        assert (await fetch(server._port, data)).status == 200
        # Page and assets are not counted.
        assert (await fetch(server._port, f"/b/{TOKEN}/")).status == 200
        assert (await fetch(server._port, f"/b/{TOKEN}/theme.js")).status == 200
    limited = await fetch(server._port, data)
    assert limited.status == 429
    assert json.loads(limited.body) == {"error": "请求过于频繁，请稍后再试"}
    assert 1 <= int(limited.headers["retry-after"]) <= 60
    assert_common(limited)
    # Unknown and malformed tokens are limited too, so 429 is no oracle.
    assert (await fetch(server._port, f"/b/{OTHER}/api/board")).status == 429
    assert (await fetch(server._port, "/b/bad/api/board")).status == 429
    # Spoofed X-Forwarded-For is ignored without trust_proxy.
    spoofed = await fetch(server._port, data, headers=[("X-Forwarded-For", "9.9.9.9")])
    assert spoofed.status == 429
    assert (await fetch(server._port, f"/b/{TOKEN}/")).status == 200


@pytest.mark.asyncio
async def test_rate_limit_window_expires(make_server, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(
        public_server, "time", SimpleNamespace(monotonic=lambda: clock[0])
    )
    server = await make_server(rate_limit_per_minute=1)
    data = f"/b/{TOKEN}/api/board"
    assert (await fetch(server._port, data)).status == 200
    clock[0] += 59.5
    limited = await fetch(server._port, data)
    assert limited.status == 429
    assert limited.headers["retry-after"] == "1"
    clock[0] += 0.5
    assert (await fetch(server._port, data)).status == 200


@pytest.mark.asyncio
async def test_trust_proxy_uses_rightmost_forwarded_ip(make_server):
    server = await make_server(rate_limit_per_minute=1, trust_proxy=True)
    data = f"/b/{TOKEN}/api/board"

    async def status(forwarded):
        headers = [("X-Forwarded-For", value) for value in forwarded]
        return (await fetch(server._port, data, headers=headers)).status

    assert await status(["1.1.1.1"]) == 200
    assert await status(["1.1.1.1"]) == 429
    # Another client behind the proxy is unaffected.
    assert await status(["2.2.2.2"]) == 200
    # The rightmost entry counts; spoofed left entries do not matter.
    assert await status(["2.2.2.2, 3.3.3.3"]) == 200
    assert await status(["9.9.9.9, 3.3.3.3"]) == 429
    # Several headers are read as one list.
    assert await status(["4.4.4.4", "5.5.5.5"]) == 200
    assert await status(["5.5.5.5"]) == 429
    # Garbage falls back to the socket peer (127.0.0.1).
    assert await status(["not-an-ip"]) == 200
    assert await status(["1.2.3.4:80"]) == 429
    assert await status([]) == 429
    assert await status(["1.2.3.4, "]) == 429


def fake_request(remote, forwarded=()):
    headers = CIMultiDict(("X-Forwarded-For", value) for value in forwarded)
    return SimpleNamespace(remote=remote, headers=headers)


def test_client_key():
    trusting = PublicServer(
        Lookup(),
        BOARD_DIR,
        host="127.0.0.1",
        port=1,
        rate_limit_per_minute=60,
        trust_proxy=True,
    )
    assert trusting._client_key(fake_request("10.0.0.1")) == "10.0.0.1"
    assert trusting._client_key(fake_request("10.0.0.1", ["8.8.8.8"])) == "8.8.8.8"
    # IPv6 clients are grouped by /64; IPv4-mapped addresses count as IPv4.
    first = trusting._client_key(fake_request("2001:db8:1:2::1"))
    second = trusting._client_key(fake_request("10.0.0.1", ["2001:db8:1:2:ffff::9"]))
    assert first == second == "2001:db8:1:2::/64"
    assert trusting._client_key(fake_request("::ffff:1.2.3.4")) == "1.2.3.4"
    assert trusting._client_key(fake_request(None, ["junk"])) == "unknown"
    assert trusting._client_key(fake_request("fe80::1%eth0")) == "fe80::/64"
    # X-Forwarded-For counts only from a loopback or private peer (the
    # proxy); a public peer wrote the header itself and could rotate it.
    for proxy in ("127.0.0.1", "::1", "::ffff:127.0.0.1", "192.168.1.2", "fd00::1"):
        assert trusting._client_key(fake_request(proxy, ["1.1.1.1"])) == "1.1.1.1"
    for public in ("8.8.4.4", "::ffff:8.8.4.4"):
        assert trusting._client_key(fake_request(public, ["1.1.1.1"])) == "8.8.4.4"
    assert trusting._client_key(fake_request("2606:4700::1", ["1.1.1.1"])) == (
        "2606:4700::/64"
    )
    assert trusting._client_key(fake_request("not-an-ip", ["1.1.1.1"])) == "unknown"


def test_rate_limit_below_one_uses_default():
    for value in (0, -5):
        server = PublicServer(
            Lookup(),
            BOARD_DIR,
            host="127.0.0.1",
            port=1,
            rate_limit_per_minute=value,
            trust_proxy=False,
        )
        assert server._rate_limit == 60


def test_rate_table_stays_bounded(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(
        public_server, "time", SimpleNamespace(monotonic=lambda: clock[0])
    )
    monkeypatch.setattr(public_server, "_RATE_TABLE_MAX", 5)
    server = PublicServer(
        Lookup(),
        BOARD_DIR,
        host="127.0.0.1",
        port=1,
        rate_limit_per_minute=1,
        trust_proxy=False,
    )
    for index in range(50):
        clock[0] += 0.1
        assert server._retry_after(f"10.0.0.{index}") == 0
        assert len(server._hits) <= 5
    # The newest clients are kept; the oldest were evicted.
    assert list(server._hits) == [f"10.0.0.{index}" for index in range(45, 50)]
    assert server._retry_after("10.0.0.49") > 0
    # Expired windows are pruned on the next request.
    clock[0] += 61
    assert server._retry_after("10.0.0.99") == 0
    assert list(server._hits) == ["10.0.0.99"]


@pytest.mark.asyncio
async def test_stop_is_idempotent_and_releases_port():
    port = free_port()
    options = {
        "host": "127.0.0.1",
        "port": port,
        "rate_limit_per_minute": 60,
        "trust_proxy": False,
    }
    server = PublicServer(Lookup(), BOARD_DIR, **options)
    await server.stop()  # before start
    await server.start()
    await server.start()  # already running: no second bind
    assert (await fetch(port, f"/b/{TOKEN}/api/board")).status == 200
    await server.stop()
    await server.stop()
    with pytest.raises(OSError):
        await fetch(port, "/")
    # A hot reload binds the same port again.
    again = PublicServer(Lookup(), BOARD_DIR, **options)
    await again.start()
    try:
        assert (await fetch(port, f"/b/{TOKEN}/api/board")).status == 200
        busy = PublicServer(Lookup(), BOARD_DIR, **options)
        with pytest.raises(OSError):
            await busy.start()
        await busy.stop()
        assert (await fetch(port, f"/b/{TOKEN}/api/board")).status == 200
    finally:
        await again.stop()


@pytest.mark.asyncio
async def test_stop_with_open_keepalive_connection():
    port = free_port()
    server = PublicServer(
        Lookup(),
        BOARD_DIR,
        host="127.0.0.1",
        port=port,
        rate_limit_per_minute=60,
        trust_proxy=False,
    )
    await server.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET /b/{TOKEN}/api/board HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    await reader.readuntil(b"\r\n\r\n")
    await asyncio.wait_for(server.stop(), timeout=10)
    writer.close()


class _Collector(logging.Handler):
    def __init__(self, records):
        super().__init__(logging.DEBUG)
        self.records = records

    def emit(self, record):
        text = record.getMessage()
        if record.exc_info:
            text += logging.Formatter().formatException(record.exc_info)
        self.records.append(text)


@pytest.fixture
def log_records(monkeypatch):
    """Collect every log line from the module logger, aiohttp and root."""
    records = []
    handler = _Collector(records)
    module_logger = logging.getLogger("teamtodo_public_server_test")
    module_logger.setLevel(logging.DEBUG)
    monkeypatch.setattr(public_server, "logger", module_logger)
    loggers = [logging.getLogger(name) for name in ("", "aiohttp", "astrbot")]
    for target in loggers:
        monkeypatch.setattr(target, "level", logging.DEBUG)
        target.addHandler(handler)
    yield records
    for target in loggers:
        target.removeHandler(handler)


@pytest.mark.asyncio
async def test_token_never_logged(make_server, log_records):
    records = log_records
    server = await make_server(rate_limit_per_minute=2)
    targets = [
        f"/b/{TOKEN}",
        f"/b/{TOKEN}/",
        f"/b/{TOKEN}/api/board",
        f"/b/{TOKEN}/api/board",
        f"/b/{TOKEN}/api/board",
        f"/b/{TOKEN}/theme.js",
        f"/b/{TOKEN}/../secret",
        f"/b/{TOKEN}/missing.js",
    ]
    for target in targets:
        await fetch(server._port, target)
    await fetch(server._port, f"/b/{TOKEN}/api/board", method="POST")
    # Lookup failure path logs, but masked.
    failing = await make_server(Lookup(error=KeyError(TOKEN)))
    assert (await fetch(failing._port, f"/b/{TOKEN}/api/board")).status == 500
    assert records, "the capture handler saw nothing"
    assert all(TOKEN not in line for line in records)


async def send_raw(port, data):
    """Send raw bytes (possibly not valid HTTP) and return the whole answer."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(data)
    answer = await asyncio.wait_for(reader.read(), timeout=10)
    writer.close()
    await writer.wait_closed()
    return answer


@pytest.mark.asyncio
async def test_malformed_requests_never_log_the_token(make_server, log_records):
    server = await make_server()
    good = f"GET /b/{TOKEN}/ HTTP/1.1\r\nHost: x\r\n\r\n".encode()
    malformed = [
        # Invalid header name: aiohttp logs it at error level by default,
        # with the raw request bytes among the traceback locals.
        f"GET /b/{TOKEN}/ HTTP/1.1\r\nHost: x\r\nBad Header<x>: 1\r\n\r\n".encode(),
        # Invalid method on the second request of a keep-alive connection.
        good + f"G<T /b/{TOKEN}/api/board HTTP/1.1\r\nHost: x\r\n\r\n".encode(),
        f"GET /b/{TOKEN}/{'a' * 9000} HTTP/1.1\r\nHost: x\r\n\r\n".encode(),
    ]
    aiohttp_records = []
    aiohttp_handler = _Collector(aiohttp_records)
    aiohttp_logger = logging.getLogger("aiohttp.server")
    aiohttp_logger.addHandler(aiohttp_handler)
    try:
        for data in malformed:
            answer = await send_raw(server._port, data)
            assert b" 400 " in answer
    finally:
        aiohttp_logger.removeHandler(aiohttp_handler)
    # aiohttp's own logger (whose traceback locals hold the raw request in
    # AstrBot's log sinks) is never used.
    assert aiohttp_records == []
    logged = "\n".join(log_records)
    assert "rejected a request from 127.0.0.1" in logged
    assert TOKEN not in logged


@pytest.mark.asyncio
async def test_unexpected_failure_is_500_logged_by_type_only(make_server, log_records):
    server = await make_server()

    def broken(request):
        raise RuntimeError(f"broken while handling {request.rel_url.raw_path}")

    server._client_key = broken
    response = await fetch(server._port, f"/b/{TOKEN}/api/board")
    assert response.status == 500
    assert json.loads(response.body) == {"error": "服务暂时不可用"}
    assert_common(response)
    logged = "\n".join(log_records)
    assert "RuntimeError" in logged
    assert TOKEN not in logged


def test_quiet_server_logger(monkeypatch):
    lines = []
    monkeypatch.setattr(
        public_server,
        "logger",
        SimpleNamespace(debug=lambda msg, *args: lines.append(msg % args)),
    )
    quiet = public_server._QuietServerLogger()
    quiet.debug("Start lingering %s", TOKEN, exc_info=True)
    quiet.info(TOKEN)
    assert lines == []
    try:
        raise ValueError(TOKEN)
    except ValueError:
        quiet.exception("Error handling request from %s", "10.1.2.3")
    quiet.error(f"Web-handler should return a response instance, got {TOKEN!r}")
    quiet.warning("Error handling request from %s", TOKEN, exc_info=KeyError(TOKEN))
    assert lines == [
        "TeamTodo public server rejected a request from 10.1.2.3: ValueError",
        "TeamTodo public server rejected a request from -: unknown error",
        "TeamTodo public server rejected a request from -: KeyError",
    ]


class FakeSite:
    """``web.TCPSite`` stand-in that fails to bind, so no socket is opened."""

    def __init__(self, runner, host, port):
        self.host = host

    async def start(self):
        raise OSError("bind failed")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("host", "warned"),
    [
        ("127.0.0.1", False),
        ("::1", False),
        ("localhost", False),
        ("0.0.0.0", True),
        ("::", True),
        ("192.168.1.5", True),
        ("board.example", True),
    ],
)
async def test_trust_proxy_on_public_host_warns(monkeypatch, log_records, host, warned):
    monkeypatch.setattr(public_server.web, "TCPSite", FakeSite)
    server = PublicServer(
        Lookup(),
        BOARD_DIR,
        host=host,
        port=1,
        rate_limit_per_minute=60,
        trust_proxy=True,
    )
    with pytest.raises(OSError):
        await server.start()
    warning = [line for line in log_records if "public_trust_proxy" in line]
    assert bool(warning) is warned
    quiet = PublicServer(
        Lookup(),
        BOARD_DIR,
        host=host,
        port=1,
        rate_limit_per_minute=60,
        trust_proxy=False,
    )
    log_records.clear()
    with pytest.raises(OSError):
        await quiet.start()
    assert not [line for line in log_records if "public_trust_proxy" in line]


@pytest.mark.asyncio
async def test_files_are_served_from_memory(make_server, private_dir, monkeypatch):
    deep = private_dir / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "ok.js").write_text("deep", encoding="utf-8")
    (deep / "d").mkdir()
    (deep / "d" / "too-deep.js").write_text("too deep", encoding="utf-8")
    server = await make_server(static_dir=private_dir)

    async def no_thread(*args, **kwargs):
        raise AssertionError("requests must not touch the disk")

    monkeypatch.setattr(public_server.asyncio, "to_thread", no_thread)
    (private_dir / "ok.js").write_text("changed on disk", encoding="utf-8")
    (private_dir / "public.html").unlink()
    (private_dir / "new.js").write_text("added later", encoding="utf-8")
    page = await fetch(server._port, f"/b/{TOKEN}/")
    assert page.status == 200
    assert page.body == b"<p>page</p>"
    asset = await fetch(server._port, f"/b/{TOKEN}/ok.js")
    assert asset.body == b"export {};"
    assert (await fetch(server._port, f"/b/{TOKEN}/a/b/c/ok.js")).body == b"deep"
    for path in ("new.js", "a/b/c/d/too-deep.js", "a%2Fb/c/ok.js", "A/b/c/ok.js"):
        assert_not_found(await fetch(server._port, f"/b/{TOKEN}/{path}"))


@pytest.mark.asyncio
async def test_unreadable_entries_are_skipped(make_server, private_dir, monkeypatch):
    real_read = Path.read_bytes
    real_iterdir = Path.iterdir

    def read_bytes(path):
        if path.name == "ok.js":
            raise PermissionError("denied")
        return real_read(path)

    def iterdir(path):
        if path.name == "locked":
            raise PermissionError("denied")
        return real_iterdir(path)

    (private_dir / "locked").mkdir()
    (private_dir / "locked" / "x.js").write_text("x", encoding="utf-8")
    (private_dir / "other.js").write_text("other", encoding="utf-8")
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "iterdir", iterdir)
    server = await make_server(static_dir=private_dir)
    assert (await fetch(server._port, f"/b/{TOKEN}/other.js")).body == b"other"
    for path in ("ok.js", "locked/x.js"):
        assert_not_found(await fetch(server._port, f"/b/{TOKEN}/{path}"))


@pytest.mark.asyncio
async def test_symlink_loop_does_not_hang(make_server, private_dir):
    try:
        os.symlink(private_dir, private_dir / "loop", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    server = await asyncio.wait_for(make_server(static_dir=private_dir), timeout=10)
    assert (await fetch(server._port, f"/b/{TOKEN}/loop/ok.js")).status == 200


@pytest.mark.asyncio
async def test_idle_keepalive_connection_is_closed(make_server, monkeypatch):
    monkeypatch.setattr(public_server, "_KEEPALIVE_TIMEOUT_SECONDS", 0.5)
    server = await make_server()
    reader, writer = await asyncio.open_connection("127.0.0.1", server._port)
    writer.write(f"GET /b/{TOKEN}/api/board HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    await reader.readuntil(b"\r\n\r\n")
    # The server closes the idle connection on its own: read() reaches EOF.
    await asyncio.wait_for(reader.read(), timeout=10)
    writer.close()
