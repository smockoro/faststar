"""FastMCP Middleware層の未処理例外をstructlogで記録するミドルウェア。"""

from typing import Any

import structlog
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

__all__ = ["ErrorLoggerMiddleware"]


class ErrorLoggerMiddleware(Middleware):
    """リクエスト処理中の未処理例外をstructlogで記録するミドルウェア。

    例外をログに記録した後そのままraiseするため、
    FastMCPのデフォルトエラーハンドリングには影響しない。

    Args:
        logger_name: structlogロガーインスタンスの名前。
    """

    def __init__(self, logger_name: str = "error_logger") -> None:
        self.logger: structlog.stdlib.BoundLogger = structlog.get_logger(
            logger_name, log_type="exception"
        )

    async def on_message(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        """全メッセージをインターセプトし、例外発生時にログを記録する。

        Args:
            context: ミドルウェアコンテキスト。
            call_next: 次のミドルウェアまたはハンドラを呼び出すコールバック。

        Returns:
            下流ハンドラの戻り値。

        Raises:
            Exception: 下流で発生した例外をログ記録後に再送出する。
        """
        try:
            return await call_next(context)
        except Exception as e:
            self.logger.exception(
                f"{e}",
                method=context.method,
                exc_type=type(e).__name__,
            )
            raise
