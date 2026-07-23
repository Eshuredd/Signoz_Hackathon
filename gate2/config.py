from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from exceptions import ConfigurationError


DEFAULT_SIGNOZ_BASE_URL = "http://localhost:8080"
DEFAULT_MCP_URL = "http://localhost:8000/mcp"
DEFAULT_MCP_HEALTH_URL = "http://localhost:8000/livez"


def repository_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_repository_env(dotenv_path: Path | None = None) -> Path:
    selected_path = dotenv_path or repository_root() / ".env"
    load_dotenv(dotenv_path=selected_path, override=False)
    return selected_path


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _bool_env(name: str, default: bool = False) -> bool:
    value = _optional_env(name)
    if value is None:
        return default
    lowered = value.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean value, got {value!r}.")


def _positive_float_env(name: str, default: float) -> float:
    value = _optional_env(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be numeric seconds, got {value!r}.") from exc
    if parsed <= 0:
        raise ConfigurationError(f"{name} must be greater than zero.")
    return parsed


def _validate_http_url(name: str, value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError(f"{name} must be an http(s) URL, got {value!r}.")
    return value.rstrip("/")


def _validate_trace_id(trace_id: str | None) -> str | None:
    if trace_id is None:
        return None
    lowered = trace_id.lower()
    if len(lowered) != 32 or any(ch not in "0123456789abcdef" for ch in lowered):
        raise ConfigurationError(
            "SIGNOZ_TRACE_ID must be a 32-character lowercase or uppercase hex trace ID."
        )
    return lowered


@dataclass(frozen=True)
class Gate2Config:
    signoz_base_url: str
    signoz_trace_id: str | None
    agent_run_id: str | None
    signoz_api_key: str | None
    request_timeout_seconds: float
    debug: bool
    mcp_url: str
    mcp_health_url: str

    @classmethod
    def from_env(cls, dotenv_path: Path | None = None) -> "Gate2Config":
        load_repository_env(dotenv_path)
        return cls(
            signoz_base_url=_validate_http_url(
                "SIGNOZ_BASE_URL",
                _optional_env("SIGNOZ_BASE_URL") or DEFAULT_SIGNOZ_BASE_URL,
            ),
            signoz_trace_id=_validate_trace_id(_optional_env("SIGNOZ_TRACE_ID")),
            agent_run_id=_optional_env("TRACEGUARD_AGENT_RUN_ID"),
            signoz_api_key=_optional_env("SIGNOZ_API_KEY"),
            request_timeout_seconds=_positive_float_env(
                "SIGNOZ_REQUEST_TIMEOUT_SECONDS",
                10.0,
            ),
            debug=_bool_env("SIGNOZ_DEBUG", False),
            mcp_url=_validate_http_url(
                "SIGNOZ_MCP_URL",
                _optional_env("SIGNOZ_MCP_URL") or DEFAULT_MCP_URL,
            ),
            mcp_health_url=_validate_http_url(
                "SIGNOZ_MCP_HEALTH_URL",
                _optional_env("SIGNOZ_MCP_HEALTH_URL") or DEFAULT_MCP_HEALTH_URL,
            ),
        )

    def non_secret_snapshot(self) -> dict[str, object]:
        return {
            "SIGNOZ_BASE_URL": self.signoz_base_url,
            "SIGNOZ_TRACE_ID": self.signoz_trace_id,
            "TRACEGUARD_AGENT_RUN_ID": self.agent_run_id,
            "SIGNOZ_API_KEY": "<set>" if self.signoz_api_key else "<unset>",
            "SIGNOZ_REQUEST_TIMEOUT_SECONDS": self.request_timeout_seconds,
            "SIGNOZ_DEBUG": self.debug,
            "SIGNOZ_MCP_URL": self.mcp_url,
            "SIGNOZ_MCP_HEALTH_URL": self.mcp_health_url,
        }
