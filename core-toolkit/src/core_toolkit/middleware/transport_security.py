"""DNS Rebinding防御のためのASGIミドルウェア。

Host/Origin/Content-Typeヘッダーを検証し、
DNS Rebinding攻撃およびCSRFを防ぐ。
"""

import logging
from collections.abc import Callable, MutableMapping
from typing import Any

__all__ = ["TransportSecurityASGIMiddleware"]

Scope = MutableMapping[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]

logger = logging.getLogger(__name__)


class TransportSecurityASGIMiddleware:
    """Host/Originヘッダーを検証しDNS Rebinding攻撃を防ぐASGIミドルウェア。

    MCP仕様で推奨されるトランスポートセキュリティを、
    Starlette/ASGIアプリケーションに適用する。

    Args:
        app: 次のASGIアプリケーション。
        allowed_hosts: 許可するHostヘッダー値。ワイルドカードポート対応
            （例: ``"localhost:*"``）。
        allowed_origins: 許可するOriginヘッダー値。ワイルドカードポート対応。

    Example::

        from core_toolkit.middleware import TransportSecurityASGIMiddleware

        app.add_middleware(
            TransportSecurityASGIMiddleware,
            allowed_hosts=("localhost:8000",),
            allowed_origins=("http://localhost:3000",),
        )
    """

    def __init__(
        self,
        app: Any,
        allowed_hosts: tuple[str, ...] = (),
        allowed_origins: tuple[str, ...] = (),
    ) -> None:
        self.app = app
        self.allowed_hosts = allowed_hosts
        self.allowed_origins = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGIインターフェース実装。"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        host = headers.get(b"host", b"").decode("latin-1")
        origin = headers.get(b"origin", b"").decode("latin-1")
        method = scope.get("method", "GET")

        if not self._validate_host(host):
            await self._send_error(send, 421, "Invalid Host header")
            return

        if origin and not self._validate_origin(origin):
            await self._send_error(send, 403, "Invalid Origin header")
            return

        if method == "POST":
            content_type = headers.get(b"content-type", b"").decode("latin-1")
            if not content_type.lower().startswith("application/json"):
                await self._send_error(send, 400, "Invalid Content-Type header")
                return

        await self.app(scope, receive, send)

    def _validate_host(self, host: str) -> bool:
        """Hostヘッダーを許可リストと照合する。"""
        if not host:
            logger.warning("Missing Host header in request")
            return False

        if host in self.allowed_hosts:
            return True

        for allowed in self.allowed_hosts:
            if allowed.endswith(":*"):
                base = allowed[:-2]
                if host.startswith(base + ":"):
                    return True

        logger.warning("Rejected Host header: %s", host)
        return False

    def _validate_origin(self, origin: str) -> bool:
        """Originヘッダーを許可リストと照合する。"""
        if origin in self.allowed_origins:
            return True

        for allowed in self.allowed_origins:
            if allowed.endswith(":*"):
                base = allowed[:-2]
                if origin.startswith(base + ":"):
                    return True

        logger.warning("Rejected Origin header: %s", origin)
        return False

    @staticmethod
    async def _send_error(send: Send, status: int, body: str) -> None:
        """エラーレスポンスを送信する。"""
        encoded = body.encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    [b"content-type", b"text/plain; charset=utf-8"],
                    [b"content-length", str(len(encoded)).encode()],
                ],
            }
        )
        await send({"type": "http.response.body", "body": encoded})
