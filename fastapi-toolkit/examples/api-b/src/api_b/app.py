"""API-B アプリケーション定義。"""

import os

import aiohttp
import jwt
import uvicorn
from fastapi import FastAPI, Request

from fastapi_toolkit import get_logger, setup_logging

host = os.environ.get("HOST", "127.0.0.1")
port = int(os.environ.get("PORT", 8002))
backend_c_url = os.environ.get("BACKEND_C_URL", "http://localhost:8003/api/c")

setup_logging(application_id="API-B")
logger = get_logger(__name__)
app = FastAPI(title="API-B")


@app.get("/hello")
async def hello() -> dict[str, str]:
    """挨拶メッセージを返す。"""
    return {"message": "hello from api-b"}


@app.get("/api/b")
async def get_api_b(request: Request) -> dict[str, str]:
    """API-B のエンドポイントを返す。"""
    auth = request.headers.get("Authorization")

    if auth and auth.startswith("Bearer "):
        token = auth.removeprefix("Bearer ")

        claims = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_exp": False,
            },
        )

        logger.info(f"JWT claims: {claims}")

    return {"message": "hello from api-b"}


@app.get("/api/b/c")
async def get_api_b_c() -> dict[str, str]:
    async with aiohttp.ClientSession() as session:
        async with session.get(backend_c_url) as response:
            return await response.json()


if __name__ == "__main__":
    uvicorn.run(app, host=host, port=port)
