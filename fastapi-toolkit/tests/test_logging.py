import json
import logging

import pytest
import structlog

from fastapi_toolkit.logging import get_logger, setup_logging


@pytest.fixture(autouse=True)
def _restore_logging():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    structlog.contextvars.clear_contextvars()
    root.handlers[:] = handlers
    root.setLevel(level)


def _last_json(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().err.strip().splitlines()[-1])


def test_setup_logging_does_not_raise():
    setup_logging(json_output=False)


def test_stdlib_extra_is_included_in_json_output(capsys):
    setup_logging(application_id="app", json_output=True)

    logging.getLogger("third.party").info("tool_call", extra={"tool": "say_hello", "tool_args": {"name": "x"}})

    event = _last_json(capsys)
    assert event["message"] == "tool_call"
    assert event["tool"] == "say_hello"
    assert event["tool_args"] == {"name": "x"}
    assert event["application_id"] == "app"


def test_stdlib_log_includes_bound_contextvars(capsys):
    setup_logging(json_output=True)

    with structlog.contextvars.bound_contextvars(request_id="r1", session_id="s1"):
        logging.getLogger("third.party").info("hello", extra={"tool": "t"})

    event = _last_json(capsys)
    assert event["request_id"] == "r1"
    assert event["session_id"] == "s1"
    assert event["tool"] == "t"


def test_structlog_native_log_is_unchanged(capsys):
    setup_logging(json_output=True)

    get_logger("native").info("hello", tool="t")

    event = _last_json(capsys)
    assert event["message"] == "hello"
    assert event["tool"] == "t"
    assert event["logger"] == "native"
