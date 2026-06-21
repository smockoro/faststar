"""log_type別のログ出力形式テスト。"""

from functools import partial

import pytest
import structlog
import structlog.testing

from fastmcp_toolkit.logging import _add_application_id, get_logger, setup_logging
from fastmcp_toolkit.middleware import AccessLogMiddleware


def _pre_chain(application_id: str = "test-server") -> list[structlog.types.Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        partial(_add_application_id, _application_id=application_id),
    ]


class TestApplicationLog:
    """log_type=application: get_logger()経由のユーザーコードからのログ。"""

    def test_includes_log_type(self):
        setup_logging(application_id="test-server")
        logger = get_logger("myapp")

        with structlog.testing.capture_logs(_pre_chain()) as logs:
            logger.info("hello", name="world")

        assert len(logs) == 1
        entry = logs[0]
        assert entry["event"] == "hello"
        assert entry["log_type"] == "application"
        assert entry["name"] == "world"

    def test_includes_application_id(self):
        setup_logging(application_id="my-mcp-server")
        logger = get_logger("myapp")

        with structlog.testing.capture_logs(_pre_chain("my-mcp-server")) as logs:
            logger.info("started")

        entry = logs[0]
        assert entry["application_id"] == "my-mcp-server"

    def test_no_application_id_when_empty(self):
        setup_logging(application_id="")
        logger = get_logger("myapp")

        with structlog.testing.capture_logs(_pre_chain("")) as logs:
            logger.info("started")

        entry = logs[0]
        assert "application_id" not in entry

    def test_custom_log_type(self):
        setup_logging(application_id="test-server")
        logger = get_logger("myapp", log_type="custom")

        with structlog.testing.capture_logs(_pre_chain()) as logs:
            logger.info("event")

        entry = logs[0]
        assert entry["log_type"] == "custom"


class TestAccessLog:
    """log_type=access: AccessLogMiddlewareからのログ。"""

    @pytest.mark.asyncio
    async def test_request_log_format(self):
        setup_logging(application_id="test-server")

        async def inner_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = AccessLogMiddleware(inner_app)
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "query_string": b"key=val",
            "client": ("10.0.0.1", 9999),
            "headers": [(b"content-type", b"application/json")],
        }

        with structlog.testing.capture_logs(_pre_chain()) as logs:
            await middleware(scope, _receive, _noop_send)

        request_log = logs[0]
        assert request_log["event"] == "request"
        assert request_log["log_type"] == "access"
        assert request_log["application_id"] == "test-server"
        assert request_log["method"] == "POST"
        assert request_log["path"] == "/mcp"
        assert request_log["query_string"] == "key=val"
        assert request_log["headers"] == {"content-type": "application/json"}

    @pytest.mark.asyncio
    async def test_response_log_format(self):
        setup_logging(application_id="test-server")

        async def inner_app(scope, receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 202,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", b"0"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b""})

        middleware = AccessLogMiddleware(inner_app)
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "query_string": b"",
            "client": ("10.0.0.1", 9999),
            "headers": [],
        }

        with structlog.testing.capture_logs(_pre_chain()) as logs:
            await middleware(scope, _receive, _noop_send)

        response_log = logs[1]
        assert response_log["event"] == "response"
        assert response_log["log_type"] == "access"
        assert response_log["application_id"] == "test-server"
        assert response_log["status"] == 202
        assert response_log["path"] == "/mcp"
        assert response_log["query_string"] == ""
        assert response_log["headers"] == {
            "content-type": "application/json",
            "content-length": "0",
        }
        assert "duration_ms" in response_log


async def _receive():
    return {"type": "http.request", "body": b""}


async def _noop_send(message):
    pass
