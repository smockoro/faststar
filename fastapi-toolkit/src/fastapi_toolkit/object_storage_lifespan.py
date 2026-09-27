"""ObjectStorage接続lifespanリソースのFastAPI向け薄いラッパー。

``ObjectStorageLifespanResource``/``get_object_storage`` はFastAPIに依存せず
``core_toolkit.object_storage_lifespan`` に実装されている。このモジュールは
それらを再エクスポートするだけで、名前ごとの型エイリアス
（``Annotated[ObjectStorage, Depends(get_object_storage("main"))]``）は
アプリ側が名前を指定して定義する。

利用にはバックエンドに応じたextra（``fastapi-toolkit[s3]``/``[gcs]``/``[azure]``）
のインストールが必要。``scheme="memory"``は追加依存なしで使える。
"""

from core_toolkit.object_storage import ObjectStorage
from core_toolkit.object_storage_lifespan import (
    ObjectStorageLifespanResource,
    get_object_storage,
)

__all__ = ["ObjectStorage", "ObjectStorageLifespanResource", "get_object_storage"]
