from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[1]


class PreflightConfigError(Exception):
    """Raised when live preflight configuration is missing or malformed."""


@dataclass(frozen=True)
class PreflightConfig:
    signoz_base_url: str
    signoz_api_key: str | None
    otlp_endpoint: str
    request_timeout_seconds: float
    poll_timeout_seconds: float
    poll_interval_seconds: float
    service_name: str

    @classmethod
    def from_env(cls) -> "PreflightConfig":
        load_dotenv(REPO_ROOT / ".env", override=False)
        return cls(
            signoz_base_url=_http_url("SIGNOZ_BASE_URL", _env("SIGNOZ_BASE_URL") or "http://localhost:8080"),
            signoz_api_key=_env("SIGNOZ_API_KEY"),
            otlp_endpoint=_http_url("TRACEGUARD_OTLP_ENDPOINT", _env("TRACEGUARD_OTLP_ENDPOINT") or "http://localhost:4318/v1/traces"),
            request_timeout_seconds=_positive_float("SIGNOZ_REQUEST_TIMEOUT_SECONDS", 10.0),
            poll_timeout_seconds=_positive_float("TRACEGUARD_PREFLIGHT_POLL_TIMEOUT_SECONDS", 60.0),
            poll_interval_seconds=_positive_float("TRACEGUARD_PREFLIGHT_POLL_INTERVAL_SECONDS", 2.0),
            service_name=_env("TRACEGUARD_PREFLIGHT_SERVICE_NAME") or "traceguard-gate3-preflight",
        )

    def non_secret_snapshot(self) -> dict[str, object]:
        return {
            "SIGNOZ_BASE_URL": self.signoz_base_url,
            "SIGNOZ_API_KEY": "<set>" if self.signoz_api_key else "<unset>",
            "TRACEGUARD_OTLP_ENDPOINT": self.otlp_endpoint,
            "SIGNOZ_REQUEST_TIMEOUT_SECONDS": self.request_timeout_seconds,
            "TRACEGUARD_PREFLIGHT_POLL_TIMEOUT_SECONDS": self.poll_timeout_seconds,
            "TRACEGUARD_PREFLIGHT_POLL_INTERVAL_SECONDS": self.poll_interval_seconds,
            "TRACEGUARD_PREFLIGHT_SERVICE_NAME": self.service_name,
        }


def _env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


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
