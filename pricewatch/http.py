"""One HTTP client for the whole swarm.

Responsibilities: browser-like headers, cookie persistence per host, a polite per-host
throttle, and retries on transient failures. Every agent goes through `Client.get`.
"""
from __future__ import annotations

import threading
import time
import hashlib
import re
from urllib.parse import urlparse

import requests

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 PriceWatch/0.1",
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class Client:
    def __init__(self, min_interval: float = 0.0, timeout: float = 10.0, retries: int = 2):
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=32)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.min_interval = min_interval  # seconds between requests to the same host
        self.timeout = timeout
        self.retries = retries
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        with self._lock:
            wait = self._last.get(host, 0) + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()

    def request(self, method: str, url: str, **kw) -> requests.Response:
        kw.setdefault("timeout", self.timeout)
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle(url)
            try:
                r = self.session.request(method, url, **kw)
            except requests.RequestException as e:  # network blip
                last_exc = e
                time.sleep(0.5 * (attempt + 1))
                continue
            if r.status_code in (429, 503) and attempt < self.retries:
                if method == "GET" and r.status_code == 503 and self._solve_browser_challenge(url, r):
                    continue
                time.sleep(float(r.headers.get("Retry-After", "1")))
                continue
            return r
        if last_exc:
            raise last_exc
        return r  # type: ignore[possibly-undefined]

    def _solve_browser_challenge(self, url: str, response: requests.Response) -> bool:
        match = re.search(r'<div id="cf-c"[^>]*data-s="([^"]+)"[^>]*data-p="([^"]+)"', response.text)
        if not match:
            return False
        token, path = match.groups()
        delay = float(response.headers.get("Retry-After", "1.3"))
        time.sleep(max(1.3, delay))
        answer = hashlib.sha256(f"{token}|{path}".encode()).hexdigest()[:16]
        challenge_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}/stores/shield/challenge"
        challenge = self.session.post(
            challenge_url,
            json={"s": token, "p": path, "a": answer},
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        return challenge.ok

    def get(self, url: str, **kw) -> requests.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> requests.Response:
        return self.request("POST", url, **kw)
