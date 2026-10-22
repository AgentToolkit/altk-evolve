"""ALTK Forge server: the admin console for integrations, observability filters and pipelines, the background worker
that runs the pipelines, and the trajectory viewer.

Usage: uv run altk-forge [--port 8999] [--host 127.0.0.1] [--no-worker]
Then open http://127.0.0.1:8999
Without the UI, run the worker alone: uv run altk-forge-worker

Pages
  /                                        pipelines, filters and integrations (web/index.html)
  /viewer.html?pipeline=<id>               the trajectories of one pipeline

API (JSON)
  GET    /api/kinds                                integration kinds, filter kinds and their fields
  GET    /api/integrations                         connections, secrets masked
  POST   /api/integrations                         create            PUT/DELETE /api/integrations/<id>
  POST   /api/integrations/<id>/test               check the connection
  GET    /api/filters                              observability filters
  POST   /api/filters                              create            PUT/DELETE /api/filters/<id>
  POST   /api/preview                              count matching traces ({"integration_id", "filter_ids"} or {"integration_id", "criteria"})
  GET    /api/pipelines                            pipelines with counts and worker state
  POST   /api/pipelines                            create            GET/PUT/DELETE /api/pipelines/<id> (DELETE ?memory=1 also purges memory)
  POST   /api/pipelines/<id>/run                   poll now
  POST   /api/pipelines/<id>/enable                {"enabled": bool}
  POST   /api/pipelines/<id>/promote               test -> production
  POST   /api/pipelines/<id>/rescan                list every matching trace again (processed ones are still skipped)
  POST   /api/pipelines/<id>/purge                 test only: {"memory": bool, "trajectories": bool, "trace_ids": [...]}
  POST   /api/pipelines/<id>/send-all              test: send every trajectory not in memory yet, in the background
  POST   /api/pipelines/<id>/send-all/stop         stop it after the turn it is sending
  GET    /api/pipelines/<id>/send-all              its progress (also in GET /api/pipelines as send_all)
  POST   /api/pipelines/<id>/retry                 production: retry failed sends ({"trace_id"} optional)
  POST   /api/pipelines/<id>/retry-convert         convert the failed traces again on the next poll ({"trace_id"} optional)
  GET    /api/pipelines/<id>/log                   worker log
  GET    /api/pipelines/<id>/converters            the converters the LLM wrote for this pipeline
  GET    /api/pipelines/<id>/converters/<cid>      one converter: code (?version=n), diff to the previous, model, history
  DELETE /api/pipelines/<id>/converters/<cid>      test only: forget a converter
  GET    /api/folders?path=<abs>                   folder picker: subfolders and JSON file count (home by default)
  GET    /api/settings                             central settings (LLM endpoint, model; key masked)
  PUT    /api/settings                             {"llm": {base_url, api_key, model, ...}}
  POST   /api/settings/test-llm                    call the model once
  GET    /api/pipelines/<id>/trajectories                   index.json
  GET    /api/pipelines/<id>/trajectories/<tid>             one trajectory
  GET    /api/pipelines/<id>/trajectories/<tid>/slices      what goes to memory, and what was sent
  POST   /api/pipelines/<id>/trajectories/<tid>/send        test: send this one trajectory to memory
  POST   /api/pipelines/<id>/trajectories/<tid>/unsend      test: delete what it produced in memory
  GET    /api/pipelines/<id>/memory/<namespace>             guidelines in a namespace of the output
  GET    /api/pipelines/<id>/sessions                       ids of sessions that have a session document

The server binds to localhost by default: it holds the integrations' secrets.
"""

import argparse
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote

from .. import pipelines as P
from ..converters import ConverterError
from ..core.errors import InputError
from .api import api

STATIC = Path(__file__).resolve().parent
PAGES = ("/", "/index.html", "/viewer.html", "/theme.js", "/confirm.js")
MAX_BODY = 1 << 20


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        path, _, raw_query = self.path.partition("?")
        if not path.startswith("/api/"):
            if method != "GET":
                return self.send_error(405)
            if path == "/":
                self.path = "/index.html"
            if path in PAGES:
                return super().do_GET()
            return self.send_error(404)
        if method != "GET" and not self.same_origin():
            return self.send_json({"error": "Cross-origin request refused"}, 403)
        parts = [unquote(p) for p in path[len("/api/") :].strip("/").split("/")]
        query = {k: v[-1] for k, v in parse_qs(raw_query).items()}
        try:
            result = api(method, parts, query, self.body)
        except (InputError, ConverterError) as e:
            return self.send_json({"error": str(e)}, 400)
        if result is None:
            return self.send_json({"error": "Not found"}, 404)
        if isinstance(result, bytes):
            return self.send_body(result, "application/json; charset=utf-8")
        if isinstance(result, str):
            return self.send_body(result.encode(), "text/plain; charset=utf-8")
        return self.send_json(result)

    def body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise InputError("Request too large")
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            raise InputError("Body is not JSON") from None
        if not isinstance(value, dict):
            raise InputError("Body must be a JSON object")
        return value

    def same_origin(self):
        """Writes come from this server's own pages: a browser on another site must not drive the server."""
        origin = self.headers.get("Origin")
        return origin is None or origin.split("://", 1)[-1] == self.headers.get("Host", "")

    def send_json(self, value, status=200):
        self.send_body(json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

    def send_body(self, body, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if not self.path.startswith("/api/"):
            super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8999)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-worker", action="store_true", help="do not run pipelines here (altk-forge-worker runs them)")
    args = ap.parse_args()
    migrated = P.startup()
    if migrated:
        print(f"Migrated experiments to test pipelines (paused): {', '.join(migrated)}")
    if not args.no_worker:
        P.WORKER.start()
    print(
        f"ALTK Forge: http://{args.host}:{args.port}  ({len(P.list_pipelines())} pipelines, "
        f"{len(P.list_filters())} filters, {len(P.list_integrations())} integrations, "
        f"worker {'off' if args.no_worker else 'on'})"
    )
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
