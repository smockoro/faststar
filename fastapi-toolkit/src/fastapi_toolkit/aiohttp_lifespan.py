"""aiohttp ClientSession lifespanリソースのFastAPI向け薄いラッパー。

``AioHttpLifespanResource``/``get_aiohttp_client`` はFastAPIに依存せず
``core_toolkit.aiohttp_lifespan`` に実装されている。このモジュールは
それらを再エクスポートするだけで、名前ごとの型エイリアス
（``Annotated[aiohttp.ClientSession, Depends(get_aiohttp_client("backend"))]``）
はアプリ側が名前を指定して定義する。

利用には ``fastapi-toolkit[aiohttp]`` extraのインストールが必要。
"""

from core_toolkit.aiohttp_lifespan import AioHttpLifespanResource, get_aiohttp_client

__all__ = ["AioHttpLifespanResource", "get_aiohttp_client"]
