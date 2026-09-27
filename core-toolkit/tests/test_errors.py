"""ApplicationProblem/BusinessProblem/SystemProblemのテスト。"""

from core_toolkit.errors import ApplicationProblem, BusinessProblem, SystemProblem


class TestApplicationProblem:
    def test_holds_error_code_and_message(self):
        exc = ApplicationProblem("test.error", "何かがおかしい")
        assert exc.error_code == "test.error"
        assert exc.message == "何かがおかしい"

    def test_str_returns_message(self):
        exc = ApplicationProblem("test.error", "何かがおかしい")
        assert str(exc) == "何かがおかしい"


class TestBusinessProblem:
    def test_is_application_problem(self):
        exc = BusinessProblem("business.invalid_input", "入力が不正")
        assert isinstance(exc, ApplicationProblem)

    def test_is_not_system_problem(self):
        exc = BusinessProblem("business.invalid_input", "入力が不正")
        assert not isinstance(exc, SystemProblem)

    def test_holds_error_code_and_message(self):
        exc = BusinessProblem("business.invalid_input", "入力が不正")
        assert exc.error_code == "business.invalid_input"
        assert exc.message == "入力が不正"


class TestSystemProblem:
    def test_is_application_problem(self):
        exc = SystemProblem("system.db_unavailable", "DB接続に失敗")
        assert isinstance(exc, ApplicationProblem)

    def test_is_not_business_problem(self):
        exc = SystemProblem("system.db_unavailable", "DB接続に失敗")
        assert not isinstance(exc, BusinessProblem)

    def test_does_not_shadow_builtin_system_error(self):
        """builtins.SystemErrorとは無関係の別クラスであることを固定する回帰テスト。"""
        assert SystemProblem is not SystemError
        assert not issubclass(SystemProblem, SystemError)
