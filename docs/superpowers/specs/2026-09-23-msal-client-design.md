# MSALクライアント lifespan部品 設計

- 日付: 2026-09-23
- スコープ: `core-toolkit` / `fastapi-toolkit` / `fastmcp-toolkit`
- ステータス: ドラフト（ユーザーレビュー待ち）

## 背景・目的

DB（[[2026-09-22-fastmcp-db-connection-design]]相当）・Redis・HTTPクライアント
（httpx/aiohttp）に続き、Microsoft Entra ID（旧Azure AD）向け認証クライアント
（MSAL: Microsoft Authentication Library for Python）を、同じ「名前付き複数
インスタンスをlifespanで管理する」設計思想に載せて提供する。

想定利用シーン:

- `fastapi-toolkit`: BFF/APIサーバーが下流APIを呼ぶために、アプリ単位の
  アクセストークンをClient Credentialsフローで取得する
- `fastmcp-toolkit`: Agent等から受け取ったユーザーのJWT（アクセストークン）
  を使い、On-Behalf-Of（OBO）フローで下流APIのトークンを取得する

MSALはDB/Redis/HTTPクライアントと異なり、**同期API（requestsベース）**で
あり、**トークンキャッシュの粒度がフローによって異なる**（アプリ単位 vs
ユーザー単位）という2点で既存パターンをそのまま適用できない。本設計は
その差分をどう吸収するかが中心的な論点になる。

## スコープ

- `ConfidentialClientApplication`を使う2フローへの対応
  - Client Credentials（`acquire_token_for_client`、アプリ単位トークン）
  - On-Behalf-Of（`acquire_token_on_behalf_of`、ユーザー単位トークン）
- 名前付き複数インスタンスの同時利用（DB/Redis/HTTPと同型のAPI）
- トークンキャッシュのRedis永続化（`SerializableTokenCache`）
- トークンキャッシュの暗号化（JWE、鍵バージョニング対応）
- MSAL呼び出し（同期API）をASGIのイベントループから安全に呼ぶための
  スレッド分離
- MSALのdict形式エラーレスポンスを例外に変換する共通エラー型
- core-toolkit / fastapi-toolkit / fastmcp-toolkitの3層への配置とテスト整備

## 非スコープ（意図的に含めない）

- **`PublicClientApplication`（Public Client）** — デスクトップ/CLI/ネイティブ
  アプリ向けのインタラクティブ認証フロー。ASGIサーバー側の実装としては
  想定利用シーンが無い。BFFパターンでは通常SPA側が認証を担い、BFFには
  セッションCookieのみが渡される構成になるため、BFF自体もPublic Client
  を必要としない
- **JWT検証・`user_assertion`/`user_id`の抽出** — OBOに渡す
  `user_assertion`（呼び出し元ユーザーのアクセストークン文字列）と、
  トークンキャッシュのキーに使う`user_id`は、呼び出し側が用意して渡す
  前提とする。JWT署名検証（JWKS取得・issuer/audience検証等）はテナント・
  IDプロバイダ設定に依存する別関心事であり、汎用ツールキットに混ぜると
  結合度が上がる。必要になった場合は独立したsub-projectとして別途検討する
- **`http_cache`（429/Retry-After情報）のRedis共有化** — MSAL
  1.16+はプロセス内`http_cache`にスロットル情報を保持し再試行を抑制する
  仕組みを持つが、これを複数プロセス/コンテナ間で共有するには別の分散
  キャッシュ層が要る。トークンキャッシュ永続化と役割が重複し複雑さが
  跳ね上がるため、まずはプロセス内dictのデフォルト動作に任せる。必要に
  なった時点で別途検討する
- **証明書ベースの`client_credential`構築ヘルパー** — secret文字列以外
  （証明書、事前署名済みassertion）の`client_credential`構築を助ける
  ヘルパーは提供しない。まずsecretベースで確定させ、要望が出た時点で
  追加する
- **`azure_region`自動検出のラップ** — MSAL公式が不安定だと明記している
  機能であり、toolkit側で特別扱いしない。固定リージョン文字列の指定は
  アプリ側の`app_kwargs`パススルーで対応できる

## 意思決定サマリー

