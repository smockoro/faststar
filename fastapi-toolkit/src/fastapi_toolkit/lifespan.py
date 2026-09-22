"""lifespanリソース管理のFastAPI向け薄いラッパー。

``LifespanResource``/``create_lifespan``/``app_state_dependency``は
FastAPIに依存せず ``core_toolkit.lifespan`` に実装されている。このモジュールは
それらを再エクスポートするだけ。
"""

from core_toolkit.lifespan import LifespanResource, app_state_dependency, create_lifespan

__all__ = ["LifespanResource", "app_state_dependency", "create_lifespan"]
