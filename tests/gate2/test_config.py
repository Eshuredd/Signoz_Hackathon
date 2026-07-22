from __future__ import annotations

import pytest

from config import DEFAULT_MCP_HEALTH_URL, DEFAULT_MCP_URL, DEFAULT_SIGNOZ_BASE_URL, Gate2Config
from exceptions import ConfigurationError


def test_default_local_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "SIGNOZ_BASE_URL",
        "SIGNOZ_TRACE_ID",
        "TRACEGUARD_AGENT_RUN_ID",
        "SIGNOZ_API_KEY",
        "SIGNOZ_REQUEST_TIMEOUT_SECONDS",
        "SIGNOZ_DEBUG",
        "SIGNOZ_MCP_URL",
        "SIGNOZ_MCP_HEALTH_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    config = Gate2Config.from_env()

    assert config.signoz_base_url == DEFAULT_SIGNOZ_BASE_URL
    assert config.mcp_url == DEFAULT_MCP_URL
    assert config.mcp_health_url == DEFAULT_MCP_HEALTH_URL


def test_valid_trace_id_is_lowercased(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNOZ_TRACE_ID", "A" * 32)

    assert Gate2Config.from_env().signoz_trace_id == "a" * 32


def test_invalid_trace_id_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNOZ_TRACE_ID", "not-a-trace")

    with pytest.raises(ConfigurationError):
        Gate2Config.from_env()


def test_positive_timeout_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNOZ_REQUEST_TIMEOUT_SECONDS", "0")

    with pytest.raises(ConfigurationError):
        Gate2Config.from_env()


def test_boolean_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNOZ_DEBUG", "yes")

    assert Gate2Config.from_env().debug is True

    monkeypatch.setenv("SIGNOZ_DEBUG", "maybe")
    with pytest.raises(ConfigurationError):
        Gate2Config.from_env()


def test_secret_redacted_in_snapshot() -> None:
    config = Gate2Config(
        signoz_base_url="http://localhost:8080",
        signoz_trace_id=None,
        agent_run_id="run-1",
        signoz_api_key="real-secret",
        request_timeout_seconds=1.0,
        debug=False,
        mcp_url="http://localhost:8000/mcp",
        mcp_health_url="http://localhost:8000/livez",
    )

    snapshot = config.non_secret_snapshot()

    assert snapshot["SIGNOZ_API_KEY"] == "<set>"
    assert "real-secret" not in repr(snapshot)
