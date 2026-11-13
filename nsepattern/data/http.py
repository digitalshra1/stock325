"""Polite HTTP session for NSE and other free sources."""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger("nsepattern.http")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


class PoliteSession:
    def __init__(self, sleep_sec: float = 0.5, retries: int = 3, timeout: int = 30):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"})
        self.sleep_sec, self.retries, self.timeout = sleep_sec, retries, timeout
        self._last = 0.0
        self._nse_warm = False

    def _wait(self):
        dt = time.time() - self._last
        if dt < self.sleep_sec:
            time.sleep(self.sleep_sec - dt)
        self._last = time.time()

    def warm_nse(self):
        """NSE pages need cookies from the home page first."""
        if self._nse_warm:
            return
        try:
            self._wait()
            self.s.get("https://www.nseindia.com", timeout=self.timeout)
        except requests.RequestException as e:
            log.warning("NSE warm-up failed: %s", e)
        self._nse_warm = True

    # circuit breaker: after this many consecutive failed URLs on one host, skip that host for a while
    BREAK_AFTER = 5
    BREAK_SECONDS = 15 * 60

    def _host(self, url: str) -> str:
        return url.split("/")[2] if "://" in url else url

    def get(self, url: str, **kw) -> requests.Response | None:
        """GET with retries and backoff. Returns None on failure (never raises).

        If a host keeps failing (e.g. NSE throttling cloud/heavy users), it is paused for
        BREAK_SECONDS so one blocked source cannot stall the whole run; its fields show N/A."""
        host = self._host(url)
        if not hasattr(self, "_fails"):
            self._fails, self._paused = {}, {}
        until = self._paused.get(host, 0)
        if until > time.time():
            return None
        if host == "www.nseindia.com":  # only the API site needs cookies; the file server (nsearchives) does not,
            self.warm_nse()             # and the warm-up is blocked from cloud servers
        for attempt in range(1, self.retries + 1):
            try:
                self._wait()
                r = self.s.get(url, timeout=self.timeout, **kw)
                if r.status_code == 200 and r.content:
                    self._fails[host] = 0
                    return r
                log.warning("GET %s -> HTTP %s (attempt %d)", url, r.status_code, attempt)
                if r.status_code == 404:
                    break  # missing file (e.g. a holiday's bhavcopy): no point retrying
            except requests.RequestException as e:
                log.warning("GET %s failed: %s (attempt %d)", url, e, attempt)
            time.sleep(2 ** attempt)
        self._fails[host] = self._fails.get(host, 0) + 1
        if self._fails[host] >= self.BREAK_AFTER:
            self._paused[host] = time.time() + self.BREAK_SECONDS
            self._fails[host] = 0
            log.warning("%s keeps failing: pausing requests to it for %d minutes (fields will show N/A)",
                        host, self.BREAK_SECONDS // 60)
        return None
