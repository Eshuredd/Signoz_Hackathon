from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from exceptions import ConfigurationError
from traceguard_runtime import (
    RuntimeManifestError,
    default_gate1_manifest_path,
    try_read_gate1_manifest,
)


DEFAULT_SIGNOZ_BASE_URL = "http://localhost:8080"
DEFAULT_MCP_URL = "http://localhost:8000/mcp"
DEFAULT_MCP_HEALTH_URL = "http://localhost:8000/livez"


def repository_root() -> Path:
    return REPOSITORY_ROOT


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
    trace_context_source: str = "not_configured"
    gate1_manifest_path: str | None = None

    @classmethod
    def from_env(
        cls,
        dotenv_path: Path | None = None,
        gate1_manifest_path: Path | None = None,
    ) -> "Gate2Config":
        load_repository_env(dotenv_path)
        signoz_trace_id, agent_run_id, trace_context_source, selected_manifest_path = (
            resolve_trace_context(gate1_manifest_path)
        )
        return cls(
            signoz_base_url=_validate_http_url(
                "SIGNOZ_BASE_URL",
                _optional_env("SIGNOZ_BASE_URL") or DEFAULT_SIGNOZ_BASE_URL,
            ),
            signoz_trace_id=signoz_trace_id,
            agent_run_id=agent_run_id,
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
            trace_context_source=trace_context_source,
            gate1_manifest_path=str(selected_manifest_path) if selected_manifest_path else None,
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
            "trace_context_source": self.trace_context_source,
            "gate1_manifest_path": self.gate1_manifest_path,
        }


def resolve_trace_context(
    gate1_manifest_path: Path | None = None,
) -> tuple[str | None, str | None, str, Path | None]:
    trace_id = _optional_env("SIGNOZ_TRACE_ID")
    run_id = _optional_env("TRACEGUARD_AGENT_RUN_ID")
    if trace_id and run_id:
        return _validate_trace_id(trace_id), run_id, "environment", None
    if trace_id or run_id:
        raise ConfigurationError(
            "SIGNOZ_TRACE_ID and TRACEGUARD_AGENT_RUN_ID must be supplied together "
            "from the same Gate 1 execution, or both left blank to use the latest "
            "Gate 1 runtime manifest."
        )

    selected_manifest_path = gate1_manifest_path or default_gate1_manifest_path()
    try:
        manifest = try_read_gate1_manifest(selected_manifest_path)
    except RuntimeManifestError as exc:
        raise ConfigurationError(f"Invalid Gate 1 runtime manifest: {exc}") from exc
    if manifest is None:
        return None, None, "not_configured", selected_manifest_path
    return (
        _validate_trace_id(manifest.trace_id),
        manifest.agent_run_id,
        "manifest",
        selected_manifest_path,
    )
