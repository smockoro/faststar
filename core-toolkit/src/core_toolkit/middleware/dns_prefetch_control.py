"""X-DNS-Prefetch-Control ASGIミドルウェア。"""

from collections.abc import Callable, MutableMapping
from typing import Any

__all__ = ["DNSPrefetchControlMiddleware"]

Scope = MutableMapping[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]


class DNSPrefetchControlMiddleware:
    """X-DNS-Prefetch-Control: offヘッダーを付与するASGIミドルウェア。

    ブラウザのDNSプリフェッチを無効化し、
    ページ内リンク先ドメインへの自動DNSクエリによる
    内部ドメイン名の漏洩を防止する。

    Args:
        app: 次のASGIアプリケーション。

    Example::

        from core_toolkit.middleware import DNSPrefetchControlMiddleware

        app.add_middleware(DNSPrefetchControlMiddleware)
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
                headers.append((b"x-dns-prefetch-control", b"off"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_header)
