"""Fail-closed reschedule verification and privacy-safe diagnostics."""
import json
import os
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from info_kierowca_notifier.browser import cdp as cdp_client
from info_kierowca_notifier.paths import RESCHEDULE_DIAGNOSTICS_DIR

VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
VERIFIED_UNCHANGED = "VERIFIED_UNCHANGED"
NEEDS_FURTHER_CONFIRMATION = "NEEDS_FURTHER_CONFIRMATION"
SLOT_LOST = "SLOT_LOST"
ERROR = "ERROR"
UNKNOWN = "UNKNOWN"

ACTIVE_STATUSES = frozenset(("potwierdzona", "aktywna", "confirmed", "active"))
INACTIVE_STATUSES = frozenset(("anulowana", "nieaktywna", "cancelled", "canceled", "inactive"))
UNCHANGED_MIN_ELAPSED_SECONDS = 4
UNCHANGED_REQUIRED_OBSERVATIONS = 3
SECRET_KEY = re.compile(r"cookie|authorization|token|secret|password|pesel|pkk|otp|value", re.I)
LONG_NUMBER = re.compile(r"(?<!\d)\d{8,}(?!\d)")
TOKENISH = re.compile(r"(?i)\b(?:bearer\s+)?[a-z0-9_-]{24,}\b")


def sanitize_url(url):
    try:
        p = urlsplit(str(url))
        if p.scheme not in ("http", "https"):
            return ""
        return urlunsplit((p.scheme, p.netloc, p.path or "/", "", ""))
    except Exception:
        return ""


def sanitize_text(value, limit=160):
    text = " ".join(str(value or "").split())[:limit]
    text = LONG_NUMBER.sub("[REDACTED_NUMBER]", text)
    return TOKENISH.sub("[REDACTED_TOKEN]", text)


def sanitize(value, key=""):
    if SECRET_KEY.search(str(key)):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): sanitize(v, k) for k, v in value.items() if not SECRET_KEY.search(str(k))}
    if isinstance(value, list):
        return [sanitize(v) for v in value[:100]]
    if isinstance(value, str):
        return sanitize_text(value, 500)
    return value if value is None or isinstance(value, (bool, int, float)) else sanitize_text(value)


# --- exam-centre ("word") identity -------------------------------------------------
# notifier.py's hit_dicts carry the centre a slot belongs to as `word` (the
# API's wordName, e.g. "WORD Warszawa M/E Odolany"), and that value was being
# copied into the transaction target and then never compared against anything:
# select_slot_js() matched on exam label + time alone, wait_and_verify_summary()
# on date/time/label alone, and matches_target() on date/time/type/status alone.
# A slot at the *wrong* centre (in practice: the existing booking's own centre,
# which is what the reschedule picker shows) with the same date, time and exam
# type therefore matched every check on the way to the irreversible confirm
# click. These helpers are the shared vocabulary for comparing a centre name
# scraped out of the DOM against the one the target slot claims.
#
# Two directions, deliberately different:
#   * conflict  — a centre was found and it is NOT the target's. Always fatal,
#     everywhere (selection, summary verification, post-booking cards).
#   * unknown   — no centre string was found at all. Tolerated while *selecting*
#     (nothing irreversible has happened yet, and the row markup may simply not
#     repeat the centre name), but NOT before the confirm click — see
#     reschedule.wait_and_verify_summary(), which requires a positive match.
# All of this is derived from screenshots and the API's own naming, never from
# a live DOM: if a real run reports center_unverified, the page's actual centre
# markup is what needs looking at, not this rule.
CENTER_PREFIXES = ("WORD", "MORD", "PORD", "ZORD", "DORD")
CENTER_LABEL_RE = re.compile(
    r"\b(?:%s)\b[^,;|\n]{0,48}" % "|".join(CENTER_PREFIXES), re.I
)

