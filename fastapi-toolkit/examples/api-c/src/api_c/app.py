"""API-C アプリケーション定義。"""

import os

import uvicorn
from fastapi import FastAPI

host = os.environ.get("HOST", "127.0.0.1")
port = int(os.environ.get("PORT", 8003))

app = FastAPI(title="API-C")


@app.get("/hello")
async def hello() -> dict[str, str]:
    """挨拶メッセージを返す。"""
    return {"message": "hello from api-c"}


@app.get("/api/c")
async def get_api_c() -> dict[str, str]:
    """API-C のメッセージを返す。"""
    return {"message": "hello from api-c"}


if __name__ == "__main__":
    uvicorn.run(app, host=host, port=port)
