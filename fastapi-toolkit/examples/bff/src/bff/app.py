"""FastAPIアプリケーション定義。"""

import aiohttp
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi_toolkit import setup_logging
from fastapi_toolkit.lifespan import AioHttpLifespanResource, create_lifespan
from fastapi_toolkit.logging import get_logger
from starlette.middleware.sessions import SessionMiddleware

from bff.auth import router as auth_router
from bff.config import load_application_settings
from bff.token_cache import get_token_cache, save_token_cache

setup_logging(application_id="BFF")

logger = get_logger(__name__)
settings = load_application_settings()

app = FastAPI(title="BFF", lifespan=create_lifespan(AioHttpLifespanResource()))

app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
app.include_router(auth_router)

app.state.azure_settings = settings


@app.get("/hello")
async def hello() -> dict[str, str]:
    """挨拶メッセージを返す。"""
    return {"message": "hello"}


@app.get("/backend/a")
async def backend_a(req: Request) -> dict[str, str]:
    """バックエンドAPI Aにアクセスする"""
    session: aiohttp.ClientSession = req.app.state.http_client
    async with session.get(settings.backend_a_url) as response:
        return await response.json()


@app.get("/backend/b")
async def backend_b(req: Request) -> JSONResponse:
    """バックエンドAPI Bにアクセスする"""
    import msal

    user = req.session.get("user")
    if user is None:
        return JSONResponse({"error": "not authenticated"}, status_code=401)

    oid = user.get("oid", "")
    cache = get_token_cache(oid)
    msal_app = msal.ConfidentialClientApplication(
        client_id=settings.client_id,
        client_credential=settings.client_secret,
        authority=settings.authority,
        token_cache=cache,
    )

    accounts = msal_app.get_accounts()
    if not accounts:
        return JSONResponse({"error": "no cached account, re-login required"}, status_code=401)

    result = msal_app.acquire_token_silent(scopes=settings.backend_b_scopes, account=accounts[0])
    if result is None or "error" in result:
        return JSONResponse({"error": "token acquisition failed, re-login required"}, status_code=401)

    save_token_cache(oid, cache)
    access_token = result["access_token"]

    session: aiohttp.ClientSession = req.app.state.http_client
    async with session.get(
        settings.backend_b_url,
        headers={"Authorization": f"Bearer {access_token}"},
    ) as response:
        data = await response.json()
    return JSONResponse(data)


@app.get("/backend/b/c")
async def backend_b_c(req: Request) -> dict[str, str]:
    """バックエンドAPI BのCエンドポイントにアクセスする"""
    session: aiohttp.ClientSession = req.app.state.http_client
    async with session.get(settings.backend_b_url + "/c") as response:
        return await response.json()


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port, access_log=False, log_config=None)
