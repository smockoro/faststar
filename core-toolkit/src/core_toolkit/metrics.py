"""Prometheusメトリクスエンドポイントハンドラファクトリとメトリクスヘルパー。"""

from collections.abc import Awaitable, Callable
from typing import Any

from opentelemetry import metrics
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
from starlette.requests import Request
from starlette.responses import Response

__all__ = ["ApplicationMeterHelper", "make_metrics_handler"]


def make_metrics_handler() -> Callable[[Request], Awaitable[Response]]:
    """Prometheusメトリクスエンドポイントのハンドラを生成する。

    prometheus_clientのデフォルトレジストリからメトリクスを収集し、
    Prometheus scrape形式で返すStarlette互換ハンドラを返す。

    Returns:
        Starlette/FastAPI互換のASGIハンドラ関数。

    Example::

        from core_toolkit.metrics import make_metrics_handler
        from starlette.routing import Route

        handler = make_metrics_handler()
        # Starlette
        Route("/metrics", handler, methods=["GET"])
        # FastMCP
        app.custom_route("/metrics", methods=["GET"])(handler)
    """

    async def handler(request: Request) -> Response:
        return Response(
            content=generate_latest(REGISTRY),
            media_type=CONTENT_TYPE_LATEST,
            headers={"X-Content-Type-Options": "nosniff"},
        )

    return handler


class ApplicationMeterHelper:
    """OTELメトリクスを1行で記録するためのヘルパー。

    Counter/Histogram/UpDownCounterを名前ベースでlazy生成しキャッシュする。
    ユーザーは名前・値・属性だけ渡せばよい。

    Args:
        name: OpenTelemetry Meterの名前。

    Example::

        from core_toolkit.metrics import ApplicationMeterHelper

        meter = ApplicationMeterHelper("my-service")

        # カウンタ加算
        meter.count("db.query.total", attributes={"table": "users"})

        # 値の記録（ヒストグラム）
        meter.record("db.select.row_count", 42, {"table": "users"})

        # 現在値の増減（ゲージ的）
        meter.gauge("connection.active", 5)
    """

    def __init__(self, name: str = "fastmcp-app") -> None:
        self._meter = metrics.get_meter(name)
        self._counters: dict[str, Any] = {}
        self._histograms: dict[str, Any] = {}
        self._up_down_counters: dict[str, Any] = {}

    def count(
        self,
        name: str,
        value: int | float = 1,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """カウンタを加算する。

        累積的な値（リクエスト数、エラー数など）に使用する。

        Args:
            name: メトリクス名。
            value: 加算する値。
            attributes: ラベルとして付与する属性。
        """
        if name not in self._counters:
            self._counters[name] = self._meter.create_counter(name)
        self._counters[name].add(value, attributes or {})

    def record(
        self,
        name: str,
        value: int | float,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """ヒストグラムに値を記録する。

        分布を見たい値（レコード件数、レイテンシ、ペイロードサイズなど）に使用する。

        Args:
            name: メトリクス名。
            value: 記録する値。
            attributes: ラベルとして付与する属性。
        """
        if name not in self._histograms:
            self._histograms[name] = self._meter.create_histogram(name)
        self._histograms[name].record(value, attributes or {})

    def gauge(
        self,
        name: str,
        value: int | float,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """ゲージ（現在値）を増減する。

        現在の状態を表す値（アクティブ接続数、キューの深さなど）に使用する。
        UpDownCounterとして実装されるため、負の値を渡せば減算になる。

        Args:
            name: メトリクス名。
            value: 増減する値（負で減算）。
            attributes: ラベルとして付与する属性。
        """
        if name not in self._up_down_counters:
            self._up_down_counters[name] = self._meter.create_up_down_counter(name)
        self._up_down_counters[name].add(value, attributes or {})
