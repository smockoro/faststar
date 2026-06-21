"""structlogを使用したASGIアクセスログミドルウェア。

uvicornのデフォルトアクセスログを、メソッド・パス・ステータスコード・
レスポンス時間・クライアントアドレスを含む構造化出力に置き換える。
"""

import time
from collections.abc import Callable, MutableMapping
from typing import Any

import structlog

__all__ = ["AccessLogMiddleware"]

Scope = MutableMapping[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]


def _decode_headers(
    raw_headers: list[tuple[bytes, bytes] | list[bytes]],
) -> dict[str, str]:
    """ASGIのrawヘッダリストをデコードしてdictに変換する。

    Args:
        raw_headers: ASGIスコープまたはメッセージのヘッダリスト。

    Returns:
        ヘッダ名をキー、値をバリューとするdict。
    """
    return {k.decode("latin-1"): v.decode("latin-1") for k, v in raw_headers}


class _AccessLogResponder:
    """HTTPリクエスト/レスポンスのボディとヘッダをキャプチャしてログに記録するレスポンダ。

    AccessLogMiddlewareが各HTTPリクエストごとにインスタンスを作成し、
    リクエスト/レスポンスの状態をインスタンス属性として保持する。

    Args:
        app: ラップ対象のASGIアプリケーション。
        logger: ログ出力に使用するstructlogロガー。
    """

    def __init__(self, app: Any, logger: structlog.stdlib.BoundLogger) -> None:
        self.app = app
        self.logger = logger
        self.request_body_chunks: list[bytes] = []
        self.status_code: int = 0
        self.response_headers: dict[str, str] = {}
        self.response_body_chunks: list[bytes] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """アプリケーションを実行し、完了後にリクエスト/レスポンスログを出力する。

        Args:
            scope: ASGIスコープ。
            receive: ASGIのreceiveコールバック。
            send: ASGIのsendコールバック。
        """
        self.scope = scope
        self.receive = receive
        self.send = send
        self.start = time.perf_counter()

        await self.app(scope, self.receive_wrapper, self.send_wrapper)

        duration_ms = (time.perf_counter() - self.start) * 1000
        path = scope["path"]
        query_string = scope.get("query_string", b"").decode("latin-1")
        request_headers = _decode_headers(scope.get("headers", []))
        request_body = b"".join(self.request_body_chunks).decode(
            "utf-8", errors="replace"
        )
        response_body = b"".join(self.response_body_chunks).decode(
            "utf-8", errors="replace"
        )

        self.logger.info(
            "request",
            method=scope["method"],
            path=path,
            query_string=query_string,
            request_body=request_body,
            headers=request_headers,
        )

        self.logger.info(
            "response",
            status=self.status_code,
            path=path,
            query_string=query_string,
            response_body=response_body,
            headers=self.response_headers,
            duration_ms=round(duration_ms, 1),
        )

    async def receive_wrapper(self) -> MutableMapping[str, Any]:
        """receiveをラップしてリクエストボディチャンクを収集する。

        Returns:
            受信したASGIメッセージ。
        """
        message = await self.receive()
        if message["type"] == "http.request":
            self.request_body_chunks.append(message.get("body", b""))
        return message

    async def send_wrapper(self, message: MutableMapping[str, Any]) -> None:
        """sendをラップしてレスポンスのステータス・ヘッダ・ボディを収集する。

        Args:
            message: ASGIのレスポンスメッセージ。
        """
        if message["type"] == "http.response.start":
            self.status_code = message["status"]
            self.response_headers = _decode_headers(message.get("headers", []))
        elif message["type"] == "http.response.body":
            self.response_body_chunks.append(message.get("body", b""))
        await self.send(message)


class AccessLogMiddleware:
    """structlog経由で構造化アクセスログを出力するASGIミドルウェア。

    リクエスト/レスポンスのヘッダとボディを構造化ログに記録する。
    ``/livez``, ``/readyz``, ``/metrics`` へのリクエストはログ対象外。

    Args:
        app: ラップ対象のASGIアプリケーション。
        logger_name: structlogロガーインスタンスの名前。

    Example::

        from core_toolkit.middleware import AccessLogMiddleware

        asgi_app = fastmcp_app.streamable_http_app()
        asgi_app.add_middleware(AccessLogMiddleware)
        uvicorn.run(asgi_app, host="0.0.0.0", port=8000, access_log=False)
    """

    def __init__(self, app: Any, *, logger_name: str = "access") -> None:
        self.app = app
        self.logger: structlog.stdlib.BoundLogger = structlog.get_logger(
            logger_name, log_type="access"
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGIインターフェースのエントリポイント。

        Args:
            scope: ASGIスコープ。
            receive: ASGIのreceiveコールバック。
            send: ASGIのsendコールバック。
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if scope["path"] in ("/livez", "/readyz", "/metrics"):
            await self.app(scope, receive, send)
            return

        responder = _AccessLogResponder(app=self.app, logger=self.logger)
        await responder(scope, receive, send)
