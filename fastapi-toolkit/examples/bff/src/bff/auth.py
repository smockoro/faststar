"""EntraID OIDC認証エンドポイント。"""

from typing import Any

import msal
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from bff.config import ApplicationSettings
from bff.token_cache import get_token_cache, remove_token_cache, save_token_cache

router = APIRouter(prefix="/auth", tags=["auth"])


def _build_msal_app(settings: ApplicationSettings, oid: str | None = None) -> msal.ConfidentialClientApplication:
    """MSAL ConfidentialClientApplicationを生成する。

    oidが指定されている場合、該当ユーザーのキャッシュを復元して紐づける。
    oid未指定でも空のSerializableTokenCacheを渡し、callback後に保存可能にする。
    """
    cache = get_token_cache(oid) if oid else msal.SerializableTokenCache()
    return msal.ConfidentialClientApplication(
        client_id=settings.client_id,
        client_credential=settings.client_secret,
        authority=settings.authority,
        token_cache=cache,
    )


@router.get("/login")
async def login(request: Request) -> RedirectResponse:
    """EntraIDの認可エンドポイントへリダイレクトする。

    MSAL の auth code flow を開始し、セッションに flow 情報を保存した上で
    EntraID のログイン画面にリダイレクトする。
    """
    settings: ApplicationSettings = request.app.state.azure_settings
    msal_app = _build_msal_app(settings)

    flow: dict[str, Any] = msal_app.initiate_auth_code_flow(
        scopes=settings.backend_b_scopes,
        redirect_uri=settings.redirect_uri,
    )
    request.session["auth_flow"] = flow
    return RedirectResponse(url=flow["auth_uri"])


@router.get("/callback")
async def callback(request: Request) -> JSONResponse:
    """EntraIDからのコールバックを処理しトークンを取得する。

    認可コードを受け取り、MSAL でトークンに交換する。
    成功時はユーザー情報とトークンをキャッシュに保存する。
    """
    settings: ApplicationSettings = request.app.state.azure_settings

    flow = request.session.pop("auth_flow", None)
    if flow is None:
        return JSONResponse({"error": "auth_flow not found in session"}, status_code=400)

    msal_app = _build_msal_app(settings)
    result: dict[str, Any] = msal_app.acquire_token_by_auth_code_flow(
        auth_code_flow=flow,
        auth_response=dict(request.query_params),
    )

    if "error" in result:
        return JSONResponse(
            {"error": result["error"], "description": result.get("error_description", "")},
            status_code=401,
        )

    id_token_claims = result.get("id_token_claims", {})
    oid = id_token_claims.get("oid", "")

    cache = msal_app.token_cache
    if cache is not None:
        save_token_cache(oid, cache)

    request.session["user"] = {
        "name": id_token_claims.get("name"),
        "preferred_username": id_token_claims.get("preferred_username"),
        "oid": oid,
    }
    request.session["id_token_claims"] = id_token_claims
    return JSONResponse({"message": "login successful", "user": request.session["user"]})


@router.get("/me")
async def me(request: Request) -> JSONResponse:
    """ログイン中のユーザー情報を返す。

    セッションにユーザー情報がなければ401を返す。
    """
    user = request.session.get("user")
    if user is None:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    return JSONResponse({"user": user})


@router.get("/token-claims")
async def token_claims(request: Request) -> JSONResponse:
    """認証済みIDトークンのクレーム情報を返す。

    セッションに保存された id_token_claims を返す。
    未認証の場合は401を返す。
    """
    claims = request.session.get("id_token_claims")
    if claims is None:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    return JSONResponse({"id_token_claims": claims})


@router.get("/roles")
async def roles(request: Request) -> JSONResponse:
    """認証済みユーザーに割り当てられたApp Roleとグループを返す。

    EntraID側で「トークンにグループクレームを含める」設定と
    アプリマニフェストへのApp Role定義が必要。
    id_token_claims 内の roles / groups / wids を抽出して返す。
    未認証の場合は401を返す。
    """
    claims = request.session.get("id_token_claims")
    if claims is None:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    return JSONResponse({
        "roles": claims.get("roles", []),
        "groups": claims.get("groups", []),
        "wids": claims.get("wids", []),
    })


@router.get("/access-token")
async def access_token(request: Request) -> JSONResponse:
    """キャッシュからアクセストークンをサイレント取得して返す。

    MSALのトークンキャッシュからアクセストークンを取得する。
    期限切れの場合はリフレッシュトークンで自動更新される。
    未認証またはトークン取得不可の場合は401を返す。
    """
    user = request.session.get("user")
    if user is None:
        return JSONResponse({"error": "not authenticated"}, status_code=401)

    oid = user.get("oid", "")
    settings: ApplicationSettings = request.app.state.azure_settings
    msal_app = _build_msal_app(settings, oid=oid)

    accounts = msal_app.get_accounts()
    if not accounts:
        return JSONResponse({"error": "no cached account, re-login required"}, status_code=401)

    result = msal_app.acquire_token_silent(scopes=settings.backend_b_scopes, account=accounts[0])
    if result is None or "error" in result:
        return JSONResponse({"error": "token acquisition failed, re-login required"}, status_code=401)

    save_token_cache(oid, msal_app.token_cache)
    return JSONResponse({
        "access_token": result["access_token"],
        "expires_in": result.get("expires_in"),
        "token_type": result.get("token_type", "Bearer"),
    })


@router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    """セッションとトークンキャッシュをクリアしてログアウトする。"""
    user = request.session.get("user")
    if user:
        remove_token_cache(user.get("oid", ""))
    request.session.clear()
    return JSONResponse({"message": "logged out"})
