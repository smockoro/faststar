"""Starlette/ASGIアプリ向けaiohttp ClientSession lifespan管理。

名前付きで複数のClientSessionを同時に登録・取得できる。

利用には ``core-toolkit[aiohttp]`` extraのインストールが必要。
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

import aiohttp
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import LifespanResource


class AioHttpLifespanResource(LifespanResource):
    """名前付きでaiohttp ClientSessionを登録するリソース。同じappに複数登録できる。

    同じ``name``で複数回登録した場合、後から登録した方で静かに上書きされる。
    同じ名前を重複登録しないこと。

    Args:
        name: このセッションを識別する名前（例: ``"backend"``）。
            ``get_aiohttp_client`` で同じ名前を指定して取得する。
        session_kwargs: ``aiohttp.ClientSession`` にそのまま渡す追加引数
            （``connector=``, ``timeout=``, ``base_url=`` 等）。デフォルトは
            一切注入しない完全パススルー。
    """

    def __init__(self, name: str, **session_kwargs: Any) -> None:
        self._name = name
        self._session_kwargs = session_kwargs

    @asynccontextmanager
    async def context(self, app: Starlette) -> AsyncGenerator[Any, Any]:
        session = aiohttp.ClientSession(**self._session_kwargs)
        clients: dict[str, aiohttp.ClientSession] = getattr(
            app.state, "aiohttp_clients", {}
        )
        app.state.aiohttp_clients = {**clients, self._name: session}

        try:
            yield session
        finally:
            await session.close()


def get_aiohttp_client(name: str) -> Callable[[Request], aiohttp.ClientSession]:
    """名前を指定してaiohttp ClientSessionを取得するprovider関数を生成する。

    Args:
        name: ``AioHttpLifespanResource(name=...)`` に登録した名前。

    Returns:
        ``request: Request`` を1引数に取るprovider関数。対応する
        ``AioHttpLifespanResource`` が登録されていない場合は ``RuntimeError``
        を送出する。
    """

    def get_client(request: Request) -> aiohttp.ClientSession:
        clients: dict[str, aiohttp.ClientSession] = getattr(
            request.app.state, "aiohttp_clients", {}
        )
        if name not in clients:
            raise RuntimeError(
                f"aiohttp_clients['{name}'] is not set. "
                f"Did you forget to register "
                f"AioHttpLifespanResource(name='{name}', ...) "
                "in create_lifespan(...)?"
            )
        return clients[name]

    get_client.__name__ = f"get_aiohttp_client_{name}"
    return get_client
