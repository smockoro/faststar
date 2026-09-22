"""Starlette/ASGIアプリ向けhttpx AsyncClient lifespan管理。

名前付きで複数のAsyncClientを同時に登録・取得できる。

利用には ``core-toolkit[httpx]`` extraのインストールが必要。
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import LifespanResource


class HttpxLifespanResource(LifespanResource):
    """名前付きでhttpx AsyncClientを登録するリソース。同じappに複数登録できる。

    同じ``name``で複数回登録した場合、後から登録した方で静かに上書きされる。
    同じ名前を重複登録しないこと。

    Args:
        name: このクライアントを識別する名前（例: ``"backend"``）。
            ``get_httpx_client`` で同じ名前を指定して取得する。
        client_kwargs: ``httpx.AsyncClient`` にそのまま渡す追加引数
            （``timeout=``, ``limits=``, ``base_url=`` 等）。デフォルトは
            一切注入しない完全パススルー。
    """

    def __init__(self, name: str, **client_kwargs: Any) -> None:
        self._name = name
        self._client_kwargs = client_kwargs

    @asynccontextmanager
    async def context(self, app: Starlette) -> AsyncGenerator[Any, Any]:
        client = httpx.AsyncClient(**self._client_kwargs)
        clients: dict[str, httpx.AsyncClient] = getattr(app.state, "httpx_clients", {})
        app.state.httpx_clients = {**clients, self._name: client}

        try:
            yield client
        finally:
            await client.aclose()


def get_httpx_client(name: str) -> Callable[[Request], httpx.AsyncClient]:
    """名前を指定してhttpx AsyncClientを取得するprovider関数を生成する。

    Args:
        name: ``HttpxLifespanResource(name=...)`` に登録した名前。

    Returns:
        ``request: Request`` を1引数に取るprovider関数。対応する
        ``HttpxLifespanResource`` が登録されていない場合は ``RuntimeError``
        を送出する。
    """

    def get_client(request: Request) -> httpx.AsyncClient:
        clients: dict[str, httpx.AsyncClient] = getattr(
            request.app.state, "httpx_clients", {}
        )
        if name not in clients:
            raise RuntimeError(
                f"httpx_clients['{name}'] is not set. "
                f"Did you forget to register "
                f"HttpxLifespanResource(name='{name}', ...) "
                "in create_lifespan(...)?"
            )
        return clients[name]

    get_client.__name__ = f"get_httpx_client_{name}"
    return get_client
