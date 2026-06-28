"""Content-Security-Policy ASGIミドルウェア。"""

from collections.abc import Callable, MutableMapping
from typing import Any

__all__ = ["CSPMiddleware"]

Scope = MutableMapping[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]

_DEFAULT_DIRECTIVES: dict[str, str] = {
    "default-src": "'self'",
    "script-src": "'self'",
    "style-src": "'self'",
    "img-src": "'self' data: https:",
    "connect-src": "'self'",
    "object-src": "'none'",
    "frame-ancestors": "'none'",
    "base-uri": "'self'",
    "form-action": "'self'",
    "upgrade-insecure-requests": "",
}


class CSPMiddleware:
    """Content-Security-Policyヘッダーを付与するASGIミドルウェア。

    読み込み可能なコンテンツの出所を制限し、
    XSSやデータインジェクション攻撃を緩和する。

    Args:
        app: 次のASGIアプリケーション。
        directives: CSPディレクティブのdict。
            キーがディレクティブ名、値がソースリスト。
            値が空文字列のディレクティブはキーのみ出力される
            （例: ``upgrade-insecure-requests``）。
            未指定の場合、MCP UI向けの厳格なデフォルトを適用する。

    Example::

        from core_toolkit.middleware import CSPMiddleware

        # デフォルト（MCP UI向け厳格設定）
        app.add_middleware(CSPMiddleware)
        # カスタム
        app.add_middleware(CSPMiddleware, directives={
            "default-src": "'self'",
            "script-src": "'self' 'unsafe-inline'",
            "style-src": "'self' 'unsafe-inline'",
        })
    """

    def __init__(self, app: Any, *, directives: dict[str, str] | None = None) -> None:
        self.app = app
        resolved = directives if directives is not None else _DEFAULT_DIRECTIVES
        self._value = self._build_policy(resolved)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGIインターフェース実装。"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_header(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"content-security-policy", self._value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_header)

    @staticmethod
    def _build_policy(directives: dict[str, str]) -> bytes:
        """ディレクティブdictからCSPポリシー文字列を構築する。"""
        parts: list[str] = []
        for key, value in directives.items():
            if value:
                parts.append(f"{key} {value}")
            else:
                parts.append(key)
        return "; ".join(parts).encode()
