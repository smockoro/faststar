"""キャッシュ制御ASGIミドルウェア。"""

from collections.abc import Callable, MutableMapping
from typing import Any

__all__ = ["CacheControlMiddleware"]

Scope = MutableMapping[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]


class CacheControlMiddleware:
    """Cache-Controlヘッダーを付与するASGIミドルウェア。

    レスポンスのキャッシュ制御を設定し、プロキシやブラウザによる
    機密データの意図しないキャッシュを防止する。

    Args:
        app: 次のASGIアプリケーション。
        value: Cache-Controlヘッダーの値。

    Example::

        from core_toolkit.middleware import CacheControlMiddleware

        app.add_middleware(CacheControlMiddleware)
        # カスタム値
        app.add_middleware(CacheControlMiddleware, value="no-store")
    """

    def __init__(self, app: Any, *, value: str = "no-store, max-age=0") -> None:
        self.app = app
        self._value = value.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGIインターフェース実装。"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_header(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"cache-control", self._value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_header)
