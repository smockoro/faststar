"""valkey_lifespanの統合テスト。"""

import pytest
from glide import GlideClient, GlideClientConfiguration
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import create_lifespan
from core_toolkit.valkey_lifespan import ValkeyLifespanResource, get_valkey_client


class FakeGlideClient:
    """テスト用のGlideClient代替。set/get/closeのみサポートする。

    GLIDE公式にはfakeredis相当のフェイクサーバーが存在しないため、
    ``GlideClient.create``をクラスレベルでmonkeypatchして返す代替実装として使う。
    """

    def __init__(self) -> None:
        self.closed = False
        self._store: dict[str, str] = {}

    async def close(self) -> None:
        self.closed = True

    async def set(self, key: str, value: str) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)


def _make_request(app: Starlette) -> Request:
    return Request({"type": "http", "app": app})


@pytest.mark.asyncio
async def test_get_valkey_client_returns_registered_client(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_create(config):
        return FakeGlideClient()

    monkeypatch.setattr(GlideClient, "create", fake_create)
    app = Starlette()
    lifespan = create_lifespan(ValkeyLifespanResource("cache", "localhost", 6379))

    async with lifespan(app):
        client = get_valkey_client("cache")(_make_request(app))
        await client.set("k", "v")
        assert await client.get("k") == "v"


def test_get_valkey_client_raises_runtime_error_when_missing():
    app = Starlette()

    with pytest.raises(RuntimeError, match="cache"):
        get_valkey_client("cache")(_make_request(app))


@pytest.mark.asyncio
async def test_multiple_clients_registered_independently(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_create(config):
        return FakeGlideClient()

    monkeypatch.setattr(GlideClient, "create", fake_create)
    app = Starlette()
    lifespan = create_lifespan(
        ValkeyLifespanResource("cache", "localhost", 6379),
        ValkeyLifespanResource("session", "localhost", 6379),
    )

    async with lifespan(app):
        cache_client = get_valkey_client("cache")(_make_request(app))
        session_client = get_valkey_client("session")(_make_request(app))
        assert cache_client is not session_client

        # objectとしての別物性だけでなく、"cache"への書き込みが"session"側の
        # キーには見えないこと（名前ごとに正しくキー分けされていること）も
        # 検証する。
        await cache_client.set("k", "v")
        assert await session_client.get("k") is None


@pytest.mark.asyncio
async def test_valkey_lifespan_resource_closes_client_on_exit(
    monkeypatch: pytest.MonkeyPatch,
):
    created: list[FakeGlideClient] = []

    async def fake_create(config):
        client = FakeGlideClient()
        created.append(client)
        return client

    monkeypatch.setattr(GlideClient, "create", fake_create)
    app = Starlette()
    lifespan = create_lifespan(ValkeyLifespanResource("cache", "localhost", 6379))

    async with lifespan(app):
        pass

    assert created[0].closed


@pytest.mark.asyncio
async def test_valkey_lifespan_resource_builds_config_from_host_port(
    monkeypatch: pytest.MonkeyPatch,
):
    captured_configs: list[GlideClientConfiguration] = []

    async def fake_create(config):
        captured_configs.append(config)
        return FakeGlideClient()

    monkeypatch.setattr(GlideClient, "create", fake_create)
    app = Starlette()
    lifespan = create_lifespan(
        ValkeyLifespanResource(
            "cache", "example.invalid", 6380, use_tls=True, database_id=2
        )
    )

    async with lifespan(app):
        pass

    config = captured_configs[0]
    assert len(config.addresses) == 1
    assert config.addresses[0].host == "example.invalid"
    assert config.addresses[0].port == 6380
    assert config.use_tls is True
    assert config.database_id == 2
