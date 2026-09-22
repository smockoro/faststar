"""キャッシュ機能のインフラ層。Redisを使った ``CacheRepository`` の実装。"""

from redis.asyncio import Redis

from bff.cache.domain import CacheRepository


class RedisCacheRepository(CacheRepository):
    """Redisクライアントによる ``CacheRepository`` の実装。"""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get(self, key: str) -> str | None:
        """キーに対応する値をRedisから取得する。存在しなければ ``None`` を返す。"""
        value = await self._redis.get(key)
        return value.decode() if value is not None else None

    async def set(self, key: str, value: str) -> None:
        """キーに値をRedisへ設定する。"""
        await self._redis.set(key, value)
