"""テスト用タイムアウトミドルウェア。

x-timer-testヘッダで指定された秒数だけレスポンスを遅延させる。
サーバーのタイムアウト設定の検証に使用する。
"""

import asyncio
from typing import Any

import structlog
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext


class TimerTest(Middleware):
    """テスト用タイムアウトミドルウェア。

    絶対に本番リリース時に利用しないこと。
    ``x-timer-test`` ヘッダで指定された秒数だけレスポンスを遅延させる。
    サーバーのタイムアウト設定の検証に使用する。

    Args:
        logger_name: structlogロガーインスタンスの名前。
    """

    def __init__(self, logger_name: str = "timer_test_logger") -> None:
        self.logger: structlog.stdlib.BoundLogger = structlog.get_logger(
            logger_name, log_type="timer_test"
        )
        self._timeout_header_name = "x-timer-test"

    async def on_message(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        """ヘッダ指定秒数だけ遅延してから下流を実行する。

        Args:
            context: ミドルウェアコンテキスト。
            call_next: 次のミドルウェアまたはハンドラを呼び出すコールバック。

        Returns:
            下流ハンドラの戻り値。
        """
        try:
            request = get_http_request()
            timer_header_value = request.headers.get(self._timeout_header_name)
        except LookupError, AttributeError:
            self.logger.warning(
                f"timer test: no {self._timeout_header_name} header or stdio mode"
            )
            return await call_next(context)

        if timer_header_value:
            seconds = int(timer_header_value)
            self.logger.warning(f"timer test: {seconds}s delaying", seconds=seconds)
            await asyncio.sleep(seconds)

        return await call_next(context)