CENTER_HELPERS_JS = r"""
function __ikw_centerLabels(text) {
  return String(text || '').match(/\b(?:WORD|MORD|PORD|ZORD|DORD)\b[^,;|\n]{0,48}/gi) || [];
}
function __ikw_normCenter(value) {
  return String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/[\/.,\-]/g, ' ').toLowerCase().replace(/\s+/g, ' ').trim();
}
function __ikw_centerCompatible(found, wanted) {
  var a = __ikw_normCenter(found), b = __ikw_normCenter(wanted);
  if (!a || !b) return true;
  return a === b || a.indexOf(b + ' ') === 0 || b.indexOf(a + ' ') === 0;
}
function __ikw_centerVerdict(text, wanted) {
  // 'unverifiable' (the target names no centre) is deliberately distinct from
  // 'unknown' (the page names none): both refuse before the confirm click, but
  // only one of them means the DOM needs looking at.
  if (!__ikw_normCenter(wanted)) return 'unverifiable';
  var labels = __ikw_centerLabels(text), verdict = 'unknown';
  for (var i = 0; i < labels.length; i++) {
    if (__ikw_centerCompatible(labels[i], wanted)) verdict = 'match';
    else return 'conflict';
  }
  return verdict;
}
"""


def normalize_center(value):
    """Casefolded, de-accented, punctuation-flattened centre name.

    Mirrors __ikw_normCenter() in CENTER_HELPERS_JS above — keep the two in
    step, since one side reads the DOM and the other reads config/hit dicts.
    """
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    for char in "/.,-":
        text = text.replace(char, " ")
    return " ".join(text.casefold().split())


def centers_compatible(found, wanted):
    """True unless both sides name a centre and they name different ones.

    Prefix-tolerant in both directions so "WORD Warszawa" (a page header) and
    "WORD Warszawa M/E Odolany" (the API's full name for one of its sites)
    aren't treated as a conflict, while "WORD Warszawa M/E Bemowo" is.
    """
    left, right = normalize_center(found), normalize_center(wanted)
    if not left or not right:
        return True
    return (left == right or left.startswith(right + " ") or
            right.startswith(left + " "))


def center_conflict(found, wanted):
    """Both sides name a centre, and they disagree."""
    return bool(normalize_center(found) and normalize_center(wanted) and
                not centers_compatible(found, wanted))


def target_center(target):
    """The centre a transaction target claims, under any of its spellings."""
    for key in ("center", "word_id", "word"):
        value = (target or {}).get(key)
        if value:
            return value
    return ""


def extract_center_labels(text):
    return [match.strip() for match in CENTER_LABEL_RE.findall(str(text or ""))]


BOOKING_CARDS_JS = CENTER_HELPERS_JS + r"""
(function() {
  function visible(e) { var r=e.getBoundingClientRect(), s=getComputedStyle(e); return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'; }
  var status=/\b(?:Potwierdzona|Anulowana|Nieaktywna|Aktywna|Confirmed|Cancelled|Canceled|Inactive|Active)\b/i;
  var nodes=Array.from(document.querySelectorAll('article,[role="article"],[data-testid*="reservation"],[data-testid*="booking"],.card,[class*="card"],[class*="reservation"],[class*="booking"]'));
  var out=[], seen=new Set();
  nodes.forEach(function(el) {
    if (!visible(el)) return;
    var text=(el.innerText||'').replace(/\s+/g,' ').trim();
    if (!status.test(text) || text.length>1200 || text.length<8) return;
    var parent=el.parentElement;
    if (parent) { var pt=(parent.innerText||'').replace(/\s+/g,' ').trim(); if (status.test(pt)&&pt.length<600&&pt.length>text.length) return; }
    var dates=text.match(/\b\d{2}[/.\-]\d{2}[/.\-]\d{4}\b/g)||[];
    var times=text.match(/\b(?:[01]\d|2[0-3]):[0-5]\d\b/g)||[];
    var st=(text.match(/\b(?:Potwierdzona|Anulowana|Nieaktywna|Aktywna|Confirmed|Cancelled|Canceled|Inactive|Active)\b/i)||[])[0]||'';
    var exam=(text.match(/Egzamin (?:teoretyczny|praktyczny)|Theoretical|Practice/i)||[])[0]||'';
    var center=(__ikw_centerLabels(text)[0]||'').trim();
    var attrs={}; ['data-testid','data-status','role'].forEach(function(n){var v=el.getAttribute(n);if(v&&v.length<80)attrs[n]=v;});
    var key=[st,exam,dates[0]||'',times[0]||'',center,JSON.stringify(attrs)].join('|');
    if (!seen.has(key)) { seen.add(key); out.push({status:st,exam_type:exam,date:dates[0]||'',time:times[0]||'',center:center,attributes:attrs}); }
  });
  return out.slice(0,20);
})()
"""

