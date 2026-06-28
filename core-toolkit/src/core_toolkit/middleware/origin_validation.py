"""Originヘッダー検証ASGIミドルウェア。"""

import logging
from collections.abc import Callable, MutableMapping
from typing import Any

__all__ = ["OriginValidationMiddleware"]

Scope = MutableMapping[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]

logger = logging.getLogger(__name__)


class OriginValidationMiddleware:
    """Originヘッダーを許可リストと照合しクロスオリジンリクエストを防ぐASGIミドルウェア。

    Originヘッダーが存在し、許可リストに含まれない場合に403で拒否する。
    Originヘッダーが存在しない（ブラウザ外アクセス等）場合は通過させる。
    ワイルドカードポート（``"http://localhost:*"`` 形式）に対応する。

    Args:
        app: 次のASGIアプリケーション。
        allowed_origins: 許可するOriginヘッダー値のタプル。

    Example::

        from core_toolkit.middleware import OriginValidationMiddleware

        app.add_middleware(
            OriginValidationMiddleware,
            allowed_origins=("https://consent.example.com",),
        )
    """

    def __init__(self, app: Any, *, allowed_origins: tuple[str, ...] = ()) -> None:
        self.app = app
        self.allowed_origins = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGIインターフェース実装。"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        origin = headers.get(b"origin", b"").decode("latin-1")

        if origin and not self._validate(origin):
            await _send_error(send, 403, "Invalid Origin header")
            return

        await self.app(scope, receive, send)

    def _validate(self, origin: str) -> bool:
        if origin in self.allowed_origins:
            return True
        for allowed in self.allowed_origins:
            if allowed.endswith(":*") and origin.startswith(allowed[:-1]):
                return True
        logger.warning("Rejected Origin header: %s", origin)
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
