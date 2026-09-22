"""キャッシュ機能のドメイン層。

Usecase層はこのモジュールが定義する抽象にのみ依存し、
Redis等の具体的な実装（Repository層）を一切知らない。
"""

from abc import ABC, abstractmethod


class CacheRepository(ABC):
    """キー・バリュー形式のキャッシュへのアクセスを抽象化するインターフェース。"""

    @abstractmethod
    async def get(self, key: str) -> str | None:
        """キーに対応する値を取得する。存在しなければ ``None`` を返す。"""
        ...

    @abstractmethod
    async def set(self, key: str, value: str) -> None:
        """キーに値を設定する。"""
        ...