BOOKING_DIAGNOSTIC_CANDIDATES_JS = r"""
(function() {
  function visible(e) { var r=e.getBoundingClientRect(),s=getComputedStyle(e); return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'; }
  function meta(e) { return e ? {tag:e.tagName.toLowerCase(),role:(e.getAttribute('role')||'').slice(0,60),class_name:String(e.className||'').slice(0,120),test_id:(e.getAttribute('data-testid')||'').slice(0,80)} : null; }
  var status=/\b(?:Potwierdzona|Anulowana|Nieaktywna|Aktywna|Confirmed|Cancelled|Canceled|Inactive|Active)\b/gi;
  var date=/\b\d{2}[/.\-]\d{2}[/.\-]\d{4}\b/g;
  var time=/\b(?:[01]\d|2[0-3]):[0-5]\d\b/g;
  var exam=/\b(?:Egzamin (?:teoretyczny|praktyczny)|Theoretical|Practice)\b/gi;
  var nodes=Array.from(document.querySelectorAll('article,section,li,div,[role="article"],[role="group"],[role="region"]'));
  var out=[],seen=new Set();
  nodes.forEach(function(el) {
    if (out.length>=25 || !visible(el)) return;
    var text=(el.innerText||'').replace(/\s+/g,' ').trim();
    if (text.length<8 || text.length>1600) return;
    var statuses=text.match(status)||[],dates=text.match(date)||[],times=text.match(time)||[],exams=text.match(exam)||[];
    if (!(statuses.length || exams.length || (dates.length && times.length))) return;
    var item={tag:el.tagName.toLowerCase(),role:(el.getAttribute('role')||'').slice(0,60),class_name:String(el.className||'').slice(0,120),test_id:(el.getAttribute('data-testid')||'').slice(0,80),statuses:Array.from(new Set(statuses)).slice(0,6),dates:Array.from(new Set(dates)).slice(0,6),times:Array.from(new Set(times)).slice(0,6),exam_types:Array.from(new Set(exams)).slice(0,6),text_length:text.length,parent:meta(el.parentElement),grandparent:meta(el.parentElement&&el.parentElement.parentElement)};
    var key=JSON.stringify(item); if (!seen.has(key)) { seen.add(key); out.push(item); }
  });
  return out;
})()
"""

PAGE_SNAPSHOT_JS = r"""
(function() {
 function vis(e){var r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';}
 function txt(e){return (e.innerText||e.textContent||e.getAttribute('aria-label')||'').replace(/\s+/g,' ').trim().slice(0,160);}
 function items(sel,n){return Array.from(document.querySelectorAll(sel)).filter(vis).slice(0,n).map(function(e){return {text:txt(e),disabled:!!e.disabled||e.getAttribute('aria-disabled')==='true',role:e.getAttribute('role')||'',test_id:(e.getAttribute('data-testid')||'').slice(0,80),class_name:String(e.className||'').slice(0,100)};});}
 return {url:location.href,title:document.title,headings:items('h1,h2,h3,[role="heading"]',12),buttons:items('button,[role="button"]',20),links:items('a[href]',12),dialogs:items('[role="dialog"],dialog,[aria-modal="true"]',8),forms:Array.from(document.forms).slice(0,8).map(function(f){return {fields:Array.from(f.querySelectorAll('input,select,textarea')).slice(0,20).map(function(e){var id=e.id||'';var l=id?document.querySelector('label[for="'+CSS.escape(id)+'"]'):null;return {type:e.type||e.tagName.toLowerCase(),name:(e.name||'').slice(0,80),label:txt(l||e.closest('label')||{textContent:''})};})};})};
})()
"""