| 論点 | 決定 | 理由 |
|---|---|---|
| 対象フロー | Client Credentials + OBOのみ（`ConfidentialClientApplication`）。Public Clientは対象外 | ASGIサーバー向けツールキットとして実際に使われるのはこの2フロー。fastmcp-toolkitはOBO中心、fastapi-toolkitはClient Credentials中心（OBOも使いうる） |
| インスタンスのライフタイム | **Client Credentials**: `ConfidentialClientApplication`をlifespanで1つ生成し使い回す。**OBO**: インスタンス自体はリクエストごとに生成し、`http_client`（Session）・`http_cache`・executorのみlifespanで共有 | `token_cache`はインスタンス属性であり、OBOはリクエストごとに異なるユーザーのキャッシュを使う。共有インスタンスの`token_cache`を都度差し替えると並行リクエスト間でキャッシュを取り違えるレースコンディションになる。これはMSAL公式のWebアプリサンプル（Flask/Django）が「ユーザーごとにインスタンスをリクエスト単位で生成する」実装になっている理由と同じ |
| sync→async境界 | 名前付きリソースが**専用`ThreadPoolExecutor`を内蔵**し、`asyncio.get_running_loop().run_in_executor(...)`でMSAL呼び出しを隔離する | MSALはrequestsベースの同期APIで公式async版が存在しない。GitHub Star上位の先行事例（aiobotocore/OpenAI SDK）を調査した結果、深い非同期統合やSDK側でのネイティブ実装はfaststar側からは選べない（前者はbotocore側のHTTP抽象があって初めて成立する統合で、MSAL内部にはそれが無い。後者はMicrosoft自身がやるべきこと）。`asyncio.to_thread`相当の隔離が現実的な唯一の手段。default executor共有だと他の同期I/Oと枯渇を奪い合うため、専用executorを名前付きリソースに内蔵する形をDB/Redis/HTTPと同じ「lifespanで1つ持つ長寿命リソース」哲学に合わせて採用 |
| トークンキャッシュ永続化 | 両フローともRedisに永続化。Client Credentialsはアプリ単位1キー、OBOはユーザーIDごとにキー分割 | Client Credentialsも複数プロセス/コンテナが個別にトークン取得すると起動時に同時多発でAADへ叩きに行く（サンダリングハード）。マルチインスタンス運用ではRedis共有が実質必須級。OBOは公式ベストプラクティスとして「アカウント（ユーザー）ごとに1キャッシュ」が明記されており、Redis永続化は必須と判断 |
| キャッシュ暗号化 | デフォルトでJWE（`alg=dir`, `enc=A256GCM`、joserfc実装）による暗号化。`TokenCacheCipher`プロトコルで差し替え可能。鍵は`kid`ごとに複数登録し「現在使うkid」だけを切り替える | MSAL自体は永続化・暗号化を行わないため、Redis侵害時にトークンが平文で漏れるリスクがある。暗号化方式の拡張性・デフォルト強化・旧デフォルトへの固定・利用者側での差し替えを両立するため、自前バージョンタグではなく**標準フォーマット（JWE）の`kid`ヘッダにバージョニングを委ねる**。新アルゴリズムは新kidの追加登録で拡張でき、旧kidの鍵を残せば過去データも復号できる。`current_kid`や鍵material自体を環境変数から読むのは呼び出し側（将来の設定値管理機能）の責務とし、core-toolkitはコンストラクタ引数として受け取るだけに留める（DB/Redis/HTTPのkwargsパススルー方針と同じ） |
| JWEライブラリ | `joserfc`を採用（`python-jose`は不採用） | MSALの依存（`cryptography`/`pyjwt`/`requests`）と重複が少ない。`authlib`は内蔵jose moduleを非推奨化し公式に`joserfc`への移行を案内している。`jwcrypto`も選択肢だが、型ヒント充実・モダンAPIという点でfaststarの規約（型ヒント必須、Python 3.14+）との親和性が高い`joserfc`を優先 |
| エラーハンドリング | MSALのdict形式エラー応答を`MsalTokenError`/`MsalClaimsChallengeError`という例外に変換して送出する | MSALは認証失敗を例外ではなく`dict`（`"error"`キー等）で返す。他クライアント（DB/Redis/HTTP）は素直に例外を投げる流儀のため、揃える。Conditional Access等の`claims`チャレンジは別例外に分け、再認可フロー実装をアプリ側が組みやすくする |
| HTTPクライアント差し替え | `http_client=`にRetry付き`requests.Session`を注入する。デフォルトは`default_msal_http_session()`が3xx/5xx/429にリトライするSessionを生成、`http_client_factory`で差し替え可能 | `verify`/`proxies`/`timeout`はMSAL内製の内部Sessionにしか効かず、自前`http_client`を渡すとこれらは無視されるという公式ドキュメント記載の落とし穴がある。差し替え時はリトライ責務も呼び出し側に移ることを踏まえ、デフォルトで妥当なRetry設定を持つSessionを提供する |

