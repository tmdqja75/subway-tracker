import logging

from app.observability import AppLogFilter, _signal_endpoint


def test_signal_endpoint_uses_otlp_http_paths():
    assert _signal_endpoint("http://127.0.0.1:4318", "traces") == "http://127.0.0.1:4318/v1/traces"
    assert _signal_endpoint("http://127.0.0.1:4318/", "metrics") == "http://127.0.0.1:4318/v1/metrics"


def test_app_log_filter_exports_only_application_records():
    log_filter = AppLogFilter()

    assert log_filter.filter(logging.LogRecord("app.journey", logging.INFO, "", 0, "message", (), None))
    assert not log_filter.filter(logging.LogRecord("opentelemetry.sdk", logging.INFO, "", 0, "message", (), None))
    assert not log_filter.filter(logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "message", (), None))
