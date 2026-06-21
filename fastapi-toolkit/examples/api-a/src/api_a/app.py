"""API-A アプリケーション定義。"""

import os

import uvicorn
from fastapi import FastAPI

host = os.environ.get("HOST", "127.0.0.1")
port = int(os.environ.get("PORT", 8001))

app = FastAPI(title="API-A")


@app.get("/hello")
async def hello() -> dict[str, str]:
    """挨拶メッセージを返す。"""
    return {"message": "hello from api-a"}


if __name__ == "__main__":
    uvicorn.run(app, host=host, port=port)
