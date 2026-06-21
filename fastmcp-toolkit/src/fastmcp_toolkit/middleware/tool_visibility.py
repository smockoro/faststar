"""特定ツールを一覧から非表示にするミドルウェア。"""

from collections.abc import Sequence

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import Tool
from mcp import types as mt

__all__ = ["ToolVisibilityMiddleware"]


class ToolVisibilityMiddleware(Middleware):
    """ツール一覧から特定のツールを非表示にするミドルウェア。

    prefix と suffix の **両方** にマッチするツールのみ非表示とする（AND条件）。
    片方だけの設定では非表示にならないため、意図的に両方を指定する運用を想定する。

    Args:
        invisible_target_prefix: 非表示対象のプレフィックス群。
        invisible_target_suffix: 非表示対象のサフィックス群。
    """

    def __init__(
        self,
        invisible_target_prefix: tuple[str, ...],
        invisible_target_suffix: tuple[str, ...],
    ) -> None:
        self._invisible_target_prefix = invisible_target_prefix
        self._invisible_target_suffix = invisible_target_suffix

    def _is_invisible(self, name: str) -> bool:
        """指定条件に基づきツールが非表示かどうかを判定する。

        Args:
            name: ツール名。

        Returns:
            非表示にする場合True。
        """
        if not self._invisible_target_prefix and not self._invisible_target_suffix:
            return False

        if len(self._invisible_target_prefix) == 0:
            return name.endswith(self._invisible_target_suffix)

        if len(self._invisible_target_suffix) == 0:
            return name.startswith(self._invisible_target_prefix)

        return name.startswith(self._invisible_target_prefix) and name.endswith(
            self._invisible_target_suffix
        )

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        """ツール一覧から非表示対象のツールを除外して返す。

        Args:
            context: ツール一覧リクエストのミドルウェアコンテキスト。
            call_next: 次のミドルウェアまたはハンドラを呼び出すコールバック。

        Returns:
            非表示対象を除外したツールのリスト。
        """
        tools = await call_next(context)
        return [t for t in tools if not self._is_invisible(t.name)]
