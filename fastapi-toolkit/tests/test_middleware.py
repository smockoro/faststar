"""ミドルウェア統合テスト。"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from fastapi_toolkit import AccessLogMiddleware, SecurityHeadersMiddleware


@pytest.fixture
def json_app() -> FastAPI:
    """JSONプリセットのSecurityHeadersMiddleware付きアプリ。"""
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, preset="json")
    app.add_middleware(AccessLogMiddleware)

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"pong": "ok"}

    return app


@pytest.fixture
def html_app() -> FastAPI:
    """HTMLプリセットのSecurityHeadersMiddleware付きアプリ。"""
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, preset="html")

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
async def test_security_headers_json_preset(json_app: FastAPI):
    """JSONプリセットではX-Content-Type-Optionsのみ付与される。"""
    async with AsyncClient(transport=ASGITransport(app=json_app), base_url="http://test") as client:
        resp = await client.get("/ping")

    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in resp.headers
    assert "x-frame-options" not in resp.headers


@pytest.mark.asyncio
async def test_security_headers_html_preset(html_app: FastAPI):
    """HTMLプリセットではブラウザ保護ヘッダー一式が付与される。"""
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
