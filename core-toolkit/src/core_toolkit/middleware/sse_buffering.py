"""SSEバッファリング制御ASGIミドルウェア。"""

from collections.abc import Callable, MutableMapping
from typing import Any

__all__ = ["SSEBufferingMiddleware"]

Scope = MutableMapping[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]


class SSEBufferingMiddleware:
    """SSEレスポンスにX-Accel-Buffering: noを付与するASGIミドルウェア。

    Nginx等のリバースプロキシがSSEストリームをバッファリングし、
    リアルタイム性が失われることを防止する。
    Content-Typeが ``text/event-stream`` のレスポンスにのみ付与する。

    Args:
        app: 次のASGIアプリケーション。

    Example::

        from core_toolkit.middleware import SSEBufferingMiddleware

        app.add_middleware(SSEBufferingMiddleware)
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
                content_type = b""
                for k, v in headers:
                    if k.lower() == b"content-type":
                        content_type = v
                        break
                if content_type.startswith(b"text/event-stream"):
                    headers.append((b"x-accel-buffering", b"no"))
                    message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_header)
