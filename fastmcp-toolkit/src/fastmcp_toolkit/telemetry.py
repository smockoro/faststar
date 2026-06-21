"""OpenTelemetryのSDK設定ヘルパー。

このモジュールはFastMCPより先にimportされることを前提とする。
FastMCP関連のimportは一切含まない。
"""

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def setup_telemetry(
    *,
    service_name: str,
    endpoint: str = "http://localhost:4317",
) -> None:
    """OpenTelemetryのTracerProviderを設定する。

    FastMCPをimportする前に呼び出すこと。SDKが設定されると、
    FastMCPの自動計装によるスパンがOTLPエクスポーターに送信される。

    Args:
        service_name: OTELリソースの ``service.name`` 属性に設定する値。
        endpoint: OTLPエクスポーターの送信先エンドポイント。
    """
    provider = TracerProvider(
        resource=Resource(attributes={"service.name": service_name})
    )
    processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
