"""Starlette/ASGIアプリ向けRedis接続lifespan管理。

名前付きで複数のRedisクライアントを同時に登録・取得できる。

利用には ``core-toolkit[redis]`` extraのインストールが必要。
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

from redis.asyncio import Redis
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import LifespanResource


class RedisLifespanResource(LifespanResource):
    """名前付きでRedisクライアントを登録するリソース。同じappに複数登録できる。

    Args:
        name: このクライアントを識別する名前（例: ``"cache"``）。
            ``get_redis_client`` で同じ名前を指定して取得する。
        url: 接続先のURL（例: ``redis://localhost:6379/0``）。
        client_kwargs: ``Redis.from_url`` にそのまま渡す追加引数。
    """

    def __init__(self, name: str, url: str, **client_kwargs: Any) -> None:
        self._name = name
        self._url = url
        self._client_kwargs = client_kwargs

    @asynccontextmanager
    async def context(self, app: Starlette) -> AsyncGenerator[Any, Any]:
        client = Redis.from_url(self._url, **self._client_kwargs)
        clients: dict[str, Redis] = getattr(app.state, "redis_clients", {})
        app.state.redis_clients = {**clients, self._name: client}

        try:
            yield client
        finally:
            await client.aclose()


def get_redis_client(name: str) -> Callable[[Request], Redis]:
    """名前を指定してRedisクライアントを取得するprovider関数を生成する。

    Args:
        name: ``RedisLifespanResource(name=...)`` に登録した名前。

    Returns:
        ``request: Request`` を1引数に取るprovider関数。対応する
        ``RedisLifespanResource`` が登録されていない場合は ``RuntimeError``
        を送出する。
    """

    def get_client(request: Request) -> Redis:
        clients: dict[str, Redis] = getattr(request.app.state, "redis_clients", {})
        if name not in clients:
            raise RuntimeError(
                f"redis_clients['{name}'] is not set. "
                f"Did you forget to register RedisLifespanResource(name='{name}', ...) "
                "in create_lifespan(...)?"
            )
        return clients[name]

    get_client.__name__ = f"get_redis_client_{name}"
    return get_client
