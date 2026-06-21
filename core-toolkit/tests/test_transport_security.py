"""TransportSecurityASGIMiddlewareのテスト。"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from core_toolkit.middleware.transport_security import (
    TransportSecurityASGIMiddleware,
)


def _create_app(
    allowed_hosts: tuple[str, ...] = ("localhost:8000",),
    allowed_origins: tuple[str, ...] = (),
) -> Starlette:
    async def homepage(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/", homepage, methods=["GET", "POST"])])
    app.add_middleware(
        TransportSecurityASGIMiddleware,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )
    return app


async def _client(app: Starlette) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://localhost:8000"
    )


@pytest.mark.asyncio
async def test_valid_host_passes():
    app = _create_app(allowed_hosts=("localhost:8000",))
    async with await _client(app) as client:
        resp = await client.get("/", headers={"host": "localhost:8000"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_invalid_host_rejected():
    app = _create_app(allowed_hosts=("localhost:8000",))
    async with await _client(app) as client:
        resp = await client.get("/", headers={"host": "evil.com"})
    assert resp.status_code == 421


@pytest.mark.asyncio
async def test_wildcard_port_host():
    app = _create_app(allowed_hosts=("localhost:*",))
    async with await _client(app) as client:
        resp = await client.get("/", headers={"host": "localhost:9999"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_missing_host_rejected():
    app = _create_app(allowed_hosts=("localhost:8000",))
    async with await _client(app) as client:
        resp = await client.get("/", headers={"host": ""})
    assert resp.status_code == 421


@pytest.mark.asyncio
async def test_valid_origin_passes():
    app = _create_app(
        allowed_hosts=("localhost:8000",),
        allowed_origins=("http://localhost:3000",),
    )
    async with await _client(app) as client:
        resp = await client.get(
            "/",
            headers={
                "host": "localhost:8000",
                "origin": "http://localhost:3000",
            },
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_invalid_origin_rejected():
    app = _create_app(
        allowed_hosts=("localhost:8000",),
        allowed_origins=("http://localhost:3000",),
    )
    async with await _client(app) as client:
        resp = await client.get(
            "/",
            headers={
                "host": "localhost:8000",
                "origin": "http://evil.com",
            },
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_wildcard_port_origin():
    app = _create_app(
        allowed_hosts=("localhost:8000",),
        allowed_origins=("http://localhost:*",),
    )
    async with await _client(app) as client:
        resp = await client.get(
            "/",
            headers={
                "host": "localhost:8000",
                "origin": "http://localhost:5173",
            },
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_no_origin_header_passes():
    app = _create_app(
        allowed_hosts=("localhost:8000",),
        allowed_origins=("http://localhost:3000",),
    )
    async with await _client(app) as client:
        resp = await client.get("/", headers={"host": "localhost:8000"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_post_valid_content_type():
    app = _create_app(allowed_hosts=("localhost:8000",))
    async with await _client(app) as client:
        resp = await client.post(
            "/",
            headers={"host": "localhost:8000"},
            json={"method": "test"},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_post_invalid_content_type_rejected():
    app = _create_app(allowed_hosts=("localhost:8000",))
    async with await _client(app) as client:
        resp = await client.post(
            "/",
            headers={
                "host": "localhost:8000",
                "content-type": "text/plain",
            },
            content=b"hello",
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_post_missing_content_type_rejected():
    app = _create_app(allowed_hosts=("localhost:8000",))
    async with await _client(app) as client:
        resp = await client.post(
            "/",
            headers={"host": "localhost:8000", "content-type": ""},
            content=b"{}",
        )
    assert resp.status_code == 400