def normalize_card(card):
    return {
        "status": sanitize_text(card.get("status"), 60),
        "exam_type": sanitize_text(card.get("exam_type"), 100),
        "date": sanitize_text(card.get("date"), 20),
        "time": sanitize_text(card.get("time"), 10),
        "center": sanitize_text(card.get("center"), 80),
        "attributes": sanitize(card.get("attributes", {})),
    }


def active(card):
    status = " ".join(str(card.get("status", "")).casefold().split())
    if status in INACTIVE_STATUSES:
        return False
    return status in ACTIVE_STATUSES


def normalize_exam_type(value):
    return " ".join(str(value or "").casefold().split())


def matches_target(card, target):
    """This exact card *is* the slot we tried to book: active, same date, same
    time, same exam type, and — new as of 2026-08-11 — not visibly at a
    different exam centre than the target's (see the centre helpers above; a
    card with no readable centre is tolerated rather than treated as a
    mismatch, because BOOKING_CARDS_JS scrapes the centre out of free text and
    a card that simply doesn't repeat it must not silently become unverifiable
    forever)."""
    return (active(card) and card.get("date") == target["date"] and
            card.get("time") == target["time"] and
            normalize_exam_type(card.get("exam_type")) ==
            normalize_exam_type(target.get("exam_type")) and
            not center_conflict(card.get("center"), target_center(target)))


def same_booking(left, right):
    return (left.get("date") == right.get("date") and
            left.get("time") == right.get("time") and
            normalize_exam_type(left.get("exam_type")) ==
            normalize_exam_type(right.get("exam_type")) and
            not center_conflict(left.get("center"), right.get("center")))


def _same_active_set(current_active, old_active):
    """Every active booking still pairs with a baseline one, and vice versa."""
    return (len(current_active) == len(old_active) and
            all(any(same_booking(now, old) for old in old_active) for now in current_active) and
            all(any(same_booking(now, old) for now in current_active) for old in old_active))


def classify_cards(cards, target, baseline):
    """Fail-closed verdict on what /cases shows after the confirm click.

    Keyed on the *target card's own identity*, not on the account having
    exactly one booking. Until 2026-08-11 both branches required
    ``len(old_active) == 1 and len(current_active) == 1``, so anyone holding
    two active bookings at once (theory + practical is the ordinary case) could
    never reach VERIFIED_SUCCESS even on a perfect match — which left
    current_slot_date permanently stale, and a stale current_slot_date re-arms
    notifier.is_urgent() for slots no better than the one just booked, i.e. the
    900s cooldown expiring could hand another *real* confirm click to a worse
    slot. The replacement evidence is:

      * exactly one visible card matches the target (zero is not success, two
        is ambiguous),
      * that card was not already active in the baseline (otherwise the
        booking we "verified" is one that predates our click), and
      * the number of active bookings did not grow — a reschedule replaces a
        booking, so a *new* active card appearing alongside every old one
        looks like a double booking and stays UNKNOWN for a human to read.

    VERIFIED_UNCHANGED likewise generalises: every previously-active booking is
    still there, unchanged, and nothing was added.
    """
    cards = [normalize_card(c) for c in cards]
    baseline = [normalize_card(c) for c in (baseline or [])]
    matches = [c for c in cards if matches_target(c, target)]
    if len(matches) > 1:
        return UNKNOWN
    old_active = [c for c in baseline if active(c)]
    current_active = [c for c in cards if active(c)]
    if not old_active:
        # No usable baseline: we cannot tell a booking we made from one that
        # was always there. capture_stable_booking_baseline() failing is
        # exactly this case, and it must not verify anything.
        return UNKNOWN
    if len(matches) == 1:
        if any(matches_target(old, target) for old in old_active):
            return UNKNOWN
        if len(current_active) > len(old_active):
            return UNKNOWN
        return VERIFIED_SUCCESS
    if _same_active_set(current_active, old_active):
        return VERIFIED_UNCHANGED
    return UNKNOWN


def capture_booking_cards(host, port, target):
    return [normalize_card(c) for c in (cdp_client.evaluate_in_page(host, port, BOOKING_CARDS_JS, target=target) or [])]


def capture_booking_diagnostic_candidates(host, port, target):
    raw = cdp_client.evaluate_in_page(
        host, port, BOOKING_DIAGNOSTIC_CANDIDATES_JS, target=target
    ) or []
    return sanitize(raw[:25])


