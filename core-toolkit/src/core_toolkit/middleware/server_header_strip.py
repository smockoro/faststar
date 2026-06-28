"""レスポンスヘッダー削除ASGIミドルウェア。"""

from collections.abc import Callable, MutableMapping
from typing import Any

__all__ = ["ServerHeaderStripMiddleware"]

Scope = MutableMapping[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]


class ServerHeaderStripMiddleware:
    """指定ヘッダーをレスポンスから削除するASGIミドルウェア。

    サーバーソフトウェア名やフレームワーク情報を含むヘッダーを削除し、
    攻撃者に技術スタックの情報を提供することを防ぐ。

    Args:
        app: 次のASGIアプリケーション。
        headers: 削除対象のヘッダー名（小文字）のタプル。

    Example::

        from core_toolkit.middleware import ServerHeaderStripMiddleware

        # デフォルト（Server, X-Powered-By を削除）
        app.add_middleware(ServerHeaderStripMiddleware)
        # 追加のヘッダーも削除
        app.add_middleware(ServerHeaderStripMiddleware, headers=(
            "server", "x-powered-by", "x-aspnet-version",
        ))
    """

    def __init__(
        self,
        app: Any,
        *,
        headers: tuple[str, ...] = ("server", "x-powered-by"),
    ) -> None:
        self.app = app
        self._strip_set = frozenset(h.lower().encode() for h in headers)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGIインターフェース実装。"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_filtered(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() not in self._strip_set
                ]
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_filtered)
