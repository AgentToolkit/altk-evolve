"""HTTP server for the Evolve Viz tool."""

import json
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar, Optional

from .data import (
    load_entities,
    load_entity_detail,
    load_trajectories,
    load_trajectory_detail,
)

# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

_PATH_SEP = frozenset("/\\")
_GLOB_CHARS = frozenset("*?[")


def _safe_filename(filename: str) -> bool:
    """Return True if filename is safe to use as a bare filename (no path traversal)."""
    return bool(filename) and not any(c in _PATH_SEP for c in filename) and filename != ".." and not filename.startswith(".")


def _safe_slug(slug: str) -> bool:
    """Return True if slug contains no path separators or glob metacharacters."""
    return bool(slug) and not any(c in (_PATH_SEP | _GLOB_CHARS) for c in slug)


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------


class VizHandler(BaseHTTPRequestHandler):
    evolve_dir: ClassVar[Optional[Path]] = None  # set before starting server

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._serve_html()
        elif path == "/api/trajectories":
            entities = load_entities(self.evolve_dir)
            self._serve_json(load_trajectories(self.evolve_dir, entities))
        elif path.startswith("/api/trajectories/"):
            filename = urllib.parse.unquote(path[len("/api/trajectories/") :])
            if not _safe_filename(filename):
                self.send_error(400)
                return
            entities = load_entities(self.evolve_dir)
            detail = load_trajectory_detail(self.evolve_dir, filename, entities)
            self._serve_json(detail, not_found=detail is None)
        elif path == "/api/entities":
            self._serve_json(load_entities(self.evolve_dir))
        elif path.startswith("/api/entities/"):
            slug = urllib.parse.unquote(path[len("/api/entities/") :])
            if not _safe_slug(slug):
                self.send_error(400)
                return
            detail = load_entity_detail(self.evolve_dir, slug)
            self._serve_json(detail, not_found=detail is None)
        else:
            self.send_error(404)

    def _serve_html(self):
        body = _HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_json(self, data, not_found=False):
        if not_found:
            self.send_error(404)
            return
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # suppress default per-request logging


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def serve(evolve_dir: Path, port: int = 7891, open_browser: bool = True):
    VizHandler.evolve_dir = evolve_dir

    server = HTTPServer(("127.0.0.1", port), VizHandler)
    url = f"http://localhost:{port}"

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    print(f"Evolve Viz  →  {url}")
    print(f"Data dir    →  {evolve_dir.resolve()}")
    print("Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


# ---------------------------------------------------------------------------
# Embedded React frontend
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Evolve Viz</title>
<script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
<script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #0f1117;
    --surface: #1a1d27;
    --surface2: #22263a;
    --border: #2e3347;
    --text: #e2e8f0;
    --text-dim: #8892a4;
    --text-dimmer: #4a5568;
    --accent: #7c8df8;
    --accent-dim: #3d4a8a;
    --green: #48bb78;
    --yellow: #ecc94b;
    --red: #fc8181;
    --user-bg: #1e2a3d;
    --user-border: #3b5280;
    --assistant-bg: #1a1d27;
    --tool-bg: #1a2420;
    --tool-border: #2d4a3e;
    --thinking-bg: #1e1a2e;
    --thinking-border: #4a3f78;
    --font-mono: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
    --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    --sidebar-width: 260px;
  }

  html, body, #root { height: 100%; overflow: hidden; }
  body { background: var(--bg); color: var(--text); font-family: var(--font-sans); font-size: 14px; line-height: 1.5; }

  /* Layout */
  .app { display: flex; flex-direction: column; height: 100%; }
  .app-header { padding: 0 16px; height: 44px; display: flex; align-items: center; gap: 12px; background: var(--surface); border-bottom: 1px solid var(--border); flex-shrink: 0; }
  .app-header h1 { font-size: 15px; font-weight: 600; color: var(--text); letter-spacing: 0.01em; }
  .app-header .subtitle { color: var(--text-dim); font-size: 12px; }
  .app-body { display: flex; flex: 1; overflow: hidden; }

  /* Sidebar */
  .sidebar { width: var(--sidebar-width); flex-shrink: 0; background: var(--surface); border-right: 1px solid var(--border); display: flex; flex-direction: column; overflow: hidden; }
  .sidebar-section { flex-shrink: 0; }
  .sidebar-section-header { padding: 10px 12px 6px; font-size: 11px; font-weight: 600; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.08em; }
  .sidebar-list { overflow-y: auto; }
  .sidebar-divider { border: none; border-top: 1px solid var(--border); margin: 4px 0; }
  .sidebar-item { display: block; padding: 7px 12px; border-radius: 4px; margin: 1px 4px; transition: background 0.1s; text-decoration: none; color: inherit; }
  .sidebar-item:hover { background: var(--surface2); }
  .sidebar-item.active { background: var(--accent-dim); }
  .sidebar-item-name { font-size: 13px; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .sidebar-item-meta { font-size: 11px; color: var(--text-dim); margin-top: 1px; }
  .sidebar-item-meta .badge { display: inline-block; background: var(--accent-dim); color: var(--accent); border-radius: 9px; padding: 0 6px; font-size: 10px; margin-left: 4px; }
  .sidebar-empty { padding: 12px; color: var(--text-dimmer); font-size: 12px; font-style: italic; }

  /* Scrollable sections */
  .sidebar-scroll-section { overflow-y: auto; flex: 1; min-height: 0; }
  .sidebar-scroll-section:first-child { max-height: 45%; }

  /* Detail panel */
  .detail-panel { flex: 1; overflow-y: auto; padding: 0; background: var(--bg); }
  .detail-empty { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--text-dimmer); font-size: 14px; }
  .loading { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--text-dim); }

  /* Detail header */
  .detail-header { padding: 20px 24px 16px; border-bottom: 1px solid var(--border); background: var(--surface); }
  .detail-title { font-size: 15px; font-weight: 600; color: var(--text); font-family: var(--font-mono); word-break: break-all; }
  .detail-meta { display: flex; gap: 16px; margin-top: 8px; flex-wrap: wrap; }
  .detail-meta-item { font-size: 12px; color: var(--text-dim); }
  .detail-meta-item strong { color: var(--text); }

  /* Linked guidelines in trajectory */
  .linked-section { padding: 14px 24px; border-bottom: 1px solid var(--border); background: var(--surface); }
  .linked-section-title { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-dim); margin-bottom: 8px; }
  .linked-pills { display: flex; flex-wrap: wrap; gap: 6px; }
  .linked-pill { display: inline-block; background: var(--accent-dim); color: var(--accent); border-radius: 4px; padding: 3px 8px; font-size: 12px; text-decoration: none; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .linked-pill:hover { background: var(--accent); color: #fff; }
  .linked-none { color: var(--text-dimmer); font-size: 12px; font-style: italic; }

  /* Transcript */
  .transcript { padding: 16px 24px; display: flex; flex-direction: column; gap: 8px; }

  /* Messages */
  .message { border-radius: 6px; overflow: hidden; }
  .message-user { background: var(--user-bg); border: 1px solid var(--user-border); }
  .message-assistant { background: var(--assistant-bg); border: 1px solid var(--border); }
  .message-tool { background: var(--tool-bg); border: 1px solid var(--tool-border); }
  .message-thinking { background: var(--thinking-bg); border: 1px solid var(--thinking-border); }

  .message-role { padding: 5px 10px; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.1em; border-bottom: 1px solid rgba(255,255,255,0.06); }
  .message-user .message-role { color: #90b4e8; }
  .message-assistant .message-role { color: #a8c8a0; }
  .message-tool .message-role { color: #80c8b0; }
  .message-thinking .message-role { color: #b090d8; }

  .message-content { padding: 8px 10px; font-size: 13px; white-space: pre-wrap; word-break: break-word; color: var(--text); }
  .message-content code { background: rgba(255,255,255,0.06); padding: 1px 4px; border-radius: 3px; font-family: var(--font-mono); font-size: 12px; }

  /* Tool call block */
  .tool-call { margin: 4px 10px 8px; background: rgba(0,0,0,0.2); border: 1px solid var(--border); border-radius: 4px; overflow: hidden; }
  .tool-call-header { padding: 4px 8px; background: rgba(255,255,255,0.04); font-size: 11px; font-family: var(--font-mono); color: #80c8b0; display: flex; align-items: center; gap: 6px; cursor: pointer; user-select: none; }
  .tool-call-header:hover { background: rgba(255,255,255,0.07); }
  .tool-call-name { font-weight: 600; }
  .tool-call-id { color: var(--text-dimmer); font-size: 10px; }
  .tool-call-chevron { margin-left: auto; color: var(--text-dimmer); }
  .tool-call-args { padding: 6px 8px; font-size: 11px; font-family: var(--font-mono); color: var(--text-dim); white-space: pre-wrap; word-break: break-all; border-top: 1px solid var(--border); }

  /* Entity detail */
  .entity-detail { padding: 0; }
  .entity-body { padding: 20px 24px; display: flex; flex-direction: column; gap: 20px; }
  .entity-field-label { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-dim); margin-bottom: 6px; }
  .entity-content-text { font-size: 14px; color: var(--text); white-space: pre-wrap; line-height: 1.6; }
  .entity-rationale-text { font-size: 13px; color: var(--text-dim); white-space: pre-wrap; line-height: 1.6; }
  .entity-trigger-text { font-size: 13px; color: var(--accent); background: var(--accent-dim); display: inline-block; padding: 3px 8px; border-radius: 4px; }
  .entity-traj-link { font-size: 12px; color: var(--accent); font-family: var(--font-mono); text-decoration: underline; text-underline-offset: 3px; }
  .entity-traj-link:hover { color: #fff; }
  .entity-no-traj { font-size: 12px; color: var(--text-dimmer); font-style: italic; }
  .entity-type-badge { display: inline-block; background: var(--green); color: #000; border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; }

  /* Scrollbars */
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--text-dimmer); }
</style>
</head>
<body>
<div id="root"></div>
<script type="text/babel">
const { useState, useEffect } = React;

// ── Hash routing ─────────────────────────────────────────────────────────────

function parseHash() {
  const h = window.location.hash.slice(1);
  if (h.startsWith('t/')) return { type: 'trajectory', id: decodeURIComponent(h.slice(2)) };
  if (h.startsWith('e/')) return { type: 'entity', id: decodeURIComponent(h.slice(2)) };
  return null;
}

function trajHash(filename) { return '#t/' + encodeURIComponent(filename); }
function entityHash(slug)   { return '#e/' + encodeURIComponent(slug); }

// ── Utility ─────────────────────────────────────────────────────────────────

function fmtTimestamp(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  } catch { return ts; }
}

function fmtFilename(filename) {
  const m = filename.match(/trajectory_(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})/);
  if (!m) return filename;
  try {
    const d = new Date(`${m[1]}T${m[2]}:${m[3]}:00Z`);
    return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  } catch { return filename; }
}

function truncate(s, n=60) {
  if (!s) return '';
  return s.length > n ? s.slice(0, n) + '…' : s;
}

// ── Tool call component ──────────────────────────────────────────────────────

function ToolCallBlock({ call, result }) {
  const [open, setOpen] = useState(false);
  let args = '';
  try {
    const parsed = typeof call.function.arguments === 'string'
      ? JSON.parse(call.function.arguments)
      : call.function.arguments;
    args = JSON.stringify(parsed, null, 2);
  } catch {
    args = call.function.arguments || '';
  }

  return (
    <div className="tool-call">
      <div className="tool-call-header" onClick={() => setOpen(o => !o)}>
        <span className="tool-call-name">🔧 {call.function.name}</span>
        <span className="tool-call-id">{call.id}</span>
        <span className="tool-call-chevron">{open ? '▾' : '▸'}</span>
      </div>
      {open && args && (
        <div className="tool-call-args">{args}</div>
      )}
      {result && (
        <div className="tool-call-args" style={{borderTop: '1px solid var(--border)', color: 'var(--text-dim)'}}>
          {'→ '}{truncate(result.content, 300)}
        </div>
      )}
    </div>
  );
}

// ── Message component ────────────────────────────────────────────────────────

function Message({ msg, toolResults }) {
  if (msg.role === 'tool') return null;

  if (msg.role === 'user') {
    return (
      <div className="message message-user">
        <div className="message-role">👤 User</div>
        <div className="message-content">{msg.content}</div>
      </div>
    );
  }

  if (msg.role === 'assistant') {
    return (
      <div className="message message-assistant">
        <div className="message-role">🤖 Assistant</div>
        {msg.thinking && (
          <div className="message message-thinking" style={{margin: '6px 8px', borderRadius: 4}}>
            <div className="message-role">💭 Thinking</div>
            <div className="message-content" style={{fontSize: 12, color: 'var(--text-dim)'}}>{msg.thinking}</div>
          </div>
        )}
        {msg.content && <div className="message-content">{msg.content}</div>}
        {msg.tool_calls && msg.tool_calls.map(call => (
          <ToolCallBlock
            key={call.id}
            call={call}
            result={toolResults[call.id]}
          />
        ))}
      </div>
    );
  }

  return null;
}

// ── Transcript ───────────────────────────────────────────────────────────────

function Transcript({ messages }) {
  const toolResults = {};
  for (const m of messages) {
    if (m.role === 'tool' && m.tool_call_id) {
      toolResults[m.tool_call_id] = m;
    }
  }

  return (
    <div className="transcript">
      {messages.map((msg, i) => (
        <Message key={i} msg={msg} toolResults={toolResults} />
      ))}
    </div>
  );
}

// ── Trajectory detail ────────────────────────────────────────────────────────

function TrajectoryDetail({ detail }) {
  return (
    <div>
      <div className="detail-header">
        <div className="detail-title">{detail.filename}</div>
        <div className="detail-meta">
          <div className="detail-meta-item"><strong>{fmtTimestamp(detail.timestamp)}</strong></div>
          <div className="detail-meta-item">Model: <strong>{detail.model || '—'}</strong></div>
          <div className="detail-meta-item">Messages: <strong>{detail.messages.length}</strong></div>
          <div className="detail-meta-item">Guidelines: <strong>{detail.guidelines.length}</strong></div>
        </div>
      </div>

      <div className="linked-section">
        <div className="linked-section-title">Guidelines extracted from this session</div>
        {detail.guidelines.length === 0
          ? <div className="linked-none">None</div>
          : <div className="linked-pills">
              {detail.guidelines.map(slug => (
                <a key={slug} href={entityHash(slug)} className="linked-pill" title={slug}>
                  {slug}
                </a>
              ))}
            </div>
        }
      </div>

      <Transcript messages={detail.messages} />
    </div>
  );
}

// ── Entity detail ────────────────────────────────────────────────────────────

function EntityDetail({ detail }) {
  const trajFilename = detail.trajectory ? detail.trajectory.split('/').pop() : null;

  return (
    <div className="entity-detail">
      <div className="detail-header">
        <div className="detail-title">{detail.slug}</div>
        <div className="detail-meta" style={{marginTop: 8}}>
          <span className="entity-type-badge">{detail.type}</span>
        </div>
      </div>

      <div className="entity-body">
        {detail.trigger && (
          <div>
            <div className="entity-field-label">When</div>
            <span className="entity-trigger-text">{detail.trigger}</span>
          </div>
        )}

        <div>
          <div className="entity-field-label">Guideline</div>
          <div className="entity-content-text">{detail.content}</div>
        </div>

        {detail.rationale && (
          <div>
            <div className="entity-field-label">Rationale</div>
            <div className="entity-rationale-text">{detail.rationale}</div>
          </div>
        )}

        <div>
          <div className="entity-field-label">Source trajectory</div>
          {trajFilename
            ? <a href={trajHash(trajFilename)} className="entity-traj-link">
                {detail.trajectory}
              </a>
            : <span className="entity-no-traj">No trajectory recorded</span>
          }
        </div>
      </div>
    </div>
  );
}

// ── Sidebar ──────────────────────────────────────────────────────────────────

function Sidebar({ trajectories, entities, selected }) {
  return (
    <div className="sidebar">
      <div className="sidebar-scroll-section">
        <div className="sidebar-section-header">Trajectories</div>
        {trajectories.length === 0
          ? <div className="sidebar-empty">No trajectories found</div>
          : trajectories.map(t => (
              <a
                key={t.filename}
                href={trajHash(t.filename)}
                className={`sidebar-item ${selected?.type === 'trajectory' && selected.id === t.filename ? 'active' : ''}`}
              >
                <div className="sidebar-item-name">{fmtFilename(t.filename)}</div>
                <div className="sidebar-item-meta">
                  {t.message_count} msgs
                  {t.guideline_count > 0 && (
                    <span className="badge">{t.guideline_count} guideline{t.guideline_count !== 1 ? 's' : ''}</span>
                  )}
                </div>
              </a>
            ))
        }
      </div>

      <hr className="sidebar-divider" />

      <div className="sidebar-scroll-section">
        <div className="sidebar-section-header">Guidelines</div>
        {entities.length === 0
          ? <div className="sidebar-empty">No guidelines found</div>
          : entities.map(e => (
              <a
                key={e.slug}
                href={entityHash(e.slug)}
                className={`sidebar-item ${selected?.type === 'entity' && selected.id === e.slug ? 'active' : ''}`}
              >
                <div className="sidebar-item-name">{e.slug}</div>
                {e.trigger && <div className="sidebar-item-meta">{truncate(e.trigger, 40)}</div>}
              </a>
            ))
        }
      </div>
    </div>
  );
}

// ── App ──────────────────────────────────────────────────────────────────────

function App() {
  const [trajectories, setTrajectories] = useState([]);
  const [entities, setEntities] = useState([]);
  const [selected, setSelected] = useState(() => parseHash());
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);

  // Sync selection from hash (handles back/forward + all link clicks)
  useEffect(() => {
    const onHashChange = () => setSelected(parseHash());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  useEffect(() => {
    fetch('/api/trajectories').then(r => r.json()).then(setTrajectories).catch(() => {});
    fetch('/api/entities').then(r => r.json()).then(setEntities).catch(() => {});
  }, []);

  useEffect(() => {
    if (!selected) { setDetail(null); return; }
    setLoading(true);
    setDetail(null);
    const url = selected.type === 'trajectory'
      ? `/api/trajectories/${encodeURIComponent(selected.id)}`
      : `/api/entities/${encodeURIComponent(selected.id)}`;
    fetch(url)
      .then(r => { if (!r.ok) throw new Error('not found'); return r.json(); })
      .then(data => { setDetail(data); setLoading(false); })
      .catch(() => setLoading(false));
  }, [selected]);

  return (
    <div className="app">
      <header className="app-header">
        <h1>Evolve Viz</h1>
        <span className="subtitle">
          {trajectories.length} trajectory{trajectories.length !== 1 ? 's' : ''}
          {' · '}
          {entities.length} guideline{entities.length !== 1 ? 's' : ''}
        </span>
      </header>
      <div className="app-body">
        <Sidebar
          trajectories={trajectories}
          entities={entities}
          selected={selected}
        />
        <main className="detail-panel">
          {loading && <div className="loading">Loading…</div>}
          {!loading && !detail && (
            <div className="detail-empty">Select a trajectory or guideline from the sidebar</div>
          )}
          {!loading && detail && selected?.type === 'trajectory' && (
            <TrajectoryDetail detail={detail} />
          )}
          {!loading && detail && selected?.type === 'entity' && (
            <EntityDetail detail={detail} />
          )}
        </main>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
</script>
</body>
</html>
"""
