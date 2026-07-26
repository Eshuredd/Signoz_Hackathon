from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from dotenv import load_dotenv

from .models import Gate3BConfigError


REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Gate3BConfig:
    signoz_base_url: str
    signoz_api_key: str
    trace_otlp_endpoint: str
    log_otlp_endpoint: str
    request_timeout_seconds: float
    ingestion_timeout_seconds: float
    poll_interval_seconds: float
    otlp_timeout_seconds: float
    service_name: str

    @classmethod
    def from_env(cls) -> "Gate3BConfig":
        load_dotenv(REPO_ROOT / ".env", override=False)
        return cls(
            signoz_base_url=_http_url("SIGNOZ_BASE_URL", _env("SIGNOZ_BASE_URL") or "http://localhost:8080"),
            signoz_api_key=_required_env("SIGNOZ_API_KEY"),
            trace_otlp_endpoint=_otlp_endpoint("traces"),
            log_otlp_endpoint=_otlp_endpoint("logs"),
            request_timeout_seconds=_positive_float("SIGNOZ_REQUEST_TIMEOUT_SECONDS", 10.0),
            ingestion_timeout_seconds=_positive_float("TRACEGUARD_GATE3B_INGEST_TIMEOUT_SECONDS", 60.0),
            poll_interval_seconds=_positive_float("TRACEGUARD_GATE3B_POLL_INTERVAL_SECONDS", 2.0),
            otlp_timeout_seconds=_positive_float("TRACEGUARD_OTLP_TIMEOUT_SECONDS", 10.0),
            service_name=_required_env("TRACEGUARD_GATE3B_SERVICE_NAME", default="traceguard-gate3b"),
        ).validate()

    def validate(self) -> "Gate3BConfig":
        if not self.signoz_api_key.strip():
            raise Gate3BConfigError("SIGNOZ_API_KEY must be non-empty.")
        if not self.service_name.strip():
            raise Gate3BConfigError("TRACEGUARD_GATE3B_SERVICE_NAME must be non-empty.")
        if self.poll_interval_seconds > self.ingestion_timeout_seconds:
            raise Gate3BConfigError("TRACEGUARD_GATE3B_POLL_INTERVAL_SECONDS must be less than or equal to TRACEGUARD_GATE3B_INGEST_TIMEOUT_SECONDS.")
        _http_url("SIGNOZ_BASE_URL", self.signoz_base_url)
        _safe_endpoint("trace OTLP endpoint", self.trace_otlp_endpoint, "traces")
        _safe_endpoint("log OTLP endpoint", self.log_otlp_endpoint, "logs")
        return self

    def non_secret_snapshot(self) -> dict[str, object]:
        return {
            "SIGNOZ_BASE_URL": self.signoz_base_url,
            "SIGNOZ_API_KEY": "<set>" if self.signoz_api_key else "<unset>",
            "TRACEGUARD_OTLP_TRACES_ENDPOINT": self.trace_otlp_endpoint,
            "TRACEGUARD_OTLP_LOGS_ENDPOINT": self.log_otlp_endpoint,
            "SIGNOZ_REQUEST_TIMEOUT_SECONDS": self.request_timeout_seconds,
            "TRACEGUARD_GATE3B_INGEST_TIMEOUT_SECONDS": self.ingestion_timeout_seconds,
            "TRACEGUARD_GATE3B_POLL_INTERVAL_SECONDS": self.poll_interval_seconds,
            "TRACEGUARD_OTLP_TIMEOUT_SECONDS": self.otlp_timeout_seconds,
            "TRACEGUARD_GATE3B_SERVICE_NAME": self.service_name,
        }


def _env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _required_env(name: str, *, default: str | None = None) -> str:
    value = _env(name) or default
    if value is None or not value.strip():
        raise Gate3BConfigError(f"{name} must be non-empty.")
    return value


def _positive_float(name: str, default: float) -> float:
    value = _env(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise Gate3BConfigError(f"{name} must be numeric seconds.") from exc
    if parsed <= 0:
        raise Gate3BConfigError(f"{name} must be greater than zero.")
    return parsed


def _http_url(name: str, value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise Gate3BConfigError(f"{name} must be an http(s) URL with a network location.")
    if parsed.query or parsed.fragment:
        raise Gate3BConfigError(f"{name} must not contain a query string or fragment.")
    return value.rstrip("/")


def _otlp_endpoint(kind: str) -> str:
    assert kind in {"traces", "logs"}
    names = (
        f"TRACEGUARD_OTLP_{kind.upper()}_ENDPOINT",
        f"OTEL_EXPORTER_OTLP_{kind.upper()}_ENDPOINT",
        "TRACEGUARD_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
    )
    for name in names:
        value = _env(name)
        if value:
            return _safe_endpoint(name, value, kind)
    return _safe_endpoint(f"TRACEGUARD_OTLP_{kind.upper()}_ENDPOINT", f"http://localhost:4318/v1/{kind}", kind)


def _safe_endpoint(name: str, value: str, kind: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise Gate3BConfigError(f"{name} must be an http(s) URL.")
    if not parsed.netloc:
        raise Gate3BConfigError(f"{name} must include a network location.")
    if parsed.query or parsed.fragment:
        raise Gate3BConfigError(f"{name} must not contain a query string or fragment.")
    path = parsed.path.rstrip("/")
    suffix = f"/v1/{kind}"
    if path in {"", "/"}:
        normalized = suffix
    elif path == "/v1":
        normalized = suffix
    elif path.endswith(suffix):
        normalized = path
    else:
        normalized = path + suffix
    return urlunparse((parsed.scheme, parsed.netloc, normalized, "", "", ""))

