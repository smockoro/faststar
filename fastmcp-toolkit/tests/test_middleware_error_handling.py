"""ErrorHandlingMiddlewareのテスト。

単体テスト（``on_call_tool``を手組みの``call_next``で直接呼ぶ）と、
実際の``FastMCP``インスタンス＋``fastmcp.Client``（インメモリ
トランスポート）を通す統合テストの両方を持つ。

FastMCP本体はツールが送出した例外を``raise ToolError(...) from e``で
包み直してからミドルウェアに渡すため、単体テストでも既定の経路は
``ToolError``（``__cause__``が``ApplicationProblem``）を送出する形で
本番の挙動を再現する。
"""

from functools import partial

import pytest
import structlog
import structlog.testing
from core_toolkit.errors import BusinessProblem, SystemProblem
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp import types as mt
from mcp.shared.exceptions import McpError

from fastmcp_toolkit.logging import _add_application_id, setup_logging
from fastmcp_toolkit.middleware import ErrorLoggerMiddleware
from fastmcp_toolkit.middleware.error_handling import ErrorHandlingMiddleware

_pre_chain = [
    structlog.contextvars.merge_contextvars,
    partial(_add_application_id, _application_id="test"),
]

_MASKED = "Internal server error"


def _make_context(
    message: mt.CallToolRequestParams | None = None,
) -> MiddlewareContext[mt.CallToolRequestParams]:
    if message is None:
        message = mt.CallToolRequestParams(name="test_tool")
    return MiddlewareContext(message=message, method="tools/call", type="request")


def _wrapped(problem: Exception) -> ToolError:
    """``FastMCP.call_tool``と同じく``ToolError``で包んだ例外を作る。"""
    try:
        raise ToolError(f"Error calling tool 'test_tool': {problem}") from problem
    except ToolError as e:
        return e


class TestErrorHandlingMiddleware:
    @pytest.mark.asyncio
    async def test_no_exception_passes_through(self):
        middleware = ErrorHandlingMiddleware()
        expected = ToolResult(content=[mt.TextContent(type="text", text="ok")])

        async def call_next(ctx):
            return expected

        result = await middleware.on_call_tool(_make_context(), call_next)
        assert result is expected

    @pytest.mark.asyncio
    async def test_wrapped_business_problem_becomes_error_tool_result(self):
        setup_logging(application_id="test")
        middleware = ErrorHandlingMiddleware()

        async def call_next(ctx):
            raise _wrapped(BusinessProblem("business.invalid_input", "入力が不正"))

        result = await middleware.on_call_tool(_make_context(), call_next)

        assert isinstance(result, ToolResult)
        assert result.is_error is True
        assert result.content == [
            mt.TextContent(type="text", text="[business.invalid_input] 入力が不正")
        ]

    @pytest.mark.asyncio
    async def test_bare_business_problem_is_also_converted(self):
        """FastMCPが将来包み直しをやめても変換できることを確認する。"""
        setup_logging(application_id="test")
        middleware = ErrorHandlingMiddleware()

        async def call_next(ctx):
            raise BusinessProblem("business.invalid_input", "入力が不正")

        result = await middleware.on_call_tool(_make_context(), call_next)

        assert result.is_error is True
        assert result.content == [
            mt.TextContent(type="text", text="[business.invalid_input] 入力が不正")
        ]

    @pytest.mark.asyncio
    async def test_business_problem_logs_at_warning_level(self):
        setup_logging(application_id="test")
        middleware = ErrorHandlingMiddleware()

        async def call_next(ctx):
            raise _wrapped(BusinessProblem("business.invalid_input", "入力が不正"))

        with structlog.testing.capture_logs(_pre_chain) as logs:
            await middleware.on_call_tool(_make_context(), call_next)

        assert len(logs) == 1
        assert logs[0]["log_level"] == "warning"
        assert logs[0]["exc_type"] == "BusinessProblem"
        assert logs[0]["method"] == "tools/call"

    @pytest.mark.asyncio
    async def test_system_problem_logs_at_error_level_with_exc_info(self):
        setup_logging(application_id="test")
        middleware = ErrorHandlingMiddleware()

        async def call_next(ctx):
            raise _wrapped(SystemProblem("system.db_unavailable", "DB接続に失敗"))

        with structlog.testing.capture_logs(_pre_chain) as logs:
            result = await middleware.on_call_tool(_make_context(), call_next)

        assert result.is_error is True
        assert len(logs) == 1
        assert logs[0]["log_level"] == "error"
        assert logs[0]["exc_type"] == "SystemProblem"
        # logger.exception()経由なのでトレースバックが付与される。
        assert logs[0]["exc_info"] is True

    @pytest.mark.asyncio
    async def test_system_problem_message_is_masked(self):
        setup_logging(application_id="test")
        middleware = ErrorHandlingMiddleware()

        async def call_next(ctx):
            raise _wrapped(
                SystemProblem("system.db_unavailable", "db-01.internal:5432 refused")
            )

        with structlog.testing.capture_logs(_pre_chain) as logs:
            result = await middleware.on_call_tool(_make_context(), call_next)

        text = result.content[0].text
        assert text == f"[system.db_unavailable] {_MASKED}"
        assert "db-01" not in text
        # 原文はログにだけ残る。
        assert "db-01.internal:5432 refused" in logs[0]["event"]

    @pytest.mark.asyncio
    async def test_non_application_problem_reraises_original_tool_error(self):
        middleware = ErrorHandlingMiddleware()
        original = _wrapped(ValueError("想定外のバグ"))

        async def call_next(ctx):
            raise original

        with pytest.raises(ToolError) as exc_info:
            await middleware.on_call_tool(_make_context(), call_next)
        # 展開した原因（ValueError）ではなく、捕捉したToolErrorそのものを
        # 再送出する。
        assert exc_info.value is original

    @pytest.mark.asyncio
    async def test_bare_non_application_problem_is_reraised(self):
        middleware = ErrorHandlingMiddleware()

        async def call_next(ctx):
            raise ValueError("想定外のバグ")

        with pytest.raises(ValueError, match="想定外のバグ"):
            await middleware.on_call_tool(_make_context(), call_next)

    @pytest.mark.asyncio
    async def test_application_problem_as_cause_of_non_tool_error_is_not_converted(
        self,
    ):
        """``__cause__``を辿るのは``ToolError``のみ。利用者が意図的に別の
        例外へ変換したものは``ApplicationProblem``として扱わない。
        """
        middleware = ErrorHandlingMiddleware()

        async def call_next(ctx):
            raise RuntimeError("変換済み") from BusinessProblem("business.x", "x")

        with pytest.raises(RuntimeError, match="変換済み"):
            await middleware.on_call_tool(_make_context(), call_next)


