"""キャッシュ機能のController層(FastAPIエンドポイント)。

``Depends`` を扱うのはこのファイルと ``dependencies.py`` のみ。
Usecase/Repositoryは ``fastapi`` を一切importしない。
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from bff.cache.dependencies import ReadCacheUseCaseDep, WriteCacheUseCaseDep

router = APIRouter(prefix="/cache", tags=["cache"])


class SetCacheValueRequest(BaseModel):
    """キャッシュ値の設定リクエストボディ。"""

    value: str


class CacheValueResponse(BaseModel):
    """キャッシュ値のレスポンスボディ。"""

    key: str
    value: str


@router.get("/{key}")
async def read_cache(key: str, usecase: ReadCacheUseCaseDep) -> CacheValueResponse:
    """キーに対応する値をキャッシュから取得する。存在しなければ404を返す。"""
    value = await usecase.execute(key)
    if value is None:
        raise HTTPException(status_code=404, detail=f"key '{key}' not found")
    return CacheValueResponse(key=key, value=value)


@router.put("/{key}")
async def write_cache(key: str, body: SetCacheValueRequest, usecase: WriteCacheUseCaseDep) -> CacheValueResponse:
    """キーに値を設定する。"""
    await usecase.execute(key, body.value)
    return CacheValueResponse(key=key, value=body.value)