def _card_structure(cards):
    return json.dumps(sanitize(cards), sort_keys=True, ensure_ascii=False)


def capture_stable_booking_baseline(host, port, target, timeout=12, interval=0.5,
                                    monotonic=time.monotonic, sleep=time.sleep):
    """Prefer two identical non-empty card snapshots; retain bounded diagnostics."""
    deadline = monotonic() + timeout
    last_cards, last_key, stable_count, candidates = [], None, 0, []
    while monotonic() < deadline:
        try:
            cards = capture_booking_cards(host, port, target)
        except Exception:
            cards = []
        try:
            candidates = capture_booking_diagnostic_candidates(host, port, target)
        except Exception:
            candidates = candidates or []
        if cards:
            key = _card_structure(cards)
            stable_count = stable_count + 1 if key == last_key else 1
            last_cards, last_key = cards, key
            if stable_count >= 2:
                return last_cards, candidates
        else:
            stable_count, last_key = 0, None
        sleep(interval)
    return last_cards, candidates


def capture_page(host, port, target, elapsed):
    raw = cdp_client.evaluate_in_page(host, port, PAGE_SNAPSHOT_JS, target=target) or {}
    raw["url"] = sanitize_url(raw.get("url"))
    raw["target_id"] = target.id
    raw["elapsed_seconds"] = round(elapsed, 3)
    return sanitize(raw)


def looks_like_further_confirmation(snapshot, initial):
    if not snapshot.get("url", "").startswith("https://info-kierowca.pl/"):
        return False
    current = {(b.get("text"), b.get("disabled")) for b in snapshot.get("buttons", [])}
    before = {(b.get("text"), b.get("disabled")) for b in initial.get("buttons", [])}
    new_buttons = current - before
    confirmation_words = ("potwier", "confirm", "przejdź", "continue", "zapisz", "submit")
    explicit_control = any(any(word in (text or "").casefold() for word in confirmation_words)
                           for text, _disabled in new_buttons)
    return bool(new_buttons and (snapshot.get("forms") or snapshot.get("dialogs") or explicit_control))


def page_fingerprint(snapshot):
    return {key: value for key, value in snapshot.items() if key != "elapsed_seconds"}


def update_unchanged_evidence(result, elapsed, consecutive):
    """Return the current consecutive unchanged count and whether it is stable."""
    if result != VERIFIED_UNCHANGED:
        return 0, False
    consecutive += 1
    ready = (elapsed >= UNCHANGED_MIN_ELAPSED_SECONDS and
             consecutive >= UNCHANGED_REQUIRED_OBSERVATIONS)
    return consecutive, ready


@dataclass
class DiagnosticRecorder:
    target_slot: dict
    baseline_booking: list
    transaction_id: str = ""
    baseline_booking_candidates: list = None

    def __post_init__(self):
        self.transaction_id = self.transaction_id or uuid.uuid4().hex[:12]
        self.data = {"schema_version": 2, "transaction_id": self.transaction_id,
                     "target_slot": sanitize(self.target_slot), "baseline_booking": sanitize(self.baseline_booking),
                     "baseline_booking_candidates": sanitize(self.baseline_booking_candidates or []),
                     "states": [], "post_submit_pages": [], "network_events": [],
                     "verification_attempts": [], "final_outcome": UNKNOWN}

    def state(self, name, **details):
        self.data["states"].append({"state": name, "at": datetime.now().astimezone().isoformat(timespec="seconds"), **sanitize(details)})

    def save(self):
        RESCHEDULE_DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix": RESCHEDULE_DIAGNOSTICS_DIR.chmod(0o700)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        path = RESCHEDULE_DIAGNOSTICS_DIR / f"{stamp}-{self.transaction_id}.json"
        payload = json.dumps(sanitize(self.data), indent=2, ensure_ascii=False)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
        return path


