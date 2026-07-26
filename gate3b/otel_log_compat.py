from __future__ import annotations

from importlib import import_module, metadata
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


PUBLIC_CANDIDATES: dict[str, tuple[str, ...]] = {
    "OTLPLogExporter": ("opentelemetry.exporter.otlp.proto.http.log_exporter:OTLPLogExporter",),
    "LoggerProvider": ("opentelemetry.sdk.logs:LoggerProvider",),
    "LoggingHandler": ("opentelemetry.sdk.logs:LoggingHandler",),
    "InMemoryLogExporter": ("opentelemetry.sdk.logs.export:InMemoryLogExporter", "opentelemetry.sdk.logs.export:InMemoryLogRecordExporter"),
    "SimpleLogRecordProcessor": ("opentelemetry.sdk.logs.export:SimpleLogRecordProcessor",),
    "LogExportResult": ("opentelemetry.sdk.logs.export:LogExportResult",),
    "LogRecordExportResult": ("opentelemetry.sdk.logs.export:LogRecordExportResult",),
}

PRIVATE_CANDIDATES: dict[str, tuple[str, ...]] = {
    "OTLPLogExporter": ("opentelemetry.exporter.otlp.proto.http._log_exporter:OTLPLogExporter",),
    "LoggerProvider": ("opentelemetry.sdk._logs:LoggerProvider",),
    "LoggingHandler": ("opentelemetry.sdk._logs:LoggingHandler",),
    "InMemoryLogExporter": ("opentelemetry.sdk._logs.export:InMemoryLogExporter",),
    "SimpleLogRecordProcessor": ("opentelemetry.sdk._logs.export:SimpleLogRecordProcessor",),
    "LogExportResult": ("opentelemetry.sdk._logs.export:LogExportResult",),
    "LogRecordExportResult": ("opentelemetry.sdk._logs.export:LogRecordExportResult",),
}


def _import_attr(path: str) -> Any:
    module_name, attr_name = path.split(":", 1)
    module = import_module(module_name)
    return getattr(module, attr_name)


def _select_component(name: str) -> tuple[Any, str, bool, list[str]]:
    attempted: list[str] = []
    for path in PUBLIC_CANDIDATES[name]:
        attempted.append(path)
        try:
            return _import_attr(path), path, False, attempted
        except (ImportError, AttributeError, ModuleNotFoundError):
            pass
    for path in PRIVATE_CANDIDATES[name]:
        attempted.append(path)
        try:
            return _import_attr(path), path, True, attempted
        except (ImportError, AttributeError, ModuleNotFoundError):
            pass
    raise Gate3BLogCompatibilityError(f"No supported OpenTelemetry import path is available for {name}.")


_SELECTED: dict[str, Any] = {}
_SELECTED_PATHS: dict[str, str] = {}
_PUBLIC_ATTEMPTED: list[str] = []
_PRIVATE_FALLBACK_USED = False

for _name in PUBLIC_CANDIDATES:
    _value, _path, _private, _attempted = _select_component(_name)
    _SELECTED[_name] = _value
    _SELECTED_PATHS[_name] = _path
    _PUBLIC_ATTEMPTED.extend(_attempted)
    _PRIVATE_FALLBACK_USED = _PRIVATE_FALLBACK_USED or _private

OTLPLogExporter = _SELECTED["OTLPLogExporter"]
LoggerProvider = _SELECTED["LoggerProvider"]
LoggingHandler = _SELECTED["LoggingHandler"]
InMemoryLogExporter = _SELECTED["InMemoryLogExporter"]
SimpleLogRecordProcessor = _SELECTED["SimpleLogRecordProcessor"]
LogExportResult = _SELECTED["LogExportResult"]
LogRecordExportResult = _SELECTED["LogRecordExportResult"]


def compatibility_contract() -> dict[str, Any]:
    return {
        "logger_provider_path": _SELECTED_PATHS["LoggerProvider"],
        "logging_handler_path": _SELECTED_PATHS["LoggingHandler"],
        "in_memory_exporter_path": _SELECTED_PATHS["InMemoryLogExporter"],
        "processor_path": _SELECTED_PATHS["SimpleLogRecordProcessor"],
        "result_enum_paths": {
            "LogExportResult": _SELECTED_PATHS["LogExportResult"],
            "LogRecordExportResult": _SELECTED_PATHS["LogRecordExportResult"],
        },
        "otlp_exporter_path": _SELECTED_PATHS["OTLPLogExporter"],
        "public_paths_attempted": list(_PUBLIC_ATTEMPTED),
        "private_fallback_used": _PRIVATE_FALLBACK_USED,
        "opentelemetry_versions": dict(OPENTELEMETRY_VERSIONS),
    }
