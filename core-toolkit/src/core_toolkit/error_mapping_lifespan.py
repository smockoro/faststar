"""ErrorCodeRegistryのlifespan管理。

``exception_handlers``とASGIミドルウェアはどちらもFastAPIの``Depends``
注入の対象外であるため、``app.state``を共有領域として使う（詳細は
``docs/superpowers/specs/2026-09-28-error-handling-design.md``参照）。
シングルトンのlifespan resourceであり、DB/Redis/ObjectStorageのような
名前付き複数インスタンスにはしない（1アプリ内で``error_code``名前空間を
複数持ちたい実需がないため）。
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from starlette.applications import Starlette

from core_toolkit.error_mapping import ErrorCodeMapping, ErrorCodeRegistry
from core_toolkit.lifespan import LifespanResource

__all__ = [
    "ErrorMappingLifespanResource",
    "get_error_mapping_resource",
    "open_error_mapping",
]


class ErrorMappingLifespanResource(LifespanResource):
    """シングルトンの``ErrorCodeRegistry``を``app.state.error_mapping``に登録する。

    Args:
        mappings: ``error_code``をキーとした``ErrorCodeMapping``の辞書。
            省略時は空のレジストリになり、全ての例外がデフォルト値
            （``SystemProblem``→500 / それ以外→400）に解決される。
    """

    def __init__(self, mappings: dict[str, ErrorCodeMapping] | None = None) -> None:
        self.registry = ErrorCodeRegistry(mappings)

    @asynccontextmanager
    async def context(self, app: Starlette) -> AsyncGenerator[Any, Any]:
        app.state.error_mapping = self
        yield self


def open_error_mapping(
    mappings: dict[str, ErrorCodeMapping] | None = None,
) -> ErrorMappingLifespanResource:
    """``ErrorMappingLifespanResource``を生成するファクトリ。

    将来「設定値管理部品」ができた場合は、
    ``open_error_mapping(mappings=load_from_config(...))``のように
    ``mappings``の構築元だけを差し替える。``ErrorCodeRegistry``自体の
    変更は不要。

    Args:
        mappings: ``error_code``をキーとした``ErrorCodeMapping``の辞書。

    Returns:
        ``create_lifespan(...)``に渡せる``ErrorMappingLifespanResource``。
    """
    return ErrorMappingLifespanResource(mappings)


def get_error_mapping_resource(app: Starlette) -> ErrorMappingLifespanResource:
    """``app.state.error_mapping``からリソースを取得する。

    ``exception_handlers``とASGIミドルウェアは``Request``を経由しない
    箇所からも呼ばれうるため、``Request``ではなく``Starlette``アプリ本体
    から直接取得する（``exception_handler``なら``request.app``、ASGI
    ミドルウェアなら``scope["app"]``を渡す）。

    Args:
        app: Starletteアプリケーションインスタンス。

    Returns:
        登録済みの``ErrorMappingLifespanResource``。

    Raises:
        RuntimeError: 対応する``ErrorMappingLifespanResource``が
            ``create_lifespan(...)``に登録されていない場合。
    """
    if not hasattr(app.state, "error_mapping"):
        raise RuntimeError(
            "app.state.error_mapping is not set. "
            "Did you forget to register ErrorMappingLifespanResource() "
            "(via open_error_mapping()) in create_lifespan(...)?"
        )
    resource: ErrorMappingLifespanResource = app.state.error_mapping
    return resource
