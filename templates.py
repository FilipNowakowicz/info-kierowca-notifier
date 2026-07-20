"""HTML/JS template strings for app.py's HTTP handlers.

Pulled out of app.py verbatim (no behavior change) since they made up the
bulk of that file's line count. app.py still owns all rendering logic
(WIZARD_PAGE's __CENTERS_JSON__ substitution, TOOLBAR_HTML splicing into
dashboard_server.PAGE, etc.) — this module only holds the literal strings.
"""

TOOLBAR_HTML = """
<style>
  /* Mouse proximity to the top edge (or focus landing inside the
     toolbar) reveals it; it hides again after a short idle. Keeps the
     resting view down to just the background color and headline. */
  #ikw-toolbar-zone { position: fixed; top: 0; left: 0; right: 0; height: 88px; z-index: 10; }
  .ikw-toolbar { position: fixed; top: 1rem; right: 1rem; display: flex; gap: 0.4rem; z-index: 11;
    opacity: 0; transform: translateY(-4px); pointer-events: none;
    transition: opacity 0.25s ease, transform 0.25s ease; }
  .ikw-toolbar.show { opacity: 1; transform: translateY(0); pointer-events: auto; }
  .ikw-icon-btn { width: 2.25rem; height: 2.25rem; display: flex; align-items: center; justify-content: center;
    border-radius: 999px; cursor: pointer;
    background: rgba(255,255,255,0.07); color: #eee; border: 1px solid rgba(255,255,255,0.18);
    backdrop-filter: blur(6px); transition: background 0.12s, border-color 0.12s, color 0.12s; }
  .ikw-icon-btn:hover { background: rgba(255,255,255,0.16); border-color: rgba(255,255,255,0.32); }
  .ikw-icon-btn:disabled { opacity: 0.5; cursor: default; }
  .ikw-icon-btn svg { width: 1.05rem; height: 1.05rem; }
  #ikw-quit-btn:hover { border-color: rgba(224,104,95,0.7); color: #ffb3ad; }
  /* Small inline icon button next to dashboard_server.py's #session-expiry
     text - deliberately lighter-weight than .ikw-icon-btn (the top toolbar's
     larger circular badges), since this one sits inline with 0.85rem text
     rather than floating over the page. */
  #session-refresh-btn { width: 1.3rem; height: 1.3rem; display: flex; align-items: center; justify-content: center;
    border-radius: 999px; cursor: pointer; background: rgba(255,255,255,0.07); color: #eee; opacity: 0.55;
    border: 1px solid rgba(255,255,255,0.18); transition: opacity 0.12s, background 0.12s; }
  #session-refresh-btn:hover { opacity: 0.9; background: rgba(255,255,255,0.16); }
  #session-refresh-btn:disabled { opacity: 0.3; cursor: default; }
  #session-refresh-btn svg { width: 0.8rem; height: 0.8rem; }
  /* Faint permanent dot so the toolbar is discoverable even before its
     hover/focus reveal has ever fired. */
  #ikw-toolbar-hint { position: fixed; top: 1.1rem; right: 1.25rem; width: 0.35rem; height: 0.35rem;
    border-radius: 999px; background: rgba(255,255,255,0.3); transition: opacity 0.2s ease; z-index: 9; }
  .ikw-toolbar.show ~ #ikw-toolbar-hint { opacity: 0; }

  /* Makes dashboard_server.py's headline markup clickable: dims the
     headline text and overlays one large centered pause/play icon on
     hover/focus, like a video player's hover control, rather than a
     small icon living beside the text. */
  #headline-wrap.ikw-pausable { cursor: pointer; }
  #headline-wrap.ikw-pausable:hover,
  #headline-wrap.ikw-pausable:focus-visible { background: rgba(255,255,255,0.06); outline: none; }
  #headline-wrap.ikw-pausable:active { background: rgba(255,255,255,0.1); }
  #headline-wrap.ikw-pausable:hover #headline,
  #headline-wrap.ikw-pausable:focus-visible #headline { opacity: 0.25; }
  #headline-wrap.ikw-pausable:hover #headline-icon,
  #headline-wrap.ikw-pausable:focus-visible #headline-icon { opacity: 0.95; transform: translate(-50%, -50%) scale(1); }
  #headline-wrap.ikw-pausable:hover ~ #headline-hint,
  #headline-wrap.ikw-pausable:focus-visible ~ #headline-hint { opacity: 0.45; }

  .ikw-toast { position: fixed; bottom: 1.2rem; left: 50%; transform: translateX(-50%) translateY(0.4rem);
    max-width: 90vw; background: rgba(20,20,20,0.92); color: #eee; padding: 0.6rem 1rem; border-radius: 8px;
    font-family: -apple-system, 'Segoe UI', system-ui, sans-serif; font-size: 0.85rem; text-align: center;
    border: 1px solid rgba(255,255,255,0.15); opacity: 0; pointer-events: none; z-index: 20;
    transition: opacity 0.2s, transform 0.2s; }
  .ikw-toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }

  /* Settings modal: the dashboard stays visible (dimmed) behind a
     translucent, blurred backdrop, with /settings loaded into an iframe
     panel on top — rather than the old full-page navigation to /settings.
     An iframe (not a merged template) keeps app.py's two big templates
     independent; see WIZARD_PAGE's IKW_EMBEDDED for the other half of
     this, which lets the same settings page run standalone (first-run
     /setup, a direct /settings visit) or embedded here. */
  #ikw-settings-overlay { position: fixed; inset: 0; z-index: 50; display: none; align-items: center;
    justify-content: center; background: rgba(0,0,0,0.45); backdrop-filter: blur(5px); -webkit-backdrop-filter: blur(5px);
    opacity: 0; transition: opacity 0.18s ease; }
  #ikw-settings-overlay.show { display: flex; opacity: 1; }
  /* Frosted glass, not a second opaque page: the panel's own background is
     translucent (+ blurred, where the browser composites it) so the dimmed
     dashboard behind shows through as a soft ambient glow instead of either
     vanishing entirely (an earlier, much lower opacity read as "just a
     transparent page") or staying sharp enough to read as legible ghosted-
     over text (an earlier, blur-reliant pass, before accounting for
     backdrop-filter needing real compositing some environments don't do -
     this opacity alone, without any blur at all, is what keeps the panel
     readable and calm either way). WIZARD_PAGE's body goes fully transparent
     when embedded (see its html.ikw-embedded rule) so this is the only
     surface painting anything behind the form content. */
  #ikw-settings-panel { width: min(600px, 92vw); height: min(85vh, 760px); border-radius: 14px;
    overflow: hidden; background: rgba(24,24,24,0.93); backdrop-filter: blur(48px) saturate(140%);
    -webkit-backdrop-filter: blur(48px) saturate(140%); box-shadow: 0 24px 70px rgba(0,0,0,0.55);
    border: 1px solid rgba(255,255,255,0.12); transform: scale(0.97) translateY(6px); transition: transform 0.18s ease; }
  #ikw-settings-overlay.show #ikw-settings-panel { transform: scale(1) translateY(0); }
  #ikw-settings-frame { width: 100%; height: 100%; border: 0; display: block; background: transparent; }
</style>
<div id="ikw-toolbar-zone"></div>
<div id="ikw-settings-overlay">
  <div id="ikw-settings-panel" role="dialog" aria-modal="true" aria-label="Settings">
    <iframe id="ikw-settings-frame" title="Settings" src="about:blank" allowtransparency="true"></iframe>
  </div>
</div>
<div class="ikw-toolbar" id="ikw-toolbar">
  <button id="ikw-browser-btn" class="ikw-icon-btn" title="Open browser" aria-label="Open browser">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
      <path d="M14 4h6v6"/><path d="M20 4 10.5 13.5"/><path d="M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5"/>
    </svg>
  </button>
  <button id="ikw-settings-btn" class="ikw-icon-btn" title="Settings" aria-label="Settings">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="12" r="3"/>
      <path d="M19.4 13a7.97 7.97 0 0 0 0-2l2.1-1.6-2-3.5-2.5 1a8 8 0 0 0-1.7-1L14.9 3h-4l-.4 2.9a8 8 0 0 0-1.7 1l-2.5-1-2 3.5L6.4 11a7.97 7.97 0 0 0 0 2l-2.1 1.6 2 3.5 2.5-1a8 8 0 0 0 1.7 1l.4 2.9h4l.4-2.9a8 8 0 0 0 1.7-1l2.5 1 2-3.5z"/>
    </svg>
  </button>
  <button id="ikw-quit-btn" class="ikw-icon-btn" title="Quit" aria-label="Quit">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
      <path d="M12 3v8"/><path d="M6.3 6.3a8 8 0 1 0 11.4 0"/>
    </svg>
  </button>
</div>
<div id="ikw-toolbar-hint"></div>
<div class="ikw-toast" id="ikw-toast"></div>
<script>
function ikwToast(msg) {
  const el = document.getElementById('ikw-toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(ikwToast._t);
  ikwToast._t = setTimeout(() => el.classList.remove('show'), 4000);
}

document.getElementById('ikw-quit-btn').addEventListener('click', async () => {
  if (!confirm('Quit info-kierowca-notifier? You will stop getting checked/notified until you start it again.')) return;
  try { await fetch('/shutdown', {method: 'POST'}); } catch (e) {}
  document.body.innerHTML =
    '<div style="padding:4rem;text-align:center;font-family:sans-serif;color:#eee;">Stopped. You can close this tab.</div>';
});

const ikwSettingsOverlay = document.getElementById('ikw-settings-overlay');
const ikwSettingsFrame = document.getElementById('ikw-settings-frame');

function ikwOpenSettingsModal() {
  // Reset to about:blank on every close (below) means this is always a
  // fresh navigation, never a same-URL no-op — so the settings form always
  // reflects the just-saved config without needing a cache-busting query
  // string (which would also need do_GET's exact-path routing to strip it).
  ikwSettingsFrame.src = '/settings';
  ikwSettingsOverlay.classList.add('show');
  document.body.style.overflow = 'hidden';
}

function ikwCloseSettingsModal() {
  ikwSettingsOverlay.classList.remove('show');
  document.body.style.overflow = '';
  ikwSettingsFrame.src = 'about:blank';
}

document.getElementById('ikw-settings-btn').addEventListener('click', ikwOpenSettingsModal);

ikwSettingsOverlay.addEventListener('mousedown', (e) => {
  if (e.target === ikwSettingsOverlay) ikwCloseSettingsModal();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && ikwSettingsOverlay.classList.contains('show')) ikwCloseSettingsModal();
});

// WIZARD_PAGE's IKW_EMBEDDED posts these instead of navigating, since it's
// running inside #ikw-settings-frame rather than as its own top-level page.
window.addEventListener('message', (e) => {
  if (e.origin !== window.location.origin) return;
  const type = e.data && e.data.type;
  if (type === 'ikw-settings-close') {
    ikwCloseSettingsModal();
  } else if (type === 'ikw-settings-saved') {
    ikwCloseSettingsModal();
    // poll() is dashboard_server.py's own function, sharing this page's
    // script scope — re-reads status.json immediately so a changed poll
    // interval/countdown shows right away instead of waiting up to 5s.
    if (typeof poll === 'function') poll();
    ikwToast('Settings saved.');
  } else if (type === 'ikw-settings-reset') {
    // Reset clears config.json/session.json — a full top-level navigation
    // to the login screen, not just closing the modal.
    window.location.href = '/';
  }
});

document.getElementById('ikw-browser-btn').addEventListener('click', async () => {
  const btn = document.getElementById('ikw-browser-btn');
  btn.disabled = true;
  try {
    const res = await fetch('/manual-login', {method: 'POST'});
    const data = await res.json();
    ikwToast(data.message || 'Something went wrong.');
  } catch (e) {
    ikwToast('Could not reach the app.');
  } finally {
    btn.disabled = false;
  }
});

// dashboard_server.py's #session-refresh-btn stays display:none (and has no
// listener) without this script - see its CSS comment there for why. Shown
// unconditionally rather than only when data.session_expires_estimate is
// set, since it's also the way to get a *first* estimate going.
const ikwSessionRefreshBtn = document.getElementById('session-refresh-btn');
ikwSessionRefreshBtn.style.display = 'flex';
ikwSessionRefreshBtn.addEventListener('click', async () => {
  if (!confirm('Open Chrome for a fresh QR login now? This replaces your current session.')) return;
  ikwSessionRefreshBtn.disabled = true;
  try {
    const res = await fetch('/relogin-now', {method: 'POST'});
    const data = await res.json();
    ikwToast(data.message || 'Something went wrong.');
  } catch (e) {
    ikwToast('Could not reach the app.');
  } finally {
    ikwSessionRefreshBtn.disabled = false;
  }
});

// Headline becomes the pause/resume control here rather than in
// dashboard_server.py, so the plain read-only dashboard (no /pause or
// /resume endpoints exist there) never shows a cursor or hover affordance
// it can't back up.
const ikwHeadlineWrap = document.getElementById('headline-wrap');
ikwHeadlineWrap.classList.add('ikw-pausable');
ikwHeadlineWrap.setAttribute('role', 'button');
ikwHeadlineWrap.setAttribute('tabindex', '0');
ikwHeadlineWrap.setAttribute('aria-label', 'Toggle pause');
let ikwPauseInFlight = false;
async function ikwTogglePause() {
  if (ikwPauseInFlight) return;
  ikwPauseInFlight = true;
  // isPaused is dashboard_server.py's own script-scoped variable, kept
  // current by its poll() loop (same cross-script visibility already
  // relied on below for `poll` itself).
  const resuming = isPaused;
  try {
    const res = await fetch(resuming ? '/resume' : '/pause', {method: 'POST'});
    const data = await res.json();
    // poll() (defined in dashboard_server.py's own script, sharing this
    // page) re-reads the now-updated status.json and redraws the
    // headline/icon immediately, instead of waiting up to 5s for its
    // own interval to fire.
    if (typeof poll === 'function') await poll();
    ikwToast(data.paused ? 'Paused — checking will stop until you resume.' : 'Resumed checking.');
  } catch (e) {
    ikwToast('Could not reach the app.');
  } finally {
    ikwPauseInFlight = false;
  }
}
ikwHeadlineWrap.addEventListener('click', ikwTogglePause);
ikwHeadlineWrap.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); ikwTogglePause(); }
});

const ikwToolbar = document.getElementById('ikw-toolbar');
const ikwToolbarZone = document.getElementById('ikw-toolbar-zone');
let ikwHideTimer = null;
function ikwRevealToolbar() {
  ikwToolbar.classList.add('show');
  clearTimeout(ikwHideTimer);
  ikwHideTimer = setTimeout(() => ikwToolbar.classList.remove('show'), 2200);
}
ikwToolbarZone.addEventListener('mousemove', ikwRevealToolbar);
ikwToolbar.addEventListener('mousemove', ikwRevealToolbar);
ikwToolbar.addEventListener('focusin', () => { ikwToolbar.classList.add('show'); clearTimeout(ikwHideTimer); });
ikwToolbar.addEventListener('focusout', ikwRevealToolbar);
document.addEventListener('mousemove', (e) => { if (e.clientY < 88) ikwRevealToolbar(); });
</script>
"""

LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>info-kierowca notifier — connect your account</title>
<style>
  * { box-sizing: border-box; }
  :root { --accent: #6a9c7c; --accent-soft: #9dc2ac; }
  body { margin: 0; min-height: 100vh; font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
    background: #1c1c1c; color: #eee; padding: 2rem; display: flex; justify-content: center; align-items: center; }
  #card { max-width: 440px; width: 100%; text-align: center; }
  h1 { font-size: 1.5rem; margin-bottom: 0.4rem; }
  p.lead { opacity: 0.75; margin-top: 0; margin-bottom: 1.8rem; }
  button { width: 100%; padding: 0.85rem; background: var(--accent); color: #1c1c1c; border: none;
    border-radius: 8px; font-size: 1rem; font-weight: 600; cursor: pointer; }
  button:hover { background: var(--accent-soft); }
  button:disabled { opacity: 0.6; cursor: default; }
  #hint { opacity: 0.65; font-size: 0.88rem; margin-top: 1.1rem; display: none; }
  #hint.show { display: block; }
  #skip { display: block; opacity: 0.5; font-size: 0.85rem; margin-top: 1.6rem; color: #ccc; }
  #skip:hover { opacity: 0.8; }
  #error { display: none; margin-top: 1rem; background: #3a1f1f; color: #ff9d9d;
    border: 1px solid rgba(255,128,128,0.45); padding: 0.6rem 0.9rem; border-radius: 8px; font-size: 0.88rem; }
  #error.show { display: block; }
</style>
</head>
<body>
<div id="card">
  <h1>Connect your account</h1>
  <p class="lead">Log in once via the mObywatel QR code — this is what lets the notifier check for
  slots on your behalf. While we're at it, we'll also find your PKK number and license category
  automatically, so you don't have to type them in.</p>
  <button id="login-btn">Log in with mObywatel</button>
  <div id="hint">A Chrome window should open — scan the QR code in the mObywatel app. This page
  continues on its own once you're logged in.</div>
  <div id="error"></div>
  <a href="/setup" id="skip">Skip and enter my PKK number manually</a>
</div>
<script>
const loginBtn = document.getElementById('login-btn');
const loginHint = document.getElementById('hint');
const loginError = document.getElementById('error');

loginBtn.addEventListener('click', async () => {
  loginBtn.disabled = true;
  loginError.classList.remove('show');
  try {
    const res = await fetch('/login-start', {method: 'POST'});
    const data = await res.json();
    if (!data.ok || data.action === 'launch_failed' || data.action === 'no_chromium_browser') {
      throw new Error(data.message || 'Could not open Chrome — try the manual option below.');
    }
    loginHint.classList.add('show');
    loginBtn.textContent = 'Waiting for QR scan...';
    let elapsed = 0;
    const polling = setInterval(async () => {
      elapsed += 2000;
      const r = await fetch('/login-status');
      const d = await r.json();
      if (d.ready) {
        clearInterval(polling);
        window.location.href = '/';
      } else if (!d.in_progress && elapsed > 8000) {
        // Chrome closed or crashed before the QR was scanned — nothing left
        // to wait on, so let the user try again instead of spinning forever.
        // (The grace period covers the moment right after launch, before the
        // spawned process has even had a chance to acquire its lock file.)
        clearInterval(polling);
        loginBtn.disabled = false;
        loginBtn.textContent = 'Log in with mObywatel';
        loginHint.classList.remove('show');
        loginError.textContent = "Login didn't complete — the Chrome window may have been closed. Try again.";
        loginError.classList.add('show');
      }
    }, 2000);
  } catch (e) {
    loginBtn.disabled = false;
    loginError.textContent = e.message;
    loginError.classList.add('show');
  }
});
</script>
</body>
</html>
"""

WIZARD_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>info-kierowca notifier — setup</title>
<script>
  // Runs before <body> paints (no defer/async, and this sits ahead of the
  // rest of <head>) so the transparent-background rule below is already
  // active on first paint - otherwise the modal would flash opaque for a
  // frame before turning see-through. window.parent !== window is the same
  // embedded-in-the-dashboard-modal check the bottom-of-body script (see
  // IKW_EMBEDDED there) uses for postMessage vs. navigation.
  if (window.parent !== window) document.documentElement.classList.add('ikw-embedded');
</script>
<style>
  * { box-sizing: border-box; }
  :root {
    --accent: #6a9c7c; --accent-soft: #9dc2ac;
    --accent-dim: rgba(106,156,124,0.15); --accent-line: rgba(106,156,124,0.55);
  }
  body {
    margin: 0; min-height: 100vh; font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
    background: #1c1c1c; color: #eee; padding: 2rem; display: flex; justify-content: center;
  }
  /* Embedded in the dashboard's Settings modal (see TOOLBAR_HTML's
     #ikw-settings-panel): let the panel's own frosted-glass background show
     through the iframe instead of painting a second opaque page over it.
     Standalone (first-run /setup, a direct /settings visit) keeps the solid
     background above untouched. */
  html.ikw-embedded, html.ikw-embedded body { background: transparent; }
  #card { max-width: 560px; width: 100%; }
  /* Fixed to the (iframe's own) viewport, so scrolled fieldset content
     passes underneath it - a solid-enough backdrop plus its own shadow is
     what keeps that reading as "floating above the content" instead of the
     button visibly colliding with whatever border/text happens to scroll
     past behind it (its background used to be too faint - close to the
     page's own transparent-when-embedded background - for that separation
     to read at all). */
  #wiz-close-btn { display: none; position: fixed; top: 1rem; right: 1rem; width: 2.2rem; height: 2.2rem;
    border-radius: 999px; background: rgba(24,24,24,0.9); color: #eee; border: 1px solid rgba(255,255,255,0.18);
    box-shadow: 0 3px 12px rgba(0,0,0,0.45); font-size: 1.2rem; line-height: 1; cursor: pointer;
    align-items: center; justify-content: center; }
  #wiz-close-btn:hover { background: rgba(36,36,36,0.95); border-color: rgba(255,255,255,0.32); }
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

  /* license-category pills */
  .cat-group { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.9rem; }
  .cat-pill { flex: 0 0 auto; min-width: 3.2rem; padding: 0.5rem 0.7rem; }
  .cat-rest { display: none; }
  .cat-rest.open { display: flex; }
  .cat-more { background: none; border: none; color: var(--accent-soft); cursor: pointer;
    font-size: 0.85rem; padding: 0; margin: -0.4rem 0 0.9rem; }
  .cat-more:hover { text-decoration: underline; }

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
  #test-push-btn { width: auto; padding: 0.5rem 0.9rem; background: #2f2f2f; color: #eee;
    border: 1px solid #3d3d3d; border-radius: 7px; cursor: pointer; font-size: 0.85rem; font-weight: 400; }
  #test-push-btn:hover { border-color: #555; }
  #reset-account-btn { width: auto; padding: 0.55rem 1rem; background: transparent; color: #d98c8c;
    border: 1px solid rgba(217,140,140,0.4); border-radius: 7px; cursor: pointer; font-size: 0.88rem; font-weight: 500; }
  #reset-account-btn:hover { background: rgba(217,140,140,0.1); border-color: rgba(217,140,140,0.7); }

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

  /* check-frequency slider */
  .freq-head { display: flex; justify-content: space-between; align-items: baseline; }
  .freq-head label { margin-bottom: 0; }
  .freq-value { font-size: 0.88rem; font-weight: 600; color: var(--accent-soft); white-space: nowrap; }
  input[type=range] {
    -webkit-appearance: none; appearance: none; width: 100%; height: 4px; border-radius: 999px;
    background: #3d3d3d; margin: 0.6rem 0 0.9rem; cursor: pointer;
  }
  input[type=range]:focus { outline: none; }
  input[type=range]::-webkit-slider-runnable-track { height: 4px; border-radius: 999px; background: #3d3d3d; }
  input[type=range]::-webkit-slider-thumb {
    -webkit-appearance: none; appearance: none; width: 16px; height: 16px; border-radius: 50%;
    background: var(--accent); cursor: pointer; margin-top: -6px; box-shadow: 0 1px 3px rgba(0,0,0,0.4);
  }
  input[type=range]::-moz-range-track { height: 4px; border-radius: 999px; background: #3d3d3d; }
  input[type=range]::-moz-range-thumb {
    width: 16px; height: 16px; border-radius: 50%; background: var(--accent); border: none; cursor: pointer;
  }
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
  #error { display: none; position: fixed; top: 1rem; left: 50%; transform: translateX(-50%); z-index: 100;
    max-width: 90%; background: #3a1f1f; color: #ff9d9d; border: 1px solid rgba(255,128,128,0.45);
    padding: 0.7rem 1rem; border-radius: 8px; box-shadow: 0 10px 30px rgba(0,0,0,0.55);
    font-size: 0.9rem; white-space: pre-line; }
  #error.show { display: block; }
</style>
</head>
<body>
<button id="wiz-close-btn" type="button" title="Back to dashboard" aria-label="Back to dashboard">&times;</button>
<div id="card">
  <h1 id="page-title">Set up info-kierowca notifier</h1>
  <p class="lead" id="page-lead">This runs entirely on your machine — nothing but info-kierowca.pl ever sees your PKK number or session.</p>

  <div id="error"></div>

  <form id="form">
    <fieldset>
      <legend>Exam &amp; centers</legend>
      <div id="pkk-auto-block" style="display:none;">
        <label for="pkk-profile-select">Your PKK profile</label>
        <select id="pkk-profile-select"></select>
        <button type="button" class="cat-more" id="pkk-manual-link">Enter manually instead</button>
      </div>

      <div id="pkk-manual-block">
        <label for="profile_number">PKK number</label>
        <div class="reveal">
          <input type="text" id="profile_number" autocomplete="off" required>
          <button type="button" class="reveal-btn" id="reveal-pkk" aria-label="Show or hide PKK number"></button>
        </div>

        <label>License category</label>
        <div class="cat-group" id="cat-primary"></div>
        <button type="button" class="cat-more" id="cat-more-btn">More categories</button>
        <div class="cat-group cat-rest" id="cat-rest"></div>
        <button type="button" class="cat-more" id="pkk-auto-link" style="display:none;">Use my PKK profile instead</button>
      </div>

      <label>Exam type</label>
      <div class="pill-group" id="exam-types">
        <div class="pill on" data-val="Theoretical" role="button" tabindex="0">Theoretical</div>
        <div class="pill" data-val="Practice" role="button" tabindex="0">Practical</div>
      </div>

      <div class="divider"></div>

      <label>WORD centers to watch (__CENTER_COUNT__ nationwide)</label>
      <div class="combobox">
        <input type="text" id="center-search" placeholder="Click to browse all centers, or type to filter..." autocomplete="off">
        <div id="center-dropdown"></div>
      </div>
      <div id="selected-centers"></div>
      <div class="center-count" id="center-count"></div>
    </fieldset>

    <fieldset>
      <legend>Alerts</legend>
      <label for="current_slot_date_display">Date of your current booked slot — a found slot on an earlier date beats this and triggers the alerts below</label>
      <div class="datepick" id="datepick">
        <input type="text" class="datepick-input" id="current_slot_date_display" placeholder="Select a date" readonly required>
        <input type="hidden" id="current_slot_date">
        <div class="calendar" id="calendar"></div>
      </div>

      <div class="divider"></div>

      <div class="toggle-row">
        <div class="toggle-text">
          <div class="tt-title">Send a phone alert when a slot beats your booked date</div>
          <div class="tt-sub">Buzzes your phone when a watched center opens a slot on or before your booked date. Turn off to just watch the dashboard.</div>
        </div>
        <div class="switch on" id="phone-alerts" role="switch" aria-checked="true" tabindex="0"></div>
      </div>

      <div class="divider"></div>

      <div class="toggle-row">
        <div class="toggle-text">
          <div class="tt-title">Send a phone alert when your session expires</div>
          <div class="tt-sub">Buzzes your phone when your login expires and Chrome reopens for you to scan the QR again. Turn off to only get the desktop popup.</div>
        </div>
        <div class="switch on" id="phone-alerts-relogin" role="switch" aria-checked="true" tabindex="0"></div>
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
        <button type="button" id="test-push-btn" style="margin-top:0.8rem;">Send test push</button>
        <div class="hint" id="test-push-status" style="margin-top:0.5rem;"></div>
      </div>
    </fieldset>

    <fieldset>
      <legend>Automation</legend>
      <div class="freq-head">
        <label for="poll_interval_slider">Check frequency</label>
        <span class="freq-value" id="poll-interval-label"></span>
      </div>
      <!-- Steps must stay within notifier.MIN_POLL_INTERVAL_SECONDS/MAX_POLL_INTERVAL_SECONDS
           (see POLL_INTERVAL_STEPS below) - poll_interval_seconds is the hidden field actually
           submitted; the range input is just an index into that array. -->
      <input type="range" id="poll_interval_slider" min="0" max="18" step="1" value="6">
      <input type="hidden" id="poll_interval_seconds" value="60">
      <div class="hint" style="margin-top:-0.35rem;">Checks land a little later than this at random each time (up to +15%), so requests don't all hit the site on one exact, predictable cadence. Faster than a minute means noticeably more requests against a site with no documented rate limits.</div>

      <div class="divider"></div>

      <div class="toggle-row">
        <div class="toggle-text">
          <div class="tt-title">Reopen Chrome to log back in</div>
          <div class="tt-sub">When your session expires, relaunch Chrome at the login screen so you can scan the QR again.</div>
        </div>
        <div class="switch on" id="auto_refresh_chrome" role="switch" aria-checked="true" tabindex="0"></div>
      </div>
      <div class="toggle-row">
        <div class="toggle-text">
          <div class="tt-title">Open my booking when a slot beats your booked date</div>
          <div class="tt-sub">Opens a logged-in browser at your booking's "change date" screen. You still pick the date and confirm yourself.</div>
        </div>
        <div class="switch on" id="auto_open_browser" role="switch" aria-checked="true" tabindex="0"></div>
      </div>
    </fieldset>

    <button type="submit" id="submit-btn">Save and log in</button>
  </form>

  <div id="reset-account-block" style="display:none; margin-top:1.5rem; text-align:center;">
    <button type="button" id="reset-account-btn">Reset account</button>
    <div class="hint" style="margin-top:0.5rem;">Logs you out and clears your saved settings — you'll land back on the QR login screen.</div>
  </div>
</div>

<script>
const CENTERS = __CENTERS_JSON__;
const CATEGORIES = __CATEGORIES_JSON__;
const EXISTING_CONFIG = __EXISTING_CONFIG_JSON__;
const KNOWN_IDS = new Set(CENTERS.map(c => c.id));
// True when this page is loaded inside the dashboard's Settings modal
// (see TOOLBAR_HTML's #ikw-settings-frame) rather than as its own top-level
// page (first-run /setup, or a direct /settings visit) — same-origin, so
// postMessage is just the cleanest way to hand control back to the parent
// rather than assuming direct window.parent access always stays safe.
const IKW_EMBEDDED = window.parent !== window;
function ikwGoDashboard(type) {
  if (IKW_EMBEDDED) {
    window.parent.postMessage({ type }, window.location.origin);
  } else {
    window.location.href = '/';
  }
}
const EYE = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_OFF = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.9 4.24A9.1 9.1 0 0 1 12 4c6.5 0 10 8 10 8a18 18 0 0 1-2.16 3.19M6.6 6.6A18 18 0 0 0 2 12s3.5 7 10 7a9 9 0 0 0 5.4-1.6"/><path d="m2 2 20 20"/></svg>';
const CENTERS_BY_ID = new Map(CENTERS.map(c => [c.id, c]));
// The search endpoint rejects anything but exactly 5 organizationIds, so at
// most 5 centers can ever be watched — notifier.py pads the rest with
// unrelated fillers whose results get discarded, but that only works up to
// this many real picks.
const MAX_CENTERS = 5;
const selectedIds = new Set(
  (EXISTING_CONFIG ? EXISTING_CONFIG.organization_ids : []).filter(id => KNOWN_IDS.has(id))
);

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
  centerCount.innerHTML = `Watching <b>${n}</b> of ${MAX_CENTERS} centers for open slots.`;
}

function closeDropdown() {
  dropdown.style.display = 'none';
  dropdown.innerHTML = '';
  currentMatches = [];
  activeIndex = -1;
}

function selectCenter(id) {
  if (selectedIds.size >= MAX_CENTERS) return;
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
  const atCap = selectedIds.size >= MAX_CENTERS;
  currentMatches = atCap ? [] : CENTERS.filter(c => !selectedIds.has(c.id) && (!f || centerLabel(c).toLowerCase().includes(f)));
  activeIndex = currentMatches.length ? 0 : -1;
  dropdown.innerHTML = '';
  if (!currentMatches.length) {
    const empty = document.createElement('div');
    empty.className = 'dropdown-empty';
    empty.textContent = atCap ? `Maximum of ${MAX_CENTERS} centers reached — remove one to add another.` : (f ? 'No matching centers.' : 'All centers added.');
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
const phoneAlertsReloginSwitch = document.getElementById('phone-alerts-relogin');
const ntfyField = document.getElementById('ntfy-field');
function applyNtfyDim() {
  ntfyField.classList.toggle('disabled', !phoneAlertsSwitch.classList.contains('on') && !phoneAlertsReloginSwitch.classList.contains('on'));
}
wireSwitch(phoneAlertsSwitch, applyNtfyDim);
wireSwitch(phoneAlertsReloginSwitch, applyNtfyDim);
wireSwitch(document.getElementById('auto_refresh_chrome'));
wireSwitch(document.getElementById('auto_open_browser'));

// ---- check-frequency slider ----
// Non-linear steps (finer near the low end, coarser near the high end) so the
// slider gives many more real options than a handful of dropdown presets did,
// without a purely linear 15s-1800s scale wasting most of its range on
// intervals nobody wants. Must stay within notifier.MIN_POLL_INTERVAL_SECONDS/
// MAX_POLL_INTERVAL_SECONDS - build_config() validates the submitted value
// against those independently of this array.
const POLL_INTERVAL_STEPS = [15, 20, 25, 30, 40, 50, 60, 75, 90, 120, 150, 180, 240, 300, 420, 600, 900, 1200, 1800];
const pollSlider = document.getElementById('poll_interval_slider');
const pollIntervalHidden = document.getElementById('poll_interval_seconds');
const pollIntervalLabel = document.getElementById('poll-interval-label');

function fmtInterval(seconds) {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s ? `${m}m ${s}s` : `${m} min`;
}

function updatePollIntervalDisplay() {
  const seconds = POLL_INTERVAL_STEPS[Number(pollSlider.value)];
  pollIntervalHidden.value = seconds;
  pollIntervalLabel.textContent = `Every ${fmtInterval(seconds)}`;
}

function setPollIntervalSeconds(seconds) {
  // Snaps to the closest step so a value from an older config (or a raw
  // --interval on the CLI) that isn't one of today's steps still lands
  // somewhere sensible on the slider instead of defaulting silently.
  let bestIdx = 0;
  let bestDiff = Infinity;
  POLL_INTERVAL_STEPS.forEach((s, i) => {
    const diff = Math.abs(s - seconds);
    if (diff < bestDiff) { bestDiff = diff; bestIdx = i; }
  });
  pollSlider.value = bestIdx;
  updatePollIntervalDisplay();
}

pollSlider.addEventListener('input', updatePollIntervalDisplay);
updatePollIntervalDisplay();

// ---- license-category pills (data-driven from categories.json) ----
// A and B are shown up top; the rest live behind a "More categories" reveal.
const TOP_CATEGORY_CODES = ['A', 'B'];
const catPrimary = document.getElementById('cat-primary');
const catRest = document.getElementById('cat-rest');
const catMoreBtn = document.getElementById('cat-more-btn');
let selectedCategory = null;
function setCategory(id) {
  selectedCategory = id;
  document.querySelectorAll('.cat-pill').forEach((p) => p.classList.toggle('on', p.dataset.id === String(id)));
}
function setCatRestOpen(open) {
  catRest.classList.toggle('open', open);
  catMoreBtn.textContent = open ? 'Fewer categories' : 'More categories';
}
function expandCatRest() { setCatRestOpen(true); }
CATEGORIES.forEach((c) => {
  const el = document.createElement('div');
  el.className = 'pill cat-pill';
  el.dataset.id = String(c.id);
  el.textContent = c.code || ('Cat ' + c.id);
  el.setAttribute('role', 'button');
  el.tabIndex = 0;
  const select = () => setCategory(c.id);
  el.addEventListener('click', select);
  el.addEventListener('keydown', (e) => { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); select(); } });
  (TOP_CATEGORY_CODES.includes(c.code) ? catPrimary : catRest).appendChild(el);
});
if (!catRest.children.length) catMoreBtn.style.display = 'none';
catMoreBtn.addEventListener('click', () => setCatRestOpen(!catRest.classList.contains('open')));
if (CATEGORIES.some((c) => c.id === 5)) setCategory(5);

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

// ---- PKK profile picker (prefilled after QR login, see build_pkk_prefill) ----
const PKK_PROFILES = __PKK_PROFILES_JSON__;
if (PKK_PROFILES.length) {
  const pkkAutoBlock = document.getElementById('pkk-auto-block');
  const pkkManualBlock = document.getElementById('pkk-manual-block');
  const pkkProfileSelect = document.getElementById('pkk-profile-select');
  const pkkManualLink = document.getElementById('pkk-manual-link');
  const pkkAutoLink = document.getElementById('pkk-auto-link');

  PKK_PROFILES.forEach((p, i) => {
    const opt = document.createElement('option');
    opt.value = String(i);
    opt.textContent = `${p.pkkNumber} — ${p.categoryCode}`;
    pkkProfileSelect.appendChild(opt);
  });

  function applyPkkProfile(p) {
    pkkInput.value = p.pkkNumber;
    setCategory(p.categoryId);
    const isTop = CATEGORIES.some((c) => c.id === p.categoryId && TOP_CATEGORY_CODES.includes(c.code));
    if (!isTop) expandCatRest();
  }

  pkkAutoBlock.style.display = 'block';
  pkkManualBlock.style.display = 'none';
  applyPkkProfile(PKK_PROFILES[0]);

  pkkProfileSelect.addEventListener('change', () => applyPkkProfile(PKK_PROFILES[Number(pkkProfileSelect.value)]));
  pkkManualLink.addEventListener('click', () => {
    pkkAutoBlock.style.display = 'none';
    pkkManualBlock.style.display = 'block';
    pkkAutoLink.style.display = 'block';
  });
  pkkAutoLink.addEventListener('click', () => {
    pkkAutoBlock.style.display = 'block';
    pkkManualBlock.style.display = 'none';
    pkkAutoLink.style.display = 'none';
    applyPkkProfile(PKK_PROFILES[Number(pkkProfileSelect.value)]);
  });
}

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
  const pageTitle = document.getElementById('page-title');
  pageTitle.textContent = 'Settings';
  pageTitle.style.marginBottom = '1.6rem'; // replaces the gap the (now-hidden) lead paragraph used to provide
  document.getElementById('page-lead').style.display = 'none';
  document.getElementById('submit-btn').textContent = 'Save changes';

  // Only shown once a config already exists (i.e. this is /settings, not
  // first-run /setup) — there's no dashboard to go "back" to otherwise.
  const closeBtn = document.getElementById('wiz-close-btn');
  closeBtn.style.display = 'flex';
  closeBtn.addEventListener('click', () => { ikwGoDashboard('ikw-settings-close'); });

  pkkInput.value = EXISTING_CONFIG.profile_number || '';
  if (pkkInput.value) { pkkInput.type = 'password'; pkkSync(); }

  if (EXISTING_CONFIG.category != null) {
    setCategory(EXISTING_CONFIG.category);
    const isTop = CATEGORIES.some((c) => c.id === EXISTING_CONFIG.category && TOP_CATEGORY_CODES.includes(c.code));
    if (!isTop) expandCatRest();
  }

  const examTypes = EXISTING_CONFIG.exam_types || [];
  examGroup.querySelectorAll('.pill').forEach((p) => p.classList.toggle('on', examTypes.includes(p.dataset.val)));

  if (EXISTING_CONFIG.current_slot_date) {
    const parts = EXISTING_CONFIG.current_slot_date.split('-').map(Number);
    if (parts.length === 3 && parts.every((n) => !Number.isNaN(n))) {
      selectedDate = new Date(parts[0], parts[1] - 1, parts[2]);
      dpValue.value = EXISTING_CONFIG.current_slot_date;
      dpInput.value = fmtDate(selectedDate);
    }
  }

  setPollIntervalSeconds(EXISTING_CONFIG.poll_interval_seconds || 60);
  setSwitch(phoneAlertsSwitch, EXISTING_CONFIG.phone_alerts !== false);
  setSwitch(phoneAlertsReloginSwitch, EXISTING_CONFIG.phone_alerts_relogin !== false);
  setSwitch(document.getElementById('auto_refresh_chrome'), EXISTING_CONFIG.auto_refresh_chrome !== false);
  setSwitch(document.getElementById('auto_open_browser'), EXISTING_CONFIG.auto_open_browser !== false);
  applyNtfyDim();

  // Nothing to reset on a fresh /setup with no saved config yet.
  document.getElementById('reset-account-block').style.display = 'block';
}

document.getElementById('copy-ntfy').addEventListener('click', () => {
  navigator.clipboard.writeText('https://ntfy.sh/' + ntfyInput.value);
});

const testPushBtn = document.getElementById('test-push-btn');
const testPushStatus = document.getElementById('test-push-status');
testPushBtn.addEventListener('click', async () => {
  testPushBtn.disabled = true;
  testPushStatus.textContent = 'Sending...';
  try {
    const res = await fetch('/test-push', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({topic: ntfyInput.value}),
    });
    const data = await res.json();
    testPushStatus.textContent = data.ok ? 'Sent — check your phone.' : (data.error || 'Failed to send.');
  } catch (e) {
    testPushStatus.textContent = 'Failed to send.';
  } finally {
    testPushBtn.disabled = false;
  }
});

const resetAccountBtn = document.getElementById('reset-account-btn');
resetAccountBtn.addEventListener('click', async () => {
  if (!confirm("This logs you out and clears your saved settings. You'll need to scan the QR code again. Continue?")) return;
  resetAccountBtn.disabled = true;
  try {
    await fetch('/reset-account', {method: 'POST'});
    // Always a full top-level navigation, even when embedded: reset clears
    // config.json and session.json, so what comes next is the login screen,
    // not just an updated settings form — there's no "back to dashboard" to
    // return to inside the modal.
    if (IKW_EMBEDDED) { window.parent.postMessage({ type: 'ikw-settings-reset' }, window.location.origin); }
    else { window.location.href = '/'; }
  } catch (e) {
    resetAccountBtn.disabled = false;
    alert('Reset failed — check the log.');
  }
});

document.getElementById('form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById('error');
  errorEl.textContent = '';
  errorEl.classList.remove('show');
  try {
    const examTypes = selectedExamTypes();
    if (!examTypes.length) throw new Error('Pick at least one exam type.');

    const orgIds = Array.from(selectedIds);
    if (!orgIds.length) throw new Error('Pick at least one WORD center.');
    if (orgIds.length > MAX_CENTERS) throw new Error(`Pick at most ${MAX_CENTERS} WORD centers — the site's search only accepts ${MAX_CENTERS} at a time.`);

    const profileNumber = pkkInput.value.trim();
    if (!profileNumber) throw new Error('PKK number is required.');

    const category = selectedCategory;
    if (!category) throw new Error('Pick a license category.');

    const currentSlotDate = dpValue.value;
    if (!currentSlotDate) throw new Error('Pick the date of your current booked slot.');

    const body = {
      profile_number: profileNumber,
      organization_ids: orgIds,
      category: category,
      exam_types: examTypes,
      current_slot_date: currentSlotDate,
      poll_interval_seconds: parseInt(document.getElementById('poll_interval_seconds').value, 10),
      phone_alerts: switchOn('phone-alerts'),
      phone_alerts_relogin: switchOn('phone-alerts-relogin'),
      auto_refresh_chrome: switchOn('auto_refresh_chrome'),
      auto_open_browser: switchOn('auto_open_browser'),
      ntfy_topic: ntfyInput.value,
    };

    const res = await fetch('/setup', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Save failed.');

    ikwGoDashboard('ikw-settings-saved');
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.add('show');
  }
});
</script>
</body>
</html>
"""
