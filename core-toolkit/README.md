# core-toolkit

Starlette / ASGI 向け共通ミドルウェアライブラリ。

fastapi-toolkit および fastmcp-toolkit から共有されるミドルウェアを提供する。

## 提供コンポーネント

- `AccessLogMiddleware` — structlog ベースの構造化アクセスログ
- `TransportSecurityASGIMiddleware` — DNS Rebinding 防御（Host/Origin/Content-Type 検証）
- `SecurityHeadersMiddleware` — セキュリティヘッダー付与（JSON / HTML プリセット）

## インストール

```bash
uv add core-toolkit --path ../core-toolkit
```
