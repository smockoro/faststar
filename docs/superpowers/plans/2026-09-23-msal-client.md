# MSALクライアント lifespan部品 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** core-toolkit / fastapi-toolkit / fastmcp-toolkitに、DB/Redis/HTTPクライアントと同じ名前付き複数インスタンス方式のMSAL（Microsoft Authentication Library）lifespan管理部品を新規実装する。Client Credentials（アプリ単位トークン）とOn-Behalf-Of（ユーザー単位トークン）の2フローに対応する。

**Architecture:** core-toolkitに実体（`msal_errors.py`/`token_cache_cipher.py`/`msal_lifespan.py`）を持ち、fastapi-toolkitは薄い再エクスポート、fastmcp-toolkitは`lifespan_context`ベースの独立実装という3層構造（db_lifespan/redis_lifespanと同型）。MSALは同期API（requestsベース）のため専用`ThreadPoolExecutor`で呼び出しを隔離する。Client Credentialsは`ConfidentialClientApplication`をlifespanで1つ使い回すが、OBOは`token_cache`がユーザーごとに異なるためインスタンス自体をリクエストごとに生成し、`Session`/`http_cache`/executorのみ共有する。トークンキャッシュはRedisにJWE（`alg=dir`, `enc=A256GCM`、`joserfc`実装）で暗号化して永続化し、鍵は`kid`で複数バージョン管理する。

**Tech Stack:** `msal`（Microsoft Authentication Library） + `joserfc`（JWE） + `requests`（`msal`の推移的依存） + `redis.asyncio`（既存の`RedisLifespanResource`をトークンキャッシュ永続化に利用） + `fakeredis`（テスト）。Starlette/FastAPI/FastMCP/`uncalled_for`は既存依存をそのまま利用。

**Spec:** `docs/superpowers/specs/2026-09-23-msal-client-design.md`

## Global Constraints

- Python 3.14+（既存パッケージのrequires-pythonに合わせる）
- ruffでE, F, I, UPルールをlint。core-toolkit/fastmcp-toolkitは既定line-length（88）、fastapi-toolkitは`line-length = 120`
- 公開API（`MsalClientCredentialLifespanResource`, `MsalOboLifespanResource`, `get_msal_app_token`, `get_msal_obo_token`, `TokenCacheCipher`, `JweTokenCacheCipher`, `default_token_cache_cipher`, `MsalTokenError`, `MsalClaimsChallengeError`, fastmcp-toolkit側の対応物）には日本語・GoogleスタイルのDocstringを付ける
- 対象フローはClient CredentialsとOBOのみ。`PublicClientApplication`は実装しない
- JWT検証・`user_assertion`/`user_id`の抽出は実装しない。呼び出し側が渡す前提のシグネチャにする
- `http_cache`（429スロットル情報）のRedis共有化は実装しない。プロセス内dict（`{}`）をそのまま使う
- 証明書ベースの`client_credential`構築ヘルパーは実装しない。`client_credential: str`（シークレット文字列）のみサポートする
- トークンキャッシュの暗号化はデフォルトでJWE（`joserfc`、`alg=dir`/`enc=A256GCM`）。鍵は`kid`ごとに複数登録し、`current_kid`で現在使う鍵を切り替えられるようにする
- `current_kid`・鍵material・Redis接続情報等を環境変数から読むロジックはcore-toolkit側に持たせない。すべてコンストラクタ引数として受け取る
- pyproject.tomlへの依存追加後は必ず対象ディレクトリで`uv sync --all-extras`を実行してから実装・テストに進む
- MSALの実際のHTTP呼び出し（`acquire_token_for_client`/`acquire_token_on_behalf_of`）はテストで`monkeypatch`によりスタブする。実際のAADへは接続しない
- テストのRedis永続化は`fakeredis.aioredis.FakeRedis`のみを使う

---

## Task 1: core-toolkit — `msal_errors.py`

**Files:**
- Create: `core-toolkit/src/core_toolkit/msal_errors.py`
- Test: `core-toolkit/tests/test_msal_errors.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `MsalTokenError(error: str | None, error_description: str | None, error_codes: list[int] | None = None)` — `Exception`のサブクラス。属性`error`/`error_description`/`error_codes`を持つ
  - `MsalClaimsChallengeError(error: str | None, error_description: str | None, claims: str, error_codes: list[int] | None = None)` — `MsalTokenError`のサブクラス。属性`claims`を追加で持つ
  - Task 3/4が結果dictをこれらの例外に変換する際に使う

- [ ] **Step 1: 失敗するテストを書く**

`core-toolkit/tests/test_msal_errors.py`を作成する。

```python
"""msal_errorsの単体テスト。"""

from core_toolkit.msal_errors import MsalClaimsChallengeError, MsalTokenError


def test_msal_token_error_holds_fields_and_message():
    err = MsalTokenError("invalid_client", "AADSTS7000215: Invalid client secret", [7000215])

    assert err.error == "invalid_client"
    assert err.error_description == "AADSTS7000215: Invalid client secret"
    assert err.error_codes == [7000215]
    assert str(err) == "invalid_client: AADSTS7000215: Invalid client secret"


def test_msal_token_error_defaults_error_codes_to_empty_list():
    err = MsalTokenError("invalid_scope", "scope is invalid")

    assert err.error_codes == []


def test_msal_claims_challenge_error_is_a_msal_token_error_with_claims():
    err = MsalClaimsChallengeError(
        "interaction_required", "MFA required", claims='{"access_token":{}}', error_codes=[50076]
    )

    assert isinstance(err, MsalTokenError)
    assert err.claims == '{"access_token":{}}'
    assert err.error == "interaction_required"
    assert err.error_codes == [50076]
```

- [ ] **Step 2: テストを実行し失敗を確認**

Run: `cd core-toolkit && uv run pytest tests/test_msal_errors.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'core_toolkit.msal_errors'`）

- [ ] **Step 3: 実装を書く**

`core-toolkit/src/core_toolkit/msal_errors.py`を作成する。

```python
"""MSALのdict形式エラー応答を例外に変換するための共通エラー型。"""


class MsalTokenError(Exception):
    """トークン取得に失敗した場合に送出する。

    Args:
        error: MSALが返す``"error"``の値（例: ``"invalid_client"``）。
        error_description: MSALが返す``"error_description"``の値。
        error_codes: MSALが返す``"error_codes"``の値（AADSTSエラーコード列）。
    """

    def __init__(
        self,
        error: str | None,
        error_description: str | None,
        error_codes: list[int] | None = None,
    ) -> None:
        self.error = error
        self.error_description = error_description
        self.error_codes = error_codes or []
        super().__init__(f"{error}: {error_description}")


class MsalClaimsChallengeError(MsalTokenError):
    """Conditional Access等でユーザーの再認可（MFA等）が必要な場合に送出する。

    Args:
        error: MSALが返す``"error"``の値。
        error_description: MSALが返す``"error_description"``の値。
        claims: 再認可フローのauthorize URLへそのまま付与する必要がある値。
        error_codes: MSALが返す``"error_codes"``の値。
    """

    def __init__(
        self,
        error: str | None,
        error_description: str | None,
        claims: str,
        error_codes: list[int] | None = None,
    ) -> None:
        super().__init__(error, error_description, error_codes)
        self.claims = claims
```

- [ ] **Step 4: テストを実行し成功を確認**

Run: `cd core-toolkit && uv run pytest tests/test_msal_errors.py -v`
Expected: PASS（3件全て）

- [ ] **Step 5: lint/formatを実行**

Run: `cd core-toolkit && uv run ruff format src/ tests/ && uv run ruff check --fix src/ tests/`
Expected: エラーなし

- [ ] **Step 6: コミット**

```bash
git add core-toolkit/src/core_toolkit/msal_errors.py core-toolkit/tests/test_msal_errors.py
git commit -m "$(cat <<'EOF'
feat(core-toolkit): add MSAL error types

MSALのdict形式エラー応答(error/error_description/error_codes)を例外に
変換するMsalTokenError/MsalClaimsChallengeErrorを追加。他クライアント
(DB/Redis/HTTP)と同じ「例外を投げる」流儀に揃える。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: core-toolkit — `token_cache_cipher.py`

**Files:**
- Modify: `core-toolkit/pyproject.toml`
- Create: `core-toolkit/src/core_toolkit/token_cache_cipher.py`
- Test: `core-toolkit/tests/test_token_cache_cipher.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `TokenCacheCipher`（`Protocol`） — `encrypt(self, plaintext: bytes) -> bytes` / `decrypt(self, ciphertext: bytes) -> bytes`
  - `JweTokenCacheCipher(keys: dict[str, bytes], current_kid: str)` — 上記Protocolを満たすクラス
  - `default_token_cache_cipher(keys: dict[str, bytes], current_kid: str = "v1") -> JweTokenCacheCipher`
  - Task 3/4が`cipher: TokenCacheCipher`引数の型として使う

- [ ] **Step 1: pyproject.tomlに`msal` extraを追加（joserfcのみ）**

`core-toolkit/pyproject.toml`の`[project.optional-dependencies]`に、`redis`の直後へ以下を追加する。

```toml
msal = [
    "joserfc>=1.0",
]
```

- [ ] **Step 2: 依存をインストール**

Run: `cd core-toolkit && uv sync --all-extras`
Expected: `joserfc`が解決されてインストールされる。

- [ ] **Step 3: 失敗するテストを書く**

`core-toolkit/tests/test_token_cache_cipher.py`を作成する。

```python
"""token_cache_cipherの単体テスト。"""

import pytest

from core_toolkit.token_cache_cipher import JweTokenCacheCipher, default_token_cache_cipher


def _key(seed: int) -> bytes:
    return bytes([seed]) * 32


def test_encrypt_then_decrypt_roundtrip():
    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")

    ciphertext = cipher.encrypt(b"secret-token-cache-payload")

    assert cipher.decrypt(ciphertext) == b"secret-token-cache-payload"


def test_ciphertext_does_not_contain_plaintext():
    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")

    ciphertext = cipher.encrypt(b"secret-token-cache-payload")

    assert b"secret-token-cache-payload" not in ciphertext


def test_decrypt_selects_key_by_kid_after_rotation():
    old_cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    ciphertext = old_cipher.encrypt(b"payload-from-v1")

    rotated_cipher = JweTokenCacheCipher(keys={"v1": _key(1), "v2": _key(2)}, current_kid="v2")

    assert rotated_cipher.decrypt(ciphertext) == b"payload-from-v1"


