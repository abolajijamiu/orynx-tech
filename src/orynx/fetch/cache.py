"""Content-addressed HTTP cache on disk.

Re-running a crawl during development should not re-hit the origin. Entries are
keyed by URL and expire after `ttl_seconds`.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path


class HttpCache:
    def __init__(self, directory: Path, ttl_seconds: int, enabled: bool = True) -> None:
        self.directory = Path(directory)
        self.ttl_seconds = ttl_seconds
        self.enabled = enabled
        if self.enabled:
            self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def _path(self, url: str) -> Path:
        digest = self.key(url)
        # Two-level fan-out keeps directory listings manageable on big crawls.
        return self.directory / digest[:2] / f"{digest}.json"

    def get(self, url: str) -> dict | None:
        if not self.enabled:
            return None
        path = self._path(url)
        if not path.exists():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if time.time() - entry.get("stored_at", 0) > self.ttl_seconds:
            return None
        return entry

    def set(self, url: str, status: int, text: str, headers: dict[str, str]) -> None:
        if not self.enabled:
            return
        path = self._path(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "url": url,
            "status": status,
            "text": text,
            "headers": headers,
            "stored_at": time.time(),
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry), encoding="utf-8")
        tmp.replace(path)
