"""キャッシュ機能のUsecase層。``CacheRepository`` インターフェースのみに依存する。

FastAPIやRedisといった外側の層のモジュールは一切importしない。
"""

from dataclasses import dataclass

from bff.cache.domain import CacheRepository


@dataclass
class GetCachedValueUseCase:
    """キャッシュから値を取得するユースケース。"""

    repository: CacheRepository

    async def execute(self, key: str) -> str | None:
        """キーに対応する値を返す。存在しなければ ``None`` を返す。"""
        return await self.repository.get(key)


@dataclass
class SetCachedValueUseCase:
    """キャッシュに値を設定するユースケース。"""

    repository: CacheRepository

    async def execute(self, key: str, value: str) -> None:
        """キーに値を設定する。"""
        await self.repository.set(key, value)
