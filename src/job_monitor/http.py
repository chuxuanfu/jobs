from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import Message
import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SAFE_RESPONSE_HEADERS = {
    "content-type", "content-length", "date", "etag", "last-modified",
    "retry-after", "cache-control",
}


@dataclass(frozen=True)
class HttpResponse:
    request_url: str
    fetched_at: str
    status: int
    headers: dict[str, str]
    body: bytes


class HttpRequestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        request_url: str,
        status: int | None = None,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> None:
        super().__init__(message)
        self.request_url = request_url
        self.status = status
        self.headers = headers or {}
        self.body = body


class PoliteHttpClient:
    def __init__(self, settings: dict):
        self.timeout = int(settings.get("request_timeout_seconds", 45))
        self.max_retries = int(settings.get("max_retries", 3))
        self.user_agent = settings["user_agent"]
        self.minimum_interval = float(settings.get("minimum_request_interval_seconds", 0.75))
        self._last_request_at: float | None = None

    def get(self, url: str, *, accept: str = "*/*") -> HttpResponse:
        return self.request("GET", url, headers={"Accept": accept})

    def post_json(self, url: str, payload: dict[str, Any]) -> HttpResponse:
        return self.request(
            "POST",
            url,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> HttpResponse:
        request_headers = {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "identity",
            **(headers or {}),
        }
        last_error: HttpRequestError | None = None
        for attempt in range(self.max_retries):
            self._throttle()
            fetched_at = datetime.now(timezone.utc).isoformat()
            request = Request(url, data=body, headers=request_headers, method=method)
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    return HttpResponse(
                        request_url=response.geturl(),
                        fetched_at=fetched_at,
                        status=response.status,
                        headers=safe_headers(response.headers),
                        body=response.read(),
                    )
            except HTTPError as exc:
                response_body = exc.read()
                response_headers = safe_headers(exc.headers)
                last_error = HttpRequestError(
                    f"HTTP {exc.code}: {exc.reason}",
                    request_url=url,
                    status=exc.code,
                    headers=response_headers,
                    body=response_body,
                )
                if exc.code in {401, 403} or (exc.code != 429 and exc.code < 500):
                    break
                retry_after = _retry_after_seconds(exc.headers)
                time.sleep(min(retry_after if retry_after is not None else 2 ** attempt, 30))
            except (URLError, TimeoutError, OSError) as exc:
                last_error = HttpRequestError(f"{type(exc).__name__}: {exc}", request_url=url)
                if attempt + 1 < self.max_retries:
                    time.sleep(2 ** attempt)
        raise last_error or HttpRequestError("Unknown HTTP failure", request_url=url)

    def _throttle(self) -> None:
        now = time.monotonic()
        if self._last_request_at is not None:
            remaining = self.minimum_interval - (now - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at = time.monotonic()


def safe_headers(headers: Message | None) -> dict[str, str]:
    if headers is None:
        return {}
    return {key.lower(): value for key, value in headers.items() if key.lower() in SAFE_RESPONSE_HEADERS}


def _retry_after_seconds(headers: Message | None) -> int | None:
    if not headers:
        return None
    value = headers.get("Retry-After")
    return int(value) if value and value.isdigit() else None
