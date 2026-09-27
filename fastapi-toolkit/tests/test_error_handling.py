"""ApplicationProblem系例外のRFC 7807変換テスト。"""

import pytest
from core_toolkit.error_mapping import ErrorCodeMapping
from core_toolkit.error_mapping_lifespan import open_error_mapping
from core_toolkit.errors import ApplicationProblem, BusinessProblem, SystemProblem
from core_toolkit.lifespan import create_lifespan
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.types import Message, Receive, Scope, Send

from fastapi_toolkit.error_handling import ErrorHandlingMiddleware, handle_application_error


class _RaisingMiddleware(BaseHTTPMiddleware):
    """ルーターより外側でApplicationProblemを送出するユーザー定義ミドルウェアのダミー。"""

    async def dispatch(self, request: StarletteRequest, call_next):
        if request.url.path == "/raise-in-middleware":
            raise BusinessProblem("business.blocked", "ミドルウェアで拒否")
        if request.url.path == "/system-in-middleware":
            raise SystemProblem("system.auth_backend_down", "auth-db-01.internal:5432 unreachable")
        return await call_next(request)


def _make_app() -> FastAPI:
    app = FastAPI(
        lifespan=create_lifespan(
            open_error_mapping(
                {
                    "business.not_found": ErrorCodeMapping(http_status=404),
                    "business.upstream_unavailable": ErrorCodeMapping(http_status=503),
                }
            )
        ),
        exception_handlers={ApplicationProblem: handle_application_error},
    )

    @app.get("/business")
    async def business_route() -> None:
        raise BusinessProblem("business.not_found", "見つからない")

    @app.get("/system")
    async def system_route() -> None:
        raise SystemProblem("system.unknown", "SELECT * FROM users WHERE id=42 failed on db-01")

    @app.get("/business-5xx")
    async def business_5xx_route() -> None:
        raise BusinessProblem("business.upstream_unavailable", "外部サービスが一時的に利用不可")

    @app.get("/raise-in-middleware")
    async def middleware_route() -> dict[str, str]:
        return {"ok": "true"}

    # _RaisingMiddlewareが先、ErrorHandlingMiddlewareが最後
    # （Starletteは最後にadd_middlewareしたものが一番外側になるため）。
    app.add_middleware(_RaisingMiddleware)
    app.add_middleware(ErrorHandlingMiddleware)
    return app


@pytest.fixture
async def client():
    # httpx.ASGITransportはASGIの"lifespan"プロトコルを送出しないため
    # （httpリクエストのみを扱う設計）、FastAPI(lifespan=...)のstartup/shutdown
    # を明示的に起動しないとcreate_lifespan(open_error_mapping(...))が実行されず
    # app.state.error_mappingが設定されない。app.router.lifespan_contextを直接
    # 使ってstartup/shutdownを能動的に駆動する。
    app = _make_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        yield AsyncClient(transport=transport, base_url="http://test")


class TestExceptionHandler:
    @pytest.mark.asyncio
    async def test_business_problem_from_route_returns_mapped_status(self, client):
        async with client as c:
            response = await c.get("/business")
        assert response.status_code == 404
        body = response.json()
        assert body["title"] == "business.not_found"
        assert body["detail"] == "見つからない"
        assert body["status"] == 404
        assert response.headers["content-type"] == "application/problem+json"

    @pytest.mark.asyncio
    async def test_system_problem_from_route_falls_back_to_500(self, client):
        async with client as c:
            response = await c.get("/system")
        assert response.status_code == 500
        assert response.json()["title"] == "system.unknown"

    @pytest.mark.asyncio
    async def test_system_problem_message_is_masked_in_detail(self, client):
        async with client as c:
            response = await c.get("/system")
        body = response.json()
        assert body["title"] == "system.unknown"
        assert body["detail"] == "Internal server error"
        assert "SELECT" not in response.text
        assert "db-01" not in response.text

    @pytest.mark.asyncio
    async def test_business_problem_mapped_to_5xx_keeps_message(self, client):
        """マスク判定はステータスではなく例外型で行う。"""
        async with client as c:
            response = await c.get("/business-5xx")
        assert response.status_code == 503
        assert response.json()["detail"] == "外部サービスが一時的に利用不可"

    @pytest.mark.asyncio
    async def test_non_application_problem_raises_type_error(self):
        request = StarletteRequest({"type": "http", "app": FastAPI()})
        with pytest.raises(TypeError, match="ApplicationProblem"):
            await handle_application_error(request, ValueError("boom"))


class TestErrorHandlingMiddleware:
    @pytest.mark.asyncio
    async def test_application_problem_from_middleware_is_caught_by_safety_net(self, client):
        async with client as c:
            response = await c.get("/raise-in-middleware")
        assert response.status_code == 400
        body = response.json()
        assert body["title"] == "business.blocked"
        assert body["detail"] == "ミドルウェアで拒否"

    @pytest.mark.asyncio
    async def test_system_problem_from_middleware_message_is_masked(self, client):
        async with client as c:
            response = await c.get("/system-in-middleware")
        assert response.status_code == 500
        body = response.json()
        assert body["title"] == "system.auth_backend_down"
        assert body["detail"] == "Internal server error"
        assert "auth-db-01" not in response.text

    @pytest.mark.asyncio
    async def test_exception_after_response_started_is_reraised(self):
        """レスポンス開始後の例外は2つ目のレスポンスに変換せず再送出する。"""
        sent: list[Message] = []

        async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            raise BusinessProblem("business.late", "送信開始後の失敗")

        async def receive() -> Message:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: Message) -> None:
            sent.append(message)

        middleware = ErrorHandlingMiddleware(downstream)
        # scope["app"]は意図的に渡さない: 変換経路に入ればget_error_mapping_resource
        # の段階で別の例外になるため、BusinessProblemそのものが届くことで
        # 「変換を試みずに再送出した」ことまで確認できる。
        with pytest.raises(BusinessProblem, match="送信開始後の失敗"):
            await middleware({"type": "http", "path": "/"}, receive, send)

        assert [m["type"] for m in sent] == ["http.response.start"]

    @pytest.mark.asyncio
    async def test_route_exception_does_not_reach_safety_net(self, client, monkeypatch):
        """ルートハンドラ由来の例外はexception_handlersのみが処理し、
        ErrorHandlingMiddleware側の変換処理（共通ヘルパー
        _build_problem_response）は呼ばれない（＝二重処理にならない）
        ことを、共通ヘルパーの呼び出し回数で直接確認する。
        """
        import fastapi_toolkit.error_handling as error_handling_module

        call_count = 0
        original = error_handling_module._build_problem_response

        def _counting_build_problem_response(exc, registry):
            nonlocal call_count
            call_count += 1
            return original(exc, registry)

        monkeypatch.setattr(
            error_handling_module,
            "_build_problem_response",
            _counting_build_problem_response,
        )

        async with client as c:
            response = await c.get("/business")

        assert response.status_code == 404
        assert call_count == 1
