from fastapi_toolkit.logging import setup_logging


def test_setup_logging_does_not_raise():
    setup_logging(json_output=False)
