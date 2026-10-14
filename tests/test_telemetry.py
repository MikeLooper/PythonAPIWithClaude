import logging
import socket
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http._log_exporter import (
    OTLPLogExporter as HttpLogExporter,
)
from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter as HttpMetricExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as HttpSpanExporter,
)
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.config import Settings
from app.telemetry import (
    ExporterErrorThrottle,
    OtlpExporters,
    Telemetry,
    collector_reachable,
    create_otlp_exporters,
    setup_telemetry,
)


def test_grpc_exporters_created() -> None:
    settings = Settings(_env_file=None, otel_exporter_otlp_endpoint="http://localhost:4317")

    exporters = create_otlp_exporters(settings)

    assert isinstance(exporters.span, OTLPSpanExporter)
    assert isinstance(exporters.metric, OTLPMetricExporter)
    assert isinstance(exporters.log, OTLPLogExporter)
    _shutdown(exporters)


def test_http_exporters_use_signal_paths() -> None:
    settings = Settings(
        _env_file=None,
        otel_exporter_otlp_endpoint="http://localhost:4318/",
        otel_exporter_otlp_protocol="http/protobuf",
    )

    exporters = create_otlp_exporters(settings)

    assert isinstance(exporters.span, HttpSpanExporter)
    assert isinstance(exporters.metric, HttpMetricExporter)
    assert isinstance(exporters.log, HttpLogExporter)
    assert exporters.span._endpoint == "http://localhost:4318/v1/traces"
    assert exporters.metric._endpoint == "http://localhost:4318/v1/metrics"
    assert exporters.log._endpoint == "http://localhost:4318/v1/logs"
    _shutdown(exporters)


@pytest.fixture
def in_memory_exporters() -> OtlpExporters:
    return OtlpExporters(
        span=InMemorySpanExporter(),
        metric=ConsoleMetricExporter(out=_NullWriter()),
        log=InMemoryLogRecordExporter(),
    )


@pytest.fixture
def console_handler() -> Iterator[logging.Handler]:
    """A root handler standing in for the console handler installed by logging.basicConfig."""
    handler = _ListHandler()
    root = logging.getLogger()
    root.addHandler(handler)
    yield handler
    root.removeHandler(handler)


def _setup(
    exporters: OtlpExporters,
    settings: Settings | None = None,
    app: FastAPI | None = None,
    reachable: bool = True,
) -> Telemetry:
    return setup_telemetry(
        app or FastAPI(),
        settings or Settings(_env_file=None),
        exporter_factory=lambda _: exporters,
        reachability_check=lambda _: reachable,
    )


def test_setup_telemetry_configures_providers(in_memory_exporters: OtlpExporters) -> None:
    app = FastAPI()
    settings = Settings(_env_file=None, otel_service_name="test-service")

    telemetry = _setup(in_memory_exporters, settings, app)
    try:
        resource = telemetry.tracer_provider.resource.attributes
        assert resource["service.name"] == "test-service"
        assert "service.version" in resource
        assert telemetry.log_handler in logging.getLogger().handlers
        assert getattr(app, "_is_instrumented_by_opentelemetry", False)
    finally:
        telemetry.shutdown()

    assert telemetry.log_handler not in logging.getLogger().handlers


def test_logs_are_exported(in_memory_exporters: OtlpExporters) -> None:
    telemetry = _setup(in_memory_exporters)
    try:
        logging.getLogger("tests.telemetry").warning("hello otel")
        telemetry.logger_provider.force_flush()
    finally:
        telemetry.shutdown()

    bodies = [log.log_record.body for log in in_memory_exporters.log.get_finished_logs()]
    assert "hello otel" in bodies


def test_otel_internal_logs_are_not_exported(in_memory_exporters: OtlpExporters) -> None:
    telemetry = _setup(in_memory_exporters)
    try:
        logging.getLogger("opentelemetry.exporter.otlp.proto.grpc.exporter").error(
            "Failed to export logs"
        )
        telemetry.logger_provider.force_flush()
    finally:
        telemetry.shutdown()

    bodies = [log.log_record.body for log in in_memory_exporters.log.get_finished_logs()]
    assert "Failed to export logs" not in bodies


def test_unreachable_collector_logs_single_warning(
    in_memory_exporters: OtlpExporters, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="app.telemetry"):
        telemetry = _setup(in_memory_exporters, reachable=False)
    telemetry.shutdown()

    warnings = [r for r in caplog.records if r.name == "app.telemetry"]
    assert len(warnings) == 1
    assert "not reachable at http://localhost:4317" in warnings[0].getMessage()


