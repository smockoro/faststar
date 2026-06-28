"""POSTリクエストのContent-Type検証ASGIミドルウェア。"""

from collections.abc import Callable, MutableMapping
from typing import Any

__all__ = ["JsonContentTypeMiddleware"]

Scope = MutableMapping[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]


class JsonContentTypeMiddleware:
    """POSTリクエストのContent-TypeをJSON系に限定するASGIミドルウェア。

    HTMLフォームが送信できないContent-Type（application/json）を強制することで、
    フォームベースのCSRF攻撃を防ぐ。
    POST以外のメソッドはContent-Typeの検証を行わない。

    Args:
        app: 次のASGIアプリケーション。

    Example::

        from core_toolkit.middleware import JsonContentTypeMiddleware

        app.add_middleware(JsonContentTypeMiddleware)
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGIインターフェース実装。"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if scope.get("method") == "POST":
            headers = dict(scope.get("headers", []))
            content_type = headers.get(b"content-type", b"").decode("latin-1")
            if not content_type.lower().startswith("application/json"):
                await _send_error(send, 400, "Invalid Content-Type header")
                return

        await self.app(scope, receive, send)


async def _send_error(send: Send, status: int, body: str) -> None:
    encoded = body.encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                [b"content-type", b"text/plain; charset=utf-8"],
                [b"content-length", str(len(encoded)).encode()],
            ],
        }
    )
    await send({"type": "http.response.body", "body": encoded})
