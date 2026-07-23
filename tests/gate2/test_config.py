from __future__ import annotations

from pathlib import Path

import pytest

from config import (
    DEFAULT_MCP_HEALTH_URL,
    DEFAULT_MCP_URL,
    DEFAULT_SIGNOZ_BASE_URL,
    Gate2Config,
    load_repository_env,
    repository_root,
)
from exceptions import ConfigurationError


CONFIG_ENV_NAMES = (
    "SIGNOZ_BASE_URL",
    "SIGNOZ_TRACE_ID",
    "TRACEGUARD_AGENT_RUN_ID",
    "SIGNOZ_API_KEY",
    "SIGNOZ_REQUEST_TIMEOUT_SECONDS",
    "SIGNOZ_DEBUG",
    "SIGNOZ_MCP_URL",
    "SIGNOZ_MCP_HEALTH_URL",
)


@pytest.fixture(autouse=True)
def clear_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CONFIG_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def write_env(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


def test_default_local_urls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = Gate2Config.from_env(dotenv_path=tmp_path / "missing.env")

    assert config.signoz_base_url == DEFAULT_SIGNOZ_BASE_URL
    assert config.mcp_url == DEFAULT_MCP_URL
    assert config.mcp_health_url == DEFAULT_MCP_HEALTH_URL


def test_valid_trace_id_is_lowercased(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SIGNOZ_TRACE_ID", "A" * 32)

    assert Gate2Config.from_env(dotenv_path=tmp_path / "missing.env").signoz_trace_id == "a" * 32


def test_invalid_trace_id_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SIGNOZ_TRACE_ID", "not-a-trace")

    with pytest.raises(ConfigurationError):
        Gate2Config.from_env(dotenv_path=tmp_path / "missing.env")


def test_positive_timeout_validation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SIGNOZ_REQUEST_TIMEOUT_SECONDS", "0")

    with pytest.raises(ConfigurationError):
        Gate2Config.from_env(dotenv_path=tmp_path / "missing.env")


def test_boolean_parsing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SIGNOZ_DEBUG", "yes")

    assert Gate2Config.from_env(dotenv_path=tmp_path / "missing.env").debug is True

    monkeypatch.setenv("SIGNOZ_DEBUG", "maybe")
    with pytest.raises(ConfigurationError):
        Gate2Config.from_env(dotenv_path=tmp_path / "missing.env")


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


def test_repository_env_values_are_loaded(tmp_path: Path) -> None:
    env_path = write_env(
        tmp_path / ".env",
        "\n".join(
            [
                "SIGNOZ_BASE_URL=http://localhost:8181",
                "SIGNOZ_API_KEY=fake-key",
                "SIGNOZ_TRACE_ID=ABCDEFABCDEFABCDEFABCDEFABCDEFAB",
                "TRACEGUARD_AGENT_RUN_ID=run-from-env",
                "SIGNOZ_MCP_URL=http://localhost:9000/mcp",
                "SIGNOZ_MCP_HEALTH_URL=http://localhost:9000/livez",
                "SIGNOZ_REQUEST_TIMEOUT_SECONDS=2.5",
                "SIGNOZ_DEBUG=true",
            ]
        ),
    )

    config = Gate2Config.from_env(dotenv_path=env_path)

    assert config.signoz_base_url == "http://localhost:8181"
    assert config.signoz_api_key == "fake-key"
    assert config.signoz_trace_id == "abcdefabcdefabcdefabcdefabcdefab"
    assert config.agent_run_id == "run-from-env"
    assert config.mcp_url == "http://localhost:9000/mcp"
    assert config.mcp_health_url == "http://localhost:9000/livez"
    assert config.request_timeout_seconds == 2.5
    assert config.debug is True


def test_shell_environment_overrides_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_path = write_env(tmp_path / ".env", "SIGNOZ_BASE_URL=http://localhost:8080\n")
    monkeypatch.setenv("SIGNOZ_BASE_URL", "http://localhost:9090")

    config = Gate2Config.from_env(dotenv_path=env_path)

    assert config.signoz_base_url == "http://localhost:9090"


def test_missing_dotenv_does_not_fail(tmp_path: Path) -> None:
    missing = tmp_path / "missing.env"

    selected = load_repository_env(missing)
    config = Gate2Config.from_env(dotenv_path=missing)

    assert selected == missing
    assert config.signoz_base_url == DEFAULT_SIGNOZ_BASE_URL


def test_empty_optional_dotenv_values_become_none(tmp_path: Path) -> None:
    env_path = write_env(
        tmp_path / ".env",
        "SIGNOZ_TRACE_ID=\nTRACEGUARD_AGENT_RUN_ID=\nSIGNOZ_API_KEY=\n",
    )

    config = Gate2Config.from_env(dotenv_path=env_path)

    assert config.signoz_trace_id is None
    assert config.agent_run_id is None
    assert config.signoz_api_key is None


def test_non_secret_snapshot_redacts_dotenv_key(tmp_path: Path) -> None:
    env_path = write_env(tmp_path / ".env", "SIGNOZ_API_KEY=fake-dotenv-secret\n")

    config = Gate2Config.from_env(dotenv_path=env_path)
    snapshot = config.non_secret_snapshot()

    assert config.signoz_api_key == "fake-dotenv-secret"
    assert snapshot["SIGNOZ_API_KEY"] == "<set>"
    assert "fake-dotenv-secret" not in repr(snapshot)


def test_invalid_trace_id_from_dotenv_raises(tmp_path: Path) -> None:
    env_path = write_env(tmp_path / ".env", "SIGNOZ_TRACE_ID=bad\n")

    with pytest.raises(ConfigurationError):
        Gate2Config.from_env(dotenv_path=env_path)


def test_invalid_timeout_from_dotenv_raises(tmp_path: Path) -> None:
    env_path = write_env(tmp_path / ".env", "SIGNOZ_REQUEST_TIMEOUT_SECONDS=nope\n")

    with pytest.raises(ConfigurationError):
        Gate2Config.from_env(dotenv_path=env_path)


def test_invalid_boolean_from_dotenv_raises(tmp_path: Path) -> None:
    env_path = write_env(tmp_path / ".env", "SIGNOZ_DEBUG=sometimes\n")

    with pytest.raises(ConfigurationError):
        Gate2Config.from_env(dotenv_path=env_path)


def test_different_cwd_still_uses_config_module_repository_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    nested = tmp_path / "elsewhere"
    nested.mkdir()
    monkeypatch.chdir(nested)

    assert repository_root().name == "Signoz_Hackathon"
    assert load_repository_env() == repository_root() / ".env"


def test_repeated_calls_do_not_override_shell_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_path = write_env(tmp_path / ".env", "SIGNOZ_BASE_URL=http://localhost:8080\n")
    monkeypatch.setenv("SIGNOZ_BASE_URL", "http://localhost:9090")

    first = Gate2Config.from_env(dotenv_path=env_path)
    second = Gate2Config.from_env(dotenv_path=env_path)

    assert first.signoz_base_url == "http://localhost:9090"
    assert second.signoz_base_url == "http://localhost:9090"


def test_root_env_example_contains_placeholders_only() -> None:
    example = repository_root() / ".env.example"
    text = example.read_text()

    assert "SIGNOZ_API_KEY=" in text
    assert "your-key" not in text
    assert "real-secret" not in text
    assert "<actual-service-account-key>" not in text
