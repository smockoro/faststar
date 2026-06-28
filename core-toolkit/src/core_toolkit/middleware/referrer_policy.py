"""Referrer-Policy ASGIミドルウェア。"""

from collections.abc import Callable, MutableMapping
from typing import Any

__all__ = ["ReferrerPolicyMiddleware"]

Scope = MutableMapping[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]


class ReferrerPolicyMiddleware:
    """Referrer-Policyヘッダーを付与するASGIミドルウェア。

    リクエスト時に送信されるReferrer情報を制御し、
    内部URLやセッション情報の外部漏洩を防止する。

    Args:
        app: 次のASGIアプリケーション。
        policy: Referrer-Policyの値。

    Example::

        from core_toolkit.middleware import ReferrerPolicyMiddleware

        # API面向け（情報を一切漏らさない）
        app.add_middleware(ReferrerPolicyMiddleware, policy="no-referrer")
        # UI面向け（同一サイトナビゲーションを壊さない）
        app.add_middleware(
            ReferrerPolicyMiddleware, policy="strict-origin-when-cross-origin"
        )
    """

    def __init__(self, app: Any, *, policy: str = "no-referrer") -> None:
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
                headers.append((b"referrer-policy", self._value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_header)
