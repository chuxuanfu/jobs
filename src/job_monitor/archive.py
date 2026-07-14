from __future__ import annotations

from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from job_monitor.hashing import canonical_json
from job_monitor.models import FetchResult, RawArtifact


def archive_fetch(
    source_root: Path,
    result: FetchResult,
    adapter_version: str,
    timezone_name: str,
) -> tuple[Path, Path]:
    local_time = datetime.fromisoformat(result.fetched_at).astimezone(ZoneInfo(timezone_name))
    directory = source_root / result.company / local_time.strftime("%Y-%m-%d")
    directory.mkdir(parents=True, exist_ok=True)
    stem = local_time.strftime("%H%M%S_%f")
    metadata_path = directory / f"{stem}.metadata.json"
    artifacts = result.artifacts or [
        RawArtifact(
            request_url=result.request_url,
            fetched_at=result.fetched_at,
            http_status=result.http_status,
            response_headers=result.response_headers,
            raw_body=result.raw_body,
            suggested_name="response",
            content_type=result.response_headers.get("content-type"),
        )
    ]
    artifact_metadata = []
    payload_path: Path | None = None
    for index, artifact in enumerate(artifacts):
        extension = _artifact_extension(artifact)
        name = _safe_name(artifact.suggested_name or f"response_{index:05d}")
        path = directory / f"{stem}_{index:05d}_{name}.{extension}.gz"
        with gzip.open(path, "wb") as handle:
            handle.write(artifact.raw_body)
        payload_path = payload_path or path
        artifact_metadata.append({
            "suggested_name": artifact.suggested_name,
            "request_url": artifact.request_url,
            "fetched_at": artifact.fetched_at,
            "http_status": artifact.http_status,
            "response_headers": artifact.response_headers,
            "content_type": artifact.content_type,
            "path": str(path),
            "sha256": hashlib.sha256(artifact.raw_body).hexdigest(),
            "size_bytes": len(artifact.raw_body),
        })
    metadata = {
        "company": result.company,
        "request_url": result.request_url,
        "fetched_at": result.fetched_at,
        "http_status": result.http_status,
        "response_headers": result.response_headers,
        "adapter_version": adapter_version,
        "parsed_job_count": len(result.jobs),
        "warnings": result.warnings,
        "error": result.error,
        "artifact_count": len(artifact_metadata),
        "artifacts": artifact_metadata,
    }
    metadata_path.write_text(canonical_json(metadata) + "\n", encoding="utf-8")
    primary_path = metadata_path if len(artifact_metadata) > 1 else (payload_path or metadata_path)
    return primary_path, metadata_path


def read_archived_payload(path: Path) -> bytes:
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as handle:
            return handle.read()
    return path.read_bytes()


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def read_archive_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("._")[:80] or "response"


def _artifact_extension(artifact: RawArtifact) -> str:
    content_type = (artifact.content_type or artifact.response_headers.get("content-type") or "").lower()
    if "html" in content_type:
        return "html"
    if "xml" in content_type:
        return "xml"
    return "json"
