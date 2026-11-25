"""Arize Phoenix as a trace source: connection test only, there is no Phoenix-to-trajectory converter yet."""

from .http import SourceError, request


class Phoenix:
    """Arize Phoenix REST API (v1). Only the connection test is implemented."""

    def __init__(self, host, api_key=None, project=None):
        self.host, self.api_key, self.project = host.rstrip("/"), api_key, project

    def check(self):
        _, data, _ = request(
            "GET",
            f"{self.host}/v1/projects?limit=100",
            {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
            timeout=20,
            label="Phoenix",
        )
        names = [p.get("name") for p in (data or {}).get("data") or []]
        if self.project and self.project not in names:
            raise SourceError(f"Connected, but there is no project {self.project!r} (projects: {', '.join(names) or 'none'})")
        return f"{len(names)} projects" + (f", project {self.project} found" if self.project else "")
