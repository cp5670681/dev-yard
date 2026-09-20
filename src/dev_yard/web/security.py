"""CSRF/origin guard for the local console.

The console has no auth and can run `pi --approve`, so a cross-site form POST
from any page the developer visits must not be able to drive it. This is a raw
ASGI middleware (not BaseHTTPMiddleware) so SSE streaming is untouched.
"""

from __future__ import annotations

import hmac
from typing import Any
from urllib.parse import urlparse

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def bearer_ok(authorization: str | None, token: str) -> bool:
    raw = (authorization or "").strip()
    scheme, _, rest = raw.partition(" ")
    if scheme.lower() != "bearer" or not rest:
        return False
    got = rest.strip().encode("utf-8")
    want = token.encode("utf-8")
    if len(got) != len(want):
        return False
    return hmac.compare_digest(got, want)


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _allowed_origin(origin: str, host_header: str) -> bool:
    """Same-origin, or any loopback origin (the console is a loopback tool)."""
    host = _host_of(origin)
    if not host:
        return False
    if host in _LOOPBACK or host.startswith("127."):
        return True
    request_host = (host_header or "").rsplit(":", 1)[0].strip("[]").lower()
    return bool(request_host) and host == request_host


class OriginGuardMiddleware:
    def __init__(self, app: Any, allow_remote: bool = False) -> None:
        self.app = app
        self.allow_remote = allow_remote

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method", "GET")).upper()
        if method not in _SAFE_METHODS:
            headers = {
                k.decode("latin-1").lower(): v.decode("latin-1")
                for k, v in scope.get("headers", [])
            }
            origin = headers.get("origin") or ""
            referer = headers.get("referer") or ""
            host = headers.get("host") or ""
            # Browsers send Origin on cross-site writes. No Origin/Referer from
            # a non-browser client (curl, tests) is allowed through.
            if origin:
                allowed = _allowed_origin(origin, host)
            elif referer:
                allowed = _allowed_origin(referer, host)
            else:
                allowed = True
            if not allowed:
                body = b'{"detail":"cross-origin request rejected"}'
                await send(
                    {
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
