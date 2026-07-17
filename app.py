#!/usr/bin/env python3
"""Unified entry point: a local web app (first-run setup wizard + dashboard)
plus the background poller, all in one process — meant to be run directly
(`python app.py`) or packaged into a single no-console binary (see
pyinstaller.spec) so someone can just double-click it with zero setup.

Composes notifier.py (poll loop), dashboard_server.py (status page), and
auto_refresh_session.py (Chrome/QR login) rather than reimplementing any of
them — see each module's own docstring for what it does on its own.
"""
import http.server
import json
import os
import secrets
import socket
import socketserver
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import auto_refresh_session
import dashboard_server
import notifier
import open_logged_in_browser

HOST = dashboard_server.HOST
PORT = dashboard_server.PORT
INTERVAL = 60

# Static snapshot of every active DORD/WORD/MORD/PORD/ZORD center, fetched
# from the site's own (session-gated) dictionary endpoint — see
# fetch_word_centers.py, which regenerates this file. Baked in rather than
# fetched live because the wizard has to work before the user has ever
# logged in, and that endpoint needs a session.
WORD_CENTERS_FILE = Path(__file__).parent / "word_centers.json"


def load_word_centers():
    try:
        with open(WORD_CENTERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


WORD_CENTERS = load_word_centers()

EXAM_TYPE_CHOICES = ("Theoretical", "Practice")


def already_running():
    """True if something is already answering our status endpoint on PORT."""
    try:
        with socket.create_connection((HOST, PORT), timeout=0.3):
            pass
    except OSError:
        return False
    try:
        req = urllib.request.Request(f"http://{HOST}:{PORT}/status.json")
        with urllib.request.urlopen(req, timeout=1) as resp:
            return resp.status == 200
    except Exception:
        return False


def build_config(payload):
    """Validate a /setup POST body and assemble it into config.json's schema."""
    def require_str(key, label):
        val = payload.get(key)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"{label} is required")
        return val.strip()

    def to_int_list(values, label):
        try:
            return [int(v) for v in values]
        except (TypeError, ValueError):
            raise ValueError(f"{label} must be numeric IDs")

    profile_number = require_str("profile_number", "PKK number")
    ntfy_topic = require_str("ntfy_topic", "Notification topic")

    organization_ids = payload.get("organization_ids")
    if not isinstance(organization_ids, list) or not organization_ids:
        raise ValueError("Pick or enter at least one WORD center")
    organization_ids = to_int_list(organization_ids, "WORD center IDs")

    watch_organization_ids = payload.get("watch_organization_ids") or organization_ids
    watch_organization_ids = to_int_list(watch_organization_ids, "Watched WORD center IDs")

    exam_types = payload.get("exam_types")
    if not isinstance(exam_types, list) or not exam_types or not set(exam_types) <= set(EXAM_TYPE_CHOICES):
        raise ValueError("Pick at least one exam type")

    try:
        category = int(payload.get("category", 5))
        push_below_days = int(payload.get("push_below_days", 10))
    except (TypeError, ValueError):
        raise ValueError("Category / push-threshold must be numbers")

    config = {
        "organization_ids": organization_ids,
        "watch_organization_ids": watch_organization_ids,
        "category": category,
        "profile_number": profile_number,
        "exam_types": exam_types,
        "ntfy_topic": ntfy_topic,
        "push_below_days": push_below_days,
        "auto_refresh_chrome": bool(payload.get("auto_refresh_chrome", True)),
    }
    push_before_date = payload.get("push_before_date")
    if push_before_date:
        config["push_before_date"] = push_before_date
    return config


STOP_BUTTON_HTML = """
<button id="ikw-stop-btn" style="position:fixed;top:1rem;right:1rem;padding:0.5rem 1rem;
  background:#444;color:#eee;border:1px solid #666;border-radius:6px;cursor:pointer;
  font-family:-apple-system,'Segoe UI',system-ui,sans-serif;">Stop</button>
<script>
document.getElementById('ikw-stop-btn').addEventListener('click', async () => {
  if (!confirm('Stop info-kierowca-notifier? You will stop getting checked/notified until you start it again.')) return;
  try { await fetch('/shutdown', {method: 'POST'}); } catch (e) {}
  document.body.innerHTML =
    '<div style="padding:4rem;text-align:center;font-family:sans-serif;color:#eee;">Stopped. You can close this tab.</div>';
});
</script>
"""

