"""FastMCPアプリケーション向けヘルスチェックエンドポイント登録ヘルパー。"""

from core_toolkit.health import (
    ReadinessCheck,
    make_liveness_handler,
    make_readiness_handler,
)
from fastmcp import FastMCP

__all__ = ["ReadinessCheck", "register_health_endpoints"]


def register_health_endpoints(
    app: FastMCP,
    *,
    liveness_path: str = "/livez",
    readiness_path: str = "/readyz",
    readiness_checks: list[ReadinessCheck] | None = None,
) -> None:
    """FastMCPアプリケーションにヘルスチェックエンドポイントを登録する。

    Kubernetes等のオーケストレーションツール向けに、Liveness（生存確認）と
    Readiness（準備完了確認）の2つのHTTPエンドポイントを追加する。

    Args:
        app: FastMCPアプリケーションインスタンス。
        liveness_path: Livenessエンドポイントのパス。
        readiness_path: Readinessエンドポイントのパス。
        readiness_checks: Readiness判定用の非同期チェック関数のリスト。
            各関数は ``(ok: bool, detail: str)`` のタプルを返す。
            未指定の場合はLivenessと同じく常にreadyと判定する。
    """
    checks = readiness_checks or []
    app.custom_route(liveness_path, methods=["GET"])(make_liveness_handler())
    app.custom_route(readiness_path, methods=["GET"])(make_readiness_handler(checks))
