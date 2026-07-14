from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3


BACKUP_PATHS = (
    "config", "migrations", "src", "scripts", "tests", "README.md", "pyproject.toml",
    "data/databases", "source", "original", "results", "logs",
)


def backup_project(root: Path, configured_directory: str) -> dict:
    if not configured_directory.strip():
        return {"enabled": False, "copied_files": 0, "destination": None}
    base = Path(configured_directory).expanduser().resolve()
    destination = base / "local-job-monitor-backup"
    if destination == root or root in destination.parents:
        raise ValueError("backup_directory must be outside the project directory")
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    for relative in BACKUP_PATHS:
        source = root / relative
        target = destination / relative
        if not source.exists():
            continue
        if relative == "data/databases":
            copied += _backup_databases(source, target)
            continue
        if source.is_dir():
            for path in source.rglob("*"):
                if not path.is_file() or path.name in {".DS_Store"} or "__pycache__" in path.parts:
                    continue
                relative_file = path.relative_to(source)
                output = target / relative_file
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, output)
                copied += 1
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1
    marker = destination / "LAST_BACKUP.txt"
    marker.write_text(datetime.now(timezone.utc).isoformat() + "\n", encoding="utf-8")
    return {"enabled": True, "copied_files": copied, "destination": str(destination)}


def _backup_databases(source_directory: Path, target_directory: Path) -> int:
    target_directory.mkdir(parents=True, exist_ok=True)
    copied = 0
    for source in source_directory.glob("*.sqlite"):
        target = target_directory / source.name
        temporary = target.with_suffix(target.suffix + ".tmp")
        if temporary.exists():
            temporary.unlink()
        source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        target_connection = sqlite3.connect(temporary)
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
            source_connection.close()
        temporary.replace(target)
        copied += 1
    return copied