def test_encrypt_after_rotation_cannot_be_decrypted_by_old_key_only():
    rotated_cipher = JweTokenCacheCipher(keys={"v1": _key(1), "v2": _key(2)}, current_kid="v2")
    ciphertext = rotated_cipher.encrypt(b"payload-from-v2")

    v1_only_cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")

    with pytest.raises(KeyError):
        v1_only_cipher.decrypt(ciphertext)
```

- [ ] **Step 4: テストを実行し失敗を確認**

Run: `cd core-toolkit && uv run pytest tests/test_token_cache_cipher.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'core_toolkit.token_cache_cipher'`）

- [ ] **Step 5: 実装を書く**

`core-toolkit/src/core_toolkit/token_cache_cipher.py`を作成する。

```python
"""トークンキャッシュ（SerializableTokenCache）の暗号化。

JWE（RFC 7516、joserfc実装）による暗号化をデフォルトとする。鍵は``kid``
ごとに複数登録でき、「現在使うkid」を切り替えるだけで新しい鍵/アルゴリズム
へ移行できる。過去に別kidで暗号化されたデータも、そのkidの鍵が登録され
続けていれば復号できる。

利用には ``core-toolkit[msal]`` extraのインストールが必要。
"""

from typing import Protocol

from joserfc import jwe
from joserfc.jwk import OctKey


class TokenCacheCipher(Protocol):
    def encrypt(self, plaintext: bytes) -> bytes: ...
    def decrypt(self, ciphertext: bytes) -> bytes: ...


class JweTokenCacheCipher:
    """joserfcベースのJWE（``alg=dir``, ``enc=A256GCM``）によるCipher実装。

    Args:
        keys: ``kid``をキーとする鍵materialの辞書
            （例: ``{"v1": b"...32bytes..."}``）。複数バージョン渡すことで
            鍵ローテーション・アルゴリズム移行に対応する。
        current_kid: 暗号化時に使う``kid``。復号時は暗号文ヘッダの``kid``を
            見て``keys``から自動選択するため、この値には依存しない。
    """

    def __init__(self, keys: dict[str, bytes], current_kid: str) -> None:
        self._keys = {kid: OctKey.import_key(key) for kid, key in keys.items()}
        self._current_kid = current_kid

    def encrypt(self, plaintext: bytes) -> bytes:
        """平文をJWE Compact Serializationとして暗号化する。"""
        key = self._keys[self._current_kid]
        protected = {"alg": "dir", "enc": "A256GCM", "kid": self._current_kid}
        return jwe.encrypt_compact(protected, plaintext, key)

    def decrypt(self, ciphertext: bytes) -> bytes:
        """JWE暗号文を復号する。ヘッダの``kid``に対応する鍵が未登録の場合は``KeyError``。"""

        def resolve_key(header: dict, payload: bytes) -> OctKey:
            return self._keys[header["kid"]]

        obj = jwe.decrypt_compact(ciphertext, resolve_key)
        return obj.plaintext


def default_token_cache_cipher(
    keys: dict[str, bytes], current_kid: str = "v1"
) -> JweTokenCacheCipher:
    """デフォルトのCipherを構築する。

    ``keys``・``current_kid``を環境変数等から組み立てるのは呼び出し側
    （将来の設定値管理機能）の責務とし、ここでは受け取るだけに留める。
    """
    return JweTokenCacheCipher(keys=keys, current_kid=current_kid)
```

- [ ] **Step 6: テストを実行し成功を確認**

Run: `cd core-toolkit && uv run pytest tests/test_token_cache_cipher.py -v`
Expected: PASS（4件全て）

- [ ] **Step 7: lint/formatを実行**

Run: `cd core-toolkit && uv run ruff format src/ tests/ && uv run ruff check --fix src/ tests/`
Expected: エラーなし

- [ ] **Step 8: コミット**

```bash
git add core-toolkit/pyproject.toml core-toolkit/uv.lock core-toolkit/src/core_toolkit/token_cache_cipher.py core-toolkit/tests/test_token_cache_cipher.py
git commit -m "$(cat <<'EOF'
feat(core-toolkit): add JWE-based token cache cipher

TokenCacheCipher(Protocol)とjoserfcベースのJweTokenCacheCipherを追加。
鍵はkidごとに複数登録し、current_kidの切り替えだけで新アルゴリズムへの
移行・旧デフォルトへの固定ができる設計。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: core-toolkit — `msal_lifespan.py`（Client Credentials）

**Files:**
- Modify: `core-toolkit/pyproject.toml`
- Create: `core-toolkit/src/core_toolkit/msal_lifespan.py`
- Test: `core-toolkit/tests/test_msal_client_credential_lifespan.py`

**Interfaces:**
- Consumes:
  - `core_toolkit.lifespan.LifespanResource`/`create_lifespan`（既存）
  - `core_toolkit.redis_lifespan.RedisLifespanResource`/`get_redis_client`（既存、シグネチャはRedis設計ドキュメント参照）
  - `core_toolkit.msal_errors.MsalTokenError`/`MsalClaimsChallengeError`（Task 1）
  - `core_toolkit.token_cache_cipher.TokenCacheCipher`（Task 2）
- Produces:
  - `default_msal_http_session() -> requests.Session`
  - `MsalClientCredentialHandle`（dataclass、`app`/`cache`/`executor`/`cipher`/`redis_client_name`/`cache_key`属性）
  - `MsalClientCredentialLifespanResource(name: str, client_id: str, client_credential: str, authority: str, redis_client_name: str, cipher: TokenCacheCipher, max_workers: int = 4, http_client_factory: Any = None, **app_kwargs: Any)` — `LifespanResource`のサブクラス
  - `get_msal_app_token(name: str, scopes: list[str])` — `request: Request`を1引数に取る非同期provider関数を返す
  - Task 4（同ファイルにOBO部分を追記）、Task 5（fastapi-toolkit）、Task 6（fastmcp-toolkit）が使う

**注意:** `MsalClientCredentialLifespanResource`は`context()`内で`get_redis_client(self._redis_client_name)`を呼ぶため、`create_lifespan(...)`に渡す際は対応する`RedisLifespanResource`を**先に**登録しておく必要がある（`AsyncExitStack`は登録順に`__aenter__`する）。

- [ ] **Step 1: pyproject.tomlの`msal` extraに`msal`パッケージを追加**

`core-toolkit/pyproject.toml`の`[project.optional-dependencies]`にある`msal`エントリ（Task 2で追加済み）を以下のように変更する。

```toml
msal = [
    "msal>=1.30",
    "joserfc>=1.0",
]
```

- [ ] **Step 2: 依存をインストール**

Run: `cd core-toolkit && uv sync --all-extras`
Expected: `msal`（および推移的依存の`requests`/`pyjwt`/`cryptography`）が解決されてインストールされる。

- [ ] **Step 3: 失敗するテストを書く**

`core-toolkit/tests/test_msal_client_credential_lifespan.py`を作成する。

```python
"""msal_lifespanのClient Credentials部分の統合テスト。

実際のAADへは接続せず、``ConfidentialClientApplication.acquire_token_for_client``
をmonkeypatchでスタブする。
"""

import pytest
from fakeredis.aioredis import FakeRedis
from msal import ConfidentialClientApplication, SerializableTokenCache
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import create_lifespan
from core_toolkit.msal_errors import MsalClaimsChallengeError, MsalTokenError
from core_toolkit.msal_lifespan import (
    MsalClientCredentialLifespanResource,
    default_msal_http_session,
    get_msal_app_token,
)
from core_toolkit.redis_lifespan import RedisLifespanResource, get_redis_client
from core_toolkit.token_cache_cipher import JweTokenCacheCipher


def _key(seed: int) -> bytes:
    return bytes([seed]) * 32


def _make_request(app: Starlette) -> Request:
    return Request({"type": "http", "app": app})


def _make_resource(cipher: JweTokenCacheCipher) -> MsalClientCredentialLifespanResource:
    return MsalClientCredentialLifespanResource(
        name="downstream-api",
        client_id="client-id",
        client_credential="secret",
        authority="https://login.microsoftonline.com/tenant-id",
        redis_client_name="cache",
        cipher=cipher,
    )


def test_default_msal_http_session_retries_on_429_and_5xx():
    session = default_msal_http_session()

    adapter = session.get_adapter("https://login.microsoftonline.com")

    assert adapter.max_retries.total == 3
    assert 429 in adapter.max_retries.status_forcelist
    assert 500 in adapter.max_retries.status_forcelist


@pytest.mark.asyncio
async def test_get_msal_app_token_returns_access_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_for_client",
        lambda self, scopes: {"access_token": "fake-token", "token_type": "Bearer"},
    )

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"), _make_resource(cipher)
    )

    async with lifespan(app):
        token = await get_msal_app_token("downstream-api", scopes=["api://xxx/.default"])(
            _make_request(app)
        )

    assert token == "fake-token"


@pytest.mark.asyncio
async def test_get_msal_app_token_raises_runtime_error_when_missing():
    app = Starlette()

    with pytest.raises(RuntimeError, match="downstream-api"):
        await get_msal_app_token("downstream-api", scopes=["scope"])(_make_request(app))


@pytest.mark.asyncio
async def test_get_msal_app_token_raises_msal_token_error_on_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_for_client",
        lambda self, scopes: {
            "error": "invalid_client",
            "error_description": "AADSTS7000215: Invalid client secret",
            "error_codes": [7000215],
        },
    )

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"), _make_resource(cipher)
    )

    async with lifespan(app):
        with pytest.raises(MsalTokenError) as exc_info:
            await get_msal_app_token("downstream-api", scopes=["api://xxx/.default"])(
                _make_request(app)
            )

    assert exc_info.value.error == "invalid_client"
    assert exc_info.value.error_codes == [7000215]


@pytest.mark.asyncio
async def test_get_msal_app_token_raises_claims_challenge_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_for_client",
        lambda self, scopes: {
            "error": "interaction_required",
            "error_description": "MFA required",
            "claims": '{"access_token":{}}',
        },
    )

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"), _make_resource(cipher)
    )

    async with lifespan(app):
        with pytest.raises(MsalClaimsChallengeError) as exc_info:
            await get_msal_app_token("downstream-api", scopes=["api://xxx/.default"])(
                _make_request(app)
            )

    assert exc_info.value.claims == '{"access_token":{}}'


@pytest.mark.asyncio
async def test_token_cache_is_persisted_to_redis_encrypted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)

    def fake_acquire(self, scopes):
        # MSAL内部でトークンが追加された状態を模倣する
        self.token_cache.has_state_changed = True
        return {"access_token": "fake-token"}

    monkeypatch.setattr(ConfidentialClientApplication, "acquire_token_for_client", fake_acquire)

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"), _make_resource(cipher)
    )

    async with lifespan(app):
        await get_msal_app_token("downstream-api", scopes=["api://xxx/.default"])(
            _make_request(app)
        )
        redis = get_redis_client("cache")(_make_request(app))
        raw = await redis.get("msal:app_cache:downstream-api")

    assert raw is not None
    assert b"fake-token" not in raw
    assert cipher.decrypt(raw)  # 復号できる(=正しい鍵で暗号化されている)


@pytest.mark.asyncio
async def test_existing_cache_is_loaded_from_redis_on_startup(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    seed_serialized = SerializableTokenCache().serialize()

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")

    seed_lifespan = create_lifespan(RedisLifespanResource("cache", "redis://localhost:6379/0"))
    async with seed_lifespan(app):
        redis = get_redis_client("cache")(_make_request(app))
        await redis.set(
            "msal:app_cache:downstream-api", cipher.encrypt(seed_serialized.encode())
        )

    loaded: dict[str, str] = {}

    def fake_acquire(self, scopes):
        loaded["value"] = self.token_cache.serialize()
        return {"access_token": "fake-token"}

    monkeypatch.setattr(ConfidentialClientApplication, "acquire_token_for_client", fake_acquire)

    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"), _make_resource(cipher)
    )

    async with lifespan(app):
        await get_msal_app_token("downstream-api", scopes=["scope"])(_make_request(app))

    assert loaded["value"] == seed_serialized


@pytest.mark.asyncio
async def test_executor_is_shutdown_on_lifespan_exit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"), _make_resource(cipher)
    )

    handles = {}
    async with lifespan(app):
        handles.update(app.state.msal_client_credential_handles)

    with pytest.raises(RuntimeError, match="cannot schedule new futures after shutdown"):
        handles["downstream-api"].executor.submit(lambda: None)
```