WIZARD_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>info-kierowca watcher — setup</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
    background: #1c1c1c; color: #eee; padding: 2rem; display: flex; justify-content: center;
  }
  #card { max-width: 560px; width: 100%; }
  h1 { font-size: 1.6rem; margin-bottom: 0.2rem; }
  p.lead { opacity: 0.75; margin-top: 0; margin-bottom: 2rem; }
  fieldset { border: 1px solid #444; border-radius: 8px; margin-bottom: 1.2rem; padding: 1rem 1.2rem; }
  legend { padding: 0 0.4rem; opacity: 0.85; }
  label { display: block; margin-bottom: 0.3rem; font-size: 0.95rem; }
  input[type=text], input[type=number], input[type=date], select {
    width: 100%; padding: 0.5rem; background: #2a2a2a; color: #eee; border: 1px solid #555;
    border-radius: 6px; margin-bottom: 0.8rem; font-size: 0.95rem;
  }
  .row { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.4rem; }
  .row input[type=checkbox] { width: auto; margin: 0; }
  .hint { opacity: 0.6; font-size: 0.85rem; margin-top: -0.4rem; margin-bottom: 0.8rem; }
  .centers-head { display: flex; gap: 1rem; opacity: 0.6; font-size: 0.8rem; margin-bottom: 0.3rem; }
  .centers-head span:first-child { width: 5.4rem; }
  #centers-list { max-height: 260px; overflow-y: auto; margin-bottom: 0.6rem; }
  .center-row { display: flex; align-items: center; gap: 1rem; margin-bottom: 0.3rem; }
  .center-row .checks { display: flex; gap: 2.1rem; width: 5.4rem; flex-shrink: 0; }
  button[type=submit] {
    width: 100%; padding: 0.8rem; background: #3a6ea5; color: #fff; border: none;
    border-radius: 6px; font-size: 1rem; cursor: pointer;
  }
  #copy-ntfy { padding: 0.4rem 0.8rem; margin-left: 0.5rem; background: #444; color: #eee;
    border: 1px solid #666; border-radius: 6px; cursor: pointer; }
  #error { color: #ff8080; margin-bottom: 1rem; white-space: pre-line; }
  #done { display: none; text-align: center; padding: 2rem 0; }
  #done a { color: #8ab4f8; }
</style>
</head>
<body>
<div id="card">
  <h1>Set up info-kierowca watcher</h1>
  <p class="lead">This runs entirely on your machine — nothing but info-kierowca.pl ever sees your PKK number or session.</p>

  <div id="error"></div>

  <form id="form">
    <fieldset>
      <legend>Your exam</legend>
      <label for="profile_number">PKK number</label>
      <input type="text" id="profile_number" required>

      <label for="category">License category</label>
      <select id="category">
        <option value="5" selected>B (category 5)</option>
        <option value="other">Other — enter number</option>
      </select>
      <input type="number" id="category-other" placeholder="Category number" style="display:none;">

      <label>Exam type</label>
      <div class="row"><input type="checkbox" id="exam-theoretical" checked><label for="exam-theoretical" style="margin:0;">Theoretical</label></div>
      <div class="row"><input type="checkbox" id="exam-practice"><label for="exam-practice" style="margin:0;">Practice</label></div>
    </fieldset>

    <fieldset>
      <legend>WORD centers (__CENTER_COUNT__ nationwide)</legend>
      <input type="text" id="center-search" placeholder="Search by name or city...">
      <div class="centers-head"><span></span><span>Query</span><span>Alert me</span></div>
      <div id="centers-list"></div>
      <label for="manual-ids" style="margin-top:0.6rem;">Other center IDs (comma-separated, optional)</label>
      <input type="text" id="manual-ids" placeholder="e.g. 12345, 67890">
      <div class="hint">Don't see your center? This list is a snapshot and may be missing a newly opened one — add its numeric ID here instead (added IDs are both queried and alerted on).</div>
    </fieldset>

    <fieldset>
      <legend>Alerting</legend>
      <label for="push_below_days">Push a phone notification when the fastest slot is within this many days</label>
      <input type="number" id="push_below_days" value="10">

      <label for="push_before_date">...or push for any slot before a fixed date (optional, overrides the above)</label>
      <input type="date" id="push_before_date">

      <div class="row"><input type="checkbox" id="auto_refresh_chrome" checked><label for="auto_refresh_chrome" style="margin:0;">Automatically reopen Chrome to log back in when my session expires</label></div>
    </fieldset>

    <fieldset>
      <legend>Phone notifications</legend>
      <label>Your private notification link — install the <a href="https://ntfy.sh/app" target="_blank" style="color:#8ab4f8;">ntfy app</a> and subscribe to it exactly:</label>
      <div style="display:flex;align-items:center;">
        <input type="text" id="ntfy_topic" value="__NTFY_TOPIC__" readonly style="margin-bottom:0;">
        <button type="button" id="copy-ntfy">Copy link</button>
      </div>
      <div class="hint">Anyone who knows this link can read your notifications — don't share it.</div>
    </fieldset>

    <button type="submit">Save and log in</button>
  </form>

  <div id="done">
    <p>Config saved. A Chrome window should open shortly — scan the QR code in the mObywatel app to log in.</p>
    <p><a href="/">Go to dashboard</a></p>
  </div>
</div>

<script>
const CENTERS = __CENTERS_JSON__;

const centersList = document.getElementById('centers-list');
function renderCenters(filter) {
  const f = filter.trim().toLowerCase();
  centersList.innerHTML = '';
  CENTERS.forEach(c => {
    const label = `${c.name} (${c.location})`;
    if (f && !label.toLowerCase().includes(f)) return;
    const row = document.createElement('div');
    row.className = 'center-row';
    const checks = document.createElement('div');
    checks.className = 'checks';
    const orgCb = document.createElement('input');
    orgCb.type = 'checkbox'; orgCb.className = 'org'; orgCb.value = c.id;
    const watchCb = document.createElement('input');
    watchCb.type = 'checkbox'; watchCb.className = 'watch'; watchCb.value = c.id;
    checks.appendChild(orgCb);
    checks.appendChild(watchCb);
    const span = document.createElement('span');
    span.textContent = label;
    row.appendChild(checks);
    row.appendChild(span);
    centersList.appendChild(row);
  });
}
renderCenters('');
document.getElementById('center-search').addEventListener('input', (e) => renderCenters(e.target.value));

document.getElementById('category').addEventListener('change', (e) => {
  document.getElementById('category-other').style.display = e.target.value === 'other' ? 'block' : 'none';
});

document.getElementById('copy-ntfy').addEventListener('click', () => {
  const el = document.getElementById('ntfy_topic');
  navigator.clipboard.writeText(`https://ntfy.sh/${el.value}`);
});

function collectIds(selector) {
  return Array.from(document.querySelectorAll(selector)).filter(el => el.checked).map(el => parseInt(el.value, 10));
}

function parseManual(text) {
  return text.split(',').map(s => s.trim()).filter(Boolean).map(Number).filter(n => !Number.isNaN(n));
}

document.getElementById('form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById('error');
  errorEl.textContent = '';
  try {
    const examTypes = [];
    if (document.getElementById('exam-theoretical').checked) examTypes.push('Theoretical');
    if (document.getElementById('exam-practice').checked) examTypes.push('Practice');
    if (!examTypes.length) throw new Error('Pick at least one exam type.');

    const manualIds = parseManual(document.getElementById('manual-ids').value);
    const orgIds = Array.from(new Set([...collectIds('.org'), ...manualIds]));
    if (!orgIds.length) throw new Error('Pick or enter at least one WORD center.');
    const watchIds = Array.from(new Set([...collectIds('.watch'), ...manualIds]));

    const profileNumber = document.getElementById('profile_number').value.trim();
    if (!profileNumber) throw new Error('PKK number is required.');

    const categorySel = document.getElementById('category').value;
    const category = categorySel === 'other'
      ? parseInt(document.getElementById('category-other').value, 10)
      : parseInt(categorySel, 10);
    if (!category) throw new Error('Enter a valid category number.');

    const body = {
      profile_number: profileNumber,
      organization_ids: orgIds,
      watch_organization_ids: watchIds,
      category: category,
      exam_types: examTypes,
      push_below_days: parseInt(document.getElementById('push_below_days').value, 10) || 10,
      auto_refresh_chrome: document.getElementById('auto_refresh_chrome').checked,
      ntfy_topic: document.getElementById('ntfy_topic').value,
    };
    const pushBeforeDate = document.getElementById('push_before_date').value;
    if (pushBeforeDate) body.push_before_date = pushBeforeDate;

    const res = await fetch('/setup', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Save failed.');

    document.getElementById('form').style.display = 'none';
    document.getElementById('done').style.display = 'block';
  } catch (err) {
    errorEl.textContent = err.message;
  }
});
</script>
</body>
</html>
"""


def render_wizard():
    centers_json = json.dumps(WORD_CENTERS, ensure_ascii=False).replace("</", "<\\/")
    page = WIZARD_PAGE.replace("__CENTERS_JSON__", centers_json)
    page = page.replace("__CENTER_COUNT__", str(len(WORD_CENTERS)))
    page = page.replace("__NTFY_TOPIC__", secrets.token_urlsafe(24))
    return page.encode("utf-8")


class AppHandler(http.server.BaseHTTPRequestHandler):
    logger = None
    dash_status = None

    def log_message(self, format, *args):
        pass

    def _send(self, code, body, content_type="text/html; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def _send_json(self, code, obj):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            if notifier.CONFIG_FILE.exists():
                self._send(200, dashboard_server.PAGE.replace("</body>", STOP_BUTTON_HTML + "</body>"))
            else:
                self._send(200, render_wizard())
        elif self.path == "/status.json":
            data = notifier.STATUS_FILE.read_bytes() if notifier.STATUS_FILE.exists() else dashboard_server.EMPTY_STATUS
            self._send(200, data, "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path == "/setup":
            self._handle_setup()
        elif self.path == "/shutdown":
            self._send_json(200, {"ok": True})
            os._exit(0)
        else:
            self._send(404, b"not found", "text/plain")

    def _handle_setup(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "Invalid request."})
            return
        try:
            config = build_config(payload)
        except ValueError as e:
            self._send_json(400, {"ok": False, "error": str(e)})
            return
        notifier.save_json(notifier.CONFIG_FILE, config)
        self._send_json(200, {"ok": True})
        if not notifier.SESSION_FILE.exists():
            AppHandler.logger.info("outcome=setup_complete detail=triggering_login")
            notifier.trigger_auto_refresh(AppHandler.logger, config)


class ThreadingServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_internal_auto_refresh():
    """Dispatch target for the frozen-binary re-invocation in
    notifier.trigger_auto_refresh() — see its docstring for why this exists.
    """
    sys.argv = [arg for arg in sys.argv if arg != "--internal-auto-refresh"]
    auto_refresh_session.main()


def run_internal_open_browser():
    """Dispatch target for the frozen-binary re-invocation in
    notifier.trigger_open_browser() — see its docstring for why this exists.
    """
    sys.argv = [arg for arg in sys.argv if arg != "--internal-open-browser"]
    open_logged_in_browser.main()


def main():
    if "--internal-auto-refresh" in sys.argv:
        run_internal_auto_refresh()
        return
    if "--internal-open-browser" in sys.argv:
        run_internal_open_browser()
        return

    if already_running():
        webbrowser.open(f"http://{HOST}:{PORT}/")
        return

    logger = notifier.setup_logger()
    dash_status = notifier.load_status()
    AppHandler.logger = logger
    AppHandler.dash_status = dash_status

    stop_event = threading.Event()
    poll_thread = threading.Thread(
        target=notifier.loop, args=(logger, dash_status, INTERVAL, stop_event), daemon=True
    )
    poll_thread.start()

    httpd = ThreadingServer((HOST, PORT), AppHandler)
    webbrowser.open(f"http://{HOST}:{PORT}/")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        httpd.shutdown()


if __name__ == "__main__":
    main()
