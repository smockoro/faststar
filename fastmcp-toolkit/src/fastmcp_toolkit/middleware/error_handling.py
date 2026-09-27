"""ApplicationProblem系例外をCallToolResultに変換するFastMCP Middleware。"""

from typing import Any

import structlog
from core_toolkit.errors import ApplicationProblem, BusinessProblem
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from mcp import types as mt

__all__ = ["ErrorHandlingMiddleware"]


class ErrorHandlingMiddleware(Middleware):
    """``ApplicationProblem``系例外を``CallToolResult(isError=True)``に変換する。

    MCPツール呼び出しの失敗は``CallToolResult.isError: bool``という2値
    でしか表現できず、JSON-RPCの``error``（-32xxx）はメソッド不明等ごく
    一部のプロトコルレベルのケース専用であるため、fastapi-toolkitのような
    error_code→jsonrpc_codeの変換テーブルは持たない（詳細は設計doc
    ``docs/superpowers/specs/2026-09-28-error-handling-design.md``参照）。

    ``fastmcp_toolkit.middleware.ErrorLoggerMiddleware``より後に
    ``add_middleware``する（＝オニオン構造でErrorLoggerMiddlewareより
    内側に置く）必要がある。FastMCPのミドルウェアは内側が先に例外を見る
    オニオン構造（外側は内側が再raiseした後の例外しか受け取れない）ため、
    このMiddlewareが外側にあると``ApplicationProblem``が必ず先に
    ``ErrorLoggerMiddleware``でログされてから届いてしまい、二重ログに
    なる。内側に置き、かつ``ApplicationProblem``をここで完結させ
    （re-raiseしない）ことで、``ApplicationProblem``は外側の
    ``ErrorLoggerMiddleware``には例外として届かずログされない。
    ``ErrorLoggerMiddleware``は、このMiddlewareが変換しなかった
    （＝``ApplicationProblem``以外の）真に想定外の例外だけを拾う
    最終防波堤として機能する。

    Args:
        logger_name: structlogロガーインスタンスの名前。
    """

    def __init__(self, logger_name: str = "error_handling") -> None:
        self.logger: structlog.stdlib.BoundLogger = structlog.get_logger(
            logger_name, log_type="exception"
        )

    async def on_message(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        """``ApplicationProblem``を捕捉し``CallToolResult``へ変換する。

        Args:
            context: ミドルウェアコンテキスト。
            call_next: 次のミドルウェアまたはハンドラを呼び出すコールバック。

        Returns:
            下流ハンドラの戻り値。``ApplicationProblem``発生時は
            ``CallToolResult(isError=True, ...)``。

        Raises:
            Exception: ``ApplicationProblem``以外の例外はそのまま再送出する。
        """
        try:
            return await call_next(context)
        except ApplicationProblem as exc:
            log = (
                self.logger.warning
                if isinstance(exc, BusinessProblem)
                else self.logger.error
            )
            log(
                f"{exc.error_code}: {exc.message}",
                method=context.method,
                exc_type=type(exc).__name__,
            )
            return mt.CallToolResult(
                isError=True,
                content=[
                    mt.TextContent(
                        type="text", text=f"[{exc.error_code}] {exc.message}"
                    )
                ],
            )
