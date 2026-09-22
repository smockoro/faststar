"""DB接続lifespanリソースのFastAPI向け薄いラッパー。

``DbLifespanResource``/``get_db_engine``/``get_db_connection`` はFastAPIに
依存せず ``core_toolkit.db_lifespan`` に実装されている。このモジュールは
それらを再エクスポートするだけで、名前ごとの型エイリアス
（``Annotated[AsyncConnection, Depends(get_db_connection("main"))]``）は
アプリ側で名前を指定して定義する。

利用には ``fastapi-toolkit[sqlite]``/``fastapi-toolkit[postgres]``/
``fastapi-toolkit[mysql]`` のいずれかのextraのインストールが必要。
"""

from core_toolkit.db_lifespan import DbLifespanResource, get_db_connection, get_db_engine

__all__ = ["DbLifespanResource", "get_db_connection", "get_db_engine"]
