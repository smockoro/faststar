"""ToolMetricsMiddleware のテスト。"""

from unittest.mock import patch

import pytest
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp import types as mt

from fastmcp_toolkit.middleware.tool_metrics import ToolMetricsMiddleware


def _make_context(tool_name: str) -> MiddlewareContext[mt.CallToolRequestParams]:
    return MiddlewareContext(
        message=mt.CallToolRequestParams(name=tool_name),
        method="tools/call",
        type="request",
    )


class TestToolMetricsMiddleware:
    @pytest.mark.asyncio
    async def test_success_increments_counter(self):
        """正常終了時にstatus=successでカウンタが加算される。"""
        middleware = ToolMetricsMiddleware()
        result = ToolResult(content=[mt.TextContent(type="text", text="ok")])

        async def call_next(ctx):
            return result

        with (
            patch.object(middleware._counter, "add") as mock_counter,
            patch.object(middleware._histogram, "record") as mock_histogram,
        ):
            actual = await middleware.on_call_tool(_make_context("hello"), call_next)

        assert actual is result
        mock_counter.assert_called_once()
        args = mock_counter.call_args[0]
        assert args[0] == 1
        assert args[1]["tool_name"] == "hello"
        assert args[1]["status"] == "success"
        mock_histogram.assert_called_once()
        hist_args = mock_histogram.call_args[0]
        assert hist_args[0] > 0
        assert hist_args[1]["tool_name"] == "hello"

    @pytest.mark.asyncio
    async def test_error_increments_counter_with_error_status(self):
        """例外発生時にstatus=errorでカウンタが加算される。"""
        middleware = ToolMetricsMiddleware()

        async def call_next(ctx):
            raise RuntimeError("boom")

        with (
            patch.object(middleware._counter, "add") as mock_counter,
            patch.object(middleware._histogram, "record") as mock_histogram,
        ):
            with pytest.raises(RuntimeError, match="boom"):
                await middleware.on_call_tool(_make_context("broken_tool"), call_next)

        mock_counter.assert_called_once()
        args = mock_counter.call_args[0]
        assert args[0] == 1
        assert args[1]["tool_name"] == "broken_tool"
        assert args[1]["status"] == "error"
        mock_histogram.assert_called_once()

    @pytest.mark.asyncio
    async def test_duration_recorded_in_milliseconds(self):
        """実行時間がミリ秒で記録される。"""
        middleware = ToolMetricsMiddleware()
        result = ToolResult(content=[mt.TextContent(type="text", text="ok")])

        async def call_next(ctx):
            import asyncio

            await asyncio.sleep(0.01)
            return result

        with patch.object(middleware._histogram, "record") as mock_histogram:
            await middleware.on_call_tool(_make_context("slow_tool"), call_next)

        hist_args = mock_histogram.call_args[0]
        assert hist_args[0] >= 10.0

    @pytest.mark.asyncio
    async def test_different_tools_tracked_separately(self):
        """異なるツール名が別々に記録される。"""
        middleware = ToolMetricsMiddleware()
        result = ToolResult(content=[mt.TextContent(type="text", text="ok")])

        async def call_next(ctx):
            return result

        with patch.object(middleware._counter, "add") as mock_counter:
            await middleware.on_call_tool(_make_context("tool_a"), call_next)
            await middleware.on_call_tool(_make_context("tool_b"), call_next)

        assert mock_counter.call_count == 2
        first_call = mock_counter.call_args_list[0][0]
        second_call = mock_counter.call_args_list[1][0]
        assert first_call[1]["tool_name"] == "tool_a"
        assert second_call[1]["tool_name"] == "tool_b"
