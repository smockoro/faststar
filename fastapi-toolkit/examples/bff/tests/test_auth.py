from httpx import ASGITransport, AsyncClient


async def test_me_returns_401_when_not_authenticated():
    from bff.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/auth/me")

    assert resp.status_code == 401
    assert resp.json()["error"] == "not authenticated"


async def test_logout_clears_session():
    from bff.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/auth/logout")

    assert resp.status_code == 200
    assert resp.json()["message"] == "logged out"
