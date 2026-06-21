"""FastMCPアプリケーション向けサーバー起動ヘルパー。"""

import logging
import os

import uvicorn
from fastmcp import FastMCP

from fastmcp_toolkit.config.application_config import (
    ApplicationConfigProvider,
    ConfigType,
)
from fastmcp_toolkit.health import ReadinessCheck, register_health_endpoints
from fastmcp_toolkit.logging import setup_logging
from fastmcp_toolkit.metrics import register_metrics_endpoint
from core_toolkit.middleware import AccessLogMiddleware, TransportSecurityASGIMiddleware
from fastmcp_toolkit.middleware import ErrorLoggerMiddleware, LogContextMiddleware
from fastmcp_toolkit.middleware.tool_metrics import ToolMetricsMiddleware
from fastmcp_toolkit.middleware.tool_visibility import ToolVisibilityMiddleware

__all__ = ["run_server"]


def run_server(
    app: FastMCP,
    *,
    host: str = "0.0.0.0",
    port: int = 8000,
    application_id: str | None = None,
    json_output: bool = False,
    log_level: int = logging.INFO,
    health_check: bool = False,
    metrics_enabled: bool = False,
    readiness_checks: list[ReadinessCheck] | None = None,
) -> None:
    """構造化ロギングとミドルウェアを設定済みでFastMCPサーバーを起動する。

    設定内容:
      - application_id付きのstructlog（デフォルトはapp.name）
      - AccessLogMiddleware（ASGI層の構造化アクセスログ）
      - デフォルトアクセスログを無効化したuvicorn

    Args:
        app: FastMCPアプリケーションインスタンス。
        host: バインドアドレス。
        port: バインドポート。
        application_id: 全ログ行に含まれる識別子。
            未指定の場合 ``app.name`` がデフォルト。
        json_output: Trueの場合JSONログを出力（本番用）。
        log_level: 最小ログレベル。
        health_check: Trueの場合ヘルスチェックエンドポイントを登録する。
        metrics_enabled: Trueの場合Prometheusメトリクスを公開する。
        readiness_checks: Readiness判定用の非同期チェック関数のリスト。
            ``health_check=True`` の場合のみ有効。
    """
    resolved_app_id = application_id if application_id is not None else app.name

    application_config = ApplicationConfigProvider.get_config(
        os.getenv("CONFIG_TYPE", ConfigType.ENV)
    )

    setup_logging(
        application_id=resolved_app_id,
        json_output=json_output,
        level=log_level,
    )

    if application_config.health_check_enabled or health_check:
        register_health_endpoints(
            app,
            readiness_checks=readiness_checks,
            liveness_path=application_config.health_check_liveness_endpoint,
            readiness_path=application_config.health_check_readiness_endpoint,
        )

    app.add_middleware(LogContextMiddleware())
    app.add_middleware(ErrorLoggerMiddleware())
    app.add_middleware(
        ToolVisibilityMiddleware(
            application_config.invisible_target_prefix,
            application_config.invisible_target_suffix,
        )
    )

    if application_config.metrics_enabled or metrics_enabled:
        register_metrics_endpoint(app, path=application_config.metrics_endpoint)
        app.add_middleware(ToolMetricsMiddleware())

    starlette_app = app.http_app()
    starlette_app.add_middleware(AccessLogMiddleware)

    if application_config.transport_security_enabled:
        starlette_app.add_middleware(
            TransportSecurityASGIMiddleware,
            allowed_hosts=application_config.allowed_hosts,
            allowed_origins=application_config.allowed_origins,
        )

    uvicorn.run(
        starlette_app,
        host=host,
        port=port,
        access_log=False,
        log_level=logging.getLevelName(log_level).lower(),
        log_config=None,
    )