def _make_app(*, mask_error_details: bool = False) -> FastMCP:
    """``run_server``と同じ順序（ErrorLogger→ErrorHandling）で登録したアプリ。"""
    app = FastMCP("test", mask_error_details=mask_error_details)
    app.add_middleware(ErrorLoggerMiddleware())
    app.add_middleware(ErrorHandlingMiddleware())

    @app.tool
    async def business_tool() -> str:
        raise BusinessProblem("business.invalid_input", "入力が不正")

    @app.tool
    async def system_tool() -> str:
        raise SystemProblem("system.db_unavailable", "db-01.internal:5432 refused")

    @app.tool
    async def buggy_tool() -> str:
        raise ValueError("想定外のバグ")

    @app.tool
    async def ok_tool() -> str:
        return "ok"

    @app.resource("data://business")
    async def business_resource() -> str:
        raise BusinessProblem("business.invalid_input", "入力が不正")

    return app


class TestIntegrationWithRealFastMCP:
    """実際の``FastMCP``＋``Client``を通した統合テスト。

    ミドルウェアの登録順は``fastmcp_toolkit.server.run_server``と同じ
    （``ErrorLoggerMiddleware``が外側、``ErrorHandlingMiddleware``が内側）。
    """

    @pytest.mark.asyncio
    async def test_success_passes_through(self):
        async with Client(_make_app()) as client:
            result = await client.call_tool("ok_tool", {})
        assert result.is_error is False
        assert result.data == "ok"

    @pytest.mark.asyncio
    async def test_business_problem_becomes_is_error_result(self):
        setup_logging(application_id="test")
        async with Client(_make_app()) as client:
            result = await client.call_tool("business_tool", {}, raise_on_error=False)

        assert result.is_error is True
        assert len(result.content) == 1
        assert result.content[0].text == "[business.invalid_input] 入力が不正"

    @pytest.mark.asyncio
    async def test_system_problem_is_masked_and_logged_at_error(self):
        setup_logging(application_id="test")
        with structlog.testing.capture_logs(_pre_chain) as logs:
            async with Client(_make_app()) as client:
                result = await client.call_tool("system_tool", {}, raise_on_error=False)

        assert result.is_error is True
        text = result.content[0].text
        assert text == f"[system.db_unavailable] {_MASKED}"
        assert "db-01" not in text

        problem_logs = [r for r in logs if r.get("exc_type") == "SystemProblem"]
        assert len(problem_logs) == 1
        assert problem_logs[0]["log_level"] == "error"

    @pytest.mark.asyncio
    async def test_non_application_problem_keeps_fastmcp_default_behavior(self):
        setup_logging(application_id="test")
        async with Client(_make_app()) as client:
            result = await client.call_tool("buggy_tool", {}, raise_on_error=False)
            with pytest.raises(ToolError) as exc_info:
                await client.call_tool("buggy_tool", {})

        assert result.is_error is True
        text = result.content[0].text
        # FastMCP標準のToolErrorメッセージのまま（[error_code]形式ではない）。
        assert text == "Error calling tool 'buggy_tool': 想定外のバグ"
        assert not text.startswith("[")
        assert str(exc_info.value) == text

    @pytest.mark.asyncio
    async def test_business_problem_converted_even_with_mask_error_details(self):
        """``mask_error_details=True``でもToolErrorの``__cause__``は残るため
        ``BusinessProblem``はマスクされずに変換される。
        """
        setup_logging(application_id="test")
        async with Client(_make_app(mask_error_details=True)) as client:
            result = await client.call_tool("business_tool", {}, raise_on_error=False)

        assert result.is_error is True
        assert result.content[0].text == "[business.invalid_input] 入力が不正"

    @pytest.mark.asyncio
    async def test_resource_errors_are_not_touched(self):
        """``on_call_tool``のみにフックしているため、``resources/read``の
        例外は変換されずFastMCP標準のエラー（JSON-RPCエラー）になる。
        """
        setup_logging(application_id="test")
        async with Client(_make_app()) as client:
            with pytest.raises(McpError) as exc_info:
                await client.read_resource("data://business")

        assert (
            str(exc_info.value)
            == "Error reading resource 'data://business': 入力が不正"
        )


