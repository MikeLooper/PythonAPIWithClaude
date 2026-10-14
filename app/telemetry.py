"""OpenTelemetry setup: traces, metrics and logs exported to an OTLP collector."""

import logging
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, LogRecordExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

from app import __version__
from app.config import Settings

logger = logging.getLogger(__name__)

# How long repeated OTLP exporter warnings/errors (e.g. "collector unavailable") are suppressed.
EXPORTER_LOG_INTERVAL_SECONDS = 300.0


class ExcludeOtelInternalLogs(logging.Filter):
    """Keep the SDK's own log records out of the OTLP log pipeline.

    Without this, an "export failed" log is queued for export, fails, and logs again.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith("opentelemetry")


class ExporterErrorThrottle(logging.Filter):
    """Let the first exporter warning/error through, then drop repeats for ``interval`` seconds.

    While the collector is down, the exporters otherwise log an error every few seconds.
    """

    def __init__(
        self,
        interval: float = EXPORTER_LOG_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self._interval = interval
        self._clock = clock
        self._last_emitted: dict[tuple[str, object], float] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.WARNING or not record.name.startswith("opentelemetry.exporter"):
            return True
        key = (record.name, record.msg)
        now = self._clock()
        last = self._last_emitted.get(key)
        if last is not None and now - last < self._interval:
            return False
        self._last_emitted[key] = now
        return True


def collector_reachable(settings: Settings, timeout: float = 1.0) -> bool:
    """Return True if a TCP connection can be opened to the configured OTLP endpoint."""
    endpoint = settings.otel_exporter_otlp_endpoint
    parsed = urlsplit(endpoint if "://" in endpoint else f"http://{endpoint}")
    default_port = 4317 if settings.otel_exporter_otlp_protocol == "grpc" else 4318
    try:
        with socket.create_connection(
            (parsed.hostname or "localhost", parsed.port or default_port), timeout=timeout
        ):
            return True
    except OSError:
        return False


@dataclass(frozen=True, slots=True)
class OtlpExporters:
    span: SpanExporter
    metric: MetricExporter
    log: LogRecordExporter


@dataclass(slots=True)
class Telemetry:
    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    logger_provider: LoggerProvider
    log_handler: LoggingHandler
    # Each handler gets its own throttle, so every handler shows the first occurrence.
    exporter_throttles: list[tuple[logging.Handler, logging.Filter]] = field(default_factory=list)

    def shutdown(self) -> None:
        """Flush and stop all exporters, then undo the logging changes."""
        logging.getLogger().removeHandler(self.log_handler)
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()
        self.logger_provider.shutdown()
        for handler, throttle in self.exporter_throttles:
            handler.removeFilter(throttle)


def create_otlp_exporters(settings: Settings) -> OtlpExporters:
    """Build OTLP exporters for the configured protocol (gRPC on 4317, HTTP on 4318)."""
    endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/")
    if settings.otel_exporter_otlp_protocol == "grpc":
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        insecure = endpoint.startswith("http://")
        return OtlpExporters(
            span=OTLPSpanExporter(endpoint=endpoint, insecure=insecure),
            metric=OTLPMetricExporter(endpoint=endpoint, insecure=insecure),
            log=OTLPLogExporter(endpoint=endpoint, insecure=insecure),
        )

    from opentelemetry.exporter.otlp.proto.http._log_exporter import (
        OTLPLogExporter as HttpLogExporter,
    )
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter as HttpMetricExporter,
    )
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter as HttpSpanExporter,
    )

    return OtlpExporters(
        span=HttpSpanExporter(endpoint=f"{endpoint}/v1/traces"),
        metric=HttpMetricExporter(endpoint=f"{endpoint}/v1/metrics"),
        log=HttpLogExporter(endpoint=f"{endpoint}/v1/logs"),
    )


def setup_telemetry(
    app: FastAPI,
    settings: Settings,
    exporter_factory: Callable[[Settings], OtlpExporters] = create_otlp_exporters,
    reachability_check: Callable[[Settings], bool] = collector_reachable,
) -> Telemetry:
    """Configure global OTEL providers, instrument FastAPI and bridge stdlib logging."""
    resource = Resource.create(
        {SERVICE_NAME: settings.otel_service_name, SERVICE_VERSION: __version__}
    )
    exporters = exporter_factory(settings)

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(exporters.span))
    trace.set_tracer_provider(tracer_provider)

    meter_provider = MeterProvider(
        resource=resource, metric_readers=[PeriodicExportingMetricReader(exporters.metric)]
    )
    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(exporters.log))
    log_handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
    log_handler.addFilter(ExcludeOtelInternalLogs())
    root_logger = logging.getLogger()
    exporter_throttles: list[tuple[logging.Handler, logging.Filter]] = [
        (handler, ExporterErrorThrottle()) for handler in root_logger.handlers
    ]
    for handler, throttle in exporter_throttles:
        handler.addFilter(throttle)
    root_logger.addHandler(log_handler)

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        excluded_urls="health",
    )
    logger.info(
        "OpenTelemetry enabled (endpoint=%s, protocol=%s)",
        settings.otel_exporter_otlp_endpoint,
        settings.otel_exporter_otlp_protocol,
    )
    if not reachability_check(settings):
        logger.warning(
            "OTEL collector is not reachable at %s. Telemetry is dropped until it is available; "
            "export resumes automatically. Start the collector with Docker-Otel-builder.bat, "
            "or set OTEL_ENABLED=false. Repeated exporter errors are logged at most every %ds.",
            settings.otel_exporter_otlp_endpoint,
            EXPORTER_LOG_INTERVAL_SECONDS,
        )
    return Telemetry(
        tracer_provider,
        meter_provider,
        logger_provider,
        log_handler,
        exporter_throttles,
    )
