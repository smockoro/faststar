"""Starlette/ASGIアプリ向けのlifespanリソース管理とapp.stateアクセサ。"""

import abc
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import Any

import aiohttp
import httpx
from starlette.applications import Starlette
from starlette.requests import Request


class LifespanResource(abc.ABC):
    @abc.abstractmethod
    def context(self, app: Starlette) -> AbstractAsyncContextManager:
        pass


def app_state_dependency[T](attr: str, type_: type[T]) -> Callable[[Request], T]:
    """``app.state.<attr>`` を取り出すprovider関数を生成する。

    対応する ``LifespanResource`` が ``create_lifespan(...)`` に登録されておらず
    属性が存在しない場合は、原因が分かりやすい ``RuntimeError`` を送出する。
    FastAPIの ``Depends(...)`` にもそのまま渡せるが、ここではFastAPIに一切
    依存せず素の ``starlette.requests.Request`` のみを扱う。

    Args:
        attr: ``app.state`` の属性名（例: ``"redis_client"``）。
        type_: 戻り値の型。実行時の検証には使わず、型パラメータ ``T`` を
            呼び出し側の引数から静的に推論させるためだけに受け取る。

    Returns:
        ``request: Request`` を1引数に取るprovider関数。
    """

    def get_value(request: Request) -> T:
        if not hasattr(request.app.state, attr):
            raise RuntimeError(
                f"app.state.{attr} is not set. "
                "Did you forget to register the corresponding "
                "LifespanResource in create_lifespan(...)?"
            )
        return getattr(request.app.state, attr)

    get_value.__name__ = f"get_{attr}"
    return get_value


def create_lifespan(
    *resources: LifespanResource,
) -> Callable[..., AsyncGenerator[None, Any]]:
    @asynccontextmanager
    async def lifespan(app: Starlette):
        async with AsyncExitStack() as stack:
            for resource in resources:
                await stack.enter_async_context(resource.context(app))

            yield

    return lifespan


class AioHttpLifespanResource(LifespanResource):
    @asynccontextmanager
    async def context(self, app: Starlette) -> AsyncGenerator[Any, Any]:
        connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=20,
            use_dns_cache=True,
            ttl_dns_cache=0,
        )

        session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(
                total=30, connect=10, sock_connect=5, sock_read=5
            ),
        )
        app.state.http_client = session

        try:
            yield session
        finally:
            await session.close()


class HttpxLifespanResource(LifespanResource):
    @asynccontextmanager
    async def context(self, app: Starlette) -> AsyncGenerator[Any, Any]:
        app.state.http_client = httpx.AsyncClient()

        try:
            yield
        finally:
            await app.state.http_client.aclose()


get_http_client = app_state_dependency(
    "http_client", aiohttp.ClientSession | httpx.AsyncClient
)
"""``AioHttpLifespanResource``/``HttpxLifespanResource`` が起動時に生成した
HTTPクライアントを取得するprovider関数。どちらのリソースを使うかで実際の型は
``aiohttp.ClientSession`` か ``httpx.AsyncClient`` のいずれかになる。"""
