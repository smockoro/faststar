"""Cross-Origin-Opener-Policy ASGIミドルウェア。"""

from collections.abc import Callable, MutableMapping
from typing import Any

__all__ = ["COOPMiddleware"]

Scope = MutableMapping[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]


class COOPMiddleware:
    """Cross-Origin-Opener-Policyヘッダーを付与するASGIミドルウェア。

    クロスオリジンのドキュメントとブラウジングコンテキストグループを
    共有しないことを保証し、Spectre系サイドチャネル攻撃を防止する。

    Args:
        app: 次のASGIアプリケーション。
        policy: COOPポリシー値。

    Example::

        from core_toolkit.middleware import COOPMiddleware

        app.add_middleware(COOPMiddleware)
        app.add_middleware(COOPMiddleware, policy="same-origin-allow-popups")
    """

    def __init__(self, app: Any, *, policy: str = "same-origin") -> None:
        self.app = app
        self._value = policy.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGIインターフェース実装。"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_header(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"cross-origin-opener-policy", self._value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_header)
