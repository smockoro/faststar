"""Redis接続lifespanリソースのFastAPI向け薄いラッパー。

``RedisLifespanResource``/``get_redis_client`` はFastAPIに依存せず
``core_toolkit.redis_lifespan`` に実装されている。このモジュールは
それらを再エクスポートするだけで、名前ごとの型エイリアス
（``Annotated[Redis, Depends(get_redis_client("cache"))]``）はアプリ側が
名前を指定して定義する。

利用には ``fastapi-toolkit[redis]`` extraのインストールが必要。
"""

from core_toolkit.redis_lifespan import RedisLifespanResource, get_redis_client

__all__ = ["RedisLifespanResource", "get_redis_client"]
