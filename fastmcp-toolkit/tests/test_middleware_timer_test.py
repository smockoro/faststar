"""TimerTestミドルウェアのユニットテスト。"""

from unittest.mock import MagicMock, patch

import pytest
from fastmcp.server.middleware import MiddlewareContext
from mcp import types as mt

from fastmcp_toolkit.middleware.timer_test import TimerTest


def _make_context(
    method: str | None = "tools/call",
    message: object | None = None,
) -> MiddlewareContext:
    if message is None:
        message = mt.CallToolRequestParams(name="my_tool")
    return MiddlewareContext(
        message=message,
        method=method,
        type="request",
    )


def _mock_request(headers: dict[str, str]) -> MagicMock:
    """Starlette Requestのモックを作成する。"""
    request = MagicMock()
    request.headers = headers
    return request


class TestTimerTestMiddleware:
    """TimerTestミドルウェアのユニットテスト。"""

    @pytest.mark.asyncio
    async def test_delays_when_header_present(self):
        """x-timer-testヘッダが存在する場合、指定秒数だけ遅延する。"""
        middleware = TimerTest()
        context = _make_context()
        called = False
        slept_seconds: list[int] = []

        async def fake_sleep(seconds):
            slept_seconds.append(seconds)

        async def call_next(ctx):
            nonlocal called
            called = True
            return "ok"

        with (
            patch(
                "fastmcp_toolkit.middleware.timer_test.get_http_request",
                return_value=_mock_request({"x-timer-test": "3"}),
            ),
            patch(
                "fastmcp_toolkit.middleware.timer_test.asyncio.sleep",
                side_effect=fake_sleep,
            ),
        ):
            result = await middleware.on_message(context, call_next)

        assert result == "ok"
        assert called
        assert slept_seconds == [3]

    @pytest.mark.asyncio
    async def test_no_delay_when_header_absent(self):
        """x-timer-testヘッダが無い場合、遅延せずcall_nextを呼ぶ。"""
        middleware = TimerTest()
        context = _make_context()
        called = False

        async def call_next(ctx):
            nonlocal called
            called = True
            return "ok"

        with patch(
            "fastmcp_toolkit.middleware.timer_test.get_http_request",
            return_value=_mock_request({}),
        ):
            result = await middleware.on_message(context, call_next)

        assert result == "ok"
        assert called

    @pytest.mark.asyncio
    async def test_passes_through_on_lookup_error(self):
        """get_http_requestがLookupErrorを投げた場合、遅延せずcall_nextを呼ぶ。"""
        middleware = TimerTest()
        context = _make_context()
        called = False

        async def call_next(ctx):
            nonlocal called
            called = True
            return "ok"

        with patch(
            "fastmcp_toolkit.middleware.timer_test.get_http_request",
            side_effect=LookupError("no request context"),
        ):
            result = await middleware.on_message(context, call_next)

        assert result == "ok"
        assert called

    @pytest.mark.asyncio
    async def test_passes_through_on_attribute_error(self):
        """get_http_requestがAttributeErrorを投げた場合、遅延せずcall_nextを呼ぶ。"""
        middleware = TimerTest()
        context = _make_context()
        called = False

        async def call_next(ctx):
            nonlocal called
            called = True
            return "ok"

        with patch(
            "fastmcp_toolkit.middleware.timer_test.get_http_request",
            side_effect=AttributeError("no headers"),
        ):
            result = await middleware.on_message(context, call_next)

        assert result == "ok"
        assert called

    @pytest.mark.asyncio
    async def test_actual_delay_is_nonblocking(self):
        """asyncio.sleepを使っているため、イベントループをブロックしない。"""
        middleware = TimerTest()
        context = _make_context()

        async def call_next(ctx):
            return "ok"

        with patch(
            "fastmcp_toolkit.middleware.timer_test.get_http_request",
            return_value=_mock_request({"x-timer-test": "0"}),
        ):
            result = await middleware.on_message(context, call_next)

        assert result == "ok"

    @pytest.mark.asyncio
    async def test_custom_logger_name(self):
        """カスタムlogger_nameでインスタンス化できる。"""
        middleware = TimerTest(logger_name="custom_timer")
        assert middleware.logger is not None
