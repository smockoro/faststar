"""Cross-Origin-Resource-Policy ASGIミドルウェア。"""

from collections.abc import Callable, MutableMapping
from typing import Any

__all__ = ["CORPMiddleware"]

Scope = MutableMapping[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]


class CORPMiddleware:
    """Cross-Origin-Resource-Policyヘッダーを付与するASGIミドルウェア。

    リソースを含めることができるオリジンを制御し、
    Spectre系サイドチャネル攻撃およびXSSIを防止する。

    Args:
        app: 次のASGIアプリケーション。
        policy: CORPポリシー値。

    Example::

        from core_toolkit.middleware import CORPMiddleware

        app.add_middleware(CORPMiddleware)
        app.add_middleware(CORPMiddleware, policy="same-site")
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
                headers.append((b"cross-origin-resource-policy", self._value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_header)
