import logging
import sys


def test_http_client_info_logs_are_suppressed_to_avoid_logging_api_keys():
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    old_root_level = root.level
    old_httpx_level = logging.getLogger("httpx").level
    old_httpcore_level = logging.getLogger("httpcore").level
    root.handlers.clear()
    root.setLevel(logging.NOTSET)
    logging.getLogger("httpx").setLevel(logging.NOTSET)
    logging.getLogger("httpcore").setLevel(logging.NOTSET)

    sys.modules.pop("app.main", None)

    try:
        import app.main  # noqa: F401 - importing configures application logging

        assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
        assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
    finally:
        root.handlers.clear()
        root.handlers.extend(old_handlers)
        root.setLevel(old_root_level)
        logging.getLogger("httpx").setLevel(old_httpx_level)
        logging.getLogger("httpcore").setLevel(old_httpcore_level)
