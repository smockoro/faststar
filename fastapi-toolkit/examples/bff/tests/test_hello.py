from httpx import ASGITransport, AsyncClient


async def test_hello_returns_message():
    from bff.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/hello")

    assert resp.status_code == 200
    assert resp.json() == {"message": "hello"}
