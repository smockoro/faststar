"""Starlette/ASGIアプリ向けのlifespanリソース管理とapp.stateアクセサ。"""

import abc
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import Any

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
