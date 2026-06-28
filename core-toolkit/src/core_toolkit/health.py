"""ヘルスチェックハンドラファクトリ。"""

from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

__all__ = ["ReadinessCheck", "make_liveness_handler", "make_readiness_handler"]

ReadinessCheck = Callable[[], Awaitable[tuple[bool, str]]]
"""Readiness判定用の非同期チェック関数の型。``(ok, detail)`` を返す。"""

_NOSNIFF = {"X-Content-Type-Options": "nosniff"}


def make_liveness_handler() -> Callable[[Request], Awaitable[Response]]:
    """Livenessエンドポイントのハンドラを生成する。

    プロセスが応答可能であれば常に200を返す。

    Returns:
        Starlette/FastAPI互換のASGIハンドラ関数。

    Example::

        from core_toolkit.health import make_liveness_handler

        handler = make_liveness_handler()
        app.add_route("/livez", handler, methods=["GET"])
    """

    async def handler(request: Request) -> Response:
        return JSONResponse({"status": "ok"}, headers=_NOSNIFF)

    return handler


def make_readiness_handler(
    checks: list[ReadinessCheck],
) -> Callable[[Request], Awaitable[Response]]:
    """Readinessエンドポイントのハンドラを生成する。

    全てのチェック関数が ``True`` を返した場合に200を返す。
    いずれかが ``False`` を返した場合は503を返す。
    チェックが空の場合は常に200を返す。

    Args:
        checks: Readiness判定用の非同期チェック関数のリスト。
            各関数は ``(ok: bool, detail: str)`` のタプルを返す。

    Returns:
        Starlette/FastAPI互換のASGIハンドラ関数。

    Example::

        from core_toolkit.health import make_readiness_handler

        async def check_db() -> tuple[bool, str]:
            return True, "connected"

        handler = make_readiness_handler([check_db])
        app.add_route("/readyz", handler, methods=["GET"])
    """

    async def handler(request: Request) -> Response:
        if not checks:
            return JSONResponse({"status": "ok"}, headers=_NOSNIFF)

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
            headers=_NOSNIFF,
        )

    return handler
