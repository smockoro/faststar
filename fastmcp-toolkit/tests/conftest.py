"""共有テストフィクスチャ。"""

from functools import partial

import pytest
import structlog
import structlog.testing

from fastmcp_toolkit.logging import _add_application_id, setup_logging


@pytest.fixture(autouse=True)
def _stub_msal_tenant_discovery(monkeypatch: pytest.MonkeyPatch):
    """``ConfidentialClientApplication``構築時のtenant discoveryをスタブする。

    msal（1.30以降で確認）は``ConfidentialClientApplication.__init__``内で
    ``validate_authority``や``instance_discovery``の値に関わらず、必ず
    ``authority``のOIDC discoveryエンドポイント（``/.well-known/openid-configuration``）
    へ実際にHTTPリクエストを送る（``msal.authority.Authority.__init__``の
    docstring: "We always do a tenant discovery."）。

    そのため、``test_msal_client_credential_lifespan.py``が
    ``ConfidentialClientApplication.acquire_token_for_client``のみを
    monkeypatchしても、テストで使う架空のauthority
    （``https://login.microsoftonline.com/tenant-id``）に対しては実際に
    AADへ到達し、存在しないテナントとして``ValueError``で失敗してしまう
    （core-toolkitの同名フィクスチャと同一の対処。core-toolkit/tests/conftest.py
    参照）。

    ここでは``msal.authority.tenant_discovery``（Authority.__init__から
    モジュール関数として呼ばれる）をスタブし、要求されたdiscovery
    endpoint URLから機械的に整形した合成のOIDC discoveryレスポンスを返す
    ことで、テスト全体を通じて実際のAADへの接続を発生させない。
    """
    import msal.authority as msal_authority

    def fake_tenant_discovery(tenant_discovery_endpoint: str, http_client, **kwargs):
        base = tenant_discovery_endpoint.rsplit("/.well-known/openid-configuration", 1)[
            0
        ]
        token_base = base[: -len("/v2.0")] if base.endswith("/v2.0") else base
        return {
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth2/v2.0/authorize",
            "token_endpoint": f"{token_base}/oauth2/v2.0/token",
        }

    monkeypatch.setattr(msal_authority, "tenant_discovery", fake_tenant_discovery)


@pytest.fixture
def setup_structlog():
    """テスト出力用にstructlogを設定する。"""
    setup_logging(json_output=False)


@pytest.fixture
def capture_logs():
    """structlog出力をdictのリストとしてキャプチャする。

    merge_contextvarsと_add_application_idを実行し、キャプチャされた
    エントリにsession_id、application_id等を含める。
    このフィクスチャの前にsetup_logging()を呼ぶこと。
    """
    with structlog.testing.capture_logs(
        processors=[
            structlog.contextvars.merge_contextvars,
            partial(_add_application_id, _application_id=""),
        ]
    ) as logs:
        yield logs
