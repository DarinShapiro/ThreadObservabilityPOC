"""Core HTTP API for Thread Observability add-on.

Serves a lightweight status dashboard at ``/`` (Ingress entry-point) plus
JSON endpoints under ``/v1/...`` for programmatic access.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from . import supervisor_client

ADDON_VERSION = "0.4.0"
LOG_PATH = Path("/data/thread-observability/addon.log")


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _tail_log(n: int = 80) -> list[str]:
    if not LOG_PATH.exists():
        return []
    try:
        return LOG_PATH.read_text(errors="replace").splitlines()[-n:]
    except OSError:
        return []


DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Thread Observability</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         margin: 0; padding: 1.5rem; background: var(--bg, #f6f7f9); color: var(--fg, #111); }
  @media (prefers-color-scheme: dark) {
    body { --bg: #1c1c1e; --fg: #f2f2f7; }
    .card { background: #2c2c2e !important; border-color: #3a3a3c !important; }
    pre { background: #000 !important; color: #c8e1ff !important; }
    code { background: #3a3a3c !important; }
  }
  h1 { margin: 0 0 .25rem; font-size: 1.4rem; }
  h2 { margin: 0 0 .5rem; font-size: 1rem; text-transform: uppercase; letter-spacing: .05em; opacity: .7; }
  .sub { opacity: .65; font-size: .85rem; margin-bottom: 1.25rem; }
  .grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
  .card { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 1rem; }
  .card.wide { grid-column: 1 / -1; }
  .kv { display: grid; grid-template-columns: max-content 1fr; gap: .25rem 1rem; font-size: .9rem; }
  .kv dt { opacity: .65; }
  .kv dd { margin: 0; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
           overflow-wrap: anywhere; }
  .pill { display: inline-block; padding: .15rem .55rem; border-radius: 999px; font-size: .75rem; font-weight: 600; }
  .pill.ok { background: #d1fae5; color: #065f46; }
  .pill.warn { background: #fef3c7; color: #92400e; }
  .pill.err { background: #fee2e2; color: #991b1b; }
  pre { background: #0d1117; color: #c9d1d9; padding: .75rem; border-radius: 6px;
        font-size: .78rem; line-height: 1.35; max-height: 280px; overflow: auto; margin: 0; }
  code { background: #eef0f3; padding: .05rem .3rem; border-radius: 4px; font-size: .85em; }
  .links a { display: inline-block; margin-right: .75rem; font-size: .85rem; }
  button { font: inherit; padding: .35rem .8rem; border-radius: 6px; border: 1px solid #d1d5db;
           background: #fff; cursor: pointer; }
  button:hover { background: #f3f4f6; }
  .row { display: flex; gap: .5rem; align-items: center; justify-content: space-between; margin-bottom: .75rem; }
  .muted { opacity: .55; font-size: .8rem; }
</style>
</head>
<body>
  <h1>Thread Observability <span class="muted" id="version"></span></h1>
  <div class="sub">Status dashboard &middot; auto-refresh every 5&nbsp;s &middot;
    <span id="last-refresh">never refreshed</span>
  </div>

  <div class="grid">
    <div class="card">
      <div class="row"><h2>Add-on (Supervisor)</h2><span id="addon-pill" class="pill warn">loading</span></div>
      <dl class="kv" id="addon-kv"><dt>state</dt><dd>&hellip;</dd></dl>
    </div>

    <div class="card">
      <div class="row"><h2>Services</h2><span id="svc-pill" class="pill warn">loading</span></div>
      <dl class="kv">
        <dt>core (this page)</dt><dd id="core-state">running</dd>
        <dt>mcp (port 8100)</dt><dd id="mcp-state">&hellip;</dd>
      </dl>
    </div>

    <div class="card">
      <h2>Thread Network</h2>
      <dl class="kv">
        <dt>nodes</dt><dd id="n-nodes">&mdash;</dd>
        <dt>links</dt><dd id="n-links">&mdash;</dd>
        <dt>active issues</dt><dd id="n-issues">&mdash;</dd>
        <dt>data age</dt><dd id="n-age">&mdash;</dd>
      </dl>
      <div class="muted" style="margin-top:.5rem">Ingestion not yet implemented &mdash; values populate when collectors come online.</div>
    </div>

    <div class="card wide">
      <div class="row">
        <h2>Recent logs</h2>
        <button onclick="refresh()">Refresh now</button>
      </div>
      <pre id="logs">loading&hellip;</pre>
    </div>

    <div class="card wide">
      <h2>Endpoints</h2>
      <div class="links">
        <a href="v1/health/snapshot" target="_blank">/v1/health/snapshot</a>
        <a href="v1/issues/active" target="_blank">/v1/issues/active</a>
        <a href="v1/topology" target="_blank">/v1/topology</a>
        <a href="v1/dev/status" target="_blank">/v1/dev/status</a>
        <a href="health" target="_blank">/health</a>
      </div>
      <div class="muted" style="margin-top:.75rem">
        MCP JSON-RPC: <code>POST http://&lt;ha-host&gt;:8100/mcp</code> &middot;
        tools include <code>ha_get_addon_state</code>, <code>ha_get_addon_logs</code>,
        <code>ha_rebuild_addon</code>, <code>get_recent_logs</code>.
      </div>
    </div>
  </div>

<script>
async function fetchJSON(u) {
  const r = await fetch(u, {cache:'no-store'});
  if (!r.ok) throw new Error(u + ' -> ' + r.status);
  return r.json();
}
function setPill(el, kind, text) { el.className = 'pill ' + kind; el.textContent = text; }
function fmtKV(parent, obj) {
  parent.innerHTML = '';
  for (const [k,v] of Object.entries(obj)) {
    const dt = document.createElement('dt'); dt.textContent = k;
    const dd = document.createElement('dd');
    dd.textContent = v === null || v === undefined ? '—' : (typeof v === 'object' ? JSON.stringify(v) : String(v));
    parent.append(dt, dd);
  }
}
async function refresh() {
  document.getElementById('last-refresh').textContent = 'refreshed ' + new Date().toLocaleTimeString();
  try {
    const s = await fetchJSON('v1/dev/status');
    document.getElementById('version').textContent = 'v' + (s.addon_version || '?');

    const a = s.supervisor || {};
    if (a.error) {
      setPill(document.getElementById('addon-pill'), 'err', 'unreachable');
      fmtKV(document.getElementById('addon-kv'), {error: a.error});
    } else {
      const sum = a.summary || {};
      const state = (sum.state || 'unknown').toLowerCase();
      setPill(document.getElementById('addon-pill'),
              state === 'started' ? 'ok' : (state === 'stopped' ? 'err' : 'warn'),
              state);
      fmtKV(document.getElementById('addon-kv'), {
        version: sum.version, latest: sum.version_latest,
        update_available: sum.update_available, boot: sum.boot,
        watchdog: sum.watchdog, ingress: sum.ingress,
      });
    }

    try {
      const m = await fetchJSON('v1/dev/mcp-health');
      document.getElementById('mcp-state').textContent = m.ok ? 'running' : ('error: ' + (m.detail || m.status_code));
      setPill(document.getElementById('svc-pill'), m.ok ? 'ok' : 'err', m.ok ? 'healthy' : 'degraded');
    } catch (e) {
      document.getElementById('mcp-state').textContent = 'probe failed';
      setPill(document.getElementById('svc-pill'), 'warn', 'partial');
    }

    const h = s.health || {}, t = s.topology || {}, i = s.issues || {};
    document.getElementById('n-nodes').textContent = (t.nodes || []).length;
    document.getElementById('n-links').textContent = (t.links || []).length;
    document.getElementById('n-issues').textContent = i.count ?? (i.issues || []).length;
    document.getElementById('n-age').textContent =
      h.data_age_seconds === null || h.data_age_seconds === undefined ? '—' : (h.data_age_seconds + ' s');

    document.getElementById('logs').textContent =
      (s.recent_logs || []).join('\\n') || '(no log entries yet)';
  } catch (e) {
    document.getElementById('logs').textContent = 'Error: ' + e.message;
  }
}
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


def create_core_app() -> FastAPI:
    """Create the core FastAPI application."""
    app = FastAPI(title="Thread Observability Core API", version=ADDON_VERSION)

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(DASHBOARD_HTML)

    @app.get("/api")
    def api_root() -> dict[str, str]:
        return {"service": "core", "name": "thread-observability", "version": ADDON_VERSION}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "core", "checked_at": _utc_now()}

    @app.get("/v1/health/snapshot")
    def health_snapshot() -> dict[str, object]:
        return {
            "snapshot_id": "scaffold-snapshot",
            "computed_at": _utc_now(),
            "data_age_seconds": None,
            "summary": {"healthy_nodes": 0, "degraded_nodes": 0, "offline_nodes": 0},
            "active_issues": [],
        }

    @app.get("/v1/issues/active")
    def list_active_issues() -> dict[str, object]:
        return {"count": 0, "issues": [], "computed_at": _utc_now()}

    @app.get("/v1/topology")
    def topology_snapshot() -> dict[str, object]:
        return {"nodes": [], "links": [], "computed_at": _utc_now()}

    @app.get("/v1/dev/status")
    async def dev_status() -> dict[str, object]:
        try:
            sup: dict[str, object] = await supervisor_client.get_addon_info()
        except Exception as exc:  # noqa: BLE001
            sup = {"error": str(exc)}
        return {
            "addon_version": ADDON_VERSION,
            "checked_at": _utc_now(),
            "supervisor": sup,
            "health": health_snapshot(),
            "issues": list_active_issues(),
            "topology": topology_snapshot(),
            "recent_logs": _tail_log(80),
        }

    @app.get("/v1/dev/mcp-health")
    async def dev_mcp_health() -> dict[str, object]:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get("http://127.0.0.1:8100/health")
            return {"ok": r.status_code == 200, "status_code": r.status_code}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": str(exc)}

    return app
