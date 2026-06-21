import pytest


@pytest.fixture(autouse=True)
def _set_azure_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("AZURE_TENANT_ID", "test-tenant-id")
    for key in ("AZURE_REDIRECT_URI", "AZURE_SCOPES", "SESSION_SECRET"):
        monkeypatch.delenv(key, raising=False)
