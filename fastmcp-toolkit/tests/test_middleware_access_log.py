"""AccessLogMiddlewareのテスト。"""

import pytest

from fastmcp_toolkit.middleware import AccessLogMiddleware


async def _make_asgi_app(status: int = 200, response_body: bytes = b"OK"):
    """テスト用の最小ASGIアプリを作成する。"""

    async def app(scope, receive, send):
        # receiveを呼んでリクエストボディを消費する（実際のサーバーと同じ動作）
        await receive()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [[b"content-type", b"text/plain"]],
            }
        )
        await send({"type": "http.response.body", "body": response_body})

    return app


def _make_http_scope(
    method: str = "POST",
    path: str = "/mcp",
    query_string: bytes = b"",
    headers: list | None = None,
) -> dict:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query_string,
        "client": ("127.0.0.1", 12345),
        "headers": headers or [],
    }


@pytest.mark.asyncio
async def test_logs_request_and_response(capture_logs):
    """requestとresponseの2つのログが出力される。"""
    inner_app = await _make_asgi_app(status=200)
    middleware = AccessLogMiddleware(inner_app)

    async def receive():
        return {"type": "http.request", "body": b'{"method":"test"}'}

    async def send(message):
        pass

    await middleware(_make_http_scope("POST", "/mcp"), receive, send)

    request_logs = [e for e in capture_logs if e.get("event") == "request"]
    response_logs = [e for e in capture_logs if e.get("event") == "response"]
    assert len(request_logs) == 1
    assert len(response_logs) == 1


@pytest.mark.asyncio
async def test_request_log_contains_body(capture_logs):
    """リクエストログにリクエストボディが含まれる。"""
    inner_app = await _make_asgi_app(status=200)
    middleware = AccessLogMiddleware(inner_app)

    async def receive():
        return {"type": "http.request", "body": b'{"tool":"hello"}'}

    async def send(message):
        pass

    await middleware(_make_http_scope("POST", "/mcp"), receive, send)

    request_log = next(e for e in capture_logs if e["event"] == "request")
    assert request_log["request_body"] == '{"tool":"hello"}'
    assert request_log["method"] == "POST"
    assert request_log["path"] == "/mcp"


@pytest.mark.asyncio
async def test_response_log_contains_body(capture_logs):
    """レスポンスログにレスポンスボディが含まれる。"""
    inner_app = await _make_asgi_app(status=200, response_body=b'{"result":"ok"}')
    middleware = AccessLogMiddleware(inner_app)

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        pass

    await middleware(_make_http_scope("POST", "/mcp"), receive, send)

    response_log = next(e for e in capture_logs if e["event"] == "response")
    assert response_log["response_body"] == '{"result":"ok"}'
    assert response_log["status"] == 200
    assert "duration_ms" in response_log


@pytest.mark.asyncio
async def test_captures_status_code(capture_logs):
    """レスポンスログにステータスコードが含まれる。"""
    inner_app = await _make_asgi_app(status=404)
    middleware = AccessLogMiddleware(inner_app)

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        pass

    await middleware(_make_http_scope("GET", "/not-found"), receive, send)

    response_log = next(e for e in capture_logs if e["event"] == "response")
    assert response_log["status"] == 404


@pytest.mark.asyncio
async def test_passthrough_non_http():
    """非HTTPスコープ（websocket、lifespan）はログなしで通過する。"""
    called = False

    async def inner_app(scope, receive, send):
        nonlocal called
        called = True

    middleware = AccessLogMiddleware(inner_app)
    await middleware({"type": "lifespan"}, None, None)

    assert called


@pytest.mark.asyncio
async def test_skips_health_and_metrics_paths(capture_logs):
    """ヘルスチェックとメトリクスパスはログ出力しない。"""
    inner_app = await _make_asgi_app(status=200)

    for path in ("/livez", "/readyz", "/metrics"):
        middleware = AccessLogMiddleware(inner_app)

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            pass

        await middleware(_make_http_scope("GET", path), receive, send)

    assert len(capture_logs) == 0


@pytest.mark.asyncio
async def test_includes_request_headers(capture_logs):
    """リクエストログにヘッダが含まれる。"""
    inner_app = await _make_asgi_app(status=200)
    middleware = AccessLogMiddleware(inner_app)

    scope = _make_http_scope(
        headers=[(b"content-type", b"application/json"), (b"x-request-id", b"abc123")],
    )

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        pass

    await middleware(scope, receive, send)

    request_log = next(e for e in capture_logs if e["event"] == "request")
    assert request_log["headers"]["content-type"] == "application/json"
    assert request_log["headers"]["x-request-id"] == "abc123"


@pytest.mark.asyncio
async def test_includes_response_headers(capture_logs):
    """レスポンスログにレスポンスヘッダが含まれる。"""
    inner_app = await _make_asgi_app(status=200)
    middleware = AccessLogMiddleware(inner_app)

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        pass

    await middleware(_make_http_scope(), receive, send)

    response_log = next(e for e in capture_logs if e["event"] == "response")
    assert response_log["headers"]["content-type"] == "text/plain"


@pytest.mark.asyncio
async def test_includes_query_string(capture_logs):
    """リクエストログにクエリストリングが含まれる。"""
    inner_app = await _make_asgi_app(status=200)
    middleware = AccessLogMiddleware(inner_app)

    scope = _make_http_scope(query_string=b"page=1&limit=10")

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        pass

    await middleware(scope, receive, send)

    request_log = next(e for e in capture_logs if e["event"] == "request")
    assert request_log["query_string"] == "page=1&limit=10"
