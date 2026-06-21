"""セキュリティヘッダー付与ASGIミドルウェア。

JSON APIおよびHTML（HTMX）レスポンスに適切なセキュリティヘッダーを設定する。
"""

from collections.abc import Callable, MutableMapping
from typing import Any, Literal

__all__ = ["SecurityHeadersMiddleware"]

Scope = MutableMapping[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]

_JSON_HEADERS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
]

_HTML_HEADERS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (
        b"content-security-policy",
        b"default-src 'self'; "
        b"script-src 'self'; "
        b"style-src 'self' 'unsafe-inline'; "
        b"img-src 'self' data: https:; "
        b"connect-src 'self'; "
        b"frame-ancestors 'self'; "
        b"base-uri 'self'; "
        b"form-action 'self'",
    ),
]


class SecurityHeadersMiddleware:
    """レスポンスにセキュリティヘッダーを付与するASGIミドルウェア。

    プリセットに応じて適切なヘッダーセットを全HTTPレスポンスに追加する。

    Args:
        app: 次のASGIアプリケーション。
        preset: ヘッダーのプリセット。

            - ``"json"``: ``X-Content-Type-Options: nosniff`` のみ。
              JSON APIやMCPサーバ向け。
            - ``"html"``: XSS・クリックジャッキング・インジェクション防御の
              フルセット。HTMX/HTMLレンダリングを行うFastAPIアプリ向け。

    Example::

        from core_toolkit.middleware import SecurityHeadersMiddleware

        # JSON API
        app.add_middleware(SecurityHeadersMiddleware, preset="json")

        # HTML/HTMX アプリケーション
        app.add_middleware(SecurityHeadersMiddleware, preset="html")
    """

    def __init__(
        self,
        app: Any,
        *,
        preset: Literal["json", "html"] = "json",
    ) -> None:
        self.app = app
        self._headers = _HTML_HEADERS if preset == "html" else _JSON_HEADERS

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGIインターフェース実装。"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(self._headers)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
