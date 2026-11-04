"""Polite, cached HTTP fetching for ola.org.

- At most one request per second (MIN_INTERVAL).
- Descriptive User-Agent.
- robots.txt is honoured, including the `*` and `$` wildcards that
  urllib.robotparser does not understand (ola.org disallows `/*?`).
- Every raw response is cached under cache/ so reruns never refetch.
  404s are cached too (as a small marker file) so they aren't retried.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

BASE = "https://www.ola.org"
USER_AGENT = (
    "QueensParkWatch-scraper/0.1 "
    "(research dataset of recorded divisions; +https://github.com/growthway-ca/queenspark)"
)
MIN_INTERVAL = 1.0  # seconds between live requests
ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "cache"
NOT_FOUND_MARKER = "<!-- queenspark-cache: 404 -->"


class RobotsDisallowed(Exception):
    pass


class Robots:
    """Minimal robots.txt matcher for the `User-agent: *` groups."""

    def __init__(self, text: str):
        self.rules: list[tuple[bool, re.Pattern, int]] = []
        applies = False
        last_was_agent = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, val = (p.strip() for p in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if not last_was_agent:
                    applies = False
                applies = applies or val == "*"
                last_was_agent = True
                continue
            last_was_agent = False
            if not applies or key not in ("allow", "disallow") or not val:
                continue
            self.rules.append((key == "allow", self._compile(val), len(val)))

    @staticmethod
    def _compile(pattern: str) -> re.Pattern:
        anchored = pattern.endswith("$")
        if anchored:
            pattern = pattern[:-1]
        rx = "".join(".*" if c == "*" else re.escape(c) for c in pattern)
        return re.compile(rx + ("$" if anchored else ""))

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        best: tuple[int, bool] | None = None
        for allow, rx, length in self.rules:
            if rx.match(path):
                # Longest match wins; on a tie, Allow wins (Google semantics).
                if best is None or length > best[0] or (length == best[0] and allow):
                    best = (length, allow)
        return True if best is None else best[1]


class Fetcher:
    def __init__(self, cache_dir: Path = CACHE_DIR, offline: bool = False, verbose: bool = True):
        self.cache_dir = cache_dir
        self.offline = offline
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._robots: Robots | None = None
        self.live_requests = 0

    # -- cache -------------------------------------------------------------
    def cache_path(self, url: str) -> Path:
        parts = urlsplit(url)
        path = parts.path.strip("/") or "index"
        safe = re.sub(r"[^A-Za-z0-9._/-]", "_", path)
        if parts.query:
            safe += "__q" + hashlib.sha1(parts.query.encode()).hexdigest()[:10]
        # Keep file names short enough for every filesystem.
        segs = []
        for seg in safe.split("/"):
            if len(seg) > 120:
                seg = seg[:100] + "_" + hashlib.sha1(seg.encode()).hexdigest()[:10]
            segs.append(seg)
        return self.cache_dir / parts.netloc / ("/".join(segs) + ".html")

    # -- network -----------------------------------------------------------
    def _throttle(self) -> None:
        """Wait until MIN_INTERVAL has passed since the last request made by
        *any* scraper process sharing this cache (lock file + timestamp)."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with open(self.cache_dir / ".ratelimit", "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.seek(0)
                try:
                    last = float(fh.read().strip() or 0)
                except ValueError:
                    last = 0.0
                wait = last + MIN_INTERVAL - time.time()
                if wait > 0:
                    time.sleep(wait)
                fh.seek(0)
                fh.truncate()
                fh.write(repr(time.time()))
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _live_get(self, url: str) -> requests.Response:
        delay = 2.0
        for attempt in range(5):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=60)
            except requests.RequestException as exc:
                err: str = str(exc)
            else:
                self.live_requests += 1
                if resp.status_code < 500 and resp.status_code != 429:
                    return resp
                err = f"HTTP {resp.status_code}"
            if attempt == 4:
                raise RuntimeError(f"giving up on {url}: {err}")
            if self.verbose:
                print(f"  retry {attempt + 1} for {url} ({err}); sleeping {delay:.0f}s", file=sys.stderr)
            time.sleep(delay)
            delay *= 2
        raise AssertionError("unreachable")

    @property
    def robots(self) -> Robots:
        if self._robots is None:
            text = self.get(BASE + "/robots.txt", check_robots=False) or ""
            self._robots = Robots(text)
        return self._robots

    def get(self, url: str, check_robots: bool = True) -> str | None:
        """Return the page body, or None for a 404. Raises on other errors."""
        url = urljoin(BASE, url)
        url = url.split("#", 1)[0]
        path = self.cache_path(url)
        if path.exists():
            body = path.read_text(encoding="utf-8")
            return None if body == NOT_FOUND_MARKER else body
        if check_robots and not self.robots.allowed(url):
            raise RobotsDisallowed(url)
        if self.offline:
            raise RuntimeError(f"offline mode and not cached: {url}")
        if self.verbose:
            print(f"  GET {url}", file=sys.stderr)
        resp = self._live_get(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        if resp.status_code == 404:
            path.write_text(NOT_FOUND_MARKER, encoding="utf-8")
            return None
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code} for {url}")
        resp.encoding = resp.encoding or "utf-8"
        path.write_text(resp.text, encoding="utf-8")
        return resp.text
