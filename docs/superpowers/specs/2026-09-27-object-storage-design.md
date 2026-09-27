# ObjectStorage接続部品 新規設計（S3 / GCS / Azure Blob 差し替え対応）

- 日付: 2026-09-27
- スコープ: `core-toolkit` / `fastapi-toolkit` / `fastmcp-toolkit`
- ステータス: ドラフト（ユーザーレビュー待ち）

## 背景・目的

アプリがObjectStorageを使う際、`boto3` / `google-cloud-storage` / `azure-storage-blob` を直接扱うと、バックエンドの切り替えにコード変更が必要になり、乱数入りの物理バケット名がコードに散らばってミスの原因になる。

本部品は、アプリが `ObjectStorage`（ABC）と**論理バケット名**だけを扱い、バックエンドは設定値（`scheme`）で決まる状態にする。既存のDB/Redis/HTTP/Valkey lifespan部品（名前付き複数インスタンス設計）と同型の3層構成で提供する。

## スコープ

- `ObjectStorage` ABCと、S3 / GCS / Azure Blob / InMemory の4アダプタ
- 操作: 作成・取得・削除・存在確認・一覧（CRUD）、署名付きURL（ダウンロード用GET / アップロード用PUT）、マルチパートアップロード（サーバー経由）
- 論理バケット名から物理バケット名への解決（3バックエンド共通、基底クラスに1実装）
- core-toolkit / fastapi-toolkit / fastmcp-toolkit の3層への配置とテスト整備

## 非スコープ（意図的に含めない）

- **バケット自体の作成・削除・一覧などの管理操作** — インフラ（Terraform等）の責務とする
- **パート単位の署名URL（ブラウザ等からの直接マルチパート）** — 大容量のクライアント直送は「署名URLの単発PUT」で対応する。S3以外にパート単位署名の相当機能がなく共通化できない
- **`put_stream`（AsyncIterableを直接受ける書き込み）** — ファイルは `put_file`（/tmp等に保管済みのファイル）に任せる
- **`copy` / メタデータ更新 / ACL**
- **`put_file` の並列アップロード** — 逐次のみ（将来の拡張を参照）
- **ローカルファイルシステムアダプタ / 署名URLのフェイク実装** — 署名URLは実物に対するテストで検証する方針
- **cache相当のサンプルアプリ** — Valkeyと同様、要望があれば別途
- **SDKクライアントの共有最適化** — 1名前付きインスタンス=1SDKクライアントで、同一アカウントの全バケットを扱う（呼び出しごとにバケット指定する設計のため、共有の問題自体が発生しない）

## 意思決定サマリー

| 論点 | 決定 | 理由 |
|---|---|---|
| 抽象化の方式 | 自前のABC + 各社ネイティブSDKのアダプタ | `fsspec`/`obstore` 等の抽象ライブラリは依存が増える。ABCを自前で持つことで署名URL・マルチパートの形を統一できる |
| 非同期方針 | 非同期ネイティブのみ（S3: `aiobotocore`、GCS: `gcloud-aio-storage`、Azure: `azure-storage-blob` の `.aio`） | DB/Redisの前例（非同期のみ）と一致。ABCを非同期のみで定義でき単純になる |
| バケット指定 | 呼び出しごとに**論理名**で指定 | 差し替え目的に対し、物理名（乱数入り）を設定側へ閉じ込める。1クライアントが1アカウントの全バケットを扱える |
| 論理名→物理名の解決 | 登録時に `Mapping[str, str]` を渡す。未登録の論理名は `UnknownBucketError` | 物理名の直書き・typoを即検知する。パススルー案は抽象化の目的（ミス防止）に反するため不採用 |
| バックエンド選択 | 登録時の `scheme`（`"s3"` / `"gs"` / `"azure"` / `"memory"`） | 設定値の切り替えだけで差し替え可能。URL形式（`s3://bucket`）は、バケットを呼び出しごとに指定する設計と役割が重複するため不採用 |
| 認証・エンドポイント | `**client_kwargs` をSDKへそのまま渡す（専用APIなし） | Redisと同様。MinIO等のエンドポイント上書きも同じ経路で可能 |
| マルチパートのABC表現 | ステートフルなセッション型（`begin_multipart()` → `MultipartUpload`） | ETag/block id/一時オブジェクト名を内部に隠蔽し、呼び出し側にベンダー差を見せない |
| GCSのマルチパート | パートを一時オブジェクトとして保存し `compose` で結合（公開APIのみ） | resumable uploadの内部API（private）はライブラリ更新で壊れるリスクがある |
| ローカル開発・テスト | `InMemoryObjectStorage` のみ。署名URLは `NotSupportedError` | 署名URLは実物で検証すべきで、フェイクは製品間の差異が大きい |

## アーキテクチャ

### レイヤー構造（DB/Redis/Valkeyと同型）

