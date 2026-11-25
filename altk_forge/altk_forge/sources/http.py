"""JSON over HTTP for every remote system Forge talks to, with retries for rate limits and transient errors."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request


class SourceError(Exception):
    """A remote system refused or kept failing a request."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def request(method, url, headers=None, body=None, timeout=60, label="Request"):
    """JSON over HTTP; retries rate limits and transient server errors. Returns (status, json or None, headers)."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {**(headers or {}), **({"Content-Type": "application/json"} if data is not None else {})}
    for attempt in range(5):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else None, resp.headers
        except urllib.error.HTTPError as e:
            if (e.code == 429 or e.code >= 500) and attempt < 4:
                time.sleep(2**attempt)
                continue
            raise SourceError(f"{label} {e.code} for {method} {url}: {e.read().decode(errors='replace')[:300]}", e.code) from None
        except urllib.error.URLError as e:
            raise SourceError(f"Cannot reach {url.split('/api')[0]}: {e.reason}") from None
    raise SourceError(f"{label} kept failing for {method} {url}")
