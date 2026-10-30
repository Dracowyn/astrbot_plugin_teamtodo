"""Read-only public board server (M4), separate from the AstrBot dashboard.

CONTRACT (owned by the main loop; the implementer fills in the bodies, the
public signatures and documented behaviour are fixed). See docs/design.md
section 8 "M4 定稿".

Routes (aiohttp, ``GET``/``HEAD`` only; anything else is 404 or 405):

- ``/b/<token>`` -> relative redirect to ``<token>/`` so a reverse-proxy path
  prefix survives.
- ``/b/<token>/`` -> ``public.html`` from ``static_dir``, served for any
  token without a lookup: the page learns whether the link is valid from the
  data endpoint, so the page itself is no token oracle.
- ``/b/<token>/api/board`` -> ``board_for_token(token)`` as JSON; ``None``
  -> 404 with the same body for every unknown, closed or deleted link.
  Rate limited per client IP.
- ``/b/<token>/<path>`` -> a static ``.js`` or ``.css`` file under
  ``static_dir`` (no lookup).
- everything else -> 404.

The page and the static files are read into memory once by ``start`` (a
plugin update reloads the plugin and therefore the files), so requests never
touch the disk or the shared thread pool.

Every response carries ``Referrer-Policy: no-referrer`` and
``Cache-Control: no-store``. Tokens never appear in logs.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import sys
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aiohttp import web

from astrbot.api import logger

BoardLookup = Callable[[str], "dict[str, Any] | None"]
"""Sync callback: token -> JSON-ready board (already field-whitelisted) or
``None``. Runs on the event loop thread; it reads the store directly."""

# Real tokens are ``secrets.token_urlsafe(24)`` (32 chars). The bounds only
# keep absurd input away from the lookup and the redirect; any token of this
# shape still gets the page, so the shape check is no oracle either.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{16,128}")
# One static path segment: no dotfiles, ``:`` (Windows streams), ``~`` (8.3
# short names) or trailing dots and spaces (which Windows strips). Applied to
# the file names when the files are loaded; requests are then matched against
# the loaded names exactly, on the raw (still percent-encoded) path.
_SEGMENT_RE = re.compile(r"[A-Za-z0-9_-](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9_-])?")
_MAX_STATIC_DEPTH = 4
_PAGE_NAME = "public.html"
_PAGE_TYPE = "text/html; charset=utf-8"
_STATIC_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}
_RATE_WINDOW_SECONDS = 60.0
_RATE_TABLE_MAX = 10_000
_DEFAULT_RATE_LIMIT = 60
_SHUTDOWN_TIMEOUT_SECONDS = 5.0
# Idle keep-alive connections are closed after this many seconds (aiohttp's
# default is about an hour, which lets idle clients pile up connections).
_KEEPALIVE_TIMEOUT_SECONDS = 15.0
_SERVER_HEADER = "TeamTodo"

_NOT_FOUND = json.dumps({"error": "链接无效或已关闭"}, ensure_ascii=False)
_METHOD_NOT_ALLOWED = json.dumps({"error": "只支持 GET 请求"}, ensure_ascii=False)
_TOO_MANY = json.dumps({"error": "请求过于频繁，请稍后再试"}, ensure_ascii=False)
_UNAVAILABLE = json.dumps({"error": "服务暂时不可用"}, ensure_ascii=False)

_COMMON_HEADERS = {
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    # Replaces aiohttp's default, which discloses the Python and aiohttp
    # versions. Protocol-level errors answered by aiohttp itself (e.g. a
    # malformed request line) bypass this hook and keep the default.
    "Server": _SERVER_HEADER,
}
# Applied to every non-page response: JSON or a script opened directly as a
# document may not load anything or be framed.
_DEFAULT_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
# The page CSP. ``'unsafe-eval'`` is required: the vendored full Vue build
# compiles the components' string templates at runtime with
# ``Function("Vue", code)``. The compiled code comes only from the static
# template strings in our own modules; board data is rendered through text
# interpolation and never reaches the compiler (verified in Chromium: without
# it the page fails with an EvalError). No ``'unsafe-inline'``: the page has
# no inline scripts or style attributes, and Vue applies ``:style`` bindings
# through CSSOM, which CSP allows. ``img-src data:`` covers the
# ``<link rel="icon" href="data:,">`` placeholder.
_PAGE_CSP = (
    "default-src 'none'; script-src 'self' 'unsafe-eval'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
    "form-action 'none'; object-src 'none'; frame-ancestors 'none'"
)


def _json_response(
    status: int, text: str, headers: dict[str, str] | None = None
) -> web.Response:
    """Build a UTF-8 JSON response.

    Args:
        status: HTTP status code.
        text: Serialized JSON body.
        headers: Extra headers.

    Returns:
        The response.
    """
    return web.Response(
        status=status,
        text=text,
        content_type="application/json",
        charset="utf-8",
        headers=headers,
    )


class _QuietServerLogger:
    """Stand-in for aiohttp's server logger that never logs request content.

    aiohttp reports protocol errors (malformed request lines or headers) and
    handler failures with ``exc_info``. AstrBot's log sinks render tracebacks
    with local variables, which include the raw request bytes and so the
    token. This logger keeps one debug line with the exception type and the
    peer address (the only argument aiohttp passes with its
    ``"Error handling request from %s"`` message) and drops everything else.
    Protocol errors are routine noise from scanners on a public port, hence
    debug level; real handler failures are caught and logged by
    ``PublicServer._handle`` itself.
    """

    def debug(self, *args: Any, **kwargs: Any) -> None:
        """Drop aiohttp's debug output (it may carry ``exc_info``)."""

    info = debug

    def exception(
        self, msg: object, *args: Any, exc_info: Any = True, **kwargs: Any
    ) -> None:
        """Log the exception type and peer only.

        Args:
            msg: aiohttp's message; never logged, it may quote the request.
            *args: Message arguments; only an IP address is kept.
            exc_info: The exception (or ``True`` for the current one).
            **kwargs: Ignored logging options.
        """
        exc = exc_info if isinstance(exc_info, BaseException) else sys.exc_info()[1]
        peer = "-"
        if args:
            try:
                peer = str(ipaddress.ip_address(str(args[0])))
            except ValueError:
                pass
        logger.debug(
            "TeamTodo public server rejected a request from %s: %s",
            peer,
            type(exc).__name__ if exc is not None else "unknown error",
        )

    error = warning = critical = exception


