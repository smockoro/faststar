"""Starlette/ASGIアプリ向けDB接続lifespan管理。

SQLAlchemy(async)経由でSQLite/Postgres/MySQL等の複数バックエンドに対応する。
名前付きで複数エンジンを同時に登録・取得できる。

利用には ``core-toolkit[sqlite]``/``core-toolkit[postgres]``/
``core-toolkit[mysql]`` のいずれかのextraのインストールが必要。
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import LifespanResource


class _DisposableAsyncEngine:
    """Wrapper that prevents usage after disposal."""

    def __init__(self, engine: AsyncEngine) -> None:
        object.__setattr__(self, "_engine", engine)
        object.__setattr__(self, "_is_disposed", False)

    def __getattr__(self, name: str) -> Any:
        is_disposed = object.__getattribute__(self, "_is_disposed")
        if is_disposed:
            raise RuntimeError("Engine has been disposed")
        engine = object.__getattribute__(self, "_engine")
        return getattr(engine, name)

    async def dispose(self) -> None:
        object.__setattr__(self, "_is_disposed", True)
        engine = object.__getattribute__(self, "_engine")
        return await engine.dispose()


class DbLifespanResource(LifespanResource):
    """名前付きでAsyncEngineを登録するリソース。同じappに複数登録できる。

    Args:
        name: このエンジンを識別する名前（例: ``"main"``）。
            ``get_db_engine``/``get_db_connection`` で同じ名前を指定して取得する。
        url: 接続先のURL（例: ``sqlite+aiosqlite:///./app.db``）。
        engine_kwargs: ``create_async_engine`` にそのまま渡す追加引数。
    """

    def __init__(self, name: str, url: str, **engine_kwargs: Any) -> None:
        self._name = name
        self._url = url
        self._engine_kwargs = engine_kwargs

    @asynccontextmanager
    async def context(self, app: Starlette) -> AsyncGenerator[Any, Any]:
        engine = create_async_engine(self._url, **self._engine_kwargs)
        wrapped_engine = _DisposableAsyncEngine(engine)
        engines: dict[str, Any] = getattr(app.state, "db_engines", {})
        app.state.db_engines = {**engines, self._name: wrapped_engine}

        try:
            yield wrapped_engine
        finally:
            await wrapped_engine.dispose()


def get_db_engine(name: str) -> Callable[[Request], AsyncEngine]:
    """名前を指定してAsyncEngineを取得するprovider関数を生成する（生アクセス用）。

    マイグレーション・ヘルスチェック・独立トランザクション（REQUIRES_NEW相当）
    など、トランザクション境界をアプリ側で制御したい場合に使う。

    Args:
        name: ``DbLifespanResource(name=...)`` に登録した名前。

    Returns:
        ``request: Request`` を1引数に取るprovider関数。対応する
        ``DbLifespanResource`` が登録されていない場合は ``RuntimeError`` を送出する。
    """

    def get_engine(request: Request) -> AsyncEngine:
        engines: dict[str, AsyncEngine] = getattr(request.app.state, "db_engines", {})
        if name not in engines:
            raise RuntimeError(
                f"db_engines['{name}'] is not set. "
                f"Did you forget to register DbLifespanResource(name='{name}', ...) "
                "in create_lifespan(...)?"
            )
        return engines[name]

    get_engine.__name__ = f"get_db_engine_{name}"
    return get_engine


def get_db_connection(
    name: str,
) -> Callable[[Request], AsyncGenerator[AsyncConnection]]:
    """名前を指定して呼び出し単位のConnectionを取得するprovider関数を生成する。

    Depends解決のライフタイム＝1トランザクションとして扱う。正常終了でcommit、
    例外でrollback、いずれの場合も必ずclose。入れ子トランザクション
    （SAVEPOINT）が必要な場合は、取得した ``AsyncConnection`` に対して
    呼び出し側が ``begin_nested()`` を直接呼ぶ。

    Args:
        name: ``DbLifespanResource(name=...)`` に登録した名前。

    Returns:
        ``request: Request`` を1引数に取る非同期ジェネレータ型のprovider関数。
    """

    async def get_connection(request: Request) -> AsyncGenerator[AsyncConnection]:
        engine = get_db_engine(name)(request)
        async with engine.begin() as conn:
            yield conn

    get_connection.__name__ = f"get_db_connection_{name}"
    return get_connection