- [ ] **Step 4: テストを実行し失敗を確認**

Run: `cd core-toolkit && uv run pytest tests/test_msal_client_credential_lifespan.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'core_toolkit.msal_lifespan'`）

- [ ] **Step 5: 実装を書く**

`core-toolkit/src/core_toolkit/msal_lifespan.py`を作成する。

```python
"""Starlette/ASGIアプリ向けMSAL（Microsoft Authentication Library）
lifespan管理。

名前付きで複数のConfidentialClientApplicationを同時に登録・取得できる。
MSALは同期API（requestsベース）のため、名前付きリソースごとに専用の
ThreadPoolExecutorを内蔵し、呼び出しをそこへ隔離する。

利用には ``core-toolkit[msal]`` extraのインストールが必要。
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import requests
from msal import ConfidentialClientApplication, SerializableTokenCache
from requests.adapters import HTTPAdapter, Retry
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import LifespanResource
from core_toolkit.msal_errors import MsalClaimsChallengeError, MsalTokenError
from core_toolkit.redis_lifespan import get_redis_client
from core_toolkit.token_cache_cipher import TokenCacheCipher


def default_msal_http_session() -> requests.Session:
    """3xx/5xx/429にリトライするrequests.Sessionを生成する。

    ``http_client=``へ自前のSessionを渡すと、MSALの``verify``/``proxies``/
    ``timeout``引数は無視される（内部Sessionにしか効かない）ため、
    リトライ設定込みでここに持たせる。
    """
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.1,
        status_forcelist=[429, 500, 501, 502, 503, 504],
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def _raise_for_result(result: dict[str, Any]) -> None:
    if "access_token" in result:
        return
    if "claims" in result:
        raise MsalClaimsChallengeError(
            result.get("error"),
            result.get("error_description"),
            result["claims"],
            result.get("error_codes"),
        )
    raise MsalTokenError(
        result.get("error"), result.get("error_description"), result.get("error_codes")
    )


@dataclass
class MsalClientCredentialHandle:
    app: ConfidentialClientApplication
    cache: SerializableTokenCache
    executor: ThreadPoolExecutor
    cipher: TokenCacheCipher
    redis_client_name: str
    cache_key: str


class MsalClientCredentialLifespanResource(LifespanResource):
    """Client Credentials用。アプリ単位のConfidentialClientApplicationを
    lifespanで1つ持ち、Redisにアプリ単位のトークンキャッシュを永続化する。

    ``context()``内で名前付きRedisクライアントを取得するため、
    ``create_lifespan(...)``へ渡す際は対応する``RedisLifespanResource``を
    先に登録しておく必要がある。

    Args:
        name: このクライアントを識別する名前。``get_msal_app_token``で
            同じ名前を指定して取得する。
        client_id: アプリ（クライアント）ID。
        client_credential: クライアントシークレット文字列。
        authority: 認証機関URL（例: ``https://login.microsoftonline.com/<tenant>``）。
        redis_client_name: トークンキャッシュ永続化に使うRedisクライアント名
            （``RedisLifespanResource(name=...)``で登録済みのもの）。
        cipher: トークンキャッシュの暗号化実装。
        max_workers: MSAL呼び出し専用ThreadPoolExecutorのワーカー数。
        http_client_factory: ``requests.Session``を生成するファクトリ。
            省略時は``default_msal_http_session``を使う。
        app_kwargs: ``ConfidentialClientApplication``にそのまま渡す追加引数。
    """

    def __init__(
        self,
        name: str,
        client_id: str,
        client_credential: str,
        authority: str,
        redis_client_name: str,
        cipher: TokenCacheCipher,
        max_workers: int = 4,
        http_client_factory: Any = None,
        **app_kwargs: Any,
    ) -> None:
        self._name = name
        self._client_id = client_id
        self._client_credential = client_credential
        self._authority = authority
        self._redis_client_name = redis_client_name
        self._cipher = cipher
        self._max_workers = max_workers
        self._http_client_factory = http_client_factory or default_msal_http_session
        self._app_kwargs = app_kwargs

    def _cache_key(self) -> str:
        return f"msal:app_cache:{self._name}"

    @asynccontextmanager
    async def context(self, app: Starlette):
        redis = get_redis_client(self._redis_client_name)(
            Request(scope={"type": "http", "app": app})
        )
        session = self._http_client_factory()
        executor = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix=f"msal-{self._name}"
        )
        cache = SerializableTokenCache()
        raw = await redis.get(self._cache_key())
        if raw:
            cache.deserialize(self._cipher.decrypt(raw).decode())

        client = ConfidentialClientApplication(
            client_id=self._client_id,
            client_credential=self._client_credential,
            authority=self._authority,
            token_cache=cache,
            http_client=session,
            **self._app_kwargs,
        )

        handle = MsalClientCredentialHandle(
            app=client,
            cache=cache,
            executor=executor,
            cipher=self._cipher,
            redis_client_name=self._redis_client_name,
            cache_key=self._cache_key(),
        )
        handles: dict[str, MsalClientCredentialHandle] = getattr(
            app.state, "msal_client_credential_handles", {}
        )
        app.state.msal_client_credential_handles = {**handles, self._name: handle}

        try:
            yield client
        finally:
            executor.shutdown(wait=True)
            session.close()


def get_msal_app_token(name: str, scopes: list[str]):
    """名前を指定してClient Credentialsのアクセストークンを取得する
    provider関数を生成する。

    Args:
        name: ``MsalClientCredentialLifespanResource(name=...)``に登録した名前。
        scopes: 要求するスコープ（例: ``["api://xxx/.default"]``）。

    Returns:
        ``request: Request``を1引数に取る非同期provider関数。対応する
        リソースが登録されていない場合は``RuntimeError``、トークン取得に
        失敗した場合は``MsalTokenError``（Conditional Access等の場合は
        ``MsalClaimsChallengeError``）を送出する。
    """

    async def get_token(request: Request) -> str:
        handles: dict[str, MsalClientCredentialHandle] = getattr(
            request.app.state, "msal_client_credential_handles", {}
        )
        if name not in handles:
            raise RuntimeError(
                f"msal_client_credential_handles['{name}'] is not set. "
                f"Did you forget to register "
                f"MsalClientCredentialLifespanResource(name='{name}', ...) "
                "in create_lifespan(...)?"
            )
        handle = handles[name]

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            handle.executor, lambda: handle.app.acquire_token_for_client(scopes=scopes)
        )

        if handle.cache.has_state_changed:
            payload = handle.cipher.encrypt(handle.cache.serialize().encode())
            redis = get_redis_client(handle.redis_client_name)(request)
            await redis.set(handle.cache_key, payload)

        _raise_for_result(result)
        return result["access_token"]

    get_token.__name__ = f"get_msal_app_token_{name}"
    return get_token
```

- [ ] **Step 6: テストを実行し成功を確認**

Run: `cd core-toolkit && uv run pytest tests/test_msal_client_credential_lifespan.py -v`
Expected: PASS（8件全て）

- [ ] **Step 7: lint/formatを実行**

Run: `cd core-toolkit && uv run ruff format src/ tests/ && uv run ruff check --fix src/ tests/`
Expected: エラーなし

- [ ] **Step 8: コミット**

```bash
git add core-toolkit/pyproject.toml core-toolkit/uv.lock core-toolkit/src/core_toolkit/msal_lifespan.py core-toolkit/tests/test_msal_client_credential_lifespan.py
git commit -m "$(cat <<'EOF'
feat(core-toolkit): add Client Credentials MSAL lifespan

MsalClientCredentialLifespanResource/get_msal_app_tokenを追加。専用
ThreadPoolExecutorでMSALの同期呼び出しを隔離し、アプリ単位のトークン
キャッシュをJWE暗号化してRedisへ永続化する。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: core-toolkit — `msal_lifespan.py`（OBO、追記）

**Files:**
- Modify: `core-toolkit/src/core_toolkit/msal_lifespan.py`
- Test: `core-toolkit/tests/test_msal_obo_lifespan.py`

**Interfaces:**
- Consumes: 同ファイル内の`default_msal_http_session`/`_raise_for_result`（Task 3）、`core_toolkit.redis_lifespan.get_redis_client`（既存）、`core_toolkit.token_cache_cipher.TokenCacheCipher`（Task 2）
- Produces:
  - `MsalOboConfig`（dataclass、`client_id`/`client_credential`/`authority`/`session`/`http_cache`/`executor`/`redis_client_name`/`cipher`/`cache_ttl`属性）
  - `MsalOboLifespanResource(name: str, client_id: str, client_credential: str, authority: str, redis_client_name: str, cipher: TokenCacheCipher, max_workers: int = 4, cache_ttl: int = 3600, http_client_factory: Any = None)` — `LifespanResource`のサブクラス
  - `get_msal_obo_token(name: str, scopes: list[str])` — `request: Request, user_assertion: str, user_id: str`を引数に取る非同期provider関数を返す
  - Task 5（fastapi-toolkit）、Task 7（fastmcp-toolkit）が使う

**注意:** Task 3の`MsalClientCredentialLifespanResource`と同様、`create_lifespan(...)`に渡す際は対応する`RedisLifespanResource`を先に登録する必要がある。

- [ ] **Step 1: 失敗するテストを書く**

`core-toolkit/tests/test_msal_obo_lifespan.py`を作成する。

```python
"""msal_lifespanのOBO部分の統合テスト。

実際のAADへは接続せず、``ConfidentialClientApplication.acquire_token_on_behalf_of``
をmonkeypatchでスタブする。
"""