_QUIET_SERVER_LOGGER = _QuietServerLogger()


class PublicServer:
    """aiohttp server bound to ``host:port``, started and stopped by the plugin."""

    def __init__(
        self,
        board_for_token: BoardLookup,
        static_dir: Path,
        *,
        host: str,
        port: int,
        rate_limit_per_minute: int,
        trust_proxy: bool,
    ) -> None:
        """Configure the server; nothing is bound until ``start``.

        Args:
            board_for_token: See ``BoardLookup``.
            static_dir: ``views/board``; holds ``public.html`` and its assets.
            host: Listen address (``127.0.0.1`` by default in the config).
            port: Listen port.
            rate_limit_per_minute: Data requests allowed per client IP per
                minute; excess requests get 429.
            trust_proxy: Take the client IP from ``X-Forwarded-For`` when the
                socket peer is a loopback or private address (the proxy).
        """
        self._board_for_token = board_for_token
        self._root = Path(static_dir).resolve()
        self._host = host
        self._port = port
        # Design section 10: a limit below 1 falls back to the default, so
        # a bad config value never disables the limiter.
        self._rate_limit = (
            rate_limit_per_minute if rate_limit_per_minute >= 1 else _DEFAULT_RATE_LIMIT
        )
        self._trust_proxy = trust_proxy
        # client key -> (window start, request count). Insertion order equals
        # window start order because a key is re-inserted when its window
        # restarts, so stale entries are always a prefix of the dict.
        self._hits: dict[str, tuple[float, int]] = {}
        self._page: bytes | None = None
        # Raw request sub-path (e.g. ``components/board.js``) -> (body, type).
        self._files: dict[str, tuple[bytes, str]] = {}
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        """Load the static files, bind and start serving.

        Raises:
            OSError: If the address cannot be bound (e.g. port in use). The
                caller logs it and keeps the rest of the plugin running.
        """
        if self._runner is not None:
            return
        if self._trust_proxy:
            try:
                loopback = (
                    self._host == "localhost"
                    or ipaddress.ip_address(self._host).is_loopback
                )
            except ValueError:
                loopback = False
            if not loopback:
                logger.warning(
                    "TeamTodo public_trust_proxy is on but public_host %s is not "
                    "a loopback address; X-Forwarded-For is only honoured from "
                    "loopback or private peers",
                    self._host,
                )
        self._page, self._files = await asyncio.to_thread(self._load_files)
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._handle)
        app.on_response_prepare.append(self._add_headers)
        # access_log=None: the request line carries the token. ``logger``:
        # see _QuietServerLogger.
        runner = web.AppRunner(
            app,
            access_log=None,
            logger=_QUIET_SERVER_LOGGER,
            keepalive_timeout=_KEEPALIVE_TIMEOUT_SECONDS,
            shutdown_timeout=_SHUTDOWN_TIMEOUT_SECONDS,
        )
        await runner.setup()
        try:
            await web.TCPSite(runner, self._host, self._port).start()
        except BaseException:
            await runner.cleanup()
            raise
        self._runner = runner

    async def stop(self) -> None:
        """Stop serving and release the port. Safe to call twice or before
        ``start``."""
        runner, self._runner = self._runner, None
        if runner is not None:
            await runner.cleanup()

    def _load_files(self) -> tuple[bytes | None, dict[str, tuple[bytes, str]]]:
        """Read ``public.html`` and every servable asset under the root.

        An asset is servable when every path segment matches ``_SEGMENT_RE``,
        it is at most ``_MAX_STATIC_DEPTH`` segments deep, its suffix is a
        key of ``_STATIC_TYPES`` (exact case) and, after resolving symlinks,
        it is a regular file inside the root with the same suffix.

        Returns:
            The page (``None`` when missing) and the assets keyed by their
            ``/``-joined relative path.
        """
        files: dict[str, tuple[bytes, str]] = {}
        pending: list[tuple[Path, tuple[str, ...]]] = [(self._root, ())]
        while pending:
            directory, parents = pending.pop()
            try:
                entries = sorted(directory.iterdir())
            except OSError:
                continue
            for entry in entries:
                if not _SEGMENT_RE.fullmatch(entry.name):
                    continue
                segments = (*parents, entry.name)
                if entry.is_dir():
                    # The depth bound also stops symlink loops.
                    if len(segments) < _MAX_STATIC_DEPTH:
                        pending.append((entry, segments))
                    continue
                suffix = entry.suffix
                if suffix not in _STATIC_TYPES:
                    continue
                resolved = entry.resolve()
                if (
                    not resolved.is_relative_to(self._root)
                    or resolved.suffix != suffix
                    or not resolved.is_file()
                ):
                    continue
                try:
                    files["/".join(segments)] = (
                        resolved.read_bytes(),
                        _STATIC_TYPES[suffix],
                    )
                except OSError:
                    continue
        try:
            page = (self._root / _PAGE_NAME).read_bytes()
        except OSError:
            page = None
        return page, files

    @staticmethod
    async def _add_headers(
        request: web.BaseRequest, response: web.StreamResponse
    ) -> None:
        """Add the security headers to every response before it is sent."""
        response.headers.update(_COMMON_HEADERS)
        response.headers.setdefault("Content-Security-Policy", _DEFAULT_CSP)

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        """Answer one request; nothing raised here ever reaches aiohttp.

        An exception escaping to aiohttp would be logged with a traceback
        whose locals include the request (and its token), so failures are
        logged here by type only and answered with a 500.
        """
        try:
            return self._route(request)
        except Exception as exc:
            logger.error(
                "TeamTodo public server failed to answer a request: %s",
                type(exc).__name__,
            )
            return _json_response(500, _UNAVAILABLE)

    def _route(self, request: web.Request) -> web.StreamResponse:
        """Dispatch every request; see the module docstring for the routes.

        The raw (percent-encoded) path is parsed by hand instead of the
        router's decoded one, so an encoded ``/``, ``\\`` or ``.`` can never
        turn into a path separator or a dot segment.
        """
        if request.method not in ("GET", "HEAD"):
            return _json_response(405, _METHOD_NOT_ALLOWED, {"Allow": "GET, HEAD"})
        raw_path = request.rel_url.raw_path
        if not raw_path.startswith("/b/"):
            return _json_response(404, _NOT_FOUND)
        token, slash, sub = raw_path[3:].partition("/")
        if sub == "api/board":
            # Count before the shape check so malformed and unknown tokens
            # are limited and answered exactly alike.
            retry_after = self._retry_after(self._client_key(request))
            if retry_after:
                return _json_response(429, _TOO_MANY, {"Retry-After": str(retry_after)})
            return self._board(token)
        if not _TOKEN_RE.fullmatch(token):
            return _json_response(404, _NOT_FOUND)
        if not slash:
            # Relative, so ``https://host/prefix/b/<token>`` keeps ``/prefix``.
            # The token shape check above keeps this from being an open
            # redirect (e.g. ``https:evil.example``).
            return web.Response(status=302, headers={"Location": f"{token}/"})
        if not sub:
            if self._page is None:
                return _json_response(404, _NOT_FOUND)
            return web.Response(
                body=self._page,
                headers={
                    "Content-Type": _PAGE_TYPE,
                    "Content-Security-Policy": _PAGE_CSP,
                    "X-Frame-Options": "DENY",
                },
            )
        # Exact match against the names validated by _load_files: anything
        # encoded, dotted, deeper or of another type is simply not a key.
        asset = self._files.get(sub)
        if asset is None:
            return _json_response(404, _NOT_FOUND)
        body, content_type = asset
        return web.Response(body=body, headers={"Content-Type": content_type})

    def _board(self, token: str) -> web.Response:
        """Answer the data endpoint for ``token`` (rate limit already passed)."""
        if not _TOKEN_RE.fullmatch(token):
            return _json_response(404, _NOT_FOUND)
        try:
            board = self._board_for_token(token)
            body = None if board is None else json.dumps(board, ensure_ascii=False)
        except Exception as exc:
            # The traceback may quote the token (e.g. in an exception
            # message), so it is logged with the token masked.
            details = "".join(traceback.format_exception(exc))
            masked = f"{token[:4]}…"
            logger.error(
                "TeamTodo public board lookup failed for token %s: %s",
                masked,
                details.replace(token, masked),
            )
            return _json_response(500, _UNAVAILABLE)
        if body is None:
            return _json_response(404, _NOT_FOUND)
        return _json_response(200, body)

    def _client_key(self, request: web.BaseRequest) -> str:
        """Rate-limit key for the client: its IPv4 address or IPv6 /64.

        With ``trust_proxy`` and a loopback or private socket peer (the
        reverse proxy), the rightmost ``X-Forwarded-For`` entry (the address
        the nearest proxy saw) is used when it is a valid IP; otherwise the
        socket peer. A public peer sent the header itself and could rotate it
        freely, so it is ignored. IPv6 clients are grouped by /64 because one
        host usually owns a whole /64 and could otherwise rotate addresses to
        dodge the limit.
        """
        candidates = [request.remote]
        if self._trust_proxy:
            try:
                peer = ipaddress.ip_address(request.remote or "")
            except ValueError:
                peer = None
            if isinstance(peer, ipaddress.IPv6Address) and peer.ipv4_mapped is not None:
                peer = peer.ipv4_mapped
            if peer is not None and (peer.is_loopback or peer.is_private):
                forwarded = ",".join(request.headers.getall("X-Forwarded-For", []))
                candidates.insert(0, forwarded.rsplit(",", 1)[-1].strip())
        for candidate in candidates:
            try:
                ip = ipaddress.ip_address(candidate or "")
            except ValueError:
                continue
            if isinstance(ip, ipaddress.IPv4Address):
                return str(ip)
            if ip.ipv4_mapped is not None:
                return str(ip.ipv4_mapped)
            return str(ipaddress.IPv6Network((int(ip) >> 64 << 64, 64)))
        return "unknown"

    def _retry_after(self, key: str) -> int:
        """Count one data request for ``key`` in its fixed one-minute window.

        Memory stays bounded: expired windows are dropped on every call and
        the table never exceeds ``_RATE_TABLE_MAX`` keys (the oldest window
        is evicted, which can only make some client's limit more lenient,
        never lock anyone out).

        Args:
            key: Client key from ``_client_key``.

        Returns:
            0 when the request is allowed, otherwise the whole seconds until
            the client's window ends (at least 1), for ``Retry-After``.
        """
        now = time.monotonic()
        hits = self._hits
        while hits:
            oldest = next(iter(hits))
            if now - hits[oldest][0] < _RATE_WINDOW_SECONDS:
                break
            del hits[oldest]
        entry = hits.get(key)
        if entry is None:
            if len(hits) >= _RATE_TABLE_MAX:
                del hits[next(iter(hits))]
            hits[key] = (now, 1)
            return 0
        start, count = entry
        if count >= self._rate_limit:
            return max(1, int(start + _RATE_WINDOW_SECONDS - now + 0.999))
        hits[key] = (start, count + 1)
        return 0
