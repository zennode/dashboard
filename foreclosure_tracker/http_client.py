"""One requests session with browser-like headers, timeouts and retries."""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
}


class Http:
    def __init__(self, timeout=40):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        retry = Retry(total=2, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=("GET", "POST"))
        self.session.mount("http://", HTTPAdapter(max_retries=retry))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def get(self, url, **kw):
        kw.setdefault("timeout", self.timeout)
        r = self.session.get(url, **kw)
        r.raise_for_status()
        return r

    def post(self, url, **kw):
        kw.setdefault("timeout", self.timeout)
        r = self.session.post(url, **kw)
        r.raise_for_status()
        return r
