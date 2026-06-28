"""X-Frame-Options ASGIミドルウェア。"""

from collections.abc import Callable, MutableMapping
from typing import Any, Literal

__all__ = ["FrameGuardMiddleware"]

Scope = MutableMapping[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]


class FrameGuardMiddleware:
    """X-Frame-Optionsヘッダーを付与するASGIミドルウェア。

    ページがiframe等に埋め込まれることを防ぎ、
    クリックジャッキング攻撃を防止する。

    Args:
        app: 次のASGIアプリケーション。
        action: フレーム埋め込みポリシー。

    Example::

        from core_toolkit.middleware import FrameGuardMiddleware

        # MCP UI向け（完全拒否）
        app.add_middleware(FrameGuardMiddleware, action="DENY")
        # 同一オリジンのみ許可
        app.add_middleware(FrameGuardMiddleware, action="SAMEORIGIN")
    """

    def __init__(
        self, app: Any, *, action: Literal["DENY", "SAMEORIGIN"] = "DENY"
    ) -> None:
        self.app = app
        self._value = action.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGIインターフェース実装。"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_header(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-frame-options", self._value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_header)