## アーキテクチャ

### レイヤー構造（db_lifespan/redis_lifespanと同型 + OBO用の非対称部分）

```
core-toolkit/src/core_toolkit/
  msal_lifespan.py
    - MsalClientCredentialLifespanResource（LifespanResourceサブクラス）
    - MsalOboLifespanResource（LifespanResourceサブクラス）
    - get_msal_app_token(name, scopes)      … Client Credentials用provider関数生成
    - get_msal_obo_token(name, scopes)      … OBO用provider関数生成（user_assertion/user_idを引数で受け取る）
    - default_msal_http_session()           … Retry付きrequests.Session生成
  msal_errors.py
    - MsalTokenError
    - MsalClaimsChallengeError（MsalTokenErrorのサブクラス）
  token_cache_cipher.py
    - TokenCacheCipher（Protocol）
    - JweTokenCacheCipher（joserfc実装）
    - default_token_cache_cipher(keys, current_kid)

fastapi-toolkit/src/fastapi_toolkit/
  msal_lifespan.py
    - 上記を再エクスポートするだけの薄いラッパー
    - 名前ごとの型エイリアスはtoolkit側では定義せず、アプリ側が名前と
      scopesを指定して自分で作る（db_lifespan/redis_lifespanと同じ運用）

fastmcp-toolkit/src/fastmcp_toolkit/
  msal_lifespan.py
    - FastMCP独自のlifespan形状向けの独立実装
    - Client Credentials: uncalled_for.Depends + CurrentMsalAppToken(name, scopes)
      （redis_lifespanのCurrentRedisClientと同じ「呼ぶとDependsを返す関数」パターン）
    - OBO: acquire_msal_obo_token(ctx, name, scopes, user_assertion, user_id)
      という**通常の非同期関数**として提供（Dependsパターンには乗せない）
```

OBOのfastmcp-toolkit側APIだけ他と非対称になる点に注意。`Depends`は
ツール関数の引数デフォルト値として**静的に**解決される仕組みで、
`user_assertion`/`user_id`という**呼び出しごとに変わる動的な値**を
必要とするOBOトークン取得とは相性が悪い。無理に`Depends`に乗せず、
ツール関数内から明示的に`await`する関数として提供する。

### core-toolkit: `msal_errors.py`

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

    ``claims``は、呼び出し元ユーザーを再認可フローへ差し戻す際に
    authorize URLへそのまま付与する必要がある値。
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

### core-toolkit: `token_cache_cipher.py`

```python
"""トークンキャッシュ（SerializableTokenCache）の暗号化。

JWE（RFC 7516、joserfc実装）による暗号化をデフォルトとする。鍵は``kid``
ごとに複数登録でき、「現在使うkid」を切り替えるだけで新しい鍵/アルゴリズム
へ移行できる。過去に別kidで暗号化されたデータも、そのkidの鍵が登録され
続けていれば復号できる。
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
        keys: ``kid``をキーとする鍵materialの辞書（例: ``{"v1": b"...32bytes..."}``）。
            複数バージョン渡すことで鍵ローテーション・アルゴリズム移行に対応する。
        current_kid: 暗号化時に使う``kid``。復号時は暗号文ヘッダの``kid``を
            見て``keys``から自動選択するため、この値には依存しない。
    """

    def __init__(self, keys: dict[str, bytes], current_kid: str) -> None:
        self._keys = {kid: OctKey.import_key(key) for kid, key in keys.items()}
        self._current_kid = current_kid

    def encrypt(self, plaintext: bytes) -> bytes:
        key = self._keys[self._current_kid]
        protected = {"alg": "dir", "enc": "A256GCM", "kid": self._current_kid}
        return jwe.encrypt_compact(protected, plaintext, key)

    def decrypt(self, ciphertext: bytes) -> bytes:
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

### core-toolkit: `msal_lifespan.py`（Client Credentials）

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
            result.get("error"), result.get("error_description"),
            result["claims"], result.get("error_codes"),
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
        redis = get_redis_client(self._redis_client_name)(Request(scope={"type": "http", "app": app}))
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
            app=client, cache=cache, executor=executor, cipher=self._cipher,
            redis_client_name=self._redis_client_name, cache_key=self._cache_key(),
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

### core-toolkit: `msal_lifespan.py`（OBO、続き）

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
    共有する（token_cacheがユーザーごとに異なるため、インスタンス自体は
    使い回せない。詳細は「意思決定サマリー」参照）。

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
            client_id=self._client_id, client_credential=self._client_credential,
            authority=self._authority, session=session, http_cache={}, executor=executor,
            redis_client_name=self._redis_client_name, cipher=self._cipher,
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
    アプリ側がJWTから取り出して渡す（本設計の非スコープ、詳細は
    「非スコープ」参照）。

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
                client_id=config.client_id, client_credential=config.client_credential,
                authority=config.authority, token_cache=cache,
                http_client=config.session, http_cache=config.http_cache,
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

Redis I/O（`await redis.get/set`）はイベントループ側で行い、`executor`に
渡す`call_msal`はMSAL呼び出しだけの純粋な同期関数にする。async Redis
クライアントはスレッド内（別のイベントループが存在しないコンテキスト）
から`await`できないため、この分離を崩すと動かない。

### fastapi-toolkit: `msal_lifespan.py`

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

利用イメージ:

```python
from typing import Annotated
from fastapi import Depends
from fastapi_toolkit.msal_lifespan import get_msal_app_token

