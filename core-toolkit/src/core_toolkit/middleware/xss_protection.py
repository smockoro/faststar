"""X-XSS-Protection ASGIミドルウェア。"""

from collections.abc import Callable, MutableMapping
from typing import Any

__all__ = ["XSSProtectionMiddleware"]

Scope = MutableMapping[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]


class XSSProtectionMiddleware:
    """X-XSS-Protection: 0ヘッダーを付与するASGIミドルウェア。

    ブラウザの古いXSSフィルタを明示的に無効化する。
    XSSフィルタの有効化は逆にXSS脆弱性を生むケースがあるため、
    XSS対策はCSPに一元化する。

    Args:
        app: 次のASGIアプリケーション。

    Example::

        from core_toolkit.middleware import XSSProtectionMiddleware

        app.add_middleware(XSSProtectionMiddleware)
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
                headers.append((b"x-xss-protection", b"0"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_header)
