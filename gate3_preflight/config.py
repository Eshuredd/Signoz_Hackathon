from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[1]


class PreflightConfigError(Exception):
    """Raised when live preflight configuration is missing or malformed."""


@dataclass(frozen=True)
class PreflightConfig:
    signoz_base_url: str
    signoz_api_key: str
    otlp_endpoint: str
    request_timeout_seconds: float
    poll_timeout_seconds: float
    poll_interval_seconds: float
    otlp_timeout_seconds: float
    service_name: str

    @classmethod
    def from_env(cls) -> "PreflightConfig":
        load_dotenv(REPO_ROOT / ".env", override=False)
        return cls(
            signoz_base_url=_http_url("SIGNOZ_BASE_URL", _env("SIGNOZ_BASE_URL") or "http://localhost:8080"),
            signoz_api_key=_required_env("SIGNOZ_API_KEY"),
            otlp_endpoint=_otlp_traces_endpoint(),
            request_timeout_seconds=_positive_float("SIGNOZ_REQUEST_TIMEOUT_SECONDS", 10.0),
            poll_timeout_seconds=_positive_float("TRACEGUARD_PREFLIGHT_POLL_TIMEOUT_SECONDS", 60.0),
            poll_interval_seconds=_positive_float("TRACEGUARD_PREFLIGHT_POLL_INTERVAL_SECONDS", 2.0),
            otlp_timeout_seconds=_positive_float("TRACEGUARD_OTLP_TIMEOUT_SECONDS", 10.0),
            service_name=_required_env("TRACEGUARD_PREFLIGHT_SERVICE_NAME", default="traceguard-gate3-preflight"),
        ).validate()

    def validate(self) -> "PreflightConfig":
        if self.poll_interval_seconds > self.poll_timeout_seconds:
            raise PreflightConfigError("TRACEGUARD_PREFLIGHT_POLL_INTERVAL_SECONDS must be less than or equal to TRACEGUARD_PREFLIGHT_POLL_TIMEOUT_SECONDS.")
        if not self.service_name.strip():
            raise PreflightConfigError("TRACEGUARD_PREFLIGHT_SERVICE_NAME must be non-empty.")
        if not self.signoz_api_key.strip():
            raise PreflightConfigError("SIGNOZ_API_KEY must be non-empty for live preflight.")
        _http_url("SIGNOZ_BASE_URL", self.signoz_base_url)
        _http_url("OTLP endpoint", self.otlp_endpoint)
        return self

    def non_secret_snapshot(self) -> dict[str, object]:
        return {
            "SIGNOZ_BASE_URL": self.signoz_base_url,
            "SIGNOZ_API_KEY": "<set>" if self.signoz_api_key else "<unset>",
            "TRACEGUARD_OTLP_ENDPOINT": self.otlp_endpoint,
            "SIGNOZ_REQUEST_TIMEOUT_SECONDS": self.request_timeout_seconds,
            "TRACEGUARD_PREFLIGHT_POLL_TIMEOUT_SECONDS": self.poll_timeout_seconds,
            "TRACEGUARD_PREFLIGHT_POLL_INTERVAL_SECONDS": self.poll_interval_seconds,
            "TRACEGUARD_OTLP_TIMEOUT_SECONDS": self.otlp_timeout_seconds,
            "TRACEGUARD_PREFLIGHT_SERVICE_NAME": self.service_name,
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
        raise PreflightConfigError(f"{name} must be non-empty for live preflight.")
    return value


def _positive_float(name: str, default: float) -> float:
    value = _env(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise PreflightConfigError(f"{name} must be numeric seconds.") from exc
    if parsed <= 0:
        raise PreflightConfigError(f"{name} must be greater than zero.")
    return parsed


def _http_url(name: str, value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PreflightConfigError(f"{name} must be an http(s) URL.")
    return value.rstrip("/")


def _otlp_traces_endpoint() -> str:
    for name in (
        "TRACEGUARD_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "TRACEGUARD_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
    ):
        value = _env(name)
        if value:
            return _append_traces_path(name, value)
    return _append_traces_path("TRACEGUARD_OTLP_ENDPOINT", "http://localhost:4318/v1/traces")


def _append_traces_path(name: str, value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise PreflightConfigError(f"{name} must be an http(s) URL.")
    if not parsed.netloc:
        raise PreflightConfigError(f"{name} must include a network location.")
    if parsed.query or parsed.fragment:
        raise PreflightConfigError(f"{name} must not contain a query string or fragment.")

    path = parsed.path.rstrip("/")
    if path in {"", "/"}:
        normalized_path = "/v1/traces"
    elif path == "/v1":
        normalized_path = "/v1/traces"
    elif path.endswith("/v1/traces"):
        normalized_path = path
    else:
        normalized_path = path + "/v1/traces"

    return urlunparse((parsed.scheme, parsed.netloc, normalized_path, "", "", ""))
