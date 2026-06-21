from httpx import ASGITransport, AsyncClient

from api_c.app import app


async def test_hello_returns_message():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/hello")

    assert resp.status_code == 200
    assert resp.json() == {"message": "hello from api-c"}
