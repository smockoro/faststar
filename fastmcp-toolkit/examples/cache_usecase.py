"""キャッシュ機能のUsecase層。

``CacheRepository`` インターフェースのみに依存し、``fastmcp``/``uncalled_for``
といったController層のモジュールは一切importしない。``CacheUsecase`` は
ABCでインターフェース化してあり、取得・設定を分けず1つのUsecaseにまとめて
いる（呼び出し側は差し替え可能な単一の窓口だけを意識すればよい）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from cache_domain import CacheRepository


class CacheUsecase(ABC):
    """キャッシュの取得・設定を行うユースケースのインターフェース。"""

    @abstractmethod
    async def get(self, key: str) -> str | None:
        """キーに対応する値を返す。存在しなければ ``None`` を返す。"""
        ...

    @abstractmethod
    async def set(self, key: str, value: str) -> None:
        """キーに値を設定する。"""
        ...


@dataclass
class CacheUsecaseImpl(CacheUsecase):
    """``CacheRepository`` に処理を委譲する ``CacheUsecase`` の実装。"""

    repository: CacheRepository

    async def get(self, key: str) -> str | None:
        return await self.repository.get(key)

    async def set(self, key: str, value: str) -> None:
        await self.repository.set(key, value)
