#!/usr/bin/env python3
"""Local-only read-only dashboard for the info-kierowca notifier.

Serves one HTML page plus the notifier's status.json. Binds to
127.0.0.1 only - never reachable off this machine.
"""
import http.server
import socketserver
from pathlib import Path

STATE_DIR = Path.home() / ".local" / "state" / "info-kierowca-notifier"
STATUS_FILE = STATE_DIR / "status.json"
HOST = "127.0.0.1"
PORT = 8787

EMPTY_STATUS = (
    b'{"last_check": null, "outcome": null, "message": "", '
    b'"current_hits": [], "history": []}'
)

# Must match the .timer unit's OnUnitActiveSec - used only to estimate the
# next-check countdown client-side; it resyncs to reality every poll.
POLL_INTERVAL_SECONDS = 60

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>info-kierowca watcher</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
    background: #1c1c1c;
    color: #eee;
    transition: background-color 1.2s ease;
    padding: 2rem;
  }
  body.hit-soon { background: #8b1e1e; }
  body.hit-far  { background: #3a3a3a; }
  body.none     { background: #1c1c1c; }
  body.error    { background: #2e3a5c; }

  #main { text-align: center; max-width: 800px; }
  #headline { font-size: 3rem; font-weight: 700; margin-bottom: 0.5rem; letter-spacing: -0.02em; }
  #subline { font-size: 1.2rem; opacity: 0.85; margin-bottom: 0.5rem; }
  #detail { font-size: 1.1rem; line-height: 1.6; white-space: pre-line; opacity: 0.9; }
  #countdown { margin-top: 2rem; font-size: 1rem; opacity: 0.6; font-variant-numeric: tabular-nums; }
  #meta { margin-top: 0.4rem; font-size: 0.85rem; opacity: 0.45; }

  #history {
    margin-top: 3rem;
    width: 100%;
    max-width: 700px;
    max-height: 28vh;
    overflow-y: auto;
    font-size: 0.85rem;
    opacity: 0.85;
    border-top: 1px solid rgba(255,255,255,0.15);
    padding-top: 1rem;
  }
  #history div { padding: 0.3rem 0; border-bottom: 1px solid rgba(255,255,255,0.06); }
  #history .ts { opacity: 0.5; margin-right: 0.6rem; }
</style>
</head>
<body class="none">
  <div id="main">
    <div id="headline">Checking&hellip;</div>
    <div id="subline"></div>
    <div id="detail"></div>
    <div id="countdown"></div>
    <div id="meta"></div>
  </div>
  <div id="history"></div>

<script>
let lastCheckRaw = null;
let lastCheckPerf = null;

function fmtDateTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    weekday: "short", day: "2-digit", month: "short",
    year: "numeric", hour: "2-digit", minute: "2-digit"
  });
}

function fmtShort(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit"
  });
}

function daysUntil(iso) {
  return (new Date(iso).getTime() - Date.now()) / 86400000;
}

function fastestOf(hits) {
  if (!hits || !hits.length) return null;
  return hits.reduce((a, b) => (new Date(a.datetime) < new Date(b.datetime) ? a : b));
}

async function poll() {
  let data;
  try {
    const res = await fetch("/status.json", {cache: "no-store"});
    data = await res.json();
  } catch (e) {
    document.body.className = "error";
    document.getElementById("headline").textContent = "Dashboard lost contact with the notifier";
    return;
  }

  const body = document.body;
  const headline = document.getElementById("headline");
  const subline = document.getElementById("subline");
  const detail = document.getElementById("detail");
  const meta = document.getElementById("meta");
  const history = document.getElementById("history");

  const fastest = fastestOf(data.current_hits);

  if (data.outcome === "slot_found" && fastest) {
    const d = daysUntil(fastest.datetime);
    body.className = d <= 10 ? "hit-soon" : "hit-far";
    headline.textContent = fmtDateTime(fastest.datetime);
    subline.textContent = `${fastest.word} · ${fastest.places} spots`;
    detail.textContent = "";
  } else if (data.outcome === "auth_expired") {
    body.className = "error";
    headline.textContent = "Session expired";
    subline.textContent = "";
    detail.textContent = "Log back in via browser and update session.json";
  } else if (data.outcome === "unexpected" || data.outcome === "unparseable") {
    body.className = "error";
    headline.textContent = "Something's wrong";
    subline.textContent = "";
    detail.textContent = data.message || "Unexpected response — check manually";
  } else if (data.outcome === "no_slot") {
    body.className = "none";
    headline.textContent = "No slot in range yet";
    subline.textContent = "";
    detail.textContent = "";
  } else {
    body.className = "none";
    headline.textContent = "Waiting for first check…";
    subline.textContent = "";
    detail.textContent = "";
  }

  meta.textContent = data.last_check ? `Last checked: ${fmtDateTime(data.last_check)}` : "No checks yet";
  if (data.last_check !== lastCheckRaw) {
    lastCheckRaw = data.last_check;
    lastCheckPerf = data.last_check ? performance.now() : null;
  }

  history.innerHTML = "";
  (data.history || []).slice().reverse().forEach(entry => {
    const div = document.createElement("div");
    const f = fastestOf(entry.hits);
    const text = f ? `${fmtShort(f.datetime)} · ${f.word} (${f.places})` : "no slots in range";
    div.innerHTML = `<span class="ts">${fmtDateTime(entry.seen_at)}</span>${text}`;
    history.appendChild(div);
  });
}

function tickCountdown() {
  const el = document.getElementById("countdown");
  if (lastCheckPerf === null) { el.textContent = ""; return; }
  const remaining = Math.round((lastCheckPerf + POLL_INTERVAL_MS - performance.now()) / 1000);
  if (remaining <= 0) {
    el.textContent = "Checking any moment now…";
  } else {
    const m = Math.floor(remaining / 60);
    const s = remaining % 60;
    el.textContent = `Next check in ${m}:${String(s).padStart(2, "0")}`;
  }
}

const POLL_INTERVAL_MS = __POLL_INTERVAL_MS__;
poll();
setInterval(poll, 5000);
setInterval(tickCountdown, 1000);
</script>
</body>
</html>
""".replace("__POLL_INTERVAL_MS__", str(POLL_INTERVAL_SECONDS * 1000))


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/status.json":
            data = STATUS_FILE.read_bytes() if STATUS_FILE.exists() else EMPTY_STATUS
            self._send(200, data, "application/json")
        else:
            self._send(404, b"not found", "text/plain")


class Server(socketserver.TCPServer):
    allow_reuse_address = True


def main():
    with Server((HOST, PORT), Handler) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
