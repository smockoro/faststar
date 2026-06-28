"""MIMEタイプスニッフィング防止ASGIミドルウェア。"""

from collections.abc import Callable, MutableMapping
from typing import Any

__all__ = ["NoSniffMiddleware"]

Scope = MutableMapping[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]


class NoSniffMiddleware:
    """X-Content-Type-Options: nosniffヘッダーを付与するASGIミドルウェア。

    ブラウザのMIMEタイプスニッフィングを防止し、
    Content-Typeの誤解釈によるXSSリスクを排除する。

    Args:
        app: 次のASGIアプリケーション。

    Example::

        from core_toolkit.middleware import NoSniffMiddleware

        app.add_middleware(NoSniffMiddleware)
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGIインターフェース実装。"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_header(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-content-type-options", b"nosniff"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_header)
