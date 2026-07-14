from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class Paths:
    root: Path
    config: Path
    databases: Path
    source: Path
    original: Path
    results: Path
    logs: Path


def project_paths(root: Path | None = None) -> Paths:
    root = (root or Path(__file__).resolve().parents[2]).resolve()
    return Paths(
        root=root,
        config=root / "config",
        databases=root / "data" / "databases",
        source=root / "source",
        original=root / "original",
        results=root / "results",
        logs=root / "logs",
    )


def load_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_settings(paths: Paths) -> dict:
    return load_toml(paths.config / "settings.toml")


def load_company_config(paths: Paths, company: str) -> dict:
    path = paths.config / "companies" / f"{company.lower()}.toml"
    if not path.exists():
        raise ValueError(f"Unknown company: {company}")
    return load_toml(path)


def ensure_runtime_directories(paths: Paths, companies: list[str]) -> None:
    for directory in (paths.databases, paths.source, paths.original, paths.results, paths.logs):
        directory.mkdir(parents=True, exist_ok=True)
    for company in companies:
        (paths.source / company).mkdir(parents=True, exist_ok=True)
        (paths.original / company).mkdir(parents=True, exist_ok=True)
