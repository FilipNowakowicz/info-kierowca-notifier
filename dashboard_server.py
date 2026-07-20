#!/usr/bin/env python3
"""Local-only read-only dashboard for the info-kierowca notifier.

Serves one HTML page plus the notifier's status.json. Binds to
127.0.0.1 only - never reachable off this machine.
"""
import http.server
import json
import socketserver

from paths import STATUS_FILE, empty_status

HOST = "127.0.0.1"
PORT = 8787

# Derived from paths.empty_status() rather than hand-written: this used to be a
# literal byte string that had already drifted from the real default shape (it
# grew "urgent"/"paused" keys the notifier's own default dict never had).
EMPTY_STATUS = json.dumps(empty_status()).encode()

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>info-kierowca notifier</title>
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

  /* The headline doubles as the pause/resume control when app.py's
     toolbar script is present (see TOOLBAR_HTML, which adds the
     .ikw-pausable class plus the click/hover/focus behavior). This
     structure is inert on its own - no cursor, no hover styling - so
     the plain read-only dashboard (dashboard_server.py run standalone)
     still renders correctly without implying a click it can't act on.
     The wrap's own top/bottom padding is cancelled out by an equal and
     opposite -0.3rem margin on both sides, so the padding only widens
     the hover/click box - it contributes zero net flow height. That
     matters because inline-block boxes (needed here so the hover
     background hugs the text instead of spanning #main's full width)
     never take part in margin collapsing, unlike the plain block <div>
     this used to be - so any non-cancelled margin would sit here as
     its own separate gap instead of collapsing into #subline/#detail's
     margins the way it used to. Zeroing it out leaves that original
     collapsing chain (#subline's/#detail's own margins into #countdown's)
     as the only thing spacing the headline from what follows, so
     #headline-wrap reproduces the exact old layout. Combined with
     #headline-hint below being position:absolute (out of flow, like
     #headline-icon already is), the headline text lands at the exact
     same spot it sat at before this element existed, whether or not
     the hint or icon are visible. */
  #headline-wrap { position: relative; display: inline-block; border-radius: 12px; padding: 0.3rem 0.7rem; margin: -0.3rem -0.7rem; -webkit-tap-highlight-color: transparent; }
  #headline { font-size: 3rem; font-weight: 700; letter-spacing: -0.02em; transition: opacity 0.15s ease; }
  #headline-icon { position: absolute; top: 50%; left: 50%; width: 3.2rem; height: 3.2rem; transform: translate(-50%, -50%) scale(0.8); opacity: 0; filter: drop-shadow(0 2px 10px rgba(0,0,0,0.45)); transition: opacity 0.15s ease, transform 0.15s ease; pointer-events: none; }
  #headline-hint { position: absolute; top: 100%; left: 50%; transform: translateX(-50%); margin-top: 0.15rem; font-size: 0.8rem; opacity: 0; white-space: nowrap; transition: opacity 0.15s ease; }

  #subline { font-size: 1.2rem; line-height: 1.4; opacity: 0.85; margin-bottom: 0.5rem; }
  #detail { font-size: 1.1rem; line-height: 1.6; white-space: pre-line; opacity: 0.9; }
  /* min-height matches this line's actual rendered height (measured at
     font-size 1rem in this font stack) so tickCountdown() clearing its
     text while paused doesn't collapse the box to zero height and shift
     everything above it - the same reason #headline-hint/#headline-icon
     are handled the way they are above. */
  #countdown { margin-top: 2rem; font-size: 1rem; min-height: 1.25rem; opacity: 0.6; font-variant-numeric: tabular-nums; }
  #meta { margin-top: 0.4rem; font-size: 0.85rem; opacity: 0.45; }
  /* #session-refresh-btn stays display:none here - only app.py's
     TOOLBAR_HTML (which backs /relogin-now) reveals and styles it, same
     reason #headline-wrap's cursor/hover styling is gated on TOOLBAR_HTML
     adding .ikw-pausable: the plain read-only dashboard has no endpoint
     behind this button and must not show an affordance it can't act on. */
  #session-expiry-wrap { margin-top: 0.15rem; display: flex; align-items: center; justify-content: center; gap: 0.35rem; }
  #session-expiry { font-size: 0.85rem; opacity: 0.45; }
  #session-refresh-btn { display: none; }

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
  /* Without this, a row that's cut off by max-height gets sliced mid-line
     (looks broken - a sliver of barely-legible text) instead of reading
     as "there's more, scroll for it". Only applied once poll() (below)
     confirms the list actually overflows, so a short history with no
     scrollbar never fades its own last, fully-visible row. */
  #history.ikw-overflowing { mask-image: linear-gradient(to bottom, black calc(100% - 1.6rem), transparent 100%); -webkit-mask-image: linear-gradient(to bottom, black calc(100% - 1.6rem), transparent 100%); }
  #history div { padding: 0.3rem 0; border-bottom: 1px solid rgba(255,255,255,0.06); }
  #history .ts { opacity: 0.5; margin-right: 0.6rem; }
</style>
</head>
<body class="none">
  <div id="main">
    <div id="headline-wrap">
      <span id="headline">Checking&hellip;</span>
      <svg id="headline-icon" viewBox="0 0 24 24" fill="#fff">
        <g id="icon-pause"><rect x="4.5" y="3" width="5.5" height="18" rx="1.6"/><rect x="14" y="3" width="5.5" height="18" rx="1.6"/></g>
        <path id="icon-play" d="M6 3.5v17a1 1 0 0 0 1.53.85l13.5-8.5a1 1 0 0 0 0-1.7L7.53 2.65A1 1 0 0 0 6 3.5z" style="display:none"/>
      </svg>
    </div>
    <div id="headline-hint"></div>
    <div id="subline"></div>
    <div id="detail"></div>
    <div id="countdown"></div>
    <div id="meta"></div>
    <div id="session-expiry-wrap">
      <span id="session-expiry"></span>
      <button id="session-refresh-btn" type="button" title="Get new session" aria-label="Get new session">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 4v5h-5"/>
        </svg>
      </button>
    </div>
  </div>
  <div id="history"></div>