import pytest
from fakeredis.aioredis import FakeRedis
from msal import ConfidentialClientApplication
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import create_lifespan
from core_toolkit.msal_errors import MsalClaimsChallengeError, MsalTokenError
from core_toolkit.msal_lifespan import MsalOboLifespanResource, get_msal_obo_token
from core_toolkit.redis_lifespan import RedisLifespanResource, get_redis_client
from core_toolkit.token_cache_cipher import JweTokenCacheCipher


def _key(seed: int) -> bytes:
    return bytes([seed]) * 32


def _make_request(app: Starlette) -> Request:
    return Request({"type": "http", "app": app})


def _make_resource(cipher: JweTokenCacheCipher) -> MsalOboLifespanResource:
    return MsalOboLifespanResource(
        name="graph",
        client_id="client-id",
        client_credential="secret",
        authority="https://login.microsoftonline.com/tenant-id",
        redis_client_name="cache",
        cipher=cipher,
    )


@pytest.mark.asyncio
async def test_get_msal_obo_token_returns_access_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_on_behalf_of",
        lambda self, user_assertion, scopes: {"access_token": "fake-token"},
    )

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"), _make_resource(cipher)
    )

    async with lifespan(app):
        token = await get_msal_obo_token("graph", scopes=["User.Read"])(
            _make_request(app), user_assertion="user-jwt", user_id="user-1"
        )

    assert token == "fake-token"


@pytest.mark.asyncio
async def test_get_msal_obo_token_raises_runtime_error_when_missing():
    app = Starlette()

    with pytest.raises(RuntimeError, match="graph"):
        await get_msal_obo_token("graph", scopes=["User.Read"])(
            _make_request(app), user_assertion="user-jwt", user_id="user-1"
        )


@pytest.mark.asyncio
async def test_get_msal_obo_token_raises_msal_token_error_on_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_on_behalf_of",
        lambda self, user_assertion, scopes: {
            "error": "invalid_grant",
            "error_description": "AADSTS50013: Assertion is invalid",
            "error_codes": [50013],
        },
    )

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"), _make_resource(cipher)
    )

    async with lifespan(app):
        with pytest.raises(MsalTokenError) as exc_info:
            await get_msal_obo_token("graph", scopes=["User.Read"])(
                _make_request(app), user_assertion="user-jwt", user_id="user-1"
            )

    assert exc_info.value.error == "invalid_grant"


@pytest.mark.asyncio
async def test_get_msal_obo_token_raises_claims_challenge_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_on_behalf_of",
        lambda self, user_assertion, scopes: {
            "error": "interaction_required",
            "error_description": "MFA required",
            "claims": '{"access_token":{}}',
        },
    )

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"), _make_resource(cipher)
    )

    async with lifespan(app):
        with pytest.raises(MsalClaimsChallengeError) as exc_info:
            await get_msal_obo_token("graph", scopes=["User.Read"])(
                _make_request(app), user_assertion="user-jwt", user_id="user-1"
            )

    assert exc_info.value.claims == '{"access_token":{}}'


@pytest.mark.asyncio
async def test_different_users_get_independent_cache_keys(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)

    def fake_acquire(self, user_assertion, scopes):
        self.token_cache.has_state_changed = True
        return {"access_token": f"token-for-{user_assertion}"}

    monkeypatch.setattr(ConfidentialClientApplication, "acquire_token_on_behalf_of", fake_acquire)

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"), _make_resource(cipher)
    )

    async with lifespan(app):
        provider = get_msal_obo_token("graph", scopes=["User.Read"])
        token1 = await provider(_make_request(app), user_assertion="jwt-1", user_id="user-1")
        token2 = await provider(_make_request(app), user_assertion="jwt-2", user_id="user-2")

        redis = get_redis_client("cache")(_make_request(app))
        raw1 = await redis.get("msal:obo_cache:graph:user-1")
        raw2 = await redis.get("msal:obo_cache:graph:user-2")

    assert token1 == "token-for-jwt-1"
    assert token2 == "token-for-jwt-2"
    assert raw1 is not None
    assert raw2 is not None
    assert raw1 != raw2


@pytest.mark.asyncio
async def test_token_cache_is_persisted_with_ttl(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)

    def fake_acquire(self, user_assertion, scopes):
        self.token_cache.has_state_changed = True
        return {"access_token": "fake-token"}

    monkeypatch.setattr(ConfidentialClientApplication, "acquire_token_on_behalf_of", fake_acquire)

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"), _make_resource(cipher)
    )

    async with lifespan(app):
        await get_msal_obo_token("graph", scopes=["User.Read"])(
            _make_request(app), user_assertion="jwt-1", user_id="user-1"
        )
        redis = get_redis_client("cache")(_make_request(app))
        ttl = await redis.ttl("msal:obo_cache:graph:user-1")

    assert ttl > 0


@pytest.mark.asyncio
async def test_executor_is_shutdown_on_lifespan_exit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"), _make_resource(cipher)
    )

    configs = {}
    async with lifespan(app):
        configs.update(app.state.msal_obo_configs)

    with pytest.raises(RuntimeError, match="cannot schedule new futures after shutdown"):
        configs["graph"].executor.submit(lambda: None)
```

- [ ] **Step 2: テストを実行し失敗を確認**

Run: `cd core-toolkit && uv run pytest tests/test_msal_obo_lifespan.py -v`
Expected: FAIL（`ImportError: cannot import name 'MsalOboLifespanResource' from 'core_toolkit.msal_lifespan'`）

- [ ] **Step 3: 実装を追記**

`core-toolkit/src/core_toolkit/msal_lifespan.py`の末尾に以下を追記する。

```python
@dataclass
class MsalOboConfig:
    client_id: str
    client_credential: str
    authority: str
    session: requests.Session
    http_cache: dict
    executor: ThreadPoolExecutor
    redis_client_name: str
    cipher: TokenCacheCipher
    cache_ttl: int


class MsalOboLifespanResource(LifespanResource):
    """OBO用。ConfidentialClientApplicationインスタンス自体はリクエストごと
    に生成するため、ここではSession/http_cache/executorのみをlifespanで
    共有する。

    ``token_cache``がインスタンス属性であり、OBOはリクエストごとに異なる
    ユーザーのキャッシュを使う。共有インスタンスの``token_cache``を都度
    差し替えると並行リクエスト間でキャッシュを取り違えるレースコンディション
    になるため、``ConfidentialClientApplication``自体はリクエストごとに
    ``get_msal_obo_token``側で生成する。

    Args:
        name: このコンフィグを識別する名前。``get_msal_obo_token``で
            同じ名前を指定して取得する。
        client_id: アプリ（クライアント）ID。
        client_credential: クライアントシークレット文字列。
        authority: 認証機関URL。
        redis_client_name: トークンキャッシュ永続化に使うRedisクライアント名。
        cipher: トークンキャッシュの暗号化実装。
        max_workers: MSAL呼び出し専用ThreadPoolExecutorのワーカー数。
        cache_ttl: Redisに保存するユーザー単位キャッシュのTTL（秒）。
        http_client_factory: ``requests.Session``を生成するファクトリ。
    """

    def __init__(
        self,
        name: str,
        client_id: str,
        client_credential: str,
        authority: str,
        redis_client_name: str,
        cipher: TokenCacheCipher,
        max_workers: int = 4,
        cache_ttl: int = 3600,
        http_client_factory: Any = None,
    ) -> None:
        self._name = name
        self._client_id = client_id
        self._client_credential = client_credential
        self._authority = authority
        self._redis_client_name = redis_client_name
        self._cipher = cipher
        self._max_workers = max_workers
        self._cache_ttl = cache_ttl
        self._http_client_factory = http_client_factory or default_msal_http_session

    @asynccontextmanager
    async def context(self, app: Starlette):
        session = self._http_client_factory()
        executor = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix=f"msal-obo-{self._name}"
        )
        config = MsalOboConfig(
            client_id=self._client_id,
            client_credential=self._client_credential,
            authority=self._authority,
            session=session,
            http_cache={},
            executor=executor,
            redis_client_name=self._redis_client_name,
            cipher=self._cipher,
            cache_ttl=self._cache_ttl,
        )
        configs: dict[str, MsalOboConfig] = getattr(app.state, "msal_obo_configs", {})
        app.state.msal_obo_configs = {**configs, self._name: config}

        try:
            yield config
        finally:
            executor.shutdown(wait=True)
            session.close()


def get_msal_obo_token(name: str, scopes: list[str]):
    """名前を指定してOBOのアクセストークンを取得するprovider関数を生成する。

    ``user_assertion``（呼び出し元ユーザーのアクセストークン文字列）と
    ``user_id``（トークンキャッシュのキーに使うユーザー識別子）は、
    アプリ側がJWTから取り出して渡す。

    Args:
        name: ``MsalOboLifespanResource(name=...)``に登録した名前。
        scopes: 要求するスコープ。

    Returns:
        ``request: Request``・``user_assertion: str``・``user_id: str``を
        引数に取る非同期provider関数。
    """

    async def get_token(request: Request, user_assertion: str, user_id: str) -> str:
        configs: dict[str, MsalOboConfig] = getattr(request.app.state, "msal_obo_configs", {})
        if name not in configs:
            raise RuntimeError(
                f"msal_obo_configs['{name}'] is not set. "
                f"Did you forget to register MsalOboLifespanResource(name='{name}', ...) "
                "in create_lifespan(...)?"
            )
        config = configs[name]

        redis = get_redis_client(config.redis_client_name)(request)
        cache_key = f"msal:obo_cache:{name}:{user_id}"

        cache = SerializableTokenCache()
        raw = await redis.get(cache_key)
        if raw:
            cache.deserialize(config.cipher.decrypt(raw).decode())

        def call_msal() -> dict[str, Any]:
            client = ConfidentialClientApplication(
                client_id=config.client_id,
                client_credential=config.client_credential,
                authority=config.authority,
                token_cache=cache,
                http_client=config.session,
                http_cache=config.http_cache,
            )
            return client.acquire_token_on_behalf_of(user_assertion, scopes=scopes)

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(config.executor, call_msal)

        if cache.has_state_changed:
            payload = config.cipher.encrypt(cache.serialize().encode())
            await redis.set(cache_key, payload, ex=config.cache_ttl)

        _raise_for_result(result)
        return result["access_token"]

    return get_token
