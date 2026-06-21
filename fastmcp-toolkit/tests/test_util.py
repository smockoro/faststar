"""util モジュールのテスト。"""

from fastmcp_toolkit.util import parse_str_tuple


class TestParseStrTuple:
    def test_single_value(self):
        assert parse_str_tuple("a") == ("a",)

    def test_multiple_values(self):
        assert parse_str_tuple("a,b,c") == ("a", "b", "c")

    def test_strips_whitespace(self):
        assert parse_str_tuple(" a , b , c ") == ("a", "b", "c")

    def test_empty_string_returns_empty_tuple(self):
        assert parse_str_tuple("") == ()

    def test_only_commas_returns_empty_tuple(self):
        assert parse_str_tuple(",,,") == ()

    def test_trailing_comma_ignored(self):
        assert parse_str_tuple("a,b,") == ("a", "b")

    def test_leading_comma_ignored(self):
        assert parse_str_tuple(",a,b") == ("a", "b")

    def test_whitespace_only_values_ignored(self):
        assert parse_str_tuple("a, , ,b") == ("a", "b")
