"""FastMCPアプリケーション向けヘルスチェックエンドポイント。"""

from collections.abc import Awaitable, Callable

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

__all__ = ["ReadinessCheck", "register_health_endpoints"]

ReadinessCheck = Callable[[], Awaitable[tuple[bool, str]]]
"""Readiness判定用の非同期チェック関数の型。``(ok, detail)`` を返す。"""


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

    Livenessエンドポイントはプロセスが応答可能であれば常に200を返す。
    Readinessエンドポイントは全てのチェック関数がTrueを返した場合に200を返し、
    いずれかがFalseを返した場合は503を返す。

    Args:
        app: FastMCPアプリケーションインスタンス。
        liveness_path: Livenessエンドポイントのパス。
        readiness_path: Readinessエンドポイントのパス。
        readiness_checks: Readiness判定用の非同期チェック関数のリスト。
            各関数は ``(ok: bool, detail: str)`` のタプルを返す。
            未指定の場合はLivenessと同じく常にreadyと判定する。
    """
    checks = readiness_checks or []

    _nosniff_headers = {"X-Content-Type-Options": "nosniff"}

    async def _liveness_handler(request: Request) -> Response:
        return JSONResponse({"status": "ok"}, headers=_nosniff_headers)

    async def _readiness_handler(request: Request) -> Response:
        if not checks:
            return JSONResponse({"status": "ok"}, headers=_nosniff_headers)

        results: dict[str, dict[str, object]] = {}
        all_ok = True
        for check in checks:
            ok, detail = await check()
            name = getattr(check, "__name__", repr(check))
            results[name] = {"ok": ok, "detail": detail}
            if not ok:
                all_ok = False

        status_code = 200 if all_ok else 503
        status_text = "ok" if all_ok else "unavailable"
        return JSONResponse(
            {"status": status_text, "checks": results},
            status_code=status_code,
            headers=_nosniff_headers,
        )

    app.custom_route(liveness_path, methods=["GET"])(_liveness_handler)
    app.custom_route(readiness_path, methods=["GET"])(_readiness_handler)