```

- [ ] **Step 4: テストを実行し成功を確認**

Run: `cd core-toolkit && uv run pytest tests/test_msal_obo_lifespan.py -v`
Expected: PASS（7件全て）

- [ ] **Step 5: 既存テストが壊れていないことを確認**

Run: `cd core-toolkit && uv run pytest -v`
Expected: 全件PASS（Task 1〜3のテスト含む）

- [ ] **Step 6: lint/formatを実行**

Run: `cd core-toolkit && uv run ruff format src/ tests/ && uv run ruff check --fix src/ tests/`
Expected: エラーなし

- [ ] **Step 7: コミット**

```bash
git add core-toolkit/src/core_toolkit/msal_lifespan.py core-toolkit/tests/test_msal_obo_lifespan.py
git commit -m "$(cat <<'EOF'
feat(core-toolkit): add OBO MSAL lifespan

MsalOboLifespanResource/get_msal_obo_tokenを追加。token_cacheがユーザー
ごとに異なるため、共有するのはSession/http_cache/executorのみとし、
ConfidentialClientApplication自体はリクエストごとに生成して並行安全性を
確保する。ユーザー単位のトークンキャッシュはTTL付きでRedisへ永続化する。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: fastapi-toolkit — `msal_lifespan.py` 薄いラッパー

**Files:**
- Modify: `fastapi-toolkit/pyproject.toml`
- Create: `fastapi-toolkit/src/fastapi_toolkit/msal_lifespan.py`
- Test: `fastapi-toolkit/tests/test_msal_lifespan.py`

**Interfaces:**
- Consumes: Task 1〜4の`core_toolkit.msal_errors`/`core_toolkit.msal_lifespan`/`core_toolkit.token_cache_cipher`（シグネチャはTask 1〜4のProducesを参照）、`fastapi_toolkit.redis_lifespan.RedisLifespanResource`（既存）
- Produces: `fastapi_toolkit.msal_lifespan`配下に`MsalClientCredentialLifespanResource`/`MsalOboLifespanResource`/`get_msal_app_token`/`get_msal_obo_token`/`default_msal_http_session`/`TokenCacheCipher`/`JweTokenCacheCipher`/`default_token_cache_cipher`/`MsalTokenError`/`MsalClaimsChallengeError`をそのまま再エクスポート（シグネチャ変更なし）。他タスクからは参照されない（末端）

- [ ] **Step 1: pyproject.tomlに`msal` extraを追加**

`fastapi-toolkit/pyproject.toml`の`[project.optional-dependencies]`に、既存の`redis`の直後へ以下を追加する。

```toml
msal = [
  "core-toolkit[msal]",
  "core-toolkit[redis]",
]
```

- [ ] **Step 2: 依存をインストール**

Run: `cd fastapi-toolkit && uv sync --all-extras`
Expected: core-toolkitの`msal`/`redis`エクストラ経由で`msal`/`joserfc`/`redis`が解決される。

- [ ] **Step 3: 失敗するテストを書く**

`fastapi-toolkit/tests/test_msal_lifespan.py`を作成する。

```python
"""fastapi_toolkit.msal_lifespanの統合テスト。

MsalClientCredentialLifespanResource/MsalOboLifespanResource自体の
起動・終了処理・トークン取得ロジックはcore-toolkit側で検証済みのため、
ここではFastAPI固有の部分――``Annotated[T, Depends(...)]``や``Request``
引数が実際のエンドポイントで解決されること――だけを検証する。
"""

from typing import Annotated

import pytest
from fakeredis.aioredis import FakeRedis
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient
from msal import ConfidentialClientApplication

from fastapi_toolkit.lifespan import create_lifespan
from fastapi_toolkit.msal_lifespan import (
    MsalClientCredentialLifespanResource,
    MsalOboLifespanResource,
    default_token_cache_cipher,
    get_msal_app_token,
    get_msal_obo_token,
)
from fastapi_toolkit.redis_lifespan import RedisLifespanResource


def _key(seed: int) -> bytes:
    return bytes([seed]) * 32


@pytest.mark.asyncio
async def test_msal_app_token_resolves_via_depends(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_for_client",
        lambda self, scopes: {"access_token": "fake-token"},
    )

    app = FastAPI()
    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")

    DownstreamApiToken = Annotated[
        str, Depends(get_msal_app_token("downstream-api", scopes=["api://xxx/.default"]))
    ]

    @app.get("/proxy")
    async def proxy(token: DownstreamApiToken) -> dict[str, str]:
        return {"token": token}

    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"),
        MsalClientCredentialLifespanResource(
            name="downstream-api",
            client_id="client-id",
            client_credential="secret",
            authority="https://login.microsoftonline.com/tenant-id",
            redis_client_name="cache",
            cipher=cipher,
        ),
    )

    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/proxy")

    assert resp.status_code == 200
    assert resp.json() == {"token": "fake-token"}


@pytest.mark.asyncio
async def test_msal_obo_token_resolves_via_explicit_call(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_on_behalf_of",
        lambda self, user_assertion, scopes: {"access_token": f"obo-{user_assertion}"},
    )

    app = FastAPI()
    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")

    @app.get("/graph")
    async def call_graph(request: Request) -> dict[str, str]:
        token = await get_msal_obo_token("graph", scopes=["User.Read"])(
            request, user_assertion="user-jwt", user_id="user-1"
        )
        return {"token": token}

    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"),
        MsalOboLifespanResource(
            name="graph",
            client_id="client-id",
            client_credential="secret",
            authority="https://login.microsoftonline.com/tenant-id",
            redis_client_name="cache",
            cipher=cipher,
        ),
    )

    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/graph")

    assert resp.status_code == 200
    assert resp.json() == {"token": "obo-user-jwt"}
```

- [ ] **Step 4: テストを実行し失敗を確認**

Run: `cd fastapi-toolkit && uv run pytest tests/test_msal_lifespan.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'fastapi_toolkit.msal_lifespan'`）

- [ ] **Step 5: 実装を書く**

`fastapi-toolkit/src/fastapi_toolkit/msal_lifespan.py`を作成する。

```python
"""MSAL lifespanリソースのFastAPI向け薄いラッパー。

実装はFastAPIに依存せず``core_toolkit.msal_lifespan``にある。このモジュール
は再エクスポートするだけで、名前ごとの型エイリアス
（``Annotated[str, Depends(get_msal_app_token("downstream-api", scopes=[...]))]``）
はアプリ側が名前とscopesを指定して定義する。

利用には ``fastapi-toolkit[msal]`` extraのインストールが必要。
"""

from core_toolkit.msal_errors import MsalClaimsChallengeError, MsalTokenError
from core_toolkit.msal_lifespan import (
    MsalClientCredentialLifespanResource,
    MsalOboLifespanResource,
    default_msal_http_session,
    get_msal_app_token,
    get_msal_obo_token,
)
from core_toolkit.token_cache_cipher import (
    JweTokenCacheCipher,
    TokenCacheCipher,
    default_token_cache_cipher,
)

__all__ = [
    "MsalClaimsChallengeError",
    "MsalClientCredentialLifespanResource",
    "MsalOboLifespanResource",
    "MsalTokenError",
    "JweTokenCacheCipher",
    "TokenCacheCipher",
    "default_msal_http_session",
    "default_token_cache_cipher",
    "get_msal_app_token",
    "get_msal_obo_token",
]
```

- [ ] **Step 6: テストを実行し成功を確認**

Run: `cd fastapi-toolkit && uv run pytest tests/test_msal_lifespan.py -v`
Expected: PASS（2件全て）

- [ ] **Step 7: 既存テストが壊れていないことを確認**

Run: `cd fastapi-toolkit && uv run pytest -v`
Expected: 全件PASS

- [ ] **Step 8: lint/formatを実行**

Run: `cd fastapi-toolkit && uv run ruff format src/ tests/ && uv run ruff check --fix src/ tests/`
Expected: エラーなし

- [ ] **Step 9: コミット**

```bash
git add fastapi-toolkit/pyproject.toml fastapi-toolkit/uv.lock fastapi-toolkit/src/fastapi_toolkit/msal_lifespan.py fastapi-toolkit/tests/test_msal_lifespan.py
git commit -m "$(cat <<'EOF'
feat(fastapi-toolkit): re-export MSAL lifespan (Client Credentials/OBO)

core_toolkit.msal_lifespan/msal_errors/token_cache_cipherをそのまま
再エクスポートする薄いラッパーを追加。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: fastmcp-toolkit — `msal_lifespan.py`（Client Credentials）

**重要な設計制約（実装計画作成時に判明）:** FastMCPの`ComposedLifespan`（`fastmcp/server/lifespan.py`）は、`|`で合成した各lifespan関数を`server: FastMCP`のみを引数に**互いに独立して**実行し、結果は`__call__`の終わりで`{**left_result, **right_result}`とマージされるだけである。合成される個々のlifespan関数は、他のlifespanが生成した値へ起動時にアクセスする手段を持たない。そのため、core-toolkit版（Task 3）のようにMSAL lifespanの起動処理内でRedisクライアントから直接トークンキャッシュをロードすることはできない。**Redisからのロードは、`CurrentContext()`が使える初回のツール呼び出し時に遅延実行する**設計にする（`MsalClientCredentialHandle.cache_loaded`フラグで1回だけロードする）。

**Files:**
- Modify: `fastmcp-toolkit/pyproject.toml`
- Create: `fastmcp-toolkit/src/fastmcp_toolkit/msal_lifespan.py`
- Test: `fastmcp-toolkit/tests/test_msal_client_credential_lifespan.py`

**Interfaces:**
- Consumes:
  - `fastmcp.server.dependencies.CurrentContext`/`fastmcp.server.lifespan.lifespan`/`Lifespan`（既存）
  - `uncalled_for.Depends`（既存）
  - `fastmcp_toolkit.redis_lifespan._get_redis_client(name) -> Callable[[Context], Redis]`（既存、パッケージ内private関数）
  - `core_toolkit.msal_errors.MsalTokenError`/`MsalClaimsChallengeError`（Task 1）
  - `core_toolkit.msal_lifespan.default_msal_http_session`（Task 3）
  - `core_toolkit.token_cache_cipher.TokenCacheCipher`（Task 2）
- Produces:
  - `msal_client_credential_lifespan(name: str, client_id: str, client_credential: str, authority: str, redis_client_name: str, cipher: TokenCacheCipher, max_workers: int = 4, http_client_factory: Any = None, **app_kwargs: Any) -> Lifespan`
  - `CurrentMsalAppToken(name: str, scopes: list[str]) -> str`
  - Task 7（同ファイルにOBO部分を追記）が使う

- [ ] **Step 1: pyproject.tomlに`msal` extraを追加**

`fastmcp-toolkit/pyproject.toml`の`[project.optional-dependencies]`に、既存の`redis`の直後へ以下を追加する。

```toml
msal = [
    "msal>=1.30",
    "joserfc>=1.0",
]
```

- [ ] **Step 2: 依存をインストール**

Run: `cd fastmcp-toolkit && uv sync --all-extras`
Expected: `msal`/`joserfc`が解決されてインストールされる。

- [ ] **Step 3: 失敗するテストを書く**

`fastmcp-toolkit/tests/test_msal_client_credential_lifespan.py`を作成する。

```python
"""fastmcp_toolkit.msal_lifespanのClient Credentials部分の統合テスト。"""

