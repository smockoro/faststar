"""Starlette/ASGIアプリ向けValkey接続lifespan管理。

名前付きで複数のValkeyクライアントを同時に登録・取得できる。

利用には ``core-toolkit[valkey]`` extraのインストールが必要。
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

from glide import GlideClient, GlideClientConfiguration, NodeAddress
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import LifespanResource


class ValkeyLifespanResource(LifespanResource):
    """名前付きでValkeyクライアントを登録するリソース。同じappに複数登録できる。

    Args:
        name: このクライアントを識別する名前（例: ``"cache"``）。
            ``get_valkey_client`` で同じ名前を指定して取得する。
        host: 接続先ホスト名。
        port: 接続先ポート番号。
        config_kwargs: ``GlideClientConfiguration`` にそのまま渡す追加引数
            （``use_tls``、``database_id``、``request_timeout``、
            ``credentials``、``reconnect_strategy`` など）。
    """

    def __init__(
        self, name: str, host: str, port: int = 6379, **config_kwargs: Any
    ) -> None:
        self._name = name
        self._host = host
        self._port = port
        self._config_kwargs = config_kwargs

    @asynccontextmanager
    async def context(self, app: Starlette) -> AsyncGenerator[Any, Any]:
        config = GlideClientConfiguration(
            addresses=[NodeAddress(self._host, self._port)],
            **self._config_kwargs,
        )
        client = await GlideClient.create(config)
        clients: dict[str, GlideClient] = getattr(app.state, "valkey_clients", {})
        app.state.valkey_clients = {**clients, self._name: client}

        try:
            yield client
        finally:
            await client.close()


def get_valkey_client(name: str) -> Callable[[Request], GlideClient]:
    """名前を指定してValkeyクライアントを取得するprovider関数を生成する。

    Args:
        name: ``ValkeyLifespanResource(name=...)`` に登録した名前。

    Returns:
        ``request: Request`` を1引数に取るprovider関数。対応する
        ``ValkeyLifespanResource`` が登録されていない場合は ``RuntimeError``
        を送出する。
    """

    def get_client(request: Request) -> GlideClient:
        clients: dict[str, GlideClient] = getattr(
            request.app.state, "valkey_clients", {}
        )
        if name not in clients:
            raise RuntimeError(
                f"valkey_clients['{name}'] is not set. "
                f"Did you forget to register "
                f"ValkeyLifespanResource(name='{name}', ...) "
                "in create_lifespan(...)?"
            )
        return clients[name]

    get_client.__name__ = f"get_valkey_client_{name}"
    return get_client
