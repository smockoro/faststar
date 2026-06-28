"""HTTP Strict Transport Security ASGIミドルウェア。"""

from collections.abc import Callable, MutableMapping
from typing import Any

__all__ = ["HSTSMiddleware"]

Scope = MutableMapping[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]


class HSTSMiddleware:
    """Strict-Transport-Securityヘッダーを付与するASGIミドルウェア。

    HTTPからHTTPSへのプロトコルダウングレード攻撃を防止する。
    TLS終端がロードバランサー等で行われる環境でも、
    ブラウザに対してHTTPS接続を強制する効果がある。

    Args:
        app: 次のASGIアプリケーション。
        max_age: HSTSの有効期間（秒）。
        include_subdomains: サブドメインにも適用するか。

    Example::

        from core_toolkit.middleware import HSTSMiddleware

        app.add_middleware(HSTSMiddleware)
        # カスタム設定
        app.add_middleware(HSTSMiddleware, max_age=31536000, include_subdomains=False)
    """

    def __init__(
        self,
        app: Any,
        *,
        max_age: int = 63072000,
        include_subdomains: bool = True,
    ) -> None:
        self.app = app
        value = f"max-age={max_age}"
        if include_subdomains:
            value += "; includeSubDomains"
        self._value = value.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGIインターフェース実装。"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_header(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"strict-transport-security", self._value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_header)
