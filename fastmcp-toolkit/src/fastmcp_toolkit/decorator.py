"""OpenTelemetryスパンを付与するデコレータ。"""

import functools
from collections.abc import Callable
from typing import Any, overload

from fastmcp.telemetry import get_tracer


@overload
def span(func: Callable) -> Callable: ...


@overload
def span(
    span_name: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Callable: ...


def span(
    func: Callable | str | None = None,
    span_name: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Callable:
    """関数にOpenTelemetryスパンを付与するデコレータ。

    ``@span`` と ``@span(span_name="...", attributes={...})`` の両形式で使用可能。
    対象関数は ``async def`` である必要がある。

    Args:
        func: デコレート対象の関数。``@span`` 形式で使用時に自動で渡される。
        span_name: スパン名。未指定の場合は関数名が使用される。
        attributes: スパンに付与する静的属性。

    Returns:
        スパン計装付きの非同期ラッパー関数。

    Example::

        @span
        async def fetch_data():
            ...

        @span(span_name="db.query", attributes={"db": "postgres"})
        async def query_db():
            ...
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            name = span_name or fn.__name__
            tracer = get_tracer()
            with tracer.start_as_current_span(name) as s:
                if attributes:
                    for k, v in attributes.items():
                        s.set_attribute(k, v)
                return await fn(*args, **kwargs)

        return wrapper

    if callable(func):
        return decorator(func)

    if isinstance(func, str):
        span_name_resolved = func
        return lambda fn: span(fn, span_name=span_name_resolved, attributes=attributes)

    return decorator
