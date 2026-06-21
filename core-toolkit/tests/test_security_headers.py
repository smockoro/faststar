"""SecurityHeadersMiddlewareのテスト。"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from core_toolkit.middleware.security_headers import SecurityHeadersMiddleware


def _create_app(preset: str = "json") -> Starlette:
    async def json_endpoint(request: Request) -> JSONResponse:
        return JSONResponse({"data": "value"})

    async def html_endpoint(request: Request) -> HTMLResponse:
        return HTMLResponse("<h1>Hello</h1>")

    app = Starlette(
        routes=[
            Route("/api", json_endpoint),
            Route("/page", html_endpoint),
        ]
    )
    app.add_middleware(SecurityHeadersMiddleware, preset=preset)
    return app


async def _client(app: Starlette) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_json_preset_adds_nosniff():
    app = _create_app(preset="json")
    async with await _client(app) as client:
        resp = await client.get("/api")
    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_json_preset_does_not_add_csp():
    app = _create_app(preset="json")
    async with await _client(app) as client:
        resp = await client.get("/api")
    assert "content-security-policy" not in resp.headers


@pytest.mark.asyncio
async def test_html_preset_adds_full_headers():
    app = _create_app(preset="html")
    async with await _client(app) as client:
        resp = await client.get("/page")
    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "SAMEORIGIN"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert resp.headers["cross-origin-opener-policy"] == "same-origin"
    assert "content-security-policy" in resp.headers
    assert "permissions-policy" in resp.headers


@pytest.mark.asyncio
async def test_html_preset_csp_content():
    app = _create_app(preset="html")
    async with await _client(app) as client:
        resp = await client.get("/page")
    csp = resp.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "'unsafe-eval'" not in csp
    assert "frame-ancestors 'self'" in csp


@pytest.mark.asyncio
async def test_html_preset_no_hsts():
    app = _create_app(preset="html")
    async with await _client(app) as client:
        resp = await client.get("/page")
    assert "strict-transport-security" not in resp.headers


@pytest.mark.asyncio
async def test_headers_applied_to_all_routes():
    app = _create_app(preset="html")
    async with await _client(app) as client:
        json_resp = await client.get("/api")
        html_resp = await client.get("/page")
    assert json_resp.headers["x-content-type-options"] == "nosniff"
    assert html_resp.headers["x-content-type-options"] == "nosniff"