def test_reachable_collector_logs_no_warning(
    in_memory_exporters: OtlpExporters, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="app.telemetry"):
        telemetry = _setup(in_memory_exporters, reachable=True)
    telemetry.shutdown()

    assert not [r for r in caplog.records if r.name == "app.telemetry"]


def test_repeated_exporter_errors_are_throttled_on_console(
    in_memory_exporters: OtlpExporters, console_handler: "_ListHandler"
) -> None:
    exporter_logger = logging.getLogger("opentelemetry.exporter.otlp.proto.grpc.exporter")
    telemetry = _setup(in_memory_exporters)
    try:
        for _ in range(5):
            exporter_logger.error("Failed to export %s to %s", "logs", "localhost:4317")
    finally:
        telemetry.shutdown()

    assert len(_records_from(console_handler, exporter_logger.name)) == 1


def test_throttle_removed_on_shutdown(
    in_memory_exporters: OtlpExporters, console_handler: logging.Handler
) -> None:
    telemetry = _setup(in_memory_exporters)
    throttles = dict(telemetry.exporter_throttles)
    assert throttles[console_handler] in console_handler.filters

    telemetry.shutdown()

    assert throttles[console_handler] not in console_handler.filters


def test_each_handler_gets_its_own_throttle(
    in_memory_exporters: OtlpExporters, console_handler: "_ListHandler"
) -> None:
    second_handler = _ListHandler()
    logging.getLogger().addHandler(second_handler)
    exporter_logger = logging.getLogger("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    try:
        telemetry = _setup(in_memory_exporters)
        try:
            for _ in range(3):
                exporter_logger.warning("Transient error %s", "connection refused")
        finally:
            telemetry.shutdown()
    finally:
        logging.getLogger().removeHandler(second_handler)

    assert len(_records_from(console_handler, exporter_logger.name)) == 1
    assert len(_records_from(second_handler, exporter_logger.name)) == 1


def _record(
    name: str, level: int = logging.ERROR, msg: str = "Failed to export %s"
) -> logging.LogRecord:
    return logging.LogRecord(name, level, __file__, 1, msg, ("logs",), None)


def test_throttle_allows_again_after_interval() -> None:
    now = [0.0]
    throttle = ExporterErrorThrottle(interval=60, clock=lambda: now[0])
    name = "opentelemetry.exporter.otlp.proto.grpc.exporter"

    assert throttle.filter(_record(name)) is True
    now[0] = 59
    assert throttle.filter(_record(name)) is False
    now[0] = 60
    assert throttle.filter(_record(name)) is True


def test_throttle_tracks_messages_independently() -> None:
    throttle = ExporterErrorThrottle(interval=60, clock=lambda: 0.0)
    name = "opentelemetry.exporter.otlp.proto.grpc.exporter"

    assert throttle.filter(_record(name, msg="Failed to export %s")) is True
    assert throttle.filter(_record(name, msg="Transient error %s")) is True


@pytest.mark.parametrize(
    "record",
    [
        _record("app.services.claude_service"),
        _record("opentelemetry.exporter.otlp.proto.grpc.exporter", level=logging.INFO),
    ],
)
def test_throttle_ignores_other_records(record: logging.LogRecord) -> None:
    throttle = ExporterErrorThrottle(interval=60, clock=lambda: 0.0)

    assert all(throttle.filter(record) for _ in range(3))


def test_collector_reachable_true_when_listening() -> None:
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        settings = Settings(_env_file=None, otel_exporter_otlp_endpoint=f"http://127.0.0.1:{port}")

        assert collector_reachable(settings) is True


def test_collector_reachable_false_when_nothing_listening() -> None:
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
    settings = Settings(_env_file=None, otel_exporter_otlp_endpoint=f"127.0.0.1:{port}")

    assert collector_reachable(settings, timeout=0.5) is False


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _records_from(handler: "_ListHandler", logger_name: str) -> list[logging.LogRecord]:
    """Records from one logger, ignoring unrelated warnings such as the SDK's
    "Overriding of current TracerProvider is not allowed" emitted during setup."""
    return [r for r in handler.records if r.name == logger_name]


class _NullWriter:
    def write(self, _: str) -> int:
        return 0

    def flush(self) -> None:
        pass


def _shutdown(exporters: OtlpExporters) -> None:
    exporters.span.shutdown()
    exporters.metric.shutdown()
    exporters.log.shutdown()
