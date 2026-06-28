"""X-Robots-Tag ASGIミドルウェア。"""

from collections.abc import Callable, MutableMapping
from typing import Any

__all__ = ["RobotsTagMiddleware"]

Scope = MutableMapping[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]


class RobotsTagMiddleware:
    """X-Robots-Tagヘッダーを付与するASGIミドルウェア。

    検索エンジンによるページのインデックスを制御し、
    内部URLやパラメータ情報の公開を防止する。

    Args:
        app: 次のASGIアプリケーション。
        value: X-Robots-Tagの値。

    Example::

        from core_toolkit.middleware import RobotsTagMiddleware

        app.add_middleware(RobotsTagMiddleware)
        # カスタム値
        app.add_middleware(RobotsTagMiddleware, value="noindex")
    """

    def __init__(self, app: Any, *, value: str = "noindex, nofollow") -> None:
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
                headers.append((b"x-robots-tag", self._value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_header)
