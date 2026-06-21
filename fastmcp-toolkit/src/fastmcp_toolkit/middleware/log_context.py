"""リクエスト情報をstructlog contextvarsにバインドするミドルウェア。

後続の全ログ出力にrequest_id、session_id、method、ツール名等を自動付与し、
リクエスト単位でのログ追跡を容易にする。
"""

from typing import Any

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from mcp import types as mt
from structlog.contextvars import bind_contextvars, clear_contextvars

__all__ = ["LogContextMiddleware"]


class LogContextMiddleware(Middleware):
    """リクエストのメタデータをstructlog contextvarsにバインドするミドルウェア。

    バインドされる情報:
      - ``session_id``: MCPセッションID（HTTPヘッダー ``mcp-session-id`` から取得）
      - ``request_id``: MCPリクエストID
      - ``method``: MCPメソッド名（例: ``"tools/call"``）
      - ``target``: 操作対象の識別子（ツール名、リソースURI、プロンプト名）

    リクエスト完了後にバインドを解除するため、他のリクエストにリークしない。

    Example::

        from fastmcp import FastMCP
        from fastmcp_toolkit.middleware import LogContextMiddleware

        app = FastMCP("my-server")
        app.add_middleware(LogContextMiddleware())
    """

    async def on_message(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        """リクエストメタデータをcontextvarsにバインドして下流を実行する。

        Args:
            context: ミドルウェアコンテキスト。
            call_next: 次のミドルウェアまたはハンドラを呼び出すコールバック。

        Returns:
            下流ハンドラの戻り値。
        """
        clear_contextvars()
        bindings = self._extract_bindings(context)
        bind_contextvars(**bindings)
        return await call_next(context)

    def _extract_bindings(self, context: MiddlewareContext[Any]) -> dict[str, str]:
        """コンテキストからログバインディング用のメタデータを抽出する。

        Args:
            context: ミドルウェアコンテキスト。

        Returns:
            バインドするキーと値のdict。
        """
        bindings: dict[str, str] = {}

        if context.method:
            bindings["mcp_method"] = context.method

        ctx = context.fastmcp_context
        if ctx is not None:
            request_context = ctx.request_context
            if request_context is not None:
                bindings["request_id"] = str(request_context.request_id)
                request = getattr(request_context, "request", None)
                if request is not None:
                    session_id = request.headers.get("mcp-session-id", "")
                    if session_id:
                        bindings["session_id"] = session_id

                    trace_id = request.headers.get("traceparent", "")
                    if trace_id:
                        bindings["trace_id"] = trace_id

        target = self._extract_target(context)
        if target:
            bindings["target"] = target

        return bindings

    def _extract_target(self, context: MiddlewareContext[Any]) -> str:
        """リクエストメッセージから操作対象の識別子を抽出する。

        Args:
            context: ミドルウェアコンテキスト。

        Returns:
            操作対象の識別子。該当しない場合は空文字列。
        """
        msg = context.message
        if isinstance(msg, mt.CallToolRequestParams):
            return msg.name
        if isinstance(msg, mt.ReadResourceRequestParams):
            return str(msg.uri)
        if isinstance(msg, mt.GetPromptRequestParams):
            return msg.name
        return ""
