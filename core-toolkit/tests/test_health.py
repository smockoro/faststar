"""ヘルスチェックハンドラファクトリのテスト。"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.routing import Route

from core_toolkit.health import make_liveness_handler, make_readiness_handler


def _create_app(
    liveness_path: str = "/livez",
    readiness_path: str = "/readyz",
    checks: list | None = None,
) -> Starlette:
    return Starlette(
        routes=[
            Route(liveness_path, make_liveness_handler(), methods=["GET"]),
            Route(
                readiness_path,
                make_readiness_handler(checks or []),
                methods=["GET"],
            ),
        ]
    )


@pytest.mark.asyncio
async def test_liveness_returns_ok():
    """Livenessエンドポイントは常に200を返す。"""
    async with AsyncClient(
        transport=ASGITransport(app=_create_app()), base_url="http://test"
    ) as client:
        resp = await client.get("/livez")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_liveness_has_nosniff_header():
    """Livenessレスポンスにnosniffヘッダーが付与される。"""
    async with AsyncClient(
        transport=ASGITransport(app=_create_app()), base_url="http://test"
    ) as client:
        resp = await client.get("/livez")
    assert resp.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_readiness_without_checks_returns_ok():
    """チェックなしのReadinessは常に200を返す。"""
    async with AsyncClient(
        transport=ASGITransport(app=_create_app()), base_url="http://test"
    ) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_all_checks_pass():
    """全チェックが成功すれば200とok statusを返す。"""

    async def check_db() -> tuple[bool, str]:
        return True, "connected"

    async def check_cache() -> tuple[bool, str]:
        return True, "reachable"

    async with AsyncClient(
        transport=ASGITransport(app=_create_app(checks=[check_db, check_cache])),
        base_url="http://test",
    ) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["check_db"] == {"ok": True, "detail": "connected"}
    assert body["checks"]["check_cache"] == {"ok": True, "detail": "reachable"}


@pytest.mark.asyncio
async def test_readiness_with_failing_check_returns_503():
    """いずれかのチェックが失敗すれば503とunavailable statusを返す。"""

    async def check_ok() -> tuple[bool, str]:
        return True, "fine"

    async def check_fail() -> tuple[bool, str]:
        return False, "timeout"

    async with AsyncClient(
        transport=ASGITransport(app=_create_app(checks=[check_ok, check_fail])),
        base_url="http://test",
    ) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["check_ok"] == {"ok": True, "detail": "fine"}
    assert body["checks"]["check_fail"] == {"ok": False, "detail": "timeout"}


@pytest.mark.asyncio
async def test_readiness_check_function_name_used_as_key():
    """チェック関数名がレスポンスのキーになる。"""

    async def my_custom_check() -> tuple[bool, str]:
        return True, "ok"

    async with AsyncClient(
        transport=ASGITransport(app=_create_app(checks=[my_custom_check])),
        base_url="http://test",
    ) as client:
        resp = await client.get("/readyz")
    assert "my_custom_check" in resp.json()["checks"]


@pytest.mark.asyncio
async def test_readiness_has_nosniff_header():
    """Readinessレスポンスにnosniffヘッダーが付与される。"""
    async with AsyncClient(
        transport=ASGITransport(app=_create_app()), base_url="http://test"
    ) as client:
        resp = await client.get("/readyz")
    assert resp.headers["x-content-type-options"] == "nosniff"
