"""Cross-Origin-Embedder-Policy ASGIミドルウェア。"""

from collections.abc import Callable, MutableMapping
from typing import Any

__all__ = ["COEPMiddleware"]

Scope = MutableMapping[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]


class COEPMiddleware:
    """Cross-Origin-Embedder-Policyヘッダーを付与するASGIミドルウェア。

    クロスオリジンリソースが明示的に許可していない限り
    読み込みをブロックする。COOPと組み合わせて
    Spectre系攻撃への防御を強化する。

    Args:
        app: 次のASGIアプリケーション。
        policy: COEPポリシー値。

    Example::

        from core_toolkit.middleware import COEPMiddleware

        app.add_middleware(COEPMiddleware)
        app.add_middleware(COEPMiddleware, policy="credentialless")
    """

    def __init__(self, app: Any, *, policy: str = "require-corp") -> None:
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
                headers.append((b"cross-origin-embedder-policy", self._value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_header)
