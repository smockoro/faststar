"""Valkey接続lifespanリソースのFastAPI向け薄いラッパー。

``ValkeyLifespanResource``/``get_valkey_client`` はFastAPIに依存せず
``core_toolkit.valkey_lifespan`` に実装されている。このモジュールは
それらを再エクスポートするだけで、名前ごとの型エイリアス
（``Annotated[GlideClient, Depends(get_valkey_client("cache"))]``）は
アプリ側が名前を指定して定義する。

利用には ``fastapi-toolkit[valkey]`` extraのインストールが必要。
"""

from core_toolkit.valkey_lifespan import ValkeyLifespanResource, get_valkey_client

__all__ = ["ValkeyLifespanResource", "get_valkey_client"]
