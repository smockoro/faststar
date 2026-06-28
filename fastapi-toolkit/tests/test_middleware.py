"""ミドルウェア統合テスト。"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from fastapi_toolkit import (
    AccessLogMiddleware,
    COOPMiddleware,
    CSPMiddleware,
    FrameGuardMiddleware,
    NoSniffMiddleware,
    ReferrerPolicyMiddleware,
)


@pytest.fixture
def json_app() -> FastAPI:
    """NoSniffMiddleware付きアプリ（API向け最小構成）。"""
    app = FastAPI()
    app.add_middleware(NoSniffMiddleware)
    app.add_middleware(AccessLogMiddleware)

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"pong": "ok"}

    return app


@pytest.fixture
def html_app() -> FastAPI:
    """UI向けセキュリティヘッダー付きアプリ。"""
    app = FastAPI()
    app.add_middleware(
        CSPMiddleware,
        directives={
            "default-src": "'self'",
            "script-src": "'self'",
            "style-src": "'self' 'unsafe-inline'",
            "img-src": "'self' data: https:",
            "connect-src": "'self'",
            "frame-ancestors": "'self'",
            "base-uri": "'self'",
            "form-action": "'self'",
        },
    )
    app.add_middleware(COOPMiddleware)
    app.add_middleware(ReferrerPolicyMiddleware, policy="strict-origin-when-cross-origin")
    app.add_middleware(FrameGuardMiddleware, action="SAMEORIGIN")
    app.add_middleware(NoSniffMiddleware)

    @app.get("/page")
    async def page() -> str:
        return "<h1>Hello</h1>"

    return app


@pytest.mark.asyncio
async def test_access_log_middleware_passes_requests(json_app: FastAPI):
    """AccessLogMiddlewareがリクエストを正常に通過させる。"""
    async with AsyncClient(transport=ASGITransport(app=json_app), base_url="http://test") as client:
        resp = await client.get("/ping")

    assert resp.status_code == 200
    assert resp.json() == {"pong": "ok"}


@pytest.mark.asyncio
async def test_security_headers_json(json_app: FastAPI):
    """API向け構成ではX-Content-Type-Optionsのみ付与される。"""
    async with AsyncClient(transport=ASGITransport(app=json_app), base_url="http://test") as client:
        resp = await client.get("/ping")

    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in resp.headers
    assert "x-frame-options" not in resp.headers


@pytest.mark.asyncio
async def test_security_headers_html(html_app: FastAPI):
    """UI向け構成ではブラウザ保護ヘッダー一式が付与される。"""
    async with AsyncClient(transport=ASGITransport(app=html_app), base_url="http://test") as client:
        resp = await client.get("/page")

    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "SAMEORIGIN"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert resp.headers["cross-origin-opener-policy"] == "same-origin"
    assert "content-security-policy" in resp.headers
    csp = resp.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" in csp
