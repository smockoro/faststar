"""キャッシュ機能のDI配線。

Controller層(FastAPI)からのみ ``Depends`` を扱い、Usecase/Repositoryへは
解決済みのオブジェクトを明示的な引数として渡す。この関数群がRedisという
具体的な実装とUsecaseが依存する抽象(``CacheRepository``)を結びつける
Composition Rootにあたる。
"""

from typing import Annotated

from fastapi import Depends
from fastapi_toolkit.redis_lifespan import get_redis_client
from redis.asyncio import Redis

from bff.cache.domain import CacheRepository
from bff.cache.repository import RedisCacheRepository
from bff.cache.usecase import GetCachedValueUseCase, SetCachedValueUseCase

# 名前付きAPIのため get_redis_client("cache") が返すクロージャを一度だけ
# 生成してモジュール属性に固定する。テストの dependency_overrides はこの
# オブジェクト（呼び出すたびに新しい関数が生成される get_redis_client 自体
# ではない）をキーにする必要がある。
get_cache_redis_client = get_redis_client("cache")
CacheRedisClient = Annotated[Redis, Depends(get_cache_redis_client)]


def get_cache_repository(redis: CacheRedisClient) -> CacheRepository:
    """``CacheRepository`` の実装を組み立てる。

    Args:
        redis: 名前 ``"cache"`` で登録されたRedisクライアント。

    Returns:
        CacheRepository: Redisを使った具体的な実装。
    """
    return RedisCacheRepository(redis)


CacheRepositoryDep = Annotated[CacheRepository, Depends(get_cache_repository)]


def get_read_cache_usecase(repository: CacheRepositoryDep) -> GetCachedValueUseCase:
    """``GetCachedValueUseCase`` を組み立てる。"""
    return GetCachedValueUseCase(repository)


def get_write_cache_usecase(repository: CacheRepositoryDep) -> SetCachedValueUseCase:
    """``SetCachedValueUseCase`` を組み立てる。"""
    return SetCachedValueUseCase(repository)


ReadCacheUseCaseDep = Annotated[GetCachedValueUseCase, Depends(get_read_cache_usecase)]
WriteCacheUseCaseDep = Annotated[SetCachedValueUseCase, Depends(get_write_cache_usecase)]
