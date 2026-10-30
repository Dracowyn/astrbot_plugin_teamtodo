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

Every response carries ``Referrer-Policy: no-referrer`` and
``Cache-Control: no-store``. Tokens never appear in logs.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

BoardLookup = Callable[[str], "dict[str, Any] | None"]
"""Sync callback: token -> JSON-ready board (already field-whitelisted) or
``None``. Runs on the event loop thread; it reads the store directly."""


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
            trust_proxy: Take the client IP from ``X-Forwarded-For`` instead
                of the socket peer.
        """
        raise NotImplementedError

    async def start(self) -> None:
        """Bind and start serving.

        Raises:
            OSError: If the address cannot be bound (e.g. port in use). The
                caller logs it and keeps the rest of the plugin running.
        """
        raise NotImplementedError

    async def stop(self) -> None:
        """Stop serving and release the port. Safe to call twice or before
        ``start``."""
        raise NotImplementedError