DownstreamApiToken = Annotated[
    str, Depends(get_msal_app_token("downstream-api", scopes=["api://xxx/.default"]))
]

@app.get("/proxy")
async def proxy(token: DownstreamApiToken):
    ...
```

### fastmcp-toolkit: `msal_lifespan.py`

Client Credentials側は`redis_lifespan.py`の`CurrentRedisClient`と同じ
「呼ぶとDependsを返す関数」パターンに乗せられる。

```python
"""FastMCPサーバー向けMSAL lifespan統合。

Starlette向けの``core_toolkit.msal_lifespan``とはライフサイクルの形が
異なる（``app.state``に書き込む``asynccontextmanager``ではなく、dictを
yieldする非同期ジェネレータ）ため、独立した実装を持つ。Client Credentials
は``uncalled_for.Depends``に乗せられるが、OBOは``user_assertion``/
``user_id``という動的な値を要求するため通常の関数として提供する
（``Depends``の静的解決パターンに乗らないため）。

利用には ``fastmcp-toolkit[msal]`` extraのインストールが必要。
"""

from collections.abc import AsyncIterator, Callable
from typing import Any, cast

from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import CurrentContext
from fastmcp.server.lifespan import Lifespan, lifespan
from uncalled_for import Depends

from core_toolkit.msal_errors import MsalClaimsChallengeError, MsalTokenError
from core_toolkit.msal_lifespan import default_msal_http_session
from core_toolkit.token_cache_cipher import TokenCacheCipher

# handle/config構築・トークン取得・キャッシュ永続化のロジックは
# core_toolkit.msal_lifespanの非公開関数（_raise_for_result等）を跨パッケージ
# でimportせず、redis_lifespan.pyのfastmcp版と同じ移植パターンでFastMCPの
# lifespan_context形状（dictをyieldする非同期ジェネレータ）向けに独立実装
# する。ここでは公開APIの形のみ示す。


def msal_client_credential_lifespan(
    name: str, client_id: str, client_credential: str, authority: str,
    redis_lifespan_name: str, cipher: Any, max_workers: int = 4,
    http_client_factory: Any = None, **app_kwargs: Any,
) -> Lifespan:
    """Client Credentials用FastMCP lifespanを生成する。"""
    ...


def _get_msal_app_token(name: str, scopes: list[str]) -> Callable[[Context], Any]:
    async def get_token(ctx: Context = CurrentContext()) -> str:
        # handle取得・run_in_executor・エラー変換・キャッシュ永続化は
        # core_toolkit側のClient Credentials実装と同じロジックを、
        # ctx.lifespan_context（dict形状）向けに移植する。
        ...

    get_token.__name__ = f"get_msal_app_token_{name}"
    return get_token


def CurrentMsalAppToken(name: str, scopes: list[str]) -> str:  # noqa: N802
    """``msal_client_credential_lifespan(name, ...)``が生成したアプリ単位の
    アクセストークンを取得するDepends。

    Example::

        @app.tool
        async def call_downstream(token: str = CurrentMsalAppToken("downstream-api", scopes=["..."])) -> str:
            ...
    """
    return cast(str, Depends(_get_msal_app_token(name, scopes)))