```
core-toolkit/src/core_toolkit/
  object_storage/
    base.py      ObjectStorage(ABC) / MultipartUpload(ABC) / ObjectInfo / 例外階層 / 論理名の解決
    memory.py    InMemoryObjectStorage（追加依存なし）
    s3.py        aiobotocore ベース
    gcs.py       gcloud-aio-storage ベース
    azure.py     azure-storage-blob(.aio) ベース
    factory.py   open_object_storage(scheme, buckets, **client_kwargs)
                 asynccontextmanager。scheme で分岐し、SDKは遅延import
  object_storage_lifespan.py
    ObjectStorageLifespanResource(name, scheme, buckets, **client_kwargs)
    get_object_storage(name)   provider関数（RedisLifespanResource / get_redis_client と同型）

fastapi-toolkit/src/fastapi_toolkit/object_storage_lifespan.py
  上記の再エクスポートのみ

fastmcp-toolkit/src/fastmcp_toolkit/object_storage_lifespan.py
  object_storage_lifespan(name, scheme, buckets, **client_kwargs)  FastMCP形式のlifespan
  CurrentObjectStorage(name)   uncalled_for.Depends を返す
  ABC・アダプタは core-toolkit から利用（パッケージ間依存は core-toolkit 経由のみ）
```

- `open_object_storage` を `async with` 形式にするのは、`aiobotocore` のクライアントが `async with` の内側でしか生存できないため。ライフサイクルをこの1箇所に閉じ込め、lifespanはそれを使うだけにする。
- extrasは `s3`（`aiobotocore`）、`gcs`（`gcloud-aio-storage`）、`azure`（`azure-storage-blob` + `aiohttp`）に分ける。`scheme` に対応するextraが未インストールなら、「`core-toolkit[s3]` を入れてください」という明確な例外を出す。他のschemeは影響を受けない。
- 名前付き複数登録は既存部品と同じ。`name` は「バックエンド接続」の名前で、`"main"`（S3）と `"archive"`（GCS）を併用できる。論理バケット名は名前付きインスタンスごとに独立（`"main"` の `"uploads"` と `"archive"` の `"uploads"` は別物）。
- fastmcp-toolkit側の `object_storage_lifespan` は、同じ `name` を重複登録すると `lifespan_context` のキーが衝突し後勝ちで上書きされる（Redis実装と同じ注意事項）。docstringに明記する。

### 登録と利用

```python
# 登録
ObjectStorageLifespanResource(
    name="main",
    scheme="s3",                       # "s3" | "gs" | "azure" | "memory"
    buckets={"uploads": "uploads-x7f3", "reports": "reports-a91c"},  # 論理名 → 物理名
    **client_kwargs,                   # SDK固有（認証、endpoint_url、account_url など）
)

# 利用（呼び出し側は論理名だけを書く）
await storage.put("uploads", "a/b.png", data)
url = await storage.presigned_upload_url("uploads", "a/b.png", expires=timedelta(minutes=10))
```

### `ObjectStorage` ABC

`bucket` 引数は常に論理名。基底が `Mapping` で物理名へ解決し、未登録なら `UnknownBucketError`。アダプタは物理名だけを受け取る。

```python
class ObjectStorage(ABC):
    # --- 基本操作（アダプタが実装） ---
    async def put(bucket, key, data: bytes, *, content_type=None, metadata=None) -> ObjectInfo
    async def get(bucket, key) -> bytes
    def get_stream(bucket, key, *, chunk_size=...) -> AsyncIterator[bytes]
    async def head(bucket, key) -> ObjectInfo            # 無ければ ObjectNotFoundError
    async def delete(bucket, key) -> None                # 存在しなくても成功（冪等）
    def list(bucket, prefix="") -> AsyncIterator[ObjectInfo]
    async def presigned_download_url(bucket, key, *, expires: timedelta) -> str   # GET
    async def presigned_upload_url(bucket, key, *, expires: timedelta, content_type=None) -> str  # PUT（単発）
    async def begin_multipart(bucket, key, *, content_type=None, metadata=None) -> MultipartUpload

    # --- 便利メソッド（基底の具象。基本操作の組み合わせのみ） ---
    async def exists(bucket, key) -> bool                # head の ObjectNotFoundError を False に
    async def put_file(bucket, key, path: Path, *, content_type=None, metadata=None,
                       multipart_threshold=8 MiB, part_size=8 MiB) -> ObjectInfo
    async def download_file(bucket, key, path: Path) -> None

class MultipartUpload(ABC):
    async def upload_part(part_number: int, data: bytes) -> None   # 1始まり。並行呼び出し可
    async def complete() -> ObjectInfo
    async def abort() -> None
    # async with で使い、complete 前に抜けた場合は自動で abort

@dataclass(frozen=True)
class ObjectInfo:  bucket(論理名), key, size, content_type, etag, last_modified, metadata
```

- 署名URL生成は `async`。`aiobotocore` の `generate_presigned_url` と `gcloud-aio-storage` の `get_signed_url`（IAM署名時）がコルーチンであるため。
- `put_file`: サイズが閾値以下なら `put`、超えたらチャンクごとに `upload_part` して `complete`（途中で失敗したら `abort`）。アダプタごとの実装は不要。
- `delete` の冪等化: S3は存在しなくても成功するが、GCS/Azureは404を返すため、アダプタ側で吸収する。

