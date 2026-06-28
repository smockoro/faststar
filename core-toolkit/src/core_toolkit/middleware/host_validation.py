"""Hostヘッダー検証ASGIミドルウェア。"""

import logging
from collections.abc import Callable, MutableMapping
from typing import Any

__all__ = ["HostValidationMiddleware"]

Scope = MutableMapping[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]

logger = logging.getLogger(__name__)


class HostValidationMiddleware:
    """Hostヘッダーを許可リストと照合しDNS Rebinding攻撃を防ぐASGIミドルウェア。

    許可リストに含まれないHostヘッダーのリクエストを421で拒否する。
    ワイルドカードポート（``"localhost:*"`` 形式）に対応する。

    Args:
        app: 次のASGIアプリケーション。
        allowed_hosts: 許可するHostヘッダー値のタプル。

    Example::

        from core_toolkit.middleware import HostValidationMiddleware

        app.add_middleware(
            HostValidationMiddleware,
            allowed_hosts=("api.example.com", "localhost:*"),
        )
    """

    def __init__(self, app: Any, *, allowed_hosts: tuple[str, ...] = ()) -> None:
        self.app = app
        self.allowed_hosts = allowed_hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGIインターフェース実装。"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        host = headers.get(b"host", b"").decode("latin-1")

        if not self._validate(host):
            await _send_error(send, 421, "Invalid Host header")
            return

        await self.app(scope, receive, send)

    def _validate(self, host: str) -> bool:
        if not host:
            logger.warning("Missing Host header in request")
            return False
        if host in self.allowed_hosts:
            return True
        for allowed in self.allowed_hosts:
            if allowed.endswith(":*") and host.startswith(allowed[:-1]):
                return True
        logger.warning("Rejected Host header: %s", host)
        return False


async def _send_error(send: Send, status: int, body: str) -> None:
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
