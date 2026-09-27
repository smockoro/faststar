"""ErrorMappingLifespanResourceのテスト。"""

import pytest
from starlette.applications import Starlette

from core_toolkit.error_mapping import ErrorCodeMapping
from core_toolkit.error_mapping_lifespan import (
    get_error_mapping_resource,
    open_error_mapping,
)
from core_toolkit.errors import BusinessProblem
from core_toolkit.lifespan import create_lifespan


class TestOpenErrorMapping:
    def test_returns_resource_with_registry(self):
        resource = open_error_mapping({"business.x": ErrorCodeMapping(http_status=422)})
        exc = BusinessProblem("business.x", "検証エラー")
        assert resource.registry.resolve_http_status(exc) == 422

    def test_no_mappings_uses_default_resolution(self):
        resource = open_error_mapping()
        exc = BusinessProblem("business.unknown", "未登録")
        assert resource.registry.resolve_http_status(exc) == 400


class TestErrorMappingLifespanIntegration:
    @pytest.mark.asyncio
    async def test_context_registers_resource_on_app_state(self):
        app = Starlette()
        lifespan = create_lifespan(
            open_error_mapping({"business.x": ErrorCodeMapping(http_status=422)})
        )

        async with lifespan(app):
            resource = get_error_mapping_resource(app)
            exc = BusinessProblem("business.x", "検証エラー")
            assert resource.registry.resolve_http_status(exc) == 422

    def test_get_error_mapping_resource_raises_when_not_registered(self):
        app = Starlette()
        with pytest.raises(RuntimeError, match="error_mapping"):
            get_error_mapping_resource(app)