import pytest
from fakeredis.aioredis import FakeRedis
from fastmcp import Client, FastMCP
from msal import ConfidentialClientApplication

from core_toolkit.token_cache_cipher import default_token_cache_cipher
from fastmcp_toolkit.msal_lifespan import CurrentMsalAppToken, msal_client_credential_lifespan
from fastmcp_toolkit.redis_lifespan import redis_lifespan


def _key(seed: int) -> bytes:
    return bytes([seed]) * 32


def _make_app(cipher):
    return FastMCP(
        "test",
        lifespan=redis_lifespan("cache", "redis://localhost:6379/0")
        | msal_client_credential_lifespan(
            "downstream-api",
            client_id="client-id",
            client_credential="secret",
            authority="https://login.microsoftonline.com/tenant-id",
            redis_client_name="cache",
            cipher=cipher,
        ),
    )


@pytest.mark.asyncio
async def test_tool_resolves_msal_app_token_via_depends(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_for_client",
        lambda self, scopes: {"access_token": "fake-token"},
    )

    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")
    app = _make_app(cipher)

    @app.tool
    async def call_downstream(
        token: str = CurrentMsalAppToken("downstream-api", scopes=["api://xxx/.default"]),
    ) -> str:
        return token

    async with Client(app) as client:
        result = await client.call_tool("call_downstream", {})

    assert result.data == "fake-token"


@pytest.mark.asyncio
async def test_tool_raises_when_lifespan_not_registered():
    app = FastMCP("test")

    @app.tool
    async def call_downstream(
        token: str = CurrentMsalAppToken("downstream-api", scopes=["scope"]),
    ) -> str:
        return token

    async with Client(app) as client:
        # RPC境界を越えるとサーバー側の詳細な例外メッセージは失われ、
        # クライアントに届くのはDepends解決対象のパラメータ名を含む
        # "Failed to resolve dependency '<param>' for <fn>" のみになる
        # (fastmcp-toolkitのdb_lifespanで確認済み、a9b2fb9参照)。
        with pytest.raises(Exception, match=r"Failed to resolve dependency 'token'"):
            await client.call_tool("call_downstream", {})


@pytest.mark.asyncio
async def test_tool_raises_on_acquire_failure(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_for_client",
        lambda self, scopes: {"error": "invalid_client", "error_description": "bad secret"},
    )

    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")
    app = _make_app(cipher)

    @app.tool
    async def call_downstream(
        token: str = CurrentMsalAppToken("downstream-api", scopes=["scope"]),
    ) -> str:
        return token

    async with Client(app) as client:
        with pytest.raises(Exception, match=r"Failed to resolve dependency 'token'"):
            await client.call_tool("call_downstream", {})


@pytest.mark.asyncio
async def test_repeated_calls_reuse_the_same_cache_instance(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)

    call_count = {"n": 0}

    def fake_acquire(self, scopes):
        call_count["n"] += 1
        self.token_cache.has_state_changed = True
        return {"access_token": f"token-{call_count['n']}"}

    monkeypatch.setattr(ConfidentialClientApplication, "acquire_token_for_client", fake_acquire)

    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")
    app = _make_app(cipher)

    @app.tool
    async def call_downstream(
        token: str = CurrentMsalAppToken("downstream-api", scopes=["api://xxx/.default"]),
    ) -> str:
        return token

    async with Client(app) as client:
        first = await client.call_tool("call_downstream", {})
        second = await client.call_tool("call_downstream", {})

    assert first.data == "token-1"
    assert second.data == "token-2"
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_executor_is_shutdown_on_lifespan_exit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    from fastmcp_toolkit.msal_lifespan import _lifespan_key

    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")
    composed = redis_lifespan("cache", "redis://localhost:6379/0") | msal_client_credential_lifespan(
        "downstream-api",
        client_id="client-id",
        client_credential="secret",
        authority="https://login.microsoftonline.com/tenant-id",
        redis_client_name="cache",
        cipher=cipher,
    )
    app = FastMCP("test", lifespan=composed)

    holder = {}
    async with composed(app) as ctx:
        holder["handle"] = ctx[_lifespan_key("downstream-api")]

    with pytest.raises(RuntimeError, match="cannot schedule new futures after shutdown"):
        holder["handle"].executor.submit(lambda: None)
