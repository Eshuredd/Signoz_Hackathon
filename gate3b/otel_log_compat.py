from __future__ import annotations

from importlib import metadata
from typing import Any

from .models import Gate3BInfrastructureError


class Gate3BLogExportError(Gate3BInfrastructureError):
    """Log export failed."""


class Gate3BLogCompatibilityError(Gate3BLogExportError):
    """Installed OpenTelemetry logging APIs do not match a supported contract."""


def _version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "not-installed"


OPENTELEMETRY_VERSIONS = {
    "opentelemetry-api": _version("opentelemetry-api"),
    "opentelemetry-sdk": _version("opentelemetry-sdk"),
    "opentelemetry-exporter-otlp-proto-http": _version("opentelemetry-exporter-otlp-proto-http"),
}

IMPORT_CONTRACT: dict[str, Any] = {
    "logger_provider_path": None,
    "exporter_path": None,
    "private_fallback_used": False,
    "opentelemetry_versions": OPENTELEMETRY_VERSIONS,
}


try:
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

    IMPORT_CONTRACT["exporter_path"] = "opentelemetry.exporter.otlp.proto.http._log_exporter.OTLPLogExporter"
    IMPORT_CONTRACT["private_fallback_used"] = True
except Exception as exc:  # pragma: no cover - depends on installed OpenTelemetry
    raise Gate3BLogCompatibilityError("No supported OTLP log exporter import contract is available.") from exc

try:
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import (
        InMemoryLogExporter,
        LogExportResult,
        LogRecordExportResult,
        SimpleLogRecordProcessor,
    )

    IMPORT_CONTRACT["logger_provider_path"] = "opentelemetry.sdk._logs"
    IMPORT_CONTRACT["private_fallback_used"] = True
except Exception as exc:  # pragma: no cover - depends on installed OpenTelemetry
    raise Gate3BLogCompatibilityError("No supported OpenTelemetry SDK logging import contract is available.") from exc


def compatibility_contract() -> dict[str, Any]:
    return dict(IMPORT_CONTRACT)
