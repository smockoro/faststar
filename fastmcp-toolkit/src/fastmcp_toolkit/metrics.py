"""Prometheus形式のメトリクスエンドポイント登録ヘルパー。"""

from core_toolkit.metrics import ApplicationMeterHelper, make_metrics_handler
from fastmcp import FastMCP
from opentelemetry import metrics
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.sdk.metrics import MeterProvider

__all__ = [
    "ApplicationMeterHelper",
    "make_metrics_handler",
    "register_metrics_endpoint",
]


def register_metrics_endpoint(app: FastMCP, *, path: str = "/metrics") -> None:
    """FastMCPアプリケーションにPrometheusメトリクスエンドポイントを登録する。

    PrometheusMetricReaderを設定し、指定パスでscrape可能なエンドポイントを追加する。

    Args:
        app: FastMCPアプリケーションインスタンス。
        path: メトリクスエンドポイントのパス。
    """
    reader = PrometheusMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)
    app.custom_route(path, methods=["GET"])(make_metrics_handler())
