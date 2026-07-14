from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from job_monitor.models import FetchResult, Job


class SourceAdapter(ABC):
    company: str

    def __init__(self, company_config: dict, settings: dict):
        self.company_config = company_config
        self.settings = settings
        self.existing_jobs: dict[str, dict] = {}
        self.seen_source_ids: set[str] = set()
        self.initial_seed_only = False

    def set_existing_jobs(self, jobs: list[dict]) -> None:
        self.existing_jobs = {str(job["source_job_id"]): job for job in jobs}

    def set_incremental_context(self, source_ids: set[str], *, initial_seed_only: bool) -> None:
        self.seen_source_ids = set(source_ids)
        self.initial_seed_only = initial_seed_only

    @abstractmethod
    def fetch(self) -> FetchResult:
        raise NotImplementedError

    @abstractmethod
    def parse_payload(self, payload: bytes, fetched_at: str) -> list[Job]:
        raise NotImplementedError

    def reparse_file(self, path: Path, fetched_at: str) -> list[Job]:
        return self.parse_payload(path.read_bytes(), fetched_at)

    def reparse_archive(self, path: Path, fetched_at: str) -> list[Job]:
        """Reparse a single-response archive; multi-response adapters override this."""
        from job_monitor.archive import read_archived_payload

        return self.parse_payload(read_archived_payload(path), fetched_at)
