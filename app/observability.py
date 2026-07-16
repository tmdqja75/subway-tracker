"""OpenTelemetry setup for the shared Grafana LGTM instance."""

import logging
from dataclasses import dataclass

from fastapi import FastAPI
from opentelemetry import _logs, metrics, trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from .config import Settings

log = logging.getLogger(__name__)


class AppLogFilter(logging.Filter):
    """Export application logs without recursively exporting OTEL internals."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name == "app" or record.name.startswith("app.")


@dataclass
class Observability:
    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    logger_provider: LoggerProvider

    def shutdown(self) -> None:
        self.logger_provider.shutdown()
        self.meter_provider.shutdown()
        self.tracer_provider.shutdown()


def _signal_endpoint(endpoint: str, signal: str) -> str:
    return f"{endpoint.rstrip('/')}/v1/{signal}"


def configure_observability(app: FastAPI, settings: Settings) -> Observability | None:
    """Configure OTLP/HTTP exports when a shared collector endpoint is set."""
    if not settings.otel_exporter_otlp_endpoint:
        log.info("OpenTelemetry export disabled: OTEL_EXPORTER_OTLP_ENDPOINT is not set")
        return None

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": "0.1.0",
        }
    )
    timeout = settings.otel_export_timeout_seconds
    endpoint = settings.otel_exporter_otlp_endpoint

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=_signal_endpoint(endpoint, "traces"), timeout=timeout)
        )
    )
    trace.set_tracer_provider(tracer_provider)

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=_signal_endpoint(endpoint, "metrics"), timeout=timeout),
        export_interval_millis=settings.otel_metric_export_interval_millis,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(endpoint=_signal_endpoint(endpoint, "logs"), timeout=timeout)
        )
    )
    _logs.set_logger_provider(logger_provider)
    log_handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
    log_handler.addFilter(AppLogFilter())
    logging.getLogger().addHandler(log_handler)

    FastAPIInstrumentor.instrument_app(app)
    log.info(
        "OpenTelemetry export enabled service=%s endpoint=%s",
        settings.otel_service_name,
        endpoint,
    )
    return Observability(tracer_provider, meter_provider, logger_provider)