```

- [ ] **Step 4: テストを実行し失敗を確認**

Run: `cd fastmcp-toolkit && uv run pytest tests/test_msal_client_credential_lifespan.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'fastmcp_toolkit.msal_lifespan'`）

- [ ] **Step 5: 実装を書く**

`fastmcp-toolkit/src/fastmcp_toolkit/msal_lifespan.py`を作成する。

```python
"""FastMCPサーバー向けMSAL lifespan統合（Client Credentials）。

Starlette向けの``core_toolkit.msal_lifespan.MsalClientCredentialLifespanResource``
とはライフサイクルの形が異なる（``app.state``に書き込む``asynccontextmanager``
ではなく、dictをyieldする非同期ジェネレータ）ため、独立した実装を持つ。DIは
``fastmcp``が内部で使う``uncalled_for.Depends``に乗せる。

FastMCPの``ComposedLifespan``は合成する各lifespan関数を``server``のみを
引数に互いに独立して実行し、起動時に他のlifespanの結果へアクセスする手段を
持たない（``fastmcp.server.lifespan``参照）。そのため、Redisからのトークン
キャッシュロードは起動時ではなく、``CurrentContext()``が使える初回のツール
呼び出し時に遅延実行する（並行して初回呼び出しが複数走った場合に二重ロード
が起きうるが、``deserialize``は状態を冪等に置き換えるだけなので実害はない）。

利用には ``fastmcp-toolkit[msal]`` extraのインストールが必要。
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, cast

from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import CurrentContext
from fastmcp.server.lifespan import Lifespan, lifespan
from msal import ConfidentialClientApplication, SerializableTokenCache
from uncalled_for import Depends

from core_toolkit.msal_errors import MsalClaimsChallengeError, MsalTokenError
from core_toolkit.msal_lifespan import default_msal_http_session
from core_toolkit.token_cache_cipher import TokenCacheCipher

from fastmcp_toolkit.redis_lifespan import _get_redis_client


def _lifespan_key(name: str) -> str:
    return f"msal_client_credential:{name}"


def _cache_key(name: str) -> str:
    return f"msal:app_cache:{name}"


def _raise_for_result(result: dict[str, Any]) -> None:
    if "access_token" in result:
        return
    if "claims" in result:
        raise MsalClaimsChallengeError(
            result.get("error"),
            result.get("error_description"),
            result["claims"],
            result.get("error_codes"),
        )
    raise MsalTokenError(
        result.get("error"), result.get("error_description"), result.get("error_codes")
    )


@dataclass
class MsalClientCredentialHandle:
    app: ConfidentialClientApplication
    cache: SerializableTokenCache
    executor: ThreadPoolExecutor
    cipher: TokenCacheCipher
    redis_client_name: str
    cache_key: str
    cache_loaded: bool = field(default=False)


def msal_client_credential_lifespan(
    name: str,
    client_id: str,
    client_credential: str,
    authority: str,
    redis_client_name: str,
    cipher: TokenCacheCipher,
    max_workers: int = 4,
    http_client_factory: Any = None,
    **app_kwargs: Any,
) -> Lifespan:
    """Client Credentials用のConfidentialClientApplicationライフサイクルを
    管理するFastMCP lifespanを生成する。

    起動時は空の``SerializableTokenCache``でConfidentialClientApplicationを
    生成するだけで、Redisからのロードは行わない（モジュールdocstring参照）。
    ``redis_lifespan(name=redis_client_name, ...)``を同じ
    ``FastMCP(lifespan=...)``に``|``で合成しておく必要がある。

    Args:
        name: このクライアントを識別する名前。``CurrentMsalAppToken``で
            同じ名前を指定して取得する。
        client_id: アプリ（クライアント）ID。
        client_credential: クライアントシークレット文字列。
        authority: 認証機関URL。
        redis_client_name: トークンキャッシュ永続化に使うRedisクライアント名
            （``redis_lifespan(name=...)``で登録済みのもの）。
        cipher: トークンキャッシュの暗号化実装。
        max_workers: MSAL呼び出し専用ThreadPoolExecutorのワーカー数。
        http_client_factory: ``requests.Session``を生成するファクトリ。
        app_kwargs: ``ConfidentialClientApplication``にそのまま渡す追加引数。

    Returns:
        FastMCPの``lifespan=``にそのまま渡せる合成可能なLifespan。
    """
    factory = http_client_factory or default_msal_http_session

    @lifespan
    async def _msal_client_credential_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        session = factory()
        executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix=f"msal-{name}"
        )
        cache = SerializableTokenCache()

        client = ConfidentialClientApplication(
            client_id=client_id,
            client_credential=client_credential,
            authority=authority,
            token_cache=cache,
            http_client=session,
            **app_kwargs,
        )

        handle = MsalClientCredentialHandle(
            app=client,
            cache=cache,
            executor=executor,
            cipher=cipher,
            redis_client_name=redis_client_name,
            cache_key=_cache_key(name),
        )
        try:
            yield {_lifespan_key(name): handle}
        finally:
            executor.shutdown(wait=True)
            session.close()

    return _msal_client_credential_lifespan


def _get_msal_app_token(name: str, scopes: list[str]) -> Callable[[Context], Any]:
    async def get_token(ctx: Context = CurrentContext()) -> str:
        handle = ctx.lifespan_context.get(_lifespan_key(name))
        if handle is None:
            raise RuntimeError(
                f"lifespan_context['{_lifespan_key(name)}'] is not set. "
                f"Did you forget to pass "
                f"lifespan=msal_client_credential_lifespan(name='{name}', ...) "
                "to FastMCP(...)?"
            )
        handle = cast(MsalClientCredentialHandle, handle)

        if not handle.cache_loaded:
            redis = _get_redis_client(handle.redis_client_name)(ctx)
            raw = await redis.get(handle.cache_key)
            if raw:
                handle.cache.deserialize(handle.cipher.decrypt(raw).decode())
            handle.cache_loaded = True

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            handle.executor, lambda: handle.app.acquire_token_for_client(scopes=scopes)
        )

        if handle.cache.has_state_changed:
            payload = handle.cipher.encrypt(handle.cache.serialize().encode())
            redis = _get_redis_client(handle.redis_client_name)(ctx)
            await redis.set(handle.cache_key, payload)

        _raise_for_result(result)
        return result["access_token"]

    get_token.__name__ = f"get_msal_app_token_{name}"
    return get_token


def CurrentMsalAppToken(name: str, scopes: list[str]) -> str:  # noqa: N802
    """``msal_client_credential_lifespan(name, ...)``が生成したアプリ単位の
    アクセストークンを取得するDepends。

    Example::

        @app.tool
        async def call_downstream(
            token: str = CurrentMsalAppToken("downstream-api", scopes=["api://xxx/.default"]),
        ) -> str:
            ...

    Args:
        name: ``msal_client_credential_lifespan(name=...)``に登録した名前。
        scopes: 要求するスコープ。

    Returns:
        str: ``Depends(...)``でラップされた、実行時に解決されるアクセストークン。
    """
    return cast(str, Depends(_get_msal_app_token(name, scopes)))
```

- [ ] **Step 6: テストを実行し成功を確認**

Run: `cd fastmcp-toolkit && uv run pytest tests/test_msal_client_credential_lifespan.py -v`
Expected: PASS（5件全て）

- [ ] **Step 7: 既存テストが壊れていないことを確認**

Run: `cd fastmcp-toolkit && uv run pytest -v`
Expected: 全件PASS

- [ ] **Step 8: lint/formatを実行**

Run: `cd fastmcp-toolkit && uv run ruff format src/ tests/ && uv run ruff check --fix src/ tests/`
Expected: エラーなし

- [ ] **Step 9: コミット**

```bash
git add fastmcp-toolkit/pyproject.toml fastmcp-toolkit/uv.lock fastmcp-toolkit/src/fastmcp_toolkit/msal_lifespan.py fastmcp-toolkit/tests/test_msal_client_credential_lifespan.py
git commit -m "$(cat <<'EOF'
feat(fastmcp-toolkit): add Client Credentials MSAL lifespan

lifespan_contextベースの独立実装でmsal_client_credential_lifespan/
CurrentMsalAppTokenを追加。ComposedLifespanは合成する各lifespanが互いの
起動時結果にアクセスできないため、Redisからのトークンキャッシュロードは
初回のツール呼び出し時まで遅延させる設計にした。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: fastmcp-toolkit — `msal_lifespan.py`（OBO、追記）

**Files:**
- Modify: `fastmcp-toolkit/src/fastmcp_toolkit/msal_lifespan.py`
- Test: `fastmcp-toolkit/tests/test_msal_obo_lifespan.py`

**Interfaces:**
- Consumes: 同ファイル内の`default_msal_http_session`/`_raise_for_result`（Task 6）、`fastmcp_toolkit.redis_lifespan._get_redis_client`（既存）、`core_toolkit.token_cache_cipher.TokenCacheCipher`（Task 2）
- Produces:
  - `MsalOboConfig`（dataclass、`client_id`/`client_credential`/`authority`/`session`/`http_cache`/`executor`/`redis_client_name`/`cipher`/`cache_ttl`属性）
  - `msal_obo_lifespan(name: str, client_id: str, client_credential: str, authority: str, redis_client_name: str, cipher: TokenCacheCipher, max_workers: int = 4, cache_ttl: int = 3600, http_client_factory: Any = None) -> Lifespan`
  - `acquire_msal_obo_token(ctx: Context, name: str, scopes: list[str], user_assertion: str, user_id: str) -> str` — 通常の非同期関数（`Depends`には乗せない。理由はTask 6と同じくOBOのspec「非スコープ」節参照）
  - 他タスクからは参照されない（末端）

- [ ] **Step 1: 失敗するテストを書く**

`fastmcp-toolkit/tests/test_msal_obo_lifespan.py`を作成する。

```python
"""fastmcp_toolkit.msal_lifespanのOBO部分の統合テスト。"""

import pytest
from fakeredis.aioredis import FakeRedis
from fastmcp import Client, Context, FastMCP
from msal import ConfidentialClientApplication

from core_toolkit.token_cache_cipher import default_token_cache_cipher
from fastmcp_toolkit.msal_lifespan import acquire_msal_obo_token, msal_obo_lifespan
from fastmcp_toolkit.redis_lifespan import redis_lifespan


def _key(seed: int) -> bytes:
    return bytes([seed]) * 32


def _make_app(cipher):
    return FastMCP(
        "test",
        lifespan=redis_lifespan("cache", "redis://localhost:6379/0")
        | msal_obo_lifespan(
            "graph",
            client_id="client-id",
            client_credential="secret",
            authority="https://login.microsoftonline.com/tenant-id",
            redis_client_name="cache",
            cipher=cipher,
        ),
    )


@pytest.mark.asyncio
async def test_acquire_msal_obo_token_returns_access_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_on_behalf_of",
        lambda self, user_assertion, scopes: {"access_token": f"token-for-{user_assertion}"},
    )

    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")
    app = _make_app(cipher)

    @app.tool
    async def call_graph(ctx: Context, user_assertion: str, user_id: str) -> str:
        return await acquire_msal_obo_token(
            ctx, "graph", scopes=["User.Read"], user_assertion=user_assertion, user_id=user_id
        )

    async with Client(app) as client:
        result = await client.call_tool(
            "call_graph", {"user_assertion": "jwt-1", "user_id": "user-1"}
        )

    assert result.data == "token-for-jwt-1"


@pytest.mark.asyncio
async def test_acquire_msal_obo_token_raises_when_lifespan_not_registered():
    app = FastMCP("test")

    @app.tool
    async def call_graph(ctx: Context, user_assertion: str, user_id: str) -> str:
        return await acquire_msal_obo_token(
            ctx, "graph", scopes=["User.Read"], user_assertion=user_assertion, user_id=user_id
        )

    async with Client(app) as client:
        with pytest.raises(Exception, match="graph"):
            await client.call_tool(
                "call_graph", {"user_assertion": "jwt-1", "user_id": "user-1"}
            )


@pytest.mark.asyncio
async def test_acquire_msal_obo_token_raises_msal_token_error_on_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_on_behalf_of",
        lambda self, user_assertion, scopes: {
            "error": "invalid_grant",
            "error_description": "AADSTS50013: Assertion is invalid",
        },
    )

    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")
    app = _make_app(cipher)

    @app.tool
    async def call_graph(ctx: Context, user_assertion: str, user_id: str) -> str:
        return await acquire_msal_obo_token(
            ctx, "graph", scopes=["User.Read"], user_assertion=user_assertion, user_id=user_id
        )

    async with Client(app) as client:
        with pytest.raises(Exception, match="invalid_grant"):
            await client.call_tool(
                "call_graph", {"user_assertion": "jwt-1", "user_id": "user-1"}
            )


@pytest.mark.asyncio
async def test_acquire_msal_obo_token_raises_claims_challenge_error(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_on_behalf_of",
        lambda self, user_assertion, scopes: {
            "error": "interaction_required",
            "error_description": "MFA required",
            "claims": '{"access_token":{}}',
        },
    )

    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")
    app = _make_app(cipher)

    @app.tool
    async def call_graph(ctx: Context, user_assertion: str, user_id: str) -> str:
        return await acquire_msal_obo_token(
            ctx, "graph", scopes=["User.Read"], user_assertion=user_assertion, user_id=user_id
        )

    async with Client(app) as client:
        with pytest.raises(Exception, match="interaction_required"):
            await client.call_tool(
                "call_graph", {"user_assertion": "jwt-1", "user_id": "user-1"}
            )


@pytest.mark.asyncio
async def test_different_users_get_independent_tokens(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_on_behalf_of",
        lambda self, user_assertion, scopes: {"access_token": f"token-for-{user_assertion}"},
    )

    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")
    app = _make_app(cipher)

    @app.tool
    async def call_graph(ctx: Context, user_assertion: str, user_id: str) -> str:
        return await acquire_msal_obo_token(
            ctx, "graph", scopes=["User.Read"], user_assertion=user_assertion, user_id=user_id
        )

    async with Client(app) as client:
        result1 = await client.call_tool(
            "call_graph", {"user_assertion": "jwt-1", "user_id": "user-1"}
        )
        result2 = await client.call_tool(
            "call_graph", {"user_assertion": "jwt-2", "user_id": "user-2"}
        )

    assert result1.data == "token-for-jwt-1"
    assert result2.data == "token-for-jwt-2"


@pytest.mark.asyncio
async def test_executor_is_shutdown_on_lifespan_exit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    from fastmcp_toolkit.msal_lifespan import _obo_lifespan_key

    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")
    composed = redis_lifespan("cache", "redis://localhost:6379/0") | msal_obo_lifespan(
        "graph",
        client_id="client-id",
        client_credential="secret",
        authority="https://login.microsoftonline.com/tenant-id",
        redis_client_name="cache",
        cipher=cipher,
    )
    app = FastMCP("test", lifespan=composed)

    holder = {}
    async with composed(app) as ctx:
        holder["config"] = ctx[_obo_lifespan_key("graph")]

    with pytest.raises(RuntimeError, match="cannot schedule new futures after shutdown"):
        holder["config"].executor.submit(lambda: None)
```

- [ ] **Step 2: テストを実行し失敗を確認**

Run: `cd fastmcp-toolkit && uv run pytest tests/test_msal_obo_lifespan.py -v`
Expected: FAIL（`ImportError: cannot import name 'msal_obo_lifespan' from 'fastmcp_toolkit.msal_lifespan'`）

- [ ] **Step 3: 実装を追記**

`fastmcp-toolkit/src/fastmcp_toolkit/msal_lifespan.py`の末尾に以下を追記する。

```python
@dataclass
class MsalOboConfig:
    client_id: str
    client_credential: str
    authority: str
    session: Any
    http_cache: dict
    executor: ThreadPoolExecutor
    redis_client_name: str
    cipher: TokenCacheCipher
    cache_ttl: int


def _obo_lifespan_key(name: str) -> str:
    return f"msal_obo_config:{name}"


def msal_obo_lifespan(
    name: str,
    client_id: str,
    client_credential: str,
    authority: str,
    redis_client_name: str,
    cipher: TokenCacheCipher,
    max_workers: int = 4,
    cache_ttl: int = 3600,
    http_client_factory: Any = None,
) -> Lifespan:
    """OBO用のSession/http_cache/executorのライフサイクルを管理するFastMCP
    lifespanを生成する。

    ``ConfidentialClientApplication``インスタンス自体はリクエストごとに
    ``acquire_msal_obo_token``側で生成するため、ここではSession/http_cache/
    executorのみを共有する（``token_cache``がユーザーごとに異なるため。
    詳細はspecの「意思決定サマリー」参照）。``redis_lifespan(name=
    redis_client_name, ...)``を同じ``FastMCP(lifespan=...)``に``|``で
    合成しておく必要がある。

    Args:
        name: このコンフィグを識別する名前。``acquire_msal_obo_token``で
            同じ名前を指定して取得する。
        client_id: アプリ（クライアント）ID。
        client_credential: クライアントシークレット文字列。
        authority: 認証機関URL。
        redis_client_name: トークンキャッシュ永続化に使うRedisクライアント名。
        cipher: トークンキャッシュの暗号化実装。
        max_workers: MSAL呼び出し専用ThreadPoolExecutorのワーカー数。
        cache_ttl: Redisに保存するユーザー単位キャッシュのTTL（秒）。
        http_client_factory: ``requests.Session``を生成するファクトリ。

    Returns:
        FastMCPの``lifespan=``にそのまま渡せる合成可能なLifespan。
    """
    factory = http_client_factory or default_msal_http_session

    @lifespan
    async def _msal_obo_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        session = factory()
        executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix=f"msal-obo-{name}"
        )
        config = MsalOboConfig(
            client_id=client_id,
            client_credential=client_credential,
            authority=authority,
            session=session,
            http_cache={},
            executor=executor,
            redis_client_name=redis_client_name,
            cipher=cipher,
            cache_ttl=cache_ttl,
        )
        try:
            yield {_obo_lifespan_key(name): config}
        finally:
            executor.shutdown(wait=True)
            session.close()

    return _msal_obo_lifespan


async def acquire_msal_obo_token(
    ctx: Context, name: str, scopes: list[str], user_assertion: str, user_id: str
) -> str:
    """OBOでアクセストークンを取得する。ツール関数内から明示的にawaitする。

    ``user_assertion``（呼び出し元ユーザーのアクセストークン文字列）と
    ``user_id``（トークンキャッシュのキーに使うユーザー識別子）は、アプリ側が
    JWTから取り出して渡す。``user_assertion``/``user_id``という呼び出しごとに
    変わる動的な値を必要とするため、``Depends``の静的解決パターンには乗せず、
    通常の非同期関数として提供する。

    Example::

        @app.tool
        async def call_downstream(ctx: Context, user_assertion: str, user_id: str) -> str:
            token = await acquire_msal_obo_token(
                ctx, "graph", scopes=["User.Read"],
                user_assertion=user_assertion, user_id=user_id,
            )
            ...

    Args:
        ctx: ツール関数が受け取った``Context``。
        name: ``msal_obo_lifespan(name=...)``に登録した名前。
        scopes: 要求するスコープ。
        user_assertion: 呼び出し元ユーザーのアクセストークン文字列。
        user_id: トークンキャッシュのキーに使うユーザー識別子。

    Returns:
        str: 取得したアクセストークン。
    """
    config = ctx.lifespan_context.get(_obo_lifespan_key(name))
    if config is None:
        raise RuntimeError(
            f"lifespan_context['{_obo_lifespan_key(name)}'] is not set. "
            f"Did you forget to pass lifespan=msal_obo_lifespan(name='{name}', ...) "
            "to FastMCP(...)?"
        )
    config = cast(MsalOboConfig, config)

    redis = _get_redis_client(config.redis_client_name)(ctx)
    cache_key = f"msal:obo_cache:{name}:{user_id}"

    cache = SerializableTokenCache()
    raw = await redis.get(cache_key)
    if raw:
        cache.deserialize(config.cipher.decrypt(raw).decode())

    def call_msal() -> dict[str, Any]:
        client = ConfidentialClientApplication(
            client_id=config.client_id,
            client_credential=config.client_credential,
            authority=config.authority,
            token_cache=cache,
            http_client=config.session,
            http_cache=config.http_cache,
        )
        return client.acquire_token_on_behalf_of(user_assertion, scopes=scopes)

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(config.executor, call_msal)

    if cache.has_state_changed:
        payload = config.cipher.encrypt(cache.serialize().encode())
        await redis.set(cache_key, payload, ex=config.cache_ttl)

    _raise_for_result(result)
    return result["access_token"]
```

- [ ] **Step 4: テストを実行し成功を確認**

Run: `cd fastmcp-toolkit && uv run pytest tests/test_msal_obo_lifespan.py -v`
Expected: PASS（6件全て）

- [ ] **Step 5: 既存テストが壊れていないことを確認**

Run: `cd fastmcp-toolkit && uv run pytest -v`
Expected: 全件PASS（Task 6のテスト含む）

- [ ] **Step 6: lint/formatを実行**

Run: `cd fastmcp-toolkit && uv run ruff format src/ tests/ && uv run ruff check --fix src/ tests/`
Expected: エラーなし

- [ ] **Step 7: コミット**

```bash
git add fastmcp-toolkit/src/fastmcp_toolkit/msal_lifespan.py fastmcp-toolkit/tests/test_msal_obo_lifespan.py
git commit -m "$(cat <<'EOF'
feat(fastmcp-toolkit): add OBO MSAL lifespan

msal_obo_lifespan/acquire_msal_obo_tokenを追加。Session/http_cache/
executorのみをlifespanで共有し、ConfidentialClientApplication自体は
呼び出しごとに生成してユーザー単位のtoken_cacheの並行安全性を確保する。
user_assertion/user_idという動的な値を扱うためDependsには乗せず、
通常の非同期関数として提供する。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## 自己レビューで確認した点

- **spec網羅性**: specの「意思決定サマリー」全8項目を全タスクのコード・テストに反映済み。
  - 対象フロー（Client Credentials + OBOのみ、Public Client対象外）: Task 3/5/6がClient Credentials、Task 4/5/7がOBO。`PublicClientApplication`はどのタスクにも登場しない
  - インスタンスのライフタイム（Client Credentialsは共有、OBOはリクエストごと）: Task 3は`ConfidentialClientApplication`をlifespanで1つ保持、Task 4/7は`get_msal_obo_token`/`acquire_msal_obo_token`内で毎回生成
  - sync→async境界（専用ThreadPoolExecutor）: Task 3/4/6/7すべてで`ThreadPoolExecutor`をlifespanリソースに内蔵し、`run_in_executor`で呼び出す
  - トークンキャッシュ永続化（Client Credentialsはアプリ単位1キー、OBOはユーザー単位キー分割）: Task 3の`msal:app_cache:{name}`とTask 4/7の`msal:obo_cache:{name}:{user_id}`で反映
  - キャッシュ暗号化（JWE、kidバージョニング）: Task 2の`JweTokenCacheCipher`
  - JWEライブラリ（joserfc採用）: Task 2の依存追加・実装で反映
  - エラーハンドリング（dict→例外変換）: Task 1の`MsalTokenError`/`MsalClaimsChallengeError`をTask 3/4/6/7の`_raise_for_result`が使う
  - HTTPクライアント差し替え（Retry付きSession、差し替え可能）: Task 3の`default_msal_http_session`/`http_client_factory`引数
- **非スコープの遵守**: 「非スコープ」節の各項目（`PublicClientApplication`・JWT検証/`user_assertion`/`user_id`抽出・`http_cache`のRedis共有化・証明書ベース`client_credential`構築ヘルパー・`azure_region`自動検出のラップ）はどのタスクにも実装を含めていない。`http_cache`はTask 4/7ともプロセス内`dict`（`{}`）のまま
- **プレースホルダ**: 全コードブロックは実際に動く完全な内容。`...`が現れる箇所はすべて`Protocol`のスタブメソッド、docstring内のExample省略記法、またはエラーメッセージ文字列の一部であり、未実装コードではない
- **型/シグネチャの一貫性**: `MsalTokenError(error, error_description, error_codes=None)`/`MsalClaimsChallengeError(error, error_description, claims, error_codes=None)`はTask 1定義のままTask 3/4/6/7で使用。`MsalClientCredentialLifespanResource`/`get_msal_app_token`/`MsalOboLifespanResource`/`get_msal_obo_token`の引数（`name`/`client_id`/`client_credential`/`authority`/`redis_client_name`/`cipher`/`max_workers`/`cache_ttl`/`http_client_factory`）はTask 3/4で定義した順序・デフォルト値のまま、fastmcp-toolkit版（Task 6/7の`msal_client_credential_lifespan`/`msal_obo_lifespan`）でも同じ並びに揃えている
- **fastmcp-toolkitの設計変更（実装計画作成中に判明）**: FastMCPの`ComposedLifespan`（`fastmcp/server/lifespan.py`）は合成する各lifespan関数を`server`のみを引数に独立実行し、他のlifespanが生成した値へ起動時にアクセスする手段を持たないことをソースコードで確認した。そのためTask 6のClient Credentials用lifespanは、core-toolkit版（Task 3）と異なり起動時にRedisからトークンキャッシュをロードせず、`CurrentContext()`が使える初回のツール呼び出し時に遅延ロードする設計にした（`MsalClientCredentialHandle.cache_loaded`フラグ）。OBO（Task 4/7）はもともとリクエストごとにRedis I/Oを行う設計だったため、この制約の影響を受けない
- **executor shutdown確認のテスト手法**: `ThreadPoolExecutor.shutdown(wait=True)`が呼ばれたことは、実装詳細である`_shutdown`属性ではなく、`executor.submit(...)`が`RuntimeError: cannot schedule new futures after shutdown`を送出することで検証する（Task 3/4/6/7で統一）
- **fastmcp-toolkitのprivate関数の扱い**: Task 6/7は`fastmcp_toolkit.redis_lifespan._get_redis_client`という同一パッケージ内のprivate関数をimportして使う。パッケージをまたいだprivate関数の共有（例: core-toolkitの`_raise_for_result`をfastmcp-toolkitがimportすること）は避け、Task 6/7では同名のロジックを独立して再実装している（コード重複にはなるが、パッケージ境界を越えたprivate結合よりも安全という判断）
