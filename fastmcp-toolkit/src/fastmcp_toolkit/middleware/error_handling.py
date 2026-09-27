"""ApplicationProblem系例外をエラー扱いのToolResultに変換するFastMCP Middleware。"""

import structlog
from core_toolkit.errors import ApplicationProblem, BusinessProblem, SystemProblem
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp import types as mt

__all__ = ["ErrorHandlingMiddleware"]

# SystemProblemのmessageはホスト名・SQL・内部ID等を含みうるため、
# クライアント（LLM）には返さず固定文言に置き換える（原文はログにのみ残す）。
_SYSTEM_PROBLEM_MESSAGE = "Internal server error"


def _unwrap_application_problem(exc: Exception) -> ApplicationProblem | None:
    """捕捉した例外から``ApplicationProblem``を取り出す。

    FastMCPの``FastMCP.call_tool``は、ツールが送出した``FastMCPError``
    以外の例外を全て``raise ToolError(...) from e``で包み直す。そのため
    ミドルウェアに届くのは``ToolError``であり、元の``ApplicationProblem``
    は``__cause__``にしか残っていない。

    ``__cause__``を見るのは``ToolError``に限定する。任意の例外の
    ``__cause__``まで辿ると、利用者コードが意図的に別の例外へ変換した
    ケース（``raise RuntimeError(...) from business_problem``等）まで
    ``ApplicationProblem``として扱ってしまうため。FastMCPが将来包み直しを
    やめた場合に備え、``ApplicationProblem``が直接届いたケースも扱う。

    Args:
        exc: ミドルウェアが捕捉した例外。

    Returns:
        変換対象の``ApplicationProblem``。対象外なら``None``。
    """
    if isinstance(exc, ApplicationProblem):
        return exc
    if isinstance(exc, ToolError) and isinstance(exc.__cause__, ApplicationProblem):
        return exc.__cause__
    return None


class ErrorHandlingMiddleware(Middleware):
    """``ApplicationProblem``系例外を``ToolResult(is_error=True)``に変換する。

    MCPツール呼び出しの失敗は``CallToolResult.isError: bool``という2値
    でしか表現できず、JSON-RPCの``error``（-32xxx）はメソッド不明等ごく
    一部のプロトコルレベルのケース専用であるため、fastapi-toolkitのような
    error_code→jsonrpc_codeの変換テーブルは持たない（詳細は設計doc
    ``docs/superpowers/specs/2026-09-28-error-handling-design.md``参照）。

    フック位置と戻り値の型:
        ``on_message``ではなく``on_call_tool``（``tools/call``のみ）に
        フックする。``resources/read``や``prompts/get``等に
        ツール呼び出し用の結果型を返すのは誤りであるため。また
        FastMCPは``tools/call``のミドルウェアチェーンの戻り値に対して
        ``.to_mcp_result()``を呼ぶため、``mcp.types.CallToolResult``では
        なく``fastmcp.tools.base.ToolResult``を返す必要がある
        （``CallToolResult``を返すと``AttributeError``になる）。

    ``ToolError``の展開:
        FastMCPはツールが送出した``FastMCPError``以外の例外を全て
        ``raise ToolError(...) from e``で包み直してからミドルウェアに
        渡すため、``except ApplicationProblem``では捕捉できない。そこで
        ``Exception``を広く捕捉し、``ToolError``の``__cause__``が
        ``ApplicationProblem``であれば変換する。それ以外の例外は、
        展開した原因ではなく**捕捉した例外そのもの**を再送出し、
        FastMCP標準のエラー処理（``ToolError``のメッセージ・マスキング
        設定を含む）を一切変えない。

    メッセージのマスキング:
        ``BusinessProblem``は呼び出し側が対処できるよう``message``を
        そのまま返す。``SystemProblem``は内部情報の漏洩を防ぐため
        ``message``を固定文言に置き換え、``error_code``のみ返す
        （原文はログに残る）。

    登録順序:
        ``fastmcp_toolkit.middleware.ErrorLoggerMiddleware``より後に
        ``add_middleware``する（＝オニオン構造でErrorLoggerMiddlewareより
        内側に置く）必要がある。FastMCPのミドルウェアは内側が先に例外を
        見るオニオン構造（外側は内側が再raiseした後の例外しか受け取れない）
        ため、このMiddlewareが外側にあると``ApplicationProblem``が必ず先に
        ``ErrorLoggerMiddleware``でログされてから届いてしまい、二重ログに
        なる。内側に置き、かつ``ApplicationProblem``をここで完結させ
        （re-raiseしない）ことで、``ApplicationProblem``は外側の
        ``ErrorLoggerMiddleware``には例外として届かずログされない。
        ``ErrorLoggerMiddleware``は、このMiddlewareが変換しなかった
        （＝``ApplicationProblem``以外の）真に想定外の例外だけを拾う
        最終防波堤として機能する。

    Note:
        FastMCP本体（``FastMCP.call_tool``）は包み直しの直前に、自身の
        標準ロガーで``logger.exception("Error calling tool ...")``を
        出力する。これはミドルウェアより内側で起きるため、このMiddleware
        では抑止できない。

    Args:
        logger_name: structlogロガーインスタンスの名前。
    """

    def __init__(self, logger_name: str = "error_handling") -> None:
        self.logger: structlog.stdlib.BoundLogger = structlog.get_logger(
            logger_name, log_type="exception"
        )

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """``ApplicationProblem``を捕捉しエラー扱いの``ToolResult``へ変換する。

        Args:
            context: ミドルウェアコンテキスト。
            call_next: 次のミドルウェアまたはハンドラを呼び出すコールバック。

        Returns:
            下流ハンドラの戻り値。``ApplicationProblem``発生時は
            ``ToolResult(is_error=True, content=[TextContent(...)])``。

        Raises:
            Exception: ``ApplicationProblem``に由来しない例外は、捕捉した
                例外（``ToolError``等）をそのまま再送出する。
        """
        try:
            return await call_next(context)
        except Exception as caught:
            problem = _unwrap_application_problem(caught)
            if problem is None:
                raise

            log_fields = {
                "method": context.method,
                "exc_type": type(problem).__name__,
            }
            if isinstance(problem, BusinessProblem):
                self.logger.warning(
                    f"{problem.error_code}: {problem.message}", **log_fields
                )
            else:
                self.logger.exception(
                    f"{problem.error_code}: {problem.message}", **log_fields
                )

            message = (
                _SYSTEM_PROBLEM_MESSAGE
                if isinstance(problem, SystemProblem)
                else problem.message
            )
            return ToolResult(
                content=[
                    mt.TextContent(
                        type="text", text=f"[{problem.error_code}] {message}"
                    )
                ],
                is_error=True,
            )