<script>
let isPaused = false;
// Epoch ms of the next scheduled check, straight off status.json's own
// next_check_at (notifier.py's loop() writes it as the *actual* resolved
// wait, jitter included) - so the countdown counts down to the real next
// check instead of guessing from a fixed interval.
let nextCheckAt = null;

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
  const headlineIconPause = document.getElementById("icon-pause");
  const headlineIconPlay = document.getElementById("icon-play");
  const headlineHint = document.getElementById("headline-hint");
  const subline = document.getElementById("subline");
  const detail = document.getElementById("detail");
  const meta = document.getElementById("meta");
  const history = document.getElementById("history");

  const fastest = fastestOf(data.current_hits);
  isPaused = !!data.paused;
  headlineIconPause.style.display = isPaused ? "none" : "";
  headlineIconPlay.style.display = isPaused ? "" : "none";
  headlineHint.textContent = isPaused ? "Click to resume" : "Click to pause";

  if (isPaused) {
    // Checked first, ahead of outcome: pausing no longer overwrites the
    // last real outcome/message in status.json (see notifier.run_check),
    // so this only affects what's displayed, not what's stored — Resume
    // falls straight back to the last known state below instead of
    // waiting on a fresh check to stop saying "Paused".
    // subline/detail stay empty here (rather than a "Click to resume
    // checking." message) so pausing matches the shape of the far more
    // common empty-subline/detail states (no_slot, waiting for first
    // check) exactly - nothing to reserve layout space for, so toggling
    // pause never shifts the headline. The hover hint plus the big
    // play-icon overlay already say the same thing.
    body.className = "none";
    headline.textContent = "Paused";
    subline.textContent = "";
    detail.textContent = "";
  } else if (data.outcome === "slot_found" && fastest) {
    body.className = data.urgent ? "hit-soon" : "hit-far";
    headline.textContent = fmtDateTime(fastest.datetime);
    subline.textContent = `${fastest.word} · ${fastest.places} spots`;
    detail.textContent = "";
  } else if (data.outcome === "auth_expired") {
    body.className = "error";
    headline.textContent = "Session expired";
    subline.textContent = "";
    detail.textContent = "Log back in via browser and update session.json";
  } else if (data.outcome === "network_error") {
    // Offline is a normal, self-healing state, not an error worth alarming
    // about — styled like "no result yet" rather than red.
    body.className = "none";
    headline.textContent = "Offline";
    subline.textContent = "";
    detail.textContent = data.message || "Can't reach info-kierowca.pl — will retry";
  } else if (data.outcome === "unexpected" || data.outcome === "unparseable") {
    body.className = "error";
    headline.textContent = "Something's wrong";
    subline.textContent = "";
    detail.textContent = data.message || "Unexpected response — check manually";
  } else if (data.outcome === "no_slot") {
    body.className = "none";
    headline.textContent = "No slots in the next 31 days";
    subline.textContent = "";
    detail.textContent = "";
  } else {
    body.className = "none";
    headline.textContent = "Waiting for first check…";
    subline.textContent = "";
    detail.textContent = "";
  }

  meta.textContent = data.last_check ? `Last checked: ${fmtDateTime(data.last_check)}` : "No checks yet";
  nextCheckAt = data.next_check_at ? new Date(data.next_check_at).getTime() : null;

  const sessionExpiry = document.getElementById("session-expiry");
  // Estimate only - the real signal is the "Session expired" outcome above;
  // see SESSION_ESTIMATED_LIFETIME_SECONDS in notifier.py. Minutes, not a
  // live mm:ss ticker like tickCountdown() below: recomputed each 5s poll,
  // which is plenty for an hour-scale estimate and doesn't flicker like a
  // per-second countdown would.
  if (data.session_expires_estimate) {
    const remainingMin = Math.round((new Date(data.session_expires_estimate).getTime() - Date.now()) / 60000);
    sessionExpiry.textContent = remainingMin > 0
      ? `Session expires in ${remainingMin} min`
      : "Session expired";
  } else {
    sessionExpiry.textContent = "";
  }

  history.innerHTML = "";
  (data.history || []).slice().reverse().forEach(entry => {
    const div = document.createElement("div");
    // History entries written before the schema narrowed carry the full
    // "hits" list instead of a precomputed "fastest" — read either.
    const f = entry.fastest || fastestOf(entry.hits);
    const text = f ? `${fmtShort(f.datetime)} · ${f.word} (${f.places})` : "no slots in the next 31 days";
    div.innerHTML = `<span class="ts">${fmtDateTime(entry.seen_at)}</span>${text}`;
    history.appendChild(div);
  });
  history.classList.toggle("ikw-overflowing", history.scrollHeight > history.clientHeight + 1);
}

function tickCountdown() {
  const el = document.getElementById("countdown");
  if (isPaused || nextCheckAt === null) { el.textContent = ""; return; }
  const remaining = Math.round((nextCheckAt - Date.now()) / 1000);
  if (remaining <= 0) {
    el.textContent = "Checking any moment now…";
  } else {
    const m = Math.floor(remaining / 60);
    const s = remaining % 60;
    el.textContent = `Next check in ${m}:${String(s).padStart(2, "0")}`;
  }
}

poll();
setInterval(poll, 5000);
setInterval(tickCountdown, 1000);
</script>
</body>
</html>
"""


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
