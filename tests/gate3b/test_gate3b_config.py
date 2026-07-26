from __future__ import annotations

import json

import pytest

from gate3b.config import Gate3BConfig, Gate3BConfigError, _safe_endpoint


def test_required_api_key_and_snapshot_secret_safety(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNOZ_API_KEY", " ")
    with pytest.raises(Gate3BConfigError):
        Gate3BConfig.from_env()
    cfg = Gate3BConfig("http://localhost:8080", "secret-value", "http://localhost:4318/v1/traces", "http://localhost:4318/v1/logs", 1, 5, 1, 1, "svc")
    snapshot = cfg.non_secret_snapshot()
    assert snapshot["SIGNOZ_API_KEY"] == "<set>"
    assert "secret-value" not in json.dumps(snapshot)


@pytest.mark.parametrize(("kind", "raw", "expected"), [
    ("traces", "http://localhost:4318", "http://localhost:4318/v1/traces"),
    ("traces", "http://localhost:4318/v1", "http://localhost:4318/v1/traces"),
    ("traces", "http://localhost:4318/v1/traces", "http://localhost:4318/v1/traces"),
    ("logs", "http://localhost:4318", "http://localhost:4318/v1/logs"),
    ("logs", "http://localhost:4318/v1", "http://localhost:4318/v1/logs"),
    ("logs", "http://localhost:4318/v1/logs", "http://localhost:4318/v1/logs"),
])
def test_endpoint_normalization(kind: str, raw: str, expected: str) -> None:
    assert _safe_endpoint("x", raw, kind) == expected


@pytest.mark.parametrize("raw", ["ftp://localhost:4318", "http:///v1/logs", "http://localhost:4318/v1?x=1", "http://localhost:4318/v1#frag"])
def test_endpoint_rejects_unsafe_urls(raw: str) -> None:
    with pytest.raises(Gate3BConfigError):
        _safe_endpoint("x", raw, "logs")


def test_poll_interval_cannot_exceed_timeout() -> None:
    with pytest.raises(Gate3BConfigError):
        Gate3BConfig("http://localhost:8080", "secret", "http://localhost:4318/v1/traces", "http://localhost:4318/v1/logs", 1, 1, 2, 1, "svc").validate()

