from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def write_run_report(logs_dir: Path, summary: dict[str, Any], timezone_name: str) -> tuple[Path, Path]:
    local_time = datetime.fromisoformat(summary["finished_at"]).astimezone(ZoneInfo(timezone_name))
    date = local_time.strftime("%Y-%m-%d")
    markdown_path = logs_dir / f"{date}.md"
    jsonl_path = logs_dir / f"{date}.jsonl"
    logs_dir.mkdir(parents=True, exist_ok=True)
    if not markdown_path.exists():
        markdown_path.write_text(f"# {date}\n\n", encoding="utf-8")
    block = _markdown_block(summary, local_time.strftime("%H:%M:%S %Z"))
    with markdown_path.open("a", encoding="utf-8") as handle:
        handle.write(block)
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n")
    return markdown_path, jsonl_path


def _markdown_block(summary: dict[str, Any], local_time: str) -> str:
    health = "healthy" if summary["healthy"] else "UNHEALTHY"
    warnings = summary.get("warnings") or []
    warning_text = "; ".join(warnings) if warnings else "none"
    return (
        f"## {local_time} — {summary['company']} ({health})\n\n"
        f"- HTTP/fetch success: {summary['success']}\n"
        f"- Official jobs fetched: {summary['fetched_count']}\n"
        f"- United States jobs: {summary['us_count']}\n"
        f"- Open eligible jobs: {summary['eligible_count']}\n"
        f"- New / updated / unchanged: {summary['new_count']} / {summary['updated_count']} / {summary['unchanged_count']}\n"
        f"- Possibly closed / closed / reopened: {summary['possibly_closed_count']} / {summary['closed_count']} / {summary['reopened_count']}\n"
        f"- Location review required: {summary['review_count']}\n"
        f"- Missing posted date / salary: {summary['missing_posted_date_count']} / {summary['missing_salary_count']}\n"
        f"- Adapter warnings: {warning_text}\n"
        f"- Raw archive: {summary.get('raw_archive_path') or 'not written'}\n"
        f"- Database: {summary.get('database_path') or 'not written'}\n"
        f"- Original JSON: {summary.get('original_path') or 'not written'}\n"
        f"- Result JSON: {summary.get('result_path') or 'not written'}\n\n"
    )
