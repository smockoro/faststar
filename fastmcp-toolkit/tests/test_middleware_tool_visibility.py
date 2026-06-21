"""ToolVisibilityMiddleware のテスト。"""

import pytest
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools import Tool
from mcp import types as mt

from fastmcp_toolkit.middleware.tool_visibility import ToolVisibilityMiddleware


def _make_tool(name: str) -> Tool:
    async def _noop() -> str:
        return ""

    return Tool.from_function(_noop, name=name)


def _make_context() -> MiddlewareContext[mt.ListToolsRequest]:
    return MiddlewareContext(
        message=mt.ListToolsRequest(),
        method="tools/list",
        type="request",
    )


class TestToolVisibilityMiddleware:
    @pytest.mark.asyncio
    async def test_no_filter_when_both_empty(self):
        """prefix・suffix ともに空なら全ツールが返る。"""
        middleware = ToolVisibilityMiddleware((), ())
        tools = [_make_tool("alpha"), _make_tool("beta")]

        async def call_next(ctx):
            return tools

        result = await middleware.on_list_tools(_make_context(), call_next)
        assert [t.name for t in result] == ["alpha", "beta"]

    @pytest.mark.asyncio
    async def test_prefix_only_filters_matching(self):
        """prefix のみ設定ではprefixマッチするツールが非表示になる。"""
        middleware = ToolVisibilityMiddleware(("_",), ())
        tools = [_make_tool("_hidden"), _make_tool("visible")]

        async def call_next(ctx):
            return tools

        result = await middleware.on_list_tools(_make_context(), call_next)
        assert [t.name for t in result] == ["visible"]

    @pytest.mark.asyncio
    async def test_suffix_only_filters_matching(self):
        """suffix のみ設定ではsuffixマッチするツールが非表示になる。"""
        middleware = ToolVisibilityMiddleware((), ("_debug",))
        tools = [_make_tool("tool_debug"), _make_tool("tool_prod")]

        async def call_next(ctx):
            return tools

        result = await middleware.on_list_tools(_make_context(), call_next)
        assert [t.name for t in result] == ["tool_prod"]

    @pytest.mark.asyncio
    async def test_both_match_hides_tool(self):
        """prefix と suffix の両方が設定されている場合、AND条件で非表示になる。"""
        middleware = ToolVisibilityMiddleware(("_",), ("_internal",))
        tools = [
            _make_tool("_secret_internal"),
            _make_tool("_visible"),
            _make_tool("public_internal"),
            _make_tool("normal"),
        ]

        async def call_next(ctx):
            return tools

        result = await middleware.on_list_tools(_make_context(), call_next)
        assert [t.name for t in result] == [
            "_visible",
            "public_internal",
            "normal",
        ]

    @pytest.mark.asyncio
    async def test_multiple_prefixes_and_suffixes(self):
        """複数の prefix/suffix を指定できる。"""
        middleware = ToolVisibilityMiddleware(("_", "x_"), ("_dbg", "_test"))
        tools = [
            _make_tool("_foo_dbg"),
            _make_tool("x_bar_test"),
            _make_tool("_foo_prod"),
            _make_tool("y_bar_dbg"),
            _make_tool("normal"),
        ]

        async def call_next(ctx):
            return tools

        result = await middleware.on_list_tools(_make_context(), call_next)
        assert [t.name for t in result] == [
            "_foo_prod",
            "y_bar_dbg",
            "normal",
        ]

    @pytest.mark.asyncio
    async def test_empty_tool_list(self):
        """ツールが空でもエラーにならない。"""
        middleware = ToolVisibilityMiddleware(("_",), ("_x",))

        async def call_next(ctx):
            return []

        result = await middleware.on_list_tools(_make_context(), call_next)
        assert result == []
