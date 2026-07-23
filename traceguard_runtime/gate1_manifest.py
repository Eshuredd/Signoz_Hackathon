from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SUPPORTED_SCHEMA_VERSION = 1
TRACE_ID_HEX = frozenset("0123456789abcdef")


class RuntimeManifestError(RuntimeError):
    pass


@dataclass(frozen=True)
class Gate1RuntimeManifest:
    schema_version: int
    generated_at: datetime
    gate: str
    service_name: str
    service_version: str
    span_name: str
    trace_id: str
    agent_run_id: str
    traceguard_run_id: str
    trace_export_succeeded: bool
    metric_export_succeeded: bool
    log_export_succeeded: bool

    def __post_init__(self) -> None:
        validate_manifest(self)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        generated_at = self.generated_at
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=UTC)
        payload["generated_at"] = (
            generated_at.astimezone(UTC)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any], *, path: Path | None = None) -> "Gate1RuntimeManifest":
        required = (
            "schema_version",
            "generated_at",
            "gate",
            "service_name",
            "service_version",
            "span_name",
            "trace_id",
            "agent_run_id",
            "traceguard_run_id",
            "trace_export_succeeded",
            "metric_export_succeeded",
            "log_export_succeeded",
        )
        missing = [key for key in required if key not in payload]
        if missing:
            raise manifest_error(f"Gate 1 runtime manifest is missing required keys: {', '.join(missing)}", path)

        generated_at = parse_generated_at(payload["generated_at"], path)
        try:
            return cls(
                schema_version=int(payload["schema_version"]),
                generated_at=generated_at,
                gate=require_string(payload, "gate", path),
                service_name=require_string(payload, "service_name", path),
                service_version=require_string(payload, "service_version", path),
                span_name=require_string(payload, "span_name", path),
                trace_id=require_string(payload, "trace_id", path).lower(),
                agent_run_id=require_string(payload, "agent_run_id", path),
                traceguard_run_id=require_string(payload, "traceguard_run_id", path),
                trace_export_succeeded=require_bool(payload, "trace_export_succeeded", path),
                metric_export_succeeded=require_bool(payload, "metric_export_succeeded", path),
                log_export_succeeded=require_bool(payload, "log_export_succeeded", path),
            )
        except ValueError as exc:
            raise manifest_error("Gate 1 runtime manifest schema_version must be an integer.", path) from exc
        except RuntimeManifestError as exc:
            if path is None:
                raise
            raise manifest_error(str(exc), path) from exc


def repository_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_gate1_manifest_path() -> Path:
    return repository_root() / ".traceguard" / "runtime" / "latest_gate1.json"


def write_gate1_manifest(
    manifest: Gate1RuntimeManifest,
    path: Path | None = None,
) -> Path:
    selected_path = path or default_gate1_manifest_path()
    selected_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{selected_path.name}.",
        suffix=".tmp",
        dir=selected_path.parent,
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(selected_path)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        finally:
            raise
    return selected_path


def read_gate1_manifest(path: Path | None = None) -> Gate1RuntimeManifest:
    selected_path = path or default_gate1_manifest_path()
    try:
        raw = selected_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise manifest_error("Gate 1 runtime manifest is missing.", selected_path) from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise manifest_error("Gate 1 runtime manifest contains invalid JSON.", selected_path) from exc
    if not isinstance(payload, dict):
        raise manifest_error("Gate 1 runtime manifest must be a JSON object.", selected_path)
    return Gate1RuntimeManifest.from_dict(payload, path=selected_path)


def try_read_gate1_manifest(path: Path | None = None) -> Gate1RuntimeManifest | None:
    selected_path = path or default_gate1_manifest_path()
    if not selected_path.exists():
        return None
    return read_gate1_manifest(selected_path)


def validate_manifest(manifest: Gate1RuntimeManifest) -> None:
    if manifest.schema_version != SUPPORTED_SCHEMA_VERSION:
        raise RuntimeManifestError(
            "Unsupported Gate 1 runtime manifest schema_version "
            f"{manifest.schema_version!r}; supported version is {SUPPORTED_SCHEMA_VERSION}."
        )
    if manifest.gate != "1A":
        raise RuntimeManifestError("Gate 1 runtime manifest gate must be '1A'.")
    if manifest.generated_at.tzinfo is None:
        raise RuntimeManifestError("Gate 1 runtime manifest generated_at must include a timezone.")
    validate_trace_id(manifest.trace_id)
    for key, value in (
        ("agent_run_id", manifest.agent_run_id),
        ("traceguard_run_id", manifest.traceguard_run_id),
        ("service_name", manifest.service_name),
        ("service_version", manifest.service_version),
        ("span_name", manifest.span_name),
    ):
        if not value:
            raise RuntimeManifestError(f"Gate 1 runtime manifest {key} must be a non-empty string.")
    if manifest.agent_run_id != manifest.traceguard_run_id:
        raise RuntimeManifestError(
            "Gate 1 runtime manifest agent_run_id and traceguard_run_id must match."
        )
    if not manifest.trace_export_succeeded:
        raise RuntimeManifestError(
            "Gate 1 runtime manifest is unusable because trace_export_succeeded is false."
        )
    if not manifest.metric_export_succeeded:
        raise RuntimeManifestError(
            "Gate 1 runtime manifest is unusable because metric_export_succeeded is false."
        )


def validate_trace_id(trace_id: str) -> None:
    lowered = trace_id.lower()
    if len(lowered) != 32 or any(ch not in TRACE_ID_HEX for ch in lowered):
        raise RuntimeManifestError(
            "Gate 1 runtime manifest trace_id must be a 32-character hexadecimal string."
        )


def parse_generated_at(raw: Any, path: Path | None) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise manifest_error("Gate 1 runtime manifest generated_at must be a timestamp string.", path)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise manifest_error("Gate 1 runtime manifest generated_at is not a valid timestamp.", path) from exc
    if parsed.tzinfo is None:
        raise manifest_error("Gate 1 runtime manifest generated_at must include a timezone.", path)
    return parsed.astimezone(UTC)


def require_string(payload: dict[str, Any], key: str, path: Path | None) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise manifest_error(f"Gate 1 runtime manifest {key} must be a non-empty string.", path)
    return value.strip()


def require_bool(payload: dict[str, Any], key: str, path: Path | None) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise manifest_error(f"Gate 1 runtime manifest {key} must be a boolean.", path)
    return value


def manifest_error(message: str, path: Path | None) -> RuntimeManifestError:
    if path is None:
        return RuntimeManifestError(message)
    return RuntimeManifestError(f"{message} Path: {path}")
