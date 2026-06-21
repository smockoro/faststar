"""LogContextMiddlewareのテスト。"""

from functools import partial

import pytest
import structlog
import structlog.testing
from fastmcp.server.middleware import MiddlewareContext
from mcp import types as mt
from structlog.contextvars import get_contextvars

from fastmcp_toolkit.logging import _add_application_id, setup_logging
from fastmcp_toolkit.middleware import LogContextMiddleware

_pre_chain = [
    structlog.contextvars.merge_contextvars,
    partial(_add_application_id, _application_id="test"),
]


def _make_context(
    method: str | None = "tools/call",
    message: object | None = None,
    fastmcp_context: object | None = None,
) -> MiddlewareContext:
    if message is None:
        message = mt.CallToolRequestParams(name="my_tool")
    return MiddlewareContext(
        message=message,
        method=method,
        type="request",
        fastmcp_context=fastmcp_context,
    )


class TestLogContextMiddleware:
    """LogContextMiddlewareの動作検証。"""

    @pytest.mark.asyncio
    async def test_binds_method(self):
        """methodがcontextvarsにバインドされる。"""
        setup_logging(application_id="test")
        middleware = LogContextMiddleware()
        context = _make_context(method="tools/call")
        captured: dict = {}

        async def call_next(ctx):
            captured.update(get_contextvars())
            return "ok"

        await middleware.on_message(context, call_next)
        assert captured["mcp_method"] == "tools/call"

    @pytest.mark.asyncio
    async def test_binds_tool_name_as_target(self):
        """CallToolRequestParamsのツール名がtargetとしてバインドされる。"""
        setup_logging(application_id="test")
        middleware = LogContextMiddleware()
        message = mt.CallToolRequestParams(name="greet")
        context = _make_context(method="tools/call", message=message)
        captured: dict = {}

        async def call_next(ctx):
            captured.update(get_contextvars())
            return "ok"

        await middleware.on_message(context, call_next)
        assert captured["target"] == "greet"

    @pytest.mark.asyncio
    async def test_binds_resource_uri_as_target(self):
        """ReadResourceRequestParamsのURIがtargetとしてバインドされる。"""
        setup_logging(application_id="test")
        middleware = LogContextMiddleware()
        message = mt.ReadResourceRequestParams(uri="file:///tmp/data.txt")
        context = _make_context(method="resources/read", message=message)
        captured: dict = {}

        async def call_next(ctx):
            captured.update(get_contextvars())
            return "ok"

        await middleware.on_message(context, call_next)
        assert captured["target"] == "file:///tmp/data.txt"

    @pytest.mark.asyncio
    async def test_binds_prompt_name_as_target(self):
        """GetPromptRequestParamsのプロンプト名がtargetとしてバインドされる。"""
        setup_logging(application_id="test")
        middleware = LogContextMiddleware()
        message = mt.GetPromptRequestParams(name="summarize")
        context = _make_context(method="prompts/get", message=message)
        captured: dict = {}

        async def call_next(ctx):
            captured.update(get_contextvars())
            return "ok"

        await middleware.on_message(context, call_next)
        assert captured["target"] == "summarize"

    @pytest.mark.asyncio
    async def test_no_target_for_list_requests(self):
        """list系リクエストではtargetがバインドされない。"""
        setup_logging(application_id="test")
        middleware = LogContextMiddleware()
        context = _make_context(
            method="tools/list",
            message=mt.ListToolsRequest(method="tools/list"),
        )
        captured: dict = {}

        async def call_next(ctx):
            captured.update(get_contextvars())
            return "ok"

        await middleware.on_message(context, call_next)
        assert "target" not in captured

    @pytest.mark.asyncio
    async def test_clears_previous_context_on_new_message(self):
        """新しいメッセージ処理時に前回のcontextvarsがクリアされる。"""
        setup_logging(application_id="test")
        middleware = LogContextMiddleware()

        from structlog.contextvars import bind_contextvars as _bind

        _bind(stale_key="should_be_cleared")

        context = _make_context(method="tools/call")
        captured: dict = {}

        async def call_next(ctx):
            captured.update(get_contextvars())
            return "ok"

        await middleware.on_message(context, call_next)

        assert "stale_key" not in captured
        assert captured["mcp_method"] == "tools/call"

    @pytest.mark.asyncio
    async def test_no_leak_between_messages(self):
        """異なるメッセージ間でcontextvarsがリークしない。"""
        setup_logging(application_id="test")
        middleware = LogContextMiddleware()

        context1 = _make_context(method="tools/call")
        context2 = _make_context(
            method="tools/list",
            message=mt.ListToolsRequest(method="tools/list"),
        )
        captured: dict = {}

        async def call_next(ctx):
            captured.clear()
            captured.update(get_contextvars())
            return "ok"

        await middleware.on_message(context1, call_next)
        assert captured.get("target") == "my_tool"

        await middleware.on_message(context2, call_next)
        assert "target" not in captured

    @pytest.mark.asyncio
    async def test_context_visible_in_structlog_output(self):
        """バインドされた情報がstructlogの出力に含まれる。"""
        setup_logging(application_id="test")
        middleware = LogContextMiddleware()
        message = mt.CallToolRequestParams(name="hello")
        context = _make_context(method="tools/call", message=message)

        logger = structlog.get_logger("test_logger")

        async def call_next(ctx):
            logger.info("inside handler")
            return "ok"

        with structlog.testing.capture_logs(_pre_chain) as logs:
            await middleware.on_message(context, call_next)

        assert len(logs) == 1
        entry = logs[0]
        assert entry["mcp_method"] == "tools/call"
        assert entry["target"] == "hello"

    @pytest.mark.asyncio
    async def test_no_method_when_none(self):
        """methodがNoneの場合はバインドされない。"""
        setup_logging(application_id="test")
        middleware = LogContextMiddleware()
        context = _make_context(method=None)
        captured: dict = {}

        async def call_next(ctx):
            captured.update(get_contextvars())
            return "ok"

        await middleware.on_message(context, call_next)
        assert "method" not in captured
