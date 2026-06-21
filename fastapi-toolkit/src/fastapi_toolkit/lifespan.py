import abc
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import Any

import aiohttp
import httpx
from fastapi import FastAPI


class LifespanResource(abc.ABC):
    @abc.abstractmethod
    def context(self, app: FastAPI) -> AbstractAsyncContextManager:
        pass


def create_lifespan(*resources: LifespanResource) -> Callable[..., AsyncGenerator[None, Any]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with AsyncExitStack() as stack:
            for resource in resources:
                await stack.enter_async_context(resource.context(app))

            yield

    return lifespan


class AioHttpLifespanResource(LifespanResource):
    @asynccontextmanager
    async def context(self, app: FastAPI) -> AsyncGenerator[Any, Any]:
        connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=20,
            use_dns_cache=True,
            ttl_dns_cache=0,
        )

        session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=30, connect=10, sock_connect=5, sock_read=5),
        )
        app.state.http_client = session

        try:
            yield session
        finally:
            await session.close()


class HttpxLifespanResource(LifespanResource):
    @asynccontextmanager
    async def context(self, app: FastAPI) -> AsyncGenerator[Any, Any]:
        app.state.http_client = httpx.AsyncClient()

        try:
            yield
        finally:
            await app.state.http_client.aclose()
