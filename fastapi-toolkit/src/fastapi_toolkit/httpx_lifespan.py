"""httpx AsyncClient lifespanリソースのFastAPI向け薄いラッパー。

``HttpxLifespanResource``/``get_httpx_client`` はFastAPIに依存せず
``core_toolkit.httpx_lifespan`` に実装されている。このモジュールは
それらを再エクスポートするだけで、名前ごとの型エイリアス
（``Annotated[httpx.AsyncClient, Depends(get_httpx_client("backend"))]``）
はアプリ側が名前を指定して定義する。

利用には ``fastapi-toolkit[httpx]`` extraのインストールが必要。
"""

from core_toolkit.httpx_lifespan import HttpxLifespanResource, get_httpx_client

__all__ = ["HttpxLifespanResource", "get_httpx_client"]
