"""Usecase層の単体テスト。

Repositoryはインメモリのフェイクに差し替え、Redis・FastAPIのどちらにも
依存せずにUsecaseのロジックだけを検証する。
"""

from bff.cache.domain import CacheRepository
from bff.cache.usecase import GetCachedValueUseCase, SetCachedValueUseCase


class FakeCacheRepository(CacheRepository):
    """インメモリの ``CacheRepository`` フェイク実装。"""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str) -> None:
        self._store[key] = value


async def test_get_cached_value_usecase_returns_none_when_missing():
    repository: CacheRepository = FakeCacheRepository()
    usecase = GetCachedValueUseCase(repository)

    result = await usecase.execute("missing-key")

    assert result is None


async def test_set_then_get_returns_stored_value():
    repository: CacheRepository = FakeCacheRepository()
    write_usecase = SetCachedValueUseCase(repository)
    read_usecase = GetCachedValueUseCase(repository)

    await write_usecase.execute("greeting", "hello")
    result = await read_usecase.execute("greeting")

    assert result == "hello"