class TestInteractionWithErrorLoggerMiddleware:
    """``ErrorLoggerMiddleware``（外側）と``ErrorHandlingMiddleware``（内側）
    の役割分担を、実際の``FastMCP``＋``Client``で検証する。

    FastMCPのミドルウェアチェーンは内側が先に例外を見るオニオン構造で
    あるため、``ErrorHandlingMiddleware``が``ApplicationProblem``由来の
    ``ToolError``を変換して正常returnすれば、外側の
    ``ErrorLoggerMiddleware``には例外が届かずログされない（二重ログに
    ならない）。変換しなかった例外は外側で1回だけログされる。
    """

    @pytest.mark.asyncio
    async def test_business_problem_is_logged_once_not_by_error_logger(self):
        setup_logging(application_id="test")
        with structlog.testing.capture_logs(_pre_chain) as logs:
            async with Client(_make_app()) as client:
                result = await client.call_tool(
                    "business_tool", {}, raise_on_error=False
                )

        assert result.is_error is True
        # ErrorLoggerMiddlewareは受け取った例外の型（ToolError）をexc_typeに
        # 記録するため、ToolErrorのログが無いこと＝ErrorLoggerMiddlewareに
        # 例外が届いていないことを意味する。
        assert [r for r in logs if r.get("exc_type") == "ToolError"] == []
        exception_logs = [r for r in logs if r.get("log_type") == "exception"]
        assert len(exception_logs) == 1
        assert exception_logs[0]["exc_type"] == "BusinessProblem"
        assert exception_logs[0]["log_level"] == "warning"

    @pytest.mark.asyncio
    async def test_other_exception_is_logged_once_by_error_logger(self):
        setup_logging(application_id="test")
        with structlog.testing.capture_logs(_pre_chain) as logs:
            async with Client(_make_app()) as client:
                result = await client.call_tool("buggy_tool", {}, raise_on_error=False)

        assert result.is_error is True
        exception_logs = [r for r in logs if r.get("log_type") == "exception"]
        assert len(exception_logs) == 1
        assert exception_logs[0]["exc_type"] == "ToolError"
        assert exception_logs[0]["log_level"] == "error"
