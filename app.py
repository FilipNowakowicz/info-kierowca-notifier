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

# Static snapshot of license categories (id/code/label), shown in the setup
# wizard's dropdown so a user picks "B — car" instead of the bare numeric id
# the API wants. Seeded with the confirmed B=5; refresh/extend with
# fetch_categories.py (session-gated, same reason as word_centers.json).
CATEGORIES_FILE = Path(__file__).parent / "categories.json"


def load_categories():
    try:
        with open(CATEGORIES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return [{"id": 5, "code": "B", "label": "B — car"}]


CATEGORIES = load_categories()

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
    except (TypeError, ValueError):
        raise ValueError("Category must be a number")

    current_slot_date = require_str("current_slot_date", "Current slot date")

    config = {
        "organization_ids": organization_ids,
        "watch_organization_ids": watch_organization_ids,
        "category": category,
        "profile_number": profile_number,
        "exam_types": exam_types,
        "ntfy_topic": ntfy_topic,
        "current_slot_date": current_slot_date,
        "phone_alerts": bool(payload.get("phone_alerts", True)),
        "auto_refresh_chrome": bool(payload.get("auto_refresh_chrome", True)),
        "auto_open_browser": bool(payload.get("auto_open_browser", True)),
    }
    return config


STOP_BUTTON_HTML = """
<style>
  .ikw-btn { position: fixed; top: 1rem; padding: 0.4rem 0.85rem; border-radius: 999px; cursor: pointer;
    font-family: -apple-system, 'Segoe UI', system-ui, sans-serif; font-size: 0.85rem; line-height: 1;
    background: rgba(255,255,255,0.07); color: #eee; border: 1px solid rgba(255,255,255,0.18);
    backdrop-filter: blur(6px); transition: background 0.12s, border-color 0.12s; }
  .ikw-btn:hover { background: rgba(255,255,255,0.14); border-color: rgba(255,255,255,0.32); }
  #ikw-settings-btn { right: 5.2rem; }
  #ikw-stop-btn { right: 1rem; }
  #ikw-stop-btn:hover { border-color: rgba(224,104,95,0.7); color: #ffb3ad; }
</style>
<button id="ikw-stop-btn" class="ikw-btn">Stop</button>
<script>
document.getElementById('ikw-stop-btn').addEventListener('click', async () => {
  if (!confirm('Stop info-kierowca-notifier? You will stop getting checked/notified until you start it again.')) return;
  try { await fetch('/shutdown', {method: 'POST'}); } catch (e) {}
  document.body.innerHTML =
    '<div style="padding:4rem;text-align:center;font-family:sans-serif;color:#eee;">Stopped. You can close this tab.</div>';
});
</script>
"""

SETTINGS_BUTTON_HTML = """
<button id="ikw-settings-btn" class="ikw-btn">Settings</button>
<script>
document.getElementById('ikw-settings-btn').addEventListener('click', () => {
  window.location.href = '/settings';
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
  :root {
    --accent: #e0a13c; --accent-soft: #f0c47e;
    --accent-dim: rgba(224,161,60,0.15); --accent-line: rgba(224,161,60,0.55);
  }
  body {
    margin: 0; min-height: 100vh; font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
    background: #1c1c1c; color: #eee; padding: 2rem; display: flex; justify-content: center;
  }
  #card { max-width: 560px; width: 100%; }
  h1 { font-size: 1.6rem; margin-bottom: 0.2rem; }
  p.lead { opacity: 0.75; margin-top: 0; margin-bottom: 2rem; }
  fieldset { border: 1px solid #383838; border-radius: 10px; margin-bottom: 1.1rem; padding: 1.1rem 1.2rem 1.25rem; }
  legend { padding: 0 0.45rem; opacity: 0.8; font-size: 0.9rem; }
  label { display: block; margin-bottom: 0.35rem; font-size: 0.92rem; opacity: 0.9; }
  input[type=text], input[type=number], input[type=password], select {
    width: 100%; padding: 0.55rem 0.65rem; background: #262626; color: #eee; border: 1px solid #3d3d3d;
    border-radius: 7px; margin-bottom: 0.9rem; font-size: 0.95rem;
  }
  input:focus, select:focus { outline: none; border-color: var(--accent-line); box-shadow: 0 0 0 3px var(--accent-dim); }
  input[type=checkbox] { accent-color: var(--accent); }
  .hint { opacity: 0.55; font-size: 0.83rem; margin-top: -0.55rem; margin-bottom: 0.9rem; }
  .icon { width: 18px; height: 18px; display: block; }

  /* exam-type pills */
  .pill-group { display: flex; gap: 0.5rem; }
  .pill { flex: 1; text-align: center; padding: 0.55rem 0.6rem; border-radius: 7px; cursor: pointer;
    background: #262626; border: 1px solid #3d3d3d; color: rgba(238,238,238,0.7); font-size: 0.9rem;
    font-weight: 600; transition: 0.12s; user-select: none; }
  .pill:hover { border-color: #555; }
  .pill.on { background: var(--accent-dim); border-color: var(--accent); color: var(--accent-soft); }

  /* reveal-able inputs (PKK / ntfy link) */
  .reveal { position: relative; margin-bottom: 0.9rem; }
  .reveal input { margin-bottom: 0; padding-right: 2.5rem; }
  .reveal-btn { position: absolute; top: 50%; right: 0.35rem; transform: translateY(-50%);
    background: none; border: none; color: rgba(238,238,238,0.5); cursor: pointer; padding: 0.3rem;
    display: grid; place-items: center; }
  .reveal-btn:hover { color: var(--accent-soft); }
  .ntfy-row { display: flex; gap: 0.5rem; align-items: stretch; }
  .ntfy-row .reveal { flex: 1; margin-bottom: 0; }
  #copy-ntfy { padding: 0 0.9rem; background: #2f2f2f; color: #eee; border: 1px solid #3d3d3d;
    border-radius: 7px; cursor: pointer; font-size: 0.88rem; white-space: nowrap; }
  #copy-ntfy:hover { border-color: #555; }

  /* combobox + selected centers */
  .combobox { position: relative; margin-bottom: 0.8rem; }
  .combobox input[type=text] { margin-bottom: 0; }
  #center-dropdown {
    display: none; position: absolute; top: calc(100% + 4px); left: 0; right: 0; z-index: 10;
    background: #262626; border: 1px solid #3d3d3d; border-radius: 7px; max-height: 240px; overflow-y: auto;
    box-shadow: 0 8px 22px rgba(0,0,0,0.45);
  }
  .dropdown-item { padding: 0.5rem 0.7rem; cursor: pointer; font-size: 0.9rem; display: flex;
    justify-content: space-between; gap: 0.75rem; align-items: center; }
  .dropdown-item .dd-loc { opacity: 0.5; font-size: 0.8rem; white-space: nowrap; }
  .dropdown-item:hover, .dropdown-item.active { background: var(--accent-dim); }
  .dropdown-empty { padding: 0.5rem 0.7rem; opacity: 0.6; font-size: 0.85rem; }
  #selected-centers { max-height: 280px; overflow-y: auto; margin-bottom: 0.6rem; }
  .selected-row { display: flex; align-items: center; gap: 0.8rem; padding: 0.55rem 0; border-bottom: 1px solid #2a2a2a; }
  .selected-row:last-child { border-bottom: none; }
  .center-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); opacity: 0.8; flex: none; }
  .selected-name { flex: 1; min-width: 0; }
  .selected-name .sn-name { font-size: 0.9rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .selected-name .sn-loc { font-size: 0.76rem; opacity: 0.4; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .remove-btn { background: none; border: none; color: rgba(238,238,238,0.4); font-size: 1.15rem; line-height: 1; cursor: pointer; padding: 0 0.2rem; transition: color 0.12s; }
  .remove-btn:hover { color: #ff8080; }
  .no-selection { opacity: 0.5; font-size: 0.85rem; padding: 0.4rem 0; }
  .center-count { font-size: 0.82rem; opacity: 0.6; margin-top: 0.2rem; }
  .center-count b { opacity: 1; font-weight: 600; }

  /* switches */
  .toggle-row { display: flex; align-items: center; gap: 1rem; }
  .toggle-row + .toggle-row { margin-top: 0.9rem; }
  .toggle-row .toggle-text { flex: 1; }
  .toggle-row .toggle-text .tt-title { font-size: 0.92rem; }
  .toggle-row .toggle-text .tt-sub { font-size: 0.82rem; opacity: 0.55; margin-top: 0.1rem; }
  .switch { position: relative; width: 46px; height: 26px; border-radius: 999px; flex: none;
    background: #2a2a2a; border: 1px solid #555; cursor: pointer; transition: 0.15s; }
  .switch::after { content: ""; position: absolute; top: 2px; left: 2px; width: 20px; height: 20px;
    border-radius: 50%; background: rgba(238,238,238,0.45); transition: 0.15s; }
  .switch.on { background: var(--accent); border-color: var(--accent); }
  .switch.on::after { transform: translateX(20px); background: #1c1c1c; }
  .divider { border-top: 1px solid #2a2a2a; margin: 1rem 0; }
  #ntfy-field { transition: opacity 0.15s; }
  #ntfy-field.disabled { opacity: 0.4; pointer-events: none; }

  /* custom date picker */
  .datepick { position: relative; margin-bottom: 0.3rem; }
  .datepick-input { cursor: pointer; margin-bottom: 0 !important; }
  .calendar { display: none; position: absolute; top: calc(100% + 6px); left: 0; z-index: 30;
    width: 288px; max-width: 100%; background: #262626; border: 1px solid #3d3d3d; border-radius: 10px;
    padding: 0.8rem; box-shadow: 0 14px 34px rgba(0,0,0,0.55); }
  .calendar.open { display: block; }
  .cal-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.55rem; }
  .cal-title { font-size: 0.92rem; font-weight: 600; }
  .cal-nav { background: none; border: none; color: rgba(238,238,238,0.7); cursor: pointer;
    font-size: 1.05rem; width: 1.9rem; height: 1.9rem; border-radius: 6px; }
  .cal-nav:hover { background: #333; color: #eee; }
  .cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; }
  .cal-dow { text-align: center; font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.04em; opacity: 0.4; padding: 0.3rem 0; }
  .cal-day { text-align: center; padding: 0.42rem 0; font-size: 0.85rem; border-radius: 6px; cursor: pointer; font-variant-numeric: tabular-nums; }
  .cal-day:hover { background: #333; }
  .cal-day.muted { opacity: 0.22; }
  .cal-day.disabled { opacity: 0.15; cursor: default; }
  .cal-day.disabled:hover { background: none; }
  .cal-day.today:not(.selected) { box-shadow: inset 0 0 0 1px var(--accent-line); }
  .cal-day.selected { background: var(--accent); color: #1c1c1c; font-weight: 600; }

  button[type=submit] {
    width: 100%; padding: 0.85rem; background: var(--accent); color: #1c1c1c; border: none;
    border-radius: 8px; font-size: 1rem; font-weight: 600; cursor: pointer; margin-top: 0.3rem;
  }
  button[type=submit]:hover { background: var(--accent-soft); }
  #error { color: #ff8080; margin-bottom: 1rem; white-space: pre-line; }
  #done { display: none; text-align: center; padding: 2rem 0; }
  #done a { color: var(--accent-soft); }
</style>
</head>
<body>
<div id="card">
  <h1 id="page-title">Set up info-kierowca watcher</h1>
  <p class="lead" id="page-lead">This runs entirely on your machine — nothing but info-kierowca.pl ever sees your PKK number or session.</p>

  <div id="error"></div>

  <form id="form">
    <fieldset>
      <legend>Your exam</legend>
      <label for="profile_number">PKK number</label>
      <div class="reveal">
        <input type="text" id="profile_number" autocomplete="off" required>
        <button type="button" class="reveal-btn" id="reveal-pkk" aria-label="Show or hide PKK number"></button>
      </div>

      <label for="category">License category</label>
      <select id="category"></select>
      <input type="number" id="category-other" placeholder="Category number" style="display:none;">

      <label>Exam type</label>
      <div class="pill-group" id="exam-types">
        <div class="pill on" data-val="Theoretical" role="button" tabindex="0">Theoretical</div>
        <div class="pill" data-val="Practice" role="button" tabindex="0">Practice</div>
      </div>
    </fieldset>

    <fieldset>
      <legend>WORD centers (__CENTER_COUNT__ nationwide)</legend>
      <div class="combobox">
        <input type="text" id="center-search" placeholder="Click to browse all centers, or type to filter..." autocomplete="off">
        <div id="center-dropdown"></div>
      </div>
      <div id="selected-centers"></div>
      <div class="center-count" id="center-count"></div>
      <label for="manual-ids" style="margin-top:0.6rem;">Other center IDs (comma-separated, optional)</label>
      <input type="text" id="manual-ids" placeholder="e.g. 12345, 67890">
      <div class="hint">Don't see your center? This list is a snapshot and may be missing a newly opened one — add its numeric ID here instead.</div>
    </fieldset>

    <fieldset>
      <legend>Alerts</legend>
      <label for="current_slot_date_display">Date of your current booked slot — a found slot on or before this counts as urgent (an earlier date, or a different time the same day)</label>
      <div class="datepick" id="datepick">
        <input type="text" class="datepick-input" id="current_slot_date_display" placeholder="Select a date" readonly required>
        <input type="hidden" id="current_slot_date">
        <div class="calendar" id="calendar"></div>
      </div>

      <div class="divider"></div>

      <div class="toggle-row">
        <div class="toggle-text">
          <div class="tt-title">Send a phone alert on an urgent slot</div>
          <div class="tt-sub">Buzzes your phone when a watched center opens a slot on or before your booked date. Turn off to just watch the dashboard.</div>
        </div>
        <div class="switch on" id="phone-alerts" role="switch" aria-checked="true" tabindex="0"></div>
      </div>
      <div id="ntfy-field" style="margin-top:1rem;">
        <label>Your private notification link — install the <a href="https://ntfy.sh/app" target="_blank" style="color:var(--accent-soft);">ntfy app</a> and subscribe to it exactly:</label>
        <div class="ntfy-row">
          <div class="reveal">
            <input type="password" id="ntfy_topic" value="__NTFY_TOPIC__" readonly>
            <button type="button" class="reveal-btn" id="reveal-ntfy" aria-label="Show or hide notification link"></button>
          </div>
          <button type="button" id="copy-ntfy">Copy link</button>
        </div>
        <div class="hint" style="margin-top:0.8rem;">Anyone who knows this link can read your notifications — don't share it.</div>
      </div>
    </fieldset>

    <fieldset>
      <legend>Automation</legend>
      <div class="toggle-row">
        <div class="toggle-text">
          <div class="tt-title">Reopen Chrome to log back in</div>
          <div class="tt-sub">When your session expires, relaunch Chrome at the login screen so you can scan the QR again.</div>
        </div>
        <div class="switch on" id="auto_refresh_chrome" role="switch" aria-checked="true" tabindex="0"></div>
      </div>
      <div class="toggle-row">
        <div class="toggle-text">
          <div class="tt-title">Open my booking on an urgent slot</div>
          <div class="tt-sub">Opens a logged-in browser at your booking's "change date" screen. You still pick the date and confirm yourself.</div>
        </div>
        <div class="switch on" id="auto_open_browser" role="switch" aria-checked="true" tabindex="0"></div>
      </div>
    </fieldset>

    <button type="submit" id="submit-btn">Save and log in</button>
  </form>

  <div id="done">
    <p>Config saved. A Chrome window should open shortly — scan the QR code in the mObywatel app to log in.</p>
    <p><a href="/">Go to dashboard</a></p>
  </div>
</div>

<script>
const CENTERS = __CENTERS_JSON__;
const CATEGORIES = __CATEGORIES_JSON__;
const EXISTING_CONFIG = __EXISTING_CONFIG_JSON__;
const KNOWN_IDS = new Set(CENTERS.map(c => c.id));
const EYE = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_OFF = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.9 4.24A9.1 9.1 0 0 1 12 4c6.5 0 10 8 10 8a18 18 0 0 1-2.16 3.19M6.6 6.6A18 18 0 0 0 2 12s3.5 7 10 7a9 9 0 0 0 5.4-1.6"/><path d="m2 2 20 20"/></svg>';
const CENTERS_BY_ID = new Map(CENTERS.map(c => [c.id, c]));
const selectedIds = new Set([
  ...(EXISTING_CONFIG ? EXISTING_CONFIG.organization_ids : []),
  ...(EXISTING_CONFIG ? EXISTING_CONFIG.watch_organization_ids : []),
].filter(id => KNOWN_IDS.has(id)));

const searchInput = document.getElementById('center-search');
const dropdown = document.getElementById('center-dropdown');
const selectedList = document.getElementById('selected-centers');
const centerCount = document.getElementById('center-count');
let currentMatches = [];
let activeIndex = -1;

function centerLabel(c) { return `${c.name} (${c.location})`; }

function renderSelected() {
  selectedList.innerHTML = '';
  if (!selectedIds.size) {
    const empty = document.createElement('div');
    empty.className = 'no-selection';
    empty.textContent = 'No centers yet — search above to add one.';
    selectedList.appendChild(empty);
    centerCount.innerHTML = '';
    return;
  }
  selectedIds.forEach(id => {
    const c = CENTERS_BY_ID.get(id);
    if (!c) return;
    const row = document.createElement('div');
    row.className = 'selected-row';

    const dot = document.createElement('span');
    dot.className = 'center-dot';
    row.appendChild(dot);

    const name = document.createElement('div');
    name.className = 'selected-name';
    const nameLine = document.createElement('div');
    nameLine.className = 'sn-name';
    nameLine.textContent = c.name;
    const locLine = document.createElement('div');
    locLine.className = 'sn-loc';
    locLine.textContent = c.location;
    name.appendChild(nameLine);
    name.appendChild(locLine);
    name.title = centerLabel(c);
    row.appendChild(name);

    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'remove-btn';
    removeBtn.title = 'Remove';
    removeBtn.textContent = '×';
    removeBtn.addEventListener('click', () => {
      selectedIds.delete(id);
      renderSelected();
    });
    row.appendChild(removeBtn);

    selectedList.appendChild(row);
  });
  const n = selectedIds.size;
  centerCount.innerHTML = `Watching <b>${n}</b> center${n === 1 ? '' : 's'} for open slots.`;
}

function closeDropdown() {
  dropdown.style.display = 'none';
  dropdown.innerHTML = '';
  currentMatches = [];
  activeIndex = -1;
}

function selectCenter(id) {
  selectedIds.add(id);
  renderSelected();
  searchInput.value = '';
  renderDropdown('');
  searchInput.focus();
}

function updateActiveItem() {
  Array.from(dropdown.children).forEach((el, i) => el.classList.toggle('active', i === activeIndex));
  if (activeIndex >= 0 && dropdown.children[activeIndex]) {
    dropdown.children[activeIndex].scrollIntoView({ block: 'nearest' });
  }
}

function renderDropdown(filter) {
  const f = filter.trim().toLowerCase();
  currentMatches = CENTERS.filter(c => !selectedIds.has(c.id) && (!f || centerLabel(c).toLowerCase().includes(f)));
  activeIndex = currentMatches.length ? 0 : -1;
  dropdown.innerHTML = '';
  if (!currentMatches.length) {
    const empty = document.createElement('div');
    empty.className = 'dropdown-empty';
    empty.textContent = f ? 'No matching centers.' : 'All centers added.';
    dropdown.appendChild(empty);
  } else {
    currentMatches.forEach((c, i) => {
      const item = document.createElement('div');
      item.className = 'dropdown-item' + (i === activeIndex ? ' active' : '');
      const nm = document.createElement('span');
      nm.textContent = c.name;
      const loc = document.createElement('span');
      loc.className = 'dd-loc';
      loc.textContent = c.location;
      item.appendChild(nm);
      item.appendChild(loc);
      item.addEventListener('mousedown', (e) => { e.preventDefault(); selectCenter(c.id); });
      dropdown.appendChild(item);
    });
  }
  dropdown.style.display = 'block';
}

searchInput.addEventListener('input', (e) => renderDropdown(e.target.value));
searchInput.addEventListener('focus', (e) => renderDropdown(e.target.value));
searchInput.addEventListener('blur', () => setTimeout(closeDropdown, 150));
searchInput.addEventListener('keydown', (e) => {
  if (!currentMatches.length) return;
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    activeIndex = (activeIndex + 1) % currentMatches.length;
    updateActiveItem();
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    activeIndex = (activeIndex - 1 + currentMatches.length) % currentMatches.length;
    updateActiveItem();
  } else if (e.key === 'Enter') {
    e.preventDefault();
    if (activeIndex >= 0) selectCenter(currentMatches[activeIndex].id);
  } else if (e.key === 'Escape') {
    closeDropdown();
  }
});

// ---- switches (generalized) ----
function setSwitch(el, on) {
  el.classList.toggle('on', on);
  el.setAttribute('aria-checked', on ? 'true' : 'false');
}
function wireSwitch(el, onChange) {
  const toggle = () => { setSwitch(el, !el.classList.contains('on')); if (onChange) onChange(); };
  el.addEventListener('click', toggle);
  el.addEventListener('keydown', (e) => { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); toggle(); } });
}
function switchOn(id) { return document.getElementById(id).classList.contains('on'); }

const phoneAlertsSwitch = document.getElementById('phone-alerts');
const ntfyField = document.getElementById('ntfy-field');
function applyNtfyDim() { ntfyField.classList.toggle('disabled', !phoneAlertsSwitch.classList.contains('on')); }
wireSwitch(phoneAlertsSwitch, applyNtfyDim);
wireSwitch(document.getElementById('auto_refresh_chrome'));
wireSwitch(document.getElementById('auto_open_browser'));

// ---- license-category dropdown (data-driven from categories.json) ----
const categorySel = document.getElementById('category');
const categoryOther = document.getElementById('category-other');
CATEGORIES.forEach((c) => {
  const o = document.createElement('option');
  o.value = String(c.id);
  o.textContent = c.label || c.code || ('Category ' + c.id);
  categorySel.appendChild(o);
});
const otherOpt = document.createElement('option');
otherOpt.value = 'other';
otherOpt.textContent = 'Other — enter number';
categorySel.appendChild(otherOpt);
if (CATEGORIES.some((c) => String(c.id) === '5')) categorySel.value = '5';
categorySel.addEventListener('change', () => {
  categoryOther.style.display = categorySel.value === 'other' ? 'block' : 'none';
});

// ---- exam-type pills ----
const examGroup = document.getElementById('exam-types');
examGroup.querySelectorAll('.pill').forEach((p) => {
  const toggle = () => p.classList.toggle('on');
  p.addEventListener('click', toggle);
  p.addEventListener('keydown', (e) => { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); toggle(); } });
});
function selectedExamTypes() {
  return Array.from(examGroup.querySelectorAll('.pill.on')).map((p) => p.dataset.val);
}

// ---- reveal-able inputs (PKK / ntfy link) ----
function wireReveal(input, btn) {
  const sync = () => { btn.innerHTML = input.type === 'password' ? EYE : EYE_OFF; };
  sync();
  btn.addEventListener('click', () => { input.type = input.type === 'password' ? 'text' : 'password'; sync(); });
  return sync;
}
const pkkInput = document.getElementById('profile_number');
const pkkSync = wireReveal(pkkInput, document.getElementById('reveal-pkk'));
const ntfyInput = document.getElementById('ntfy_topic');
wireReveal(ntfyInput, document.getElementById('reveal-ntfy'));

// ---- custom date picker ----
const dpInput = document.getElementById('current_slot_date_display');
const dpValue = document.getElementById('current_slot_date');
const calendar = document.getElementById('calendar');
const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];
const DOW = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
const todayDate = new Date(); todayDate.setHours(0, 0, 0, 0);
let calView = new Date(todayDate.getFullYear(), todayDate.getMonth(), 1);
let selectedDate = null;
function isoOf(d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
function fmtDate(d) { return d.getDate() + ' ' + MONTHS[d.getMonth()].slice(0, 3) + ' ' + d.getFullYear(); }
function sameDay(a, b) { return !!a && !!b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate(); }
function renderCalendar() {
  calendar.innerHTML = '';
  const head = document.createElement('div'); head.className = 'cal-head';
  const prev = document.createElement('button'); prev.type = 'button'; prev.className = 'cal-nav'; prev.textContent = '‹';
  const title = document.createElement('div'); title.className = 'cal-title'; title.textContent = MONTHS[calView.getMonth()] + ' ' + calView.getFullYear();
  const next = document.createElement('button'); next.type = 'button'; next.className = 'cal-nav'; next.textContent = '›';
  prev.addEventListener('click', (e) => { e.stopPropagation(); calView = new Date(calView.getFullYear(), calView.getMonth() - 1, 1); renderCalendar(); });
  next.addEventListener('click', (e) => { e.stopPropagation(); calView = new Date(calView.getFullYear(), calView.getMonth() + 1, 1); renderCalendar(); });
  head.appendChild(prev); head.appendChild(title); head.appendChild(next);
  calendar.appendChild(head);
  const grid = document.createElement('div'); grid.className = 'cal-grid';
  DOW.forEach((d) => { const c = document.createElement('div'); c.className = 'cal-dow'; c.textContent = d; grid.appendChild(c); });
  const startOffset = (new Date(calView.getFullYear(), calView.getMonth(), 1).getDay() + 6) % 7;
  const daysInMonth = new Date(calView.getFullYear(), calView.getMonth() + 1, 0).getDate();
  const prevDays = new Date(calView.getFullYear(), calView.getMonth(), 0).getDate();
  for (let i = 0; i < startOffset; i++) {
    const cell = document.createElement('div'); cell.className = 'cal-day muted disabled';
    cell.textContent = prevDays - startOffset + 1 + i; grid.appendChild(cell);
  }
  for (let d = 1; d <= daysInMonth; d++) {
    const date = new Date(calView.getFullYear(), calView.getMonth(), d);
    const cell = document.createElement('div'); cell.className = 'cal-day'; cell.textContent = d;
    if (date < todayDate) cell.classList.add('disabled');
    if (sameDay(date, todayDate)) cell.classList.add('today');
    if (sameDay(date, selectedDate)) cell.classList.add('selected');
    if (date >= todayDate) cell.addEventListener('click', (e) => {
      e.stopPropagation(); selectedDate = date; dpValue.value = isoOf(date); dpInput.value = fmtDate(date); closeCalendar();
    });
    grid.appendChild(cell);
  }
  calendar.appendChild(grid);
}
function openCalendar() { if (selectedDate) calView = new Date(selectedDate.getFullYear(), selectedDate.getMonth(), 1); renderCalendar(); calendar.classList.add('open'); }
function closeCalendar() { calendar.classList.remove('open'); }
dpInput.addEventListener('click', () => { calendar.classList.contains('open') ? closeCalendar() : openCalendar(); });
dpInput.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeCalendar(); });
document.addEventListener('click', (e) => { if (!document.getElementById('datepick').contains(e.target)) closeCalendar(); });

renderSelected();

if (EXISTING_CONFIG) {
  document.getElementById('page-title').textContent = 'info-kierowca watcher — settings';
  document.getElementById('page-lead').textContent = 'Change anything below and save — takes effect on the next check.';
  document.getElementById('submit-btn').textContent = 'Save changes';

  pkkInput.value = EXISTING_CONFIG.profile_number || '';
  if (pkkInput.value) { pkkInput.type = 'password'; pkkSync(); }

  const catStr = String(EXISTING_CONFIG.category);
  if (Array.from(categorySel.options).some((o) => o.value === catStr)) {
    categorySel.value = catStr;
  } else {
    categorySel.value = 'other';
    categoryOther.style.display = 'block';
    categoryOther.value = EXISTING_CONFIG.category;
  }

  const examTypes = EXISTING_CONFIG.exam_types || [];
  examGroup.querySelectorAll('.pill').forEach((p) => p.classList.toggle('on', examTypes.includes(p.dataset.val)));

  const manualPrefillIds = Array.from(new Set([
    ...(EXISTING_CONFIG.organization_ids || []),
    ...(EXISTING_CONFIG.watch_organization_ids || []),
  ])).filter(id => !KNOWN_IDS.has(id));
  document.getElementById('manual-ids').value = manualPrefillIds.join(', ');

  if (EXISTING_CONFIG.current_slot_date) {
    const parts = EXISTING_CONFIG.current_slot_date.split('-').map(Number);
    if (parts.length === 3 && parts.every((n) => !Number.isNaN(n))) {
      selectedDate = new Date(parts[0], parts[1] - 1, parts[2]);
      dpValue.value = EXISTING_CONFIG.current_slot_date;
      dpInput.value = fmtDate(selectedDate);
    }
  }

  setSwitch(phoneAlertsSwitch, EXISTING_CONFIG.phone_alerts !== false);
  setSwitch(document.getElementById('auto_refresh_chrome'), EXISTING_CONFIG.auto_refresh_chrome !== false);
  setSwitch(document.getElementById('auto_open_browser'), EXISTING_CONFIG.auto_open_browser !== false);
  applyNtfyDim();
}

document.getElementById('copy-ntfy').addEventListener('click', () => {
  navigator.clipboard.writeText('https://ntfy.sh/' + ntfyInput.value);
});

function parseManual(text) {
  return text.split(',').map(s => s.trim()).filter(Boolean).map(Number).filter(n => !Number.isNaN(n));
}

document.getElementById('form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById('error');
  errorEl.textContent = '';
  try {
    const examTypes = selectedExamTypes();
    if (!examTypes.length) throw new Error('Pick at least one exam type.');

    const manualIds = parseManual(document.getElementById('manual-ids').value);
    const orgIds = Array.from(new Set([...selectedIds, ...manualIds]));
    if (!orgIds.length) throw new Error('Pick or enter at least one WORD center.');
    const watchIds = orgIds;

    const profileNumber = pkkInput.value.trim();
    if (!profileNumber) throw new Error('PKK number is required.');

    const category = categorySel.value === 'other'
      ? parseInt(categoryOther.value, 10)
      : parseInt(categorySel.value, 10);
    if (!category) throw new Error('Enter a valid category number.');

    const currentSlotDate = dpValue.value;
    if (!currentSlotDate) throw new Error('Pick the date of your current booked slot.');

    const body = {
      profile_number: profileNumber,
      organization_ids: orgIds,
      watch_organization_ids: watchIds,
      category: category,
      exam_types: examTypes,
      current_slot_date: currentSlotDate,
      phone_alerts: switchOn('phone-alerts'),
      auto_refresh_chrome: switchOn('auto_refresh_chrome'),
      auto_open_browser: switchOn('auto_open_browser'),
      ntfy_topic: ntfyInput.value,
    };

    const res = await fetch('/setup', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Save failed.');

    if (EXISTING_CONFIG) {
      window.location.href = '/';
    } else {
      document.getElementById('form').style.display = 'none';
      document.getElementById('done').style.display = 'block';
    }
  } catch (err) {
    errorEl.textContent = err.message;
  }
});
</script>
</body>
</html>
"""


def render_wizard(existing_config=None):
    centers_json = json.dumps(WORD_CENTERS, ensure_ascii=False).replace("</", "<\\/")
    page = WIZARD_PAGE.replace("__CENTERS_JSON__", centers_json)
    page = page.replace("__CENTER_COUNT__", str(len(WORD_CENTERS)))
    categories_json = json.dumps(CATEGORIES, ensure_ascii=False).replace("</", "<\\/")
    page = page.replace("__CATEGORIES_JSON__", categories_json)
    ntfy_topic = existing_config["ntfy_topic"] if existing_config else "ik-" + secrets.token_urlsafe(24)
    page = page.replace("__NTFY_TOPIC__", ntfy_topic)
    existing_json = (
        json.dumps(existing_config, ensure_ascii=False).replace("</", "<\\/")
        if existing_config else "null"
    )
    page = page.replace("__EXISTING_CONFIG_JSON__", existing_json)
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
                buttons = STOP_BUTTON_HTML + SETTINGS_BUTTON_HTML
                self._send(200, dashboard_server.PAGE.replace("</body>", buttons + "</body>"))
            else:
                self._send(200, render_wizard())
        elif self.path == "/settings":
            if notifier.CONFIG_FILE.exists():
                self._send(200, render_wizard(notifier.load_json(notifier.CONFIG_FILE)))
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
