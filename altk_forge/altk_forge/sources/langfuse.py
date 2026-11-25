"""Langfuse as a trace source: GET /api/public/traces with the structured `filter` parameter."""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request

from .http import request

PAGE_SIZE = 100


class Langfuse:
    def __init__(self, host, public_key, secret_key):
        self.host = host.rstrip("/")
        self.auth = "Basic " + base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()

    def get(self, path, **params):
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
        url = f"{self.host}/api/public/{path}" + (f"?{query}" if query else "")
        return request("GET", url, {"Authorization": self.auth}, label="Langfuse")[1]

    def check(self):
        """Short description of what the credentials give access to; raises SourceError when they do not work."""
        projects = self.get("projects").get("data") or []
        return "project " + ", ".join(p.get("name") or p["id"] for p in projects) if projects else "connected"

    def list_traces(self, conditions, order="timestamp.asc", limit=None):
        """Trace summaries (id, name, timestamp, updatedAt) matching structured filter conditions."""
        page, n = 1, 0
        while True:
            data = self.get(
                "traces", page=page, limit=PAGE_SIZE, orderBy=order, fields="core", filter=json.dumps(conditions) if conditions else None
            )
            for t in data["data"]:
                yield {
                    "id": t["id"],
                    "name": t.get("name"),
                    "timestamp": t.get("timestamp"),
                    "updated_at": t.get("updatedAt") or t.get("timestamp"),
                }
                n += 1
                if limit and n >= limit:
                    return
            if page >= (data.get("meta") or {}).get("totalPages", 1):
                return
            page += 1

    def trace(self, trace_id):
        """Full trace: the trace endpoint already embeds observations and scores."""
        return self.get(f"traces/{urllib.parse.quote(trace_id)}")
