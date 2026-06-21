"""ツール呼び出しの回数とレイテンシを自動計測するミドルウェア。"""

import time

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp import types as mt
from opentelemetry import metrics

__all__ = ["ToolMetricsMiddleware"]


class ToolMetricsMiddleware(Middleware):
    """全ツールの呼び出し回数と実行時間をPrometheusメトリクスとして自動収集する。

    ``metrics_enabled=True`` 時に自動登録される。ユーザーが意識する必要はない。

    計測されるメトリクス:
      - ``mcp.tool.call.total``: 呼び出し回数（tool_name, status別）
      - ``mcp.tool.call.duration_ms``: 実行時間のヒストグラム（tool_name別）

    Args:
        name: OpenTelemetry Meterの名前。
    """

    def __init__(self, name: str = "fastmcp-toolkit") -> None:
        meter = metrics.get_meter(name)
        self._counter = meter.create_counter(
            "mcp.tool.call.total",
            description="ツール呼び出し回数",
        )
        self._histogram = meter.create_histogram(
            "mcp.tool.call.duration_ms",
            unit="ms",
            description="ツール実行時間",
        )

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """ツール呼び出しの前後で回数と実行時間を計測する。

        Args:
            context: ツール呼び出しのミドルウェアコンテキスト。
            call_next: 次のミドルウェアまたはハンドラを呼び出すコールバック。

        Returns:
            ツール実行結果。

        Raises:
            Exception: 下流で発生した例外を計測後に再送出する。
        """
        tool_name = context.message.name
        start = time.perf_counter()
        try:
            result = await call_next(context)
            self._counter.add(1, {"tool_name": tool_name, "status": "success"})
            return result
        except Exception:
            self._counter.add(1, {"tool_name": tool_name, "status": "error"})
            raise
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._histogram.record(elapsed_ms, {"tool_name": tool_name})
