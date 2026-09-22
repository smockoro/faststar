"""fastmcp-toolkitのredis_lifespanを使ったキャッシュサーバーの例。

Controller層(@app.tool)からUsecase/Repositoryへのつなぎ方を示すサンプル。
cache_domain/cache_repository/cache_usecaseはfastmcp/uncalled_forに一切
依存しない素のPythonコードで、fastapi-toolkit向けに書いた場合と同じものを
そのまま使い回せる。
"""

from cache_dependencies import CurrentCacheUsecase
from cache_usecase import CacheUsecase
from fastmcp import FastMCP

from fastmcp_toolkit import run_server
from fastmcp_toolkit.logging import get_logger
from fastmcp_toolkit.redis_lifespan import redis_lifespan

logger = get_logger(__name__)

app = FastMCP(
    "cache-server", lifespan=redis_lifespan("cache", "redis://localhost:6379/0")
)


@app.tool
async def read_cache(key: str, usecase: CacheUsecase = CurrentCacheUsecase()) -> str:
    """キャッシュから値を取得する。存在しなければ空文字を返す。"""
    value = await usecase.get(key)
    logger.info("read_cache", key=key, found=value is not None)
    return value if value is not None else ""


@app.tool
async def write_cache(
    key: str, value: str, usecase: CacheUsecase = CurrentCacheUsecase()
) -> str:
    """キャッシュに値を設定する。"""
    await usecase.set(key, value)
    logger.info("write_cache", key=key)
    return "ok"


if __name__ == "__main__":
    run_server(app)
