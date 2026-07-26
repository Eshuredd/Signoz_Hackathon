from __future__ import annotations

import pytest

from gate3b import otel_log_compat


def test_compatibility_contract_records_selected_paths() -> None:
    contract = otel_log_compat.compatibility_contract()
    assert contract["otlp_exporter_path"]
    assert contract["logger_provider_path"]
    assert isinstance(contract["private_fallback_used"], bool)
    assert contract["opentelemetry_versions"]["opentelemetry-sdk"]
    assert set(contract["selected_paths"]) == set(otel_log_compat.PUBLIC_CANDIDATES)
    assert all("._" not in path and "._logs" not in path and "._log_exporter" not in path for path in contract["public_paths_attempted"])


def test_public_path_is_selected_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    marker = object()

    def fake_import(path: str):
        if path == "public.module:Thing":
            return marker
        raise ModuleNotFoundError(path)

    monkeypatch.setattr(otel_log_compat, "_import_attr", fake_import)
    monkeypatch.setitem(otel_log_compat.PUBLIC_CANDIDATES, "Thing", ("public.module:Thing",))
    monkeypatch.setitem(otel_log_compat.PRIVATE_CANDIDATES, "Thing", ("private.module:Thing",))
    value, path, private, public_attempted, private_attempted = otel_log_compat._select_component("Thing")
    assert value is marker
    assert path == "public.module:Thing"
    assert private is False
    assert public_attempted == ["public.module:Thing"]
    assert private_attempted == []


def test_private_fallback_is_selected_when_public_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    marker = object()

    def fake_import(path: str):
        if path == "private.module:Thing":
            return marker
        raise ModuleNotFoundError(path)

    monkeypatch.setattr(otel_log_compat, "_import_attr", fake_import)
    monkeypatch.setitem(otel_log_compat.PUBLIC_CANDIDATES, "Thing", ("public.module:Thing",))
    monkeypatch.setitem(otel_log_compat.PRIVATE_CANDIDATES, "Thing", ("private.module:Thing",))
    value, path, private, public_attempted, private_attempted = otel_log_compat._select_component("Thing")
    assert value is marker
    assert path == "private.module:Thing"
    assert private is True
    assert public_attempted == ["public.module:Thing"]
    assert private_attempted == ["private.module:Thing"]


def test_unsupported_imports_raise_focused_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(otel_log_compat, "_import_attr", lambda path: (_ for _ in ()).throw(ModuleNotFoundError(path)))
    monkeypatch.setitem(otel_log_compat.PUBLIC_CANDIDATES, "Thing", ("public.module:Thing",))
    monkeypatch.setitem(otel_log_compat.PRIVATE_CANDIDATES, "Thing", ("private.module:Thing",))
    with pytest.raises(otel_log_compat.Gate3BLogCompatibilityError):
        otel_log_compat._select_component("Thing")