def msal_obo_lifespan(
    name: str, client_id: str, client_credential: str, authority: str,
    redis_lifespan_name: str, cipher: Any, max_workers: int = 4,
    cache_ttl: int = 3600, http_client_factory: Any = None,
) -> Lifespan:
    """OBO用FastMCP lifespanを生成する。"""
    ...


async def acquire_msal_obo_token(
    ctx: Context, name: str, scopes: list[str], user_assertion: str, user_id: str,
) -> str:
    """OBOでアクセストークンを取得する。ツール関数内から明示的にawaitする。

    Example::

        @app.tool
        async def call_downstream(ctx: Context, user_assertion: str, user_id: str) -> str:
            token = await acquire_msal_obo_token(
                ctx, "graph", scopes=["User.Read"],
                user_assertion=user_assertion, user_id=user_id,
            )
            ...
    """
    ...
```

## テスト方針

DB/Redis接続部品で得た知見のうち該当するものを踏襲する。

- RPC境界（fastmcp-toolkitの`Client(app)`経由テスト）での例外は
  `"Failed to resolve dependency '<param>' for <fn>"`のみが届くため、
  未登録時のエラー検証は**パラメータ名**でmatchすること。詳細な
  `RuntimeError`メッセージを検証したい場合はDepends解決関数を直接呼ぶ
  ユニットテストを別途書く
- `ConfidentialClientApplication`は実際にAADへHTTPを飛ばせないため、
  `acquire_token_for_client`/`acquire_token_on_behalf_of`を
  `monkeypatch`でスタブする。実際のHTTP呼び出し（`http_client`差し替え
  ロジック自体の検証）は、`default_msal_http_session`が返す`Session`に
  対する`HTTPAdapter`/`Retry`設定の単体テストとして分離する
- トークンキャッシュのRedis永続化・暗号化は`fakeredis`＋実際の
  `JweTokenCacheCipher`（テスト用のダミー鍵）を組み合わせた統合テストで
  検証する。「暗号化→Redis保存→ロード→復号」の往復が壊れていないことを
  確認する
- `kid`ローテーションのテスト: `keys={"v1": ..., "v2": ...}`で`v1`暗号化
  したペイロードを、`current_kid="v2"`のCipherでも復号できることを
  検証する（鍵ローテーション時の後方互換性の担保）
- 専用ThreadPoolExecutorが`context`終了時に`shutdown(wait=True)`される
  ことを確認する（DB接続部品の`AsyncEngine.dispose`同様、「呼んでも
  失敗しないが実は後始末できていない」パターンの再発防止）

## extras構成

```toml
[project.optional-dependencies]
msal = ["msal>=1.30", "joserfc>=1.0"]
dev = ["fakeredis>=2.20", ...]  # 既存devの内容に追加
```

- core-toolkitに`msal` extraを新設（`msal`本体・`joserfc`を含む。
  `requests`は`msal`の推移的依存で入る）
- fastapi-toolkit/fastmcp-toolkitは`core-toolkit[msal]`を`msal` extra
  経由で引く（DB接続部品の`sqlite`/`postgres`/`mysql`extraと同じ依存の
  通し方）。加えて両toolkitとも`redis` extraへの依存も必要（トークン
  キャッシュ永続化にRedisクライアントを使うため）
- バージョン下限は実装時に`uv add`で解決し直す

## 未決事項・将来検討（本設計のスコープ外）

- 証明書ベース`client_credential`の構築ヘルパー（SHA-256指紋の
  `public_certificate`自動計算を含む）
- `http_cache`（429スロットル情報）のRedis等への共有化
- JWT検証・`user_assertion`/`user_id`抽出の共通コンポーネント化
  （独立したsub-projectとして検討）
- ロングランニングOBO（バックグラウンドジョブでの再利用）のベスト
  プラクティス（最小限のユーザーコンテキストのみ保存、コンテキストの
  有効期限設定、リフレッシュトークン失効時のユーザー通知、完了後の
  コンテキスト削除）への対応。現状のRedis TTLベースの永続化はこの
  一部（有効期限）のみカバーしている
- MSALインスタンスのスレッドセーフ性についてはMSAL公式が明言していない
  （GitHub issueベースでの確認が必要）。Client Credentials側の共有
  インスタンスを高頻度・高並列で使う場合は実装時に改めて確認する