def run_post_submit(host, port, transaction_target, recorder, *, timeout=24, interval=2,
                    monotonic=time.monotonic, sleep=time.sleep):
    """Observe transaction target and verify through a newly-created explicit target."""
    started = monotonic()
    initial = recorder.data["post_submit_pages"][-1] if recorder.data["post_submit_pages"] else capture_page(host, port, transaction_target, 0)
    last_page = capture_page(host, port, transaction_target, 0)
    if page_fingerprint(last_page) != page_fingerprint(initial):
        recorder.data["post_submit_pages"].append(last_page)
    verification_target = None
    outcome = UNKNOWN
    consecutive_unchanged = 0
    unchanged_ready = False
    transaction_target_lost = False
    recorder.state("OBSERVE_POST_SUBMIT", target_id=transaction_target.id)
    try:
        verification_target = cdp_client.create_page_target(host, port)
        recorder.state("verification_target_created", target_id=verification_target.id)
        cdp_client.navigate_target(host, port, verification_target, "https://info-kierowca.pl/cases")
        recorder.state("VERIFY_BOOKING_SEPARATELY", target_id=verification_target.id)
    except Exception as exc:
        recorder.state("verification_unavailable", error=type(exc).__name__)
    while monotonic() - started <= timeout:
        elapsed = monotonic() - started
        try:
            page = capture_page(host, port, transaction_target, elapsed)
            if page_fingerprint(page) != page_fingerprint(last_page):
                recorder.data["post_submit_pages"].append(page); last_page = page
                print(f"transaction={recorder.transaction_id} post_url={urlsplit(page.get('url','')).path or '/'}")
        except Exception as exc:
            recorder.state("transaction_target_disappeared", error=type(exc).__name__)
            outcome = UNKNOWN
            consecutive_unchanged = 0
            unchanged_ready = False
            transaction_target_lost = True
            break
        if verification_target:
            candidates = []
            try:
                try:
                    candidates = capture_booking_diagnostic_candidates(
                        host, port, verification_target
                    )
                except Exception as exc:
                    candidates = []
                    recorder.state("diagnostic_candidates_unavailable", error=type(exc).__name__)
                cards = capture_booking_cards(host, port, verification_target)
                current = classify_cards(cards, recorder.target_slot, recorder.baseline_booking)
                consecutive_unchanged, unchanged_ready = update_unchanged_evidence(
                    current, elapsed, consecutive_unchanged
                )
                recorder.data["verification_attempts"].append({
                    "elapsed_seconds": round(elapsed, 3), "cards": cards,
                    "diagnostic_candidates": candidates, "result": current,
                    "consecutive_unchanged": consecutive_unchanged,
                })
                print(f"transaction={recorder.transaction_id} verification={current.lower()}")
                if current == VERIFIED_SUCCESS:
                    outcome = current; break
            except Exception as exc:
                recorder.data["verification_attempts"].append({
                    "elapsed_seconds": round(elapsed, 3), "cards": [],
                    "diagnostic_candidates": candidates, "error": type(exc).__name__,
                    "result": UNKNOWN,
                })
                # Only a target that is genuinely gone ends verification. Until
                # 2026-08-11 *any* exception dropped verification_target, and
                # the likeliest moment for one is the very first attempt —
                # right after create_page_target()/navigate_target(), while
                # /cases is still loading (destroyed execution context, socket
                # timeout). One such blip permanently disabled verification for
                # the rest of the budget, so a reschedule that really did
                # succeed reported UNKNOWN and left current_slot_date stale.
                # Transient errors now just cost this one attempt.
                if isinstance(exc, cdp_client.TargetNotFoundError):
                    verification_target = None
                    recorder.state("verification_target_lost", error=type(exc).__name__)
                else:
                    recorder.state("verification_attempt_failed", error=type(exc).__name__)
                consecutive_unchanged = 0
                unchanged_ready = False
        sleep(interval)
    if outcome != VERIFIED_SUCCESS and not transaction_target_lost:
        outcome = VERIFIED_UNCHANGED if unchanged_ready else UNKNOWN
    if (outcome != VERIFIED_SUCCESS and not transaction_target_lost and
            looks_like_further_confirmation(last_page, initial)):
        outcome = NEEDS_FURTHER_CONFIRMATION
    recorder.data["final_outcome"] = outcome
    recorder.state("completed", outcome=outcome)
    return outcome
