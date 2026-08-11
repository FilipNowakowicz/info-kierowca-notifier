"""Shared conservative browser-clicking primitives."""
import re

def sanitize_click_diagnostics(result):
    """Keep browser click diagnostics useful without recording personal codes."""
    if not isinstance(result, dict):
        return result
    safe = dict(result)
    matched = str(safe.get("matched_text", ""))
    if re.search(r"\b(?:pkk|pesel|otp|one[ -]?time|kod(?:\s+sms)?)\b", matched, re.I) or re.search(
        r"\b\d{6,}\b", matched
    ):
        safe["matched_text"] = "[redacted sensitive text]"
    for key in ("page_url", "href"):
        if safe.get(key):
            safe[key] = re.split(r"[?#]", str(safe[key]), maxsplit=1)[0]
    return safe

# Shared by both scripts below: find eligible elements anywhere on the page
# whose text contains one of `targets` (checked in that order) and resolve
# them to the nearest real clickable ancestor — login-page rows are often a
# plain <div> wrapping an icon + label, not a bare <button>/<a>. Matching
# descendants resolving to the same control are deduplicated; one uniquely
# exact control wins, while multiple distinct plausible controls fail safely.
# Once targets[0] (the most specific/downstream one, "Aplikacja mObywatel" — the
# tile that lands on the QR page itself) gets clicked, a sessionStorage flag
# is set so neither this function nor its callers try again: sessionStorage
# survives same-origin navigations (including the browser back button), so
# if you back out of the QR page to pick a different login method, this
# won't force you straight back to it. Origin-scoped only — it resets on a
# genuine cross-origin hop, which matches the one place that's actually
# wanted: a fresh run of this script (new profile) should auto-click again.
# The "is this thing clickable" heuristic, shared verbatim with
# booking.reschedule's own click-by-text helper. This is the most
# site-fragile code in the project — when info-kierowca.pl reshuffles its
# markup it gets edited under pressure, so it lives in exactly one place
# rather than in two copies that can silently drift apart.
CLICKABLE_HELPERS_JS = r"""
function __ikw_isVisible(el) {
  var style = window.getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
  return el.offsetWidth > 0 || el.offsetHeight > 0 || el.getClientRects().length > 0;
}
function __ikw_isClickable(el) {
  if (!el) return false;
  var style = window.getComputedStyle(el);
  return el.tagName === 'BUTTON' || el.tagName === 'A' ||
    el.getAttribute('role') === 'button' || style.cursor === 'pointer';
}
function __ikw_clickableAncestor(el) {
  var cur = el;
  for (var i = 0; i < 6 && cur; i++) {
    if (__ikw_isClickable(cur)) return cur;
    cur = cur.parentElement;
  }
  return el;
}
function __ikw_text(el) {
  // Single backslash on purpose: this whole constant is a Python raw string,
  // so `\s` reaches the browser as the whitespace class it looks like. It was
  // `\\s` here until 2026-08-11, which compiled to "a literal backslash
  // followed by s" — a sequence no page text contains, so nothing was ever
  // collapsed and __ikw_text() returned raw innerText with its line breaks
  // intact. That silently broke every *exact* match (exact=true), i.e. the
  // two highest-stakes buttons in this project: SUMMARY_BUTTON_TEXT and
  // CONFIRM_SUMMARY_TEXT, for any button whose label wraps across lines.
  // The sibling helpers below (`\b`, `\d`) were always written correctly —
  // match them, not the old form.
  return (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
}
function __ikw_pageIsKnownError() {
  var body = __ikw_text(document.body).toLowerCase();
  return !!document.querySelector('.alertPage, [class*="alertPage"], .wkProcessUsed') ||
    body.indexOf('wkprocessused') !== -1 ||
    body.indexOf('został już użyty') !== -1 || body.indexOf('zostal juz uzyty') !== -1 ||
    body.indexOf('strona błędu') !== -1 || body.indexOf('strona bledu') !== -1;
}
function __ikw_hasExcludedStructure(el) {
  var cur = el;
  for (var i = 0; i < 8 && cur; i++, cur = cur.parentElement) {
    var tag = (cur.tagName || '').toLowerCase();
    var identity = ((cur.id || '') + ' ' + (cur.className || '') + ' ' +
      (cur.getAttribute('role') || '') + ' ' + (cur.getAttribute('aria-label') || '')).toLowerCase();
    if (tag === 'header' || tag === 'footer' || tag === 'nav' || tag === 'app-logo' ||
        tag === 'app-wk-footer' || tag === 'app-wk-language-switcher' ||
        /(^|[ _-])(logo|footer|header|navigation|nav|language|lang|go-back|back|help|policy|terms)([ _-]|$)/.test(identity)) {
      return true;
    }
  }
  return false;
}
function __ikw_hasExcludedText(el) {
  var text = __ikw_text(el).toLowerCase();
  return /\b(wstecz|powrót|powrot|back|cofnij|język|jezyk|language)\b/.test(text) ||
    /\b(załóż konto|zaloz konto|utwórz konto|utworz konto|create account|przypomnij hasło|przypomnij haslo|password reminder|forgot password|nie pamiętam hasła|nie pamietam hasla|polityka|privacy|pomoc|help|regulamin|terms)\b/.test(text);
}
function __ikw_hasUnrelatedGovHref(el) {
  var href = (el && (el.href || el.getAttribute('href')) || '').toLowerCase();
  if (!href) return false;
  try {
    var host = new URL(href, location.href).hostname.toLowerCase();
    return (host === 'gov.pl' || host.endsWith('.gov.pl')) &&
      host !== 'login.gov.pl' && !host.endsWith('.login.gov.pl');
  } catch (e) { return false; }
}
function __ikw_isExcludedControl(candidate, clickable) {
  clickable = clickable || __ikw_clickableAncestor(candidate);
  return __ikw_hasExcludedStructure(candidate) ||
    (clickable !== candidate && __ikw_hasExcludedStructure(clickable)) ||
    __ikw_hasExcludedText(candidate) ||
    (clickable !== candidate && __ikw_hasExcludedText(clickable)) ||
    __ikw_hasUnrelatedGovHref(clickable);
}
function __ikw_diagnostics(label, matched, el, reason) {
  var href = el ? (el.href || el.getAttribute('href') || '') : '';
  var safeHref = href;
  try { safeHref = new URL(href, location.href).origin + new URL(href, location.href).pathname; } catch (e) {}
  return {
    clicked: false, reason: reason || 'not_found', requested_label: label || '',
    matched_text: __ikw_safeMatchedText(matched || ''), page_url: String(location.origin || '') + String(location.pathname || ''),
    page_host: String(location.hostname || ''), tag: el ? el.tagName : '',
    element_id: el ? (el.id || '') : '', element_class: el ? String(el.className || '') : '',
    href: safeHref
  };
}
function __ikw_safeMatchedText(text) {
  if (/\b(pkk|pesel|otp|one[ -]?time|kod\s+sms)\b/i.test(text) || /\b\d{6,}\b/.test(text)) {
    return '[redacted sensitive text]';
  }
  return text;
}
function __ikw_clickByText(label, selector, exact, requireEnabled) {
  if (__ikw_pageIsKnownError()) return __ikw_diagnostics(label, '', null, 'known_error_page');
  var all = document.querySelectorAll(selector), candidates = [];
  var wanted = label.toLowerCase();
  for (var i = 0; i < all.length; i++) {
    var el = all[i], matched = __ikw_text(el);
    if (!__ikw_isVisible(el) || !matched) continue;
    if ((exact && matched !== label) || (!exact && matched.toLowerCase().indexOf(wanted) === -1)) continue;
    if (requireEnabled && (el.disabled || el.getAttribute('aria-disabled') === 'true')) continue;
    var clickable = __ikw_clickableAncestor(el);
    if (!clickable || __ikw_isExcludedControl(el, clickable)) continue;
    var duplicate = false;
    for (var c = 0; c < candidates.length; c++) {
      if (candidates[c][0] === clickable) {
        if (matched.toLowerCase() === wanted || matched.length < candidates[c][1].length) {
          candidates[c] = [clickable, matched, matched.toLowerCase() === wanted];
        }
        duplicate = true; break;
      }
    }
    if (!duplicate) candidates.push([clickable, matched, matched.toLowerCase() === wanted]);
  }
  if (!candidates.length) return __ikw_diagnostics(label, '', null, 'not_found');
  var exactCandidates = candidates.filter(function(candidate) { return candidate[2]; });
  if (exactCandidates.length === 1) {
    candidates = exactCandidates;
  } else if (candidates.length > 1) {
    return __ikw_diagnostics(label, '', null, 'ambiguous_match');
  }
  var best = candidates[0], result = __ikw_diagnostics(label, best[1], best[0], 'clicked');
  best[0].click(); result.clicked = true; return result;
}
"""

