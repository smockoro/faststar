"""ErrorHandlingMiddlewareのテスト。"""

from functools import partial

import pytest
import structlog
import structlog.testing
from core_toolkit.errors import BusinessProblem, SystemProblem
from fastmcp.server.middleware import MiddlewareContext
from mcp import types as mt

from fastmcp_toolkit.logging import _add_application_id, setup_logging
from fastmcp_toolkit.middleware import ErrorLoggerMiddleware
from fastmcp_toolkit.middleware.error_handling import ErrorHandlingMiddleware

_pre_chain = [
    structlog.contextvars.merge_contextvars,
    partial(_add_application_id, _application_id="test"),
]


def _make_context(
    method: str = "tools/call",
    message: mt.CallToolRequestParams | None = None,
) -> MiddlewareContext[mt.CallToolRequestParams]:
    if message is None:
        message = mt.CallToolRequestParams(name="test_tool")
    return MiddlewareContext(message=message, method=method, type="request")


class TestErrorHandlingMiddleware:
    @pytest.mark.asyncio
    async def test_no_exception_passes_through(self):
        middleware = ErrorHandlingMiddleware()
        context = _make_context()

        async def call_next(ctx):
            return "success"

        result = await middleware.on_message(context, call_next)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_business_problem_becomes_call_tool_result_with_is_error(self):
        setup_logging(application_id="test")
        middleware = ErrorHandlingMiddleware()
        context = _make_context()

        async def call_next(ctx):
            raise BusinessProblem("business.invalid_input", "入力が不正")

        result = await middleware.on_message(context, call_next)

        assert isinstance(result, mt.CallToolResult)
        assert result.isError is True
        assert result.content == [
            mt.TextContent(type="text", text="[business.invalid_input] 入力が不正")
        ]

    @pytest.mark.asyncio
    async def test_business_problem_logs_at_warning_level(self):
        setup_logging(application_id="test")
        middleware = ErrorHandlingMiddleware()
        context = _make_context()

        async def call_next(ctx):
            raise BusinessProblem("business.invalid_input", "入力が不正")

        with structlog.testing.capture_logs(_pre_chain) as logs:
            await middleware.on_message(context, call_next)

        assert len(logs) == 1
        assert logs[0]["log_level"] == "warning"
        assert logs[0]["exc_type"] == "BusinessProblem"

    @pytest.mark.asyncio
    async def test_system_problem_logs_at_error_level(self):
        setup_logging(application_id="test")
        middleware = ErrorHandlingMiddleware()
        context = _make_context()

        async def call_next(ctx):
            raise SystemProblem("system.db_unavailable", "DB接続に失敗")

        with structlog.testing.capture_logs(_pre_chain) as logs:
            result = await middleware.on_message(context, call_next)

        assert result.isError is True
        assert logs[0]["log_level"] == "error"

    @pytest.mark.asyncio
    async def test_non_application_problem_is_reraised_without_conversion(self):
        middleware = ErrorHandlingMiddleware()
        context = _make_context()

        async def call_next(ctx):
            raise ValueError("想定外のバグ")

        with pytest.raises(ValueError, match="想定外のバグ"):
            await middleware.on_message(context, call_next)


class TestInteractionWithErrorLoggerMiddleware:
    """ErrorLoggerMiddlewareを外側、ErrorHandlingMiddlewareを内側に置いた
    ときの役割分担を検証する。

    FastMCPの実際のミドルウェアチェーン構築は``for mw in
    reversed(self.middleware)``であり、**内側に置いたミドルウェアが先に
    例外を見る**（外側は内側が再raiseした後の例外しか受け取れない）。
    そのため、``ErrorHandlingMiddleware``を``ErrorLoggerMiddleware``より
    外側に置くと、``ApplicationProblem``は必ず先に内側の
    ``ErrorLoggerMiddleware``でログ＆re-raiseされてから外側に届いてしまい
    二重ログになる。ここでは正しい配置（``ErrorLoggerMiddleware``が外側、
    ``ErrorHandlingMiddleware``が内側）でチェーンを手動で再現し、
    ``ApplicationProblem``が外側の``ErrorLoggerMiddleware``まで
    例外として届かないことを検証する。
    """

    @pytest.mark.asyncio
    async def test_application_problem_does_not_reach_outer_error_logger(self):
        setup_logging(application_id="test")
        error_logger = ErrorLoggerMiddleware()
        error_handling = ErrorHandlingMiddleware()
        context = _make_context()

        async def innermost(ctx):
            raise BusinessProblem("business.invalid_input", "入力が不正")

        async def call_next_through_error_handling(ctx):
            return await error_handling.on_message(ctx, innermost)

        with structlog.testing.capture_logs(_pre_chain) as logs:
            result = await error_logger.on_message(
                context, call_next_through_error_handling
            )

        assert result.isError is True
        # ErrorHandlingMiddleware(内側)がApplicationProblemを変換して
        # 正常returnするため、外側のErrorLoggerMiddlewareには例外が
        # 伝播せず、ErrorLoggerMiddleware側のログ（logger_name=
        # "error_logger"）は発生しない。ErrorHandlingMiddleware側の
        # ログ（BusinessProblemなのでwarningレベル）のみが記録される。
        assert len(logs) == 1
        assert logs[0]["log_level"] == "warning"
        assert logs[0]["exc_type"] == "BusinessProblem"

    @pytest.mark.asyncio
    async def test_other_exception_still_reaches_outer_error_logger(self):
        setup_logging(application_id="test")
        error_logger = ErrorLoggerMiddleware()
        error_handling = ErrorHandlingMiddleware()
        context = _make_context()

        async def innermost(ctx):
            raise ValueError("想定外のバグ")

        async def call_next_through_error_handling(ctx):
            return await error_handling.on_message(ctx, innermost)

        with structlog.testing.capture_logs(_pre_chain) as logs:
            with pytest.raises(ValueError, match="想定外のバグ"):
                await error_logger.on_message(context, call_next_through_error_handling)

        # ErrorHandlingMiddleware(内側)はApplicationProblem以外を
        # 変換せず素通りさせるため、外側のErrorLoggerMiddlewareが
        # 拾ってログし、最終的にそのままraiseされる。
        assert len(logs) == 1
        assert logs[0]["exc_type"] == "ValueError"
