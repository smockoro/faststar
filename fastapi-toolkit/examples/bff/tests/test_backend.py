"""backend_a/backend_b/backend_b_cエンドポイントの結合テスト。

aiohttp ClientSessionを``dependency_overrides``でフェイクに差し替え、実際の
バックエンドサービスには接続しない。
"""

from httpx import ASGITransport, AsyncClient


class _FakeAiohttpResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def json(self) -> dict:
        return self._payload

    async def __aenter__(self) -> _FakeAiohttpResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeAiohttpSession:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.requested_urls: list[str] = []

    def get(self, url: str, **kwargs: object) -> _FakeAiohttpResponse:
        self.requested_urls.append(url)
        return _FakeAiohttpResponse(self._payload)


async def test_backend_a_returns_json_from_configured_url():
    from bff.app import app, get_backend_http_client

    fake_session = _FakeAiohttpSession({"result": "a"})
    app.dependency_overrides[get_backend_http_client] = lambda: fake_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/backend/a")
    finally:
        app.dependency_overrides.pop(get_backend_http_client, None)

    assert resp.status_code == 200
    assert resp.json() == {"result": "a"}
    assert fake_session.requested_urls == ["http://localhost:8001/api/a"]


async def test_backend_b_c_returns_json_from_configured_url():
    from bff.app import app, get_backend_http_client

    fake_session = _FakeAiohttpSession({"result": "c"})
    app.dependency_overrides[get_backend_http_client] = lambda: fake_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/backend/b/c")
    finally:
        app.dependency_overrides.pop(get_backend_http_client, None)

    assert resp.status_code == 200
    assert resp.json() == {"result": "c"}
    assert fake_session.requested_urls == ["http://localhost:8002/api/b/c"]
