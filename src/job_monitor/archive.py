from __future__ import annotations

from datetime import datetime
import gzip
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from job_monitor.hashing import canonical_json
from job_monitor.models import FetchResult


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
    payload_path = directory / f"{stem}.json.gz"
    metadata_path = directory / f"{stem}.metadata.json"
    with gzip.open(payload_path, "wb") as handle:
        handle.write(result.raw_body)
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
        "payload_path": str(payload_path),
    }
    metadata_path.write_text(canonical_json(metadata) + "\n", encoding="utf-8")
    return payload_path, metadata_path


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
