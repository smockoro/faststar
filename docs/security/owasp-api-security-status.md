# OWASP API Security 対応状況メモ

- 日付: 2026-09-26
- スコープ: `core-toolkit` / `fastapi-toolkit` / `fastmcp-toolkit`
- 位置づけ: 新規設計ではなく、既存実装がOWASPの各観点にどう対応しているかを
  棚卸しした現状メモ。今後ミドルウェアを追加・変更した際はこの表を更新する。

## 背景

「APIセキュリティに関するOWASP系の文書が何かあるか」という問いに対し、
リポジトリ内を確認したところ独立したセキュリティポリシー文書
（`SECURITY.md`等）は存在せず、OWASP関連の記述は
`fastmcp-toolkit/src/fastmcp_toolkit/cors.py` のdocstring内の1箇所のみ
だった。一方でセキュリティ関連のASGIミドルウェアはcore-toolkitに多数実装
済みであるため、対応状況を横断的に整理しておく。

## 実装済みミドルウェア一覧

| モジュール | 種別 | 概要 |
|---|---|---|
| `middleware/csp.py` | セキュアヘッダー | `Content-Security-Policy` |
| `middleware/hsts.py` | セキュアヘッダー | `Strict-Transport-Security` |
| `middleware/frame_guard.py` | セキュアヘッダー | `X-Frame-Options`（クリックジャッキング対策） |
| `middleware/no_sniff.py` | セキュアヘッダー | `X-Content-Type-Options: nosniff` |
| `middleware/xss_protection.py` | セキュアヘッダー | `X-XSS-Protection` |
| `middleware/referrer_policy.py` | セキュアヘッダー | `Referrer-Policy` |
| `middleware/permissions_policy.py` | セキュアヘッダー | `Permissions-Policy` |
| `middleware/coop.py` | セキュアヘッダー | `Cross-Origin-Opener-Policy` |
| `middleware/coep.py` | セキュアヘッダー | `Cross-Origin-Embedder-Policy` |
| `middleware/corp.py` | セキュアヘッダー | `Cross-Origin-Resource-Policy` |
| `middleware/dns_prefetch_control.py` | セキュアヘッダー | `X-DNS-Prefetch-Control` |
| `middleware/robots_tag.py` | セキュアヘッダー | `X-Robots-Tag` |
| `middleware/server_header_strip.py` | 情報漏えい対策 | `Server` 等のレスポンスヘッダー除去 |
| `middleware/origin_validation.py` | アクセス制御 | Originヘッダー許可リスト照合（CSRF/クロスオリジン対策） |
| `middleware/host_validation.py` | アクセス制御 | Hostヘッダー許可リスト照合（DNS Rebinding対策） |
| `middleware/json_content_type.py` | 入力検証 | POSTリクエストのContent-Type検証 |
| `middleware/cache_control.py` | 情報漏えい対策 | `Cache-Control`（機微情報のキャッシュ抑止） |
| `middleware/access_log.py` | 監視・監査 | structlogベースのアクセスログ |
| `metrics.py` | 監視・監査 | Prometheusメトリクス |
| `health.py` | 可用性 | Readiness/Livenessチェック |
| `token_cache_cipher.py` | 認証情報保護 | MSALトークンキャッシュのJWE暗号化（鍵ローテーション対応） |
| `msal_lifespan.py` | 認証 | MSAL（Azure AD等）連携のlifespan管理 |
| `fastmcp_toolkit/cors.py` | アクセス制御 | MCPサーバー向けCORS設定ファクトリ（OWASP推奨のデフォルト値を明記） |

## OWASP API Security Top 10 (2023) との対応

| ID | カテゴリ | 対応状況 |
|---|---|---|
| API1 | Broken Object Level Authorization | 未対応（アプリ側の実装責務。toolkitはオブジェクト単位の認可機構を提供しない） |
| API2 | Broken Authentication | 部分対応。MSAL連携・トークンキャッシュ暗号化はあるが、認証方式自体の選定・トークン検証ロジックはアプリ側 |
| API3 | Broken Object Property Level Authorization | 未対応（アプリ側の責務） |
| API4 | Unrestricted Resource Consumption | 未対応。レート制限・リクエストサイズ制限系のミドルウェアは現状なし |
| API5 | Broken Function Level Authorization | 未対応（アプリ側の責務） |
| API6 | Unrestricted Access to Sensitive Business Flows | 未対応（アプリ側の責務） |
| API7 | Server Side Request Forgery | 未対応。SSRF対策（送信先URL検証等）のミドルウェアは現状なし |
| API8 | Security Misconfiguration | **主要な対応領域**。セキュアヘッダー群、Origin/Host検証、CORS設定ファクトリ、Server ヘッダー除去が該当 |
| API9 | Improper Inventory Management | 未対応（ドキュメント・運用プロセスの話であり、ミドルウェアでは解決しない） |
| API10 | Unsafe Consumption of APIs | 部分対応。`httpx_lifespan.py`/`aiohttp_lifespan.py` で外部API呼び出しのクライアントを共通化しているが、レスポンス検証等は現状アプリ側 |

## 所感・今後の検討候補

- 現状のtoolkitは**セキュアヘッダーとOrigin/Host検証（API8寄り）に厚く、
  認可・レート制限・SSRF対策（API1/API4/API5/API7）は薄い**という偏りがある。
  これは「共通ミドルウェアとして横展開しやすいもの」を優先してきた結果と
  考えられ、設計として妥当ではあるが、アプリ側が自前で埋める必要がある
  領域として認識しておく。
- レート制限（API4）は複数アプリで重複実装されやすい領域のため、
  toolkit化する場合は候補になり得る。ただしRedis依存が前提になるため、
  `redis_lifespan.py` との連携方針を別途設計する必要がある。
- `SECURITY.md`のような脆弱性報告手順・サポートポリシーは現状未整備。
  OSSとして公開する場合は別途検討する。

## 参照

- [OWASP API Security Top 10 (2023)](https://owasp.org/API-Security/editions/2023/en/0x11-t10/)
- `fastmcp-toolkit/src/fastmcp_toolkit/cors.py`（リポジトリ内でOWASPに直接言及している唯一の箇所）