#### マルチパートの制約（ABCのdocstringに明記し、テストで担保）

- パート番号は 1〜10,000。最後のパート以外は 5 MiB 以上（S3の制約。3社の最大公約数）
- **S3**: `create_multipart_upload` / `upload_part`（ETagを内部保持）/ `complete_multipart_upload` / `abort_multipart_upload`
- **Azure**: `stage_block` / `commit_block_list`。`abort` は未コミットブロックを放置（7日で自動破棄）
- **GCS**: 各パートを一時オブジェクトとして保存し、`complete` で `compose` する。1回の `compose` は最大32個のため、超える場合は段階的に結合する。完了後と `abort` 時に一時オブジェクトを削除する。一時オブジェクトのキーは対象キーと衝突しない専用のプレフィックス配下に置く

### `InMemoryObjectStorage`（`scheme="memory"`）

- 保存先はインスタンスごとの `dict[(物理バケット, key)]`。プロセス外には出ない。
- 署名URLは `NotSupportedError`。
- マルチパートは各パートをメモリに保持し、`complete` で番号順に連結する。実バックエンドと同じ制約（パート番号範囲、最後以外の最小サイズ）を課す。最小サイズはコンストラクタ引数（`min_part_size`、既定 5 MiB）でテスト時に小さくできる。テスト用フェイクが甘いと本番でのみ失敗するため、制約は緩めない。

### エラー階層

アプリが各社のSDK例外型に依存しないよう、既知のケースのみ正規化する。

```
ObjectStorageError(Exception)              # 基底。未分類のSDK例外はここに包む（__cause__ に原因を保持）
├─ UnknownBucketError                      # 論理名が buckets に未登録（登録名の一覧をメッセージに含む）
├─ BucketNotFoundError                     # 物理バケットが存在しない（設定ミスの検知）
├─ ObjectNotFoundError                     # head / get / get_stream で対象が無い
├─ PermissionDeniedError                   # 403 / AccessDenied 相当
└─ NotSupportedError                       # そのバックエンドが対応しない操作
```

タイムアウト等の再試行は、SDK側の設定を `**client_kwargs` で利用側が制御する。

## テスト方針

| 対象 | 方法 | CI |
|---|---|---|
| 基底（論理名解決、`put_file` の閾値分岐と失敗時の `abort`、`exists`） | InMemory実装で検証 | 常時 |
| InMemory実装 | 共通の契約テスト（全操作、冪等な `delete`、マルチパート制約） | 常時 |
| factory / lifespan / `get_object_storage(name)` | scheme分岐、未インストールextraのエラー、未登録名のエラー、名前の独立性 | 常時 |
| S3 / GCS / Azureアダプタ | SDKクライアントをクラスレベルでフェイクにし、引数の対応づけ（物理名への変換、エラー正規化、`delete` の冪等化、GCSの32超での段階 `compose`）を検証 | 常時 |
| 実バックエンド（MinIO / Azurite / fake-gcs-server） | 同じ契約テストを `integration` マーカーで再利用。エンドポイントの環境変数が無ければ skip。署名URLはここで実物に対して検証 | 任意（手元） |
| fastapi-toolkit | 再エクスポートの確認のみ | 常時 |
| fastmcp-toolkit | `object_storage_lifespan` と `CurrentObjectStorage`。未登録名の詳細メッセージはRPC越しに失われる（DB接続部品のspec参照）ため、依存解決関数を直接呼ぶユニットテストで検証 | 常時 |

- 契約テストは1本にまとめ、InMemoryと各実バックエンドの両方に流す。フェイクと実物の挙動差を検出する足場とする。
- `get_object_storage(name)` は呼び出すたびに新しいクロージャを返す。`dependency_overrides` のキーにする場合はモジュールレベルで1回だけ呼んで変数に固定する（Redis作業での知見）。
- 公開APIのdocstringは日本語・Googleスタイル（プロジェクト規約）。

## 将来の拡張（設計メモ）

- **論理名解決のCallable化**: 現状は `Mapping[str, str]` のみ。実行時に動的な解決が必要になった場合は、引数の型を `Mapping[str, str] | Callable[[str], str]` に広げる（後方互換）。その際、未登録名の検知（`UnknownBucketError`）の責務をCallable側に移す点に注意する。設定値管理機能を導入する際は、まずMappingを組み立てて渡す形で足りる想定。
- **`put_file` の並列化**: `concurrency` 引数を追加し、`upload_part` を並行実行する。`MultipartUpload.upload_part` は並行呼び出し可と規定済みのため、ABCの変更は不要。
- **SDKクライアントの共有**: 同一アカウントで名前付きインスタンスを複数登録する場合の接続プール共有。必要になった時点で検討する。
- **`put_stream`**: リクエストボディの直接ストリーム転送が必要になった場合に、`begin_multipart` の上に便利メソッドとして追加できる。
