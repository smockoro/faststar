"""ErrorCodeRegistry lifespanのFastAPI向け薄いラッパー。

実装はFastAPIに依存せず``core_toolkit.error_mapping_lifespan``にある。
このモジュールは再エクスポートするだけ。
"""

from core_toolkit.error_mapping import ErrorCodeMapping, ErrorCodeRegistry
from core_toolkit.error_mapping_lifespan import (
    ErrorMappingLifespanResource,
    get_error_mapping_resource,
    open_error_mapping,
)

__all__ = [
    "ErrorCodeMapping",
    "ErrorCodeRegistry",
    "ErrorMappingLifespanResource",
    "get_error_mapping_resource",
    "open_error_mapping",
]
