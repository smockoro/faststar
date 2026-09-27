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

from fastapi_toolkit.error_handling import ErrorHandlingMiddleware, handle_application_error


class _RaisingMiddleware(BaseHTTPMiddleware):
    """ルーターより外側でApplicationProblemを送出するユーザー定義ミドルウェアのダミー。"""

    async def dispatch(self, request: StarletteRequest, call_next):
        if request.url.path == "/raise-in-middleware":
            raise BusinessProblem("business.blocked", "ミドルウェアで拒否")
        return await call_next(request)


def _make_app() -> FastAPI:
    app = FastAPI(
        lifespan=create_lifespan(open_error_mapping({"business.not_found": ErrorCodeMapping(http_status=404)})),
        exception_handlers={ApplicationProblem: handle_application_error},
    )

    @app.get("/business")
    async def business_route() -> None:
        raise BusinessProblem("business.not_found", "見つからない")

    @app.get("/system")
    async def system_route() -> None:
        raise SystemProblem("system.unknown", "内部エラー")

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
