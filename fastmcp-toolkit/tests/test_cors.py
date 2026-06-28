"""mcp_cors_optionsのテスト。"""

import pytest

from fastmcp_toolkit.cors import mcp_cors_options


def test_defaults():
    """デフォルト値が全て期待通りに設定される。"""
    result = mcp_cors_options(allow_origins=["https://example.com"])
    assert result["allow_origins"] == ["https://example.com"]
    assert result["allow_methods"] == ["GET", "POST", "DELETE", "OPTIONS"]
    assert result["expose_headers"] == ["Mcp-Session-Id"]
    assert result["allow_credentials"] is True
    assert result["max_age"] == 10


def test_default_allow_headers_contains_mcp_headers():
    """デフォルトのallow_headersにはMCPプロトコル必須ヘッダーが含まれる。"""
    result = mcp_cors_options(allow_origins=["https://example.com"])
    for header in [
        "Content-Type",
        "Authorization",
        "Mcp-Session-Id",
        "MCP-Protocol-Version",
        "Accept",
        "Last-Event-ID",
    ]:
        msg = f"{header} がallow_headersに含まれない"
        assert header in result["allow_headers"], msg


def test_custom_allow_headers():
    """カスタムallow_headersが反映される。"""
    result = mcp_cors_options(
        allow_origins=["https://a.com"],
        allow_headers=["Content-Type", "Authorization"],
    )
    assert result["allow_headers"] == ["Content-Type", "Authorization"]


def test_allow_headers_none_uses_default():
    """allow_headers=None（未指定）はデフォルトリストを返す。"""
    result = mcp_cors_options(allow_origins=["https://a.com"])
    assert "Mcp-Session-Id" in result["allow_headers"]


def test_empty_allow_headers_list():
    """空のallow_headersリストはデフォルトを使わずそのまま返る。"""
    result = mcp_cors_options(allow_origins=["https://a.com"], allow_headers=[])
    assert result["allow_headers"] == []


def test_custom_max_age():
    """カスタムmax_ageが反映される。"""
    result = mcp_cors_options(allow_origins=["https://a.com"], max_age=3600)
    assert result["max_age"] == 3600


def test_max_age_zero():
    """max_age=0はプリフライトキャッシュなし設定になる。"""
    result = mcp_cors_options(allow_origins=["https://a.com"], max_age=0)
    assert result["max_age"] == 0


def test_no_credentials():
    """allow_credentials=Falseが反映される。"""
    result = mcp_cors_options(allow_origins=["https://a.com"], allow_credentials=False)
    assert result["allow_credentials"] is False


def test_empty_allow_origins():
    """空のoriginsリストでもkwargsが生成される。"""
    result = mcp_cors_options(allow_origins=[])
    assert result["allow_origins"] == []


def test_multiple_origins():
    """複数のオリジンを指定できる。"""
    origins = ["https://a.com", "https://b.com", "http://localhost:3000"]
    result = mcp_cors_options(allow_origins=origins)
    assert result["allow_origins"] == origins


def test_allow_methods_is_fixed():
    """allow_methodsはカスタマイズできない固定値。"""
    result = mcp_cors_options(allow_origins=["https://a.com"])
    assert sorted(result["allow_methods"]) == sorted(
        ["GET", "POST", "DELETE", "OPTIONS"]
    )


def test_expose_headers_is_fixed():
    """expose_headersはMcp-Session-Idのみの固定値。"""
    result = mcp_cors_options(allow_origins=["https://a.com"])
    assert result["expose_headers"] == ["Mcp-Session-Id"]


def test_return_type_is_dict():
    """返り値はdict。"""
    result = mcp_cors_options(allow_origins=["https://a.com"])
    assert isinstance(result, dict)


def test_required_keys_present():
    """返り値に必須キーが全て含まれる。"""
    result = mcp_cors_options(allow_origins=["https://a.com"])
    for key in [
        "allow_origins",
        "allow_methods",
        "allow_headers",
        "expose_headers",
        "allow_credentials",
        "max_age",
    ]:
        assert key in result, f"キー '{key}' が欠落している"


@pytest.mark.parametrize("allow_credentials", [True, False])
def test_allow_credentials_parametrized(allow_credentials: bool):
    """allow_credentialsのTrue/Falseが正しく伝達される。"""
    result = mcp_cors_options(
        allow_origins=["https://a.com"], allow_credentials=allow_credentials
    )
    assert result["allow_credentials"] is allow_credentials
