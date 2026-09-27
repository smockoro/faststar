"""ErrorCodeRegistryのテスト。"""

from core_toolkit.error_mapping import ErrorCodeMapping, ErrorCodeRegistry
from core_toolkit.errors import BusinessProblem, SystemProblem


class TestErrorCodeRegistry:
    def test_registered_error_code_returns_mapped_status(self):
        registry = ErrorCodeRegistry(
            {"business.not_found": ErrorCodeMapping(http_status=404)}
        )
        exc = BusinessProblem("business.not_found", "見つからない")
        assert registry.resolve_http_status(exc) == 404

    def test_unregistered_error_code_falls_back_to_400_for_business_problem(self):
        registry = ErrorCodeRegistry()
        exc = BusinessProblem("business.unknown", "未登録")
        assert registry.resolve_http_status(exc) == 400

    def test_unregistered_error_code_falls_back_to_500_for_system_problem(self):
        registry = ErrorCodeRegistry()
        exc = SystemProblem("system.unknown", "未登録")
        assert registry.resolve_http_status(exc) == 500

    def test_mapping_without_http_status_falls_back_to_default(self):
        registry = ErrorCodeRegistry(
            {"system.timeout": ErrorCodeMapping(jsonrpc_code=-32001)}
        )
        exc = SystemProblem("system.timeout", "タイムアウト")
        assert registry.resolve_http_status(exc) == 500

    def test_empty_registry_uses_default_for_business_problem(self):
        registry = ErrorCodeRegistry(None)
        exc = BusinessProblem("business.anything", "何か")
        assert registry.resolve_http_status(exc) == 400
