"""ErrorLoggerMiddlewareのテスト。"""

from functools import partial

import pytest
import structlog
import structlog.testing
from fastmcp.server.middleware import MiddlewareContext
from mcp import types as mt

from fastmcp_toolkit.logging import _add_application_id, setup_logging
from fastmcp_toolkit.middleware import ErrorLoggerMiddleware

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
    return MiddlewareContext(
        message=message,
        method=method,
        type="request",
    )


class TestErrorLoggerMiddleware:
    """ErrorLoggerMiddlewareの動作検証。"""

    @pytest.mark.asyncio
    async def test_no_exception_passes_through(self):
        """例外がなければそのまま結果を返す。"""
        setup_logging(application_id="test")
        middleware = ErrorLoggerMiddleware()
        context = _make_context()

        async def call_next(ctx):
            return "success"

        result = await middleware.on_message(context, call_next)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_logs_exception_and_reraises(self):
        """例外発生時にログを記録し、同じ例外をre-raiseする。"""
        setup_logging(application_id="test")
        middleware = ErrorLoggerMiddleware()
        context = _make_context(method="tools/call")

        async def call_next(ctx):
            raise ValueError("something went wrong")

        with structlog.testing.capture_logs(_pre_chain) as logs:
            with pytest.raises(ValueError, match="something went wrong"):
                await middleware.on_message(context, call_next)

        assert len(logs) == 1
        entry = logs[0]
        assert entry["event"] == "something went wrong"
        assert entry["method"] == "tools/call"
        assert entry["exc_type"] == "ValueError"
        assert entry["log_level"] == "error"

    @pytest.mark.asyncio
    async def test_logs_exc_type_for_different_exceptions(self):
        """異なる例外クラスに対してexc_typeが正しく記録される。"""
        setup_logging(application_id="test")
        middleware = ErrorLoggerMiddleware()
        context = _make_context(method="resources/read")

        async def call_next(ctx):
            raise RuntimeError("runtime error")

        with structlog.testing.capture_logs(_pre_chain) as logs:
            with pytest.raises(RuntimeError):
                await middleware.on_message(context, call_next)

        entry = logs[0]
        assert entry["exc_type"] == "RuntimeError"
        assert entry["method"] == "resources/read"

    @pytest.mark.asyncio
    async def test_custom_logger_name(self):
        """カスタムlogger_nameが使用される。"""
        setup_logging(application_id="test")
        middleware = ErrorLoggerMiddleware(logger_name="custom_error")
        context = _make_context()

        async def call_next(ctx):
            raise TypeError("type error")

        with structlog.testing.capture_logs(_pre_chain) as logs:
            with pytest.raises(TypeError):
                await middleware.on_message(context, call_next)

        assert len(logs) == 1
        assert logs[0]["log_type"] == "exception"
