"""キャッシュ機能のDI配線。

``Depends`` を扱うのはこのファイルと ``cache_server.py`` のみ。
Usecase/Repositoryは ``fastmcp``/``uncalled_for`` を一切importしない。
"""

from typing import cast

from cache_domain import CacheRepository
from cache_repository import RedisCacheRepository
from cache_usecase import CacheUsecase, CacheUsecaseImpl
from redis.asyncio import Redis
from uncalled_for import Depends

from fastmcp_toolkit.redis_lifespan import CurrentRedisClient


def get_cache_repository(redis: Redis = CurrentRedisClient("cache")) -> CacheRepository:
    """``CacheRepository`` の実装を組み立てる。"""
    return RedisCacheRepository(redis)


def CurrentCacheRepository() -> CacheRepository:  # noqa: N802
    return cast(CacheRepository, Depends(get_cache_repository))


def get_cache_usecase(
    repository: CacheRepository = CurrentCacheRepository(),
) -> CacheUsecase:
    """``CacheUsecase`` を組み立てる。"""
    return CacheUsecaseImpl(repository)


def CurrentCacheUsecase() -> CacheUsecase:  # noqa: N802
    return cast(CacheUsecase, Depends(get_cache_usecase))
