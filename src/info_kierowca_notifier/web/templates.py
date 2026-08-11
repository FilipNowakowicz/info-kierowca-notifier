"""HTML/JS template strings for the app module's HTTP handlers.

Pulled out of the app module verbatim (no behavior change) since they made up
the bulk of that file's line count. The app module still owns all rendering logic
(WIZARD_PAGE's __CENTERS_JSON__ substitution, TOOLBAR_HTML splicing into
dashboard_server.PAGE, etc.) — this module only holds the literal strings.
"""

from info_kierowca_notifier.web.localization import LOCALIZATION_SCRIPT

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
  /* Faint permanent dot so the toolbar is discoverable even before its
     hover/focus reveal has ever fired. */
  #ikw-toolbar-hint { position: fixed; top: 1.1rem; right: 1.25rem; width: 0.35rem; height: 0.35rem;
    border-radius: 999px; background: rgba(255,255,255,0.3); transition: opacity 0.2s ease; z-index: 9; }
  .ikw-toolbar.show ~ #ikw-toolbar-hint { opacity: 0; }

  /* Makes web.server's headline markup clickable: dims the
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
     An iframe (not a merged template) keeps the app module's two big templates
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
  if (!confirm(ikwI18n.t('Quit info-kierowca-notifier? You will stop getting checked/notified until you start it again.'))) return;
  try { await fetch('/shutdown', {method: 'POST', headers: {'Content-Type': 'application/json'}}); } catch (e) {}
  document.body.innerHTML =
    `<div style="padding:4rem;text-align:center;font-family:sans-serif;color:#eee;">${ikwI18n.t('Stopped. You can close this tab.')}</div>`;
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
  } else if (type === 'ikw-language-changed') {
    ikwI18n.apply();
    if (typeof poll === 'function') poll();
  } else if (type === 'ikw-settings-saved') {
    ikwCloseSettingsModal();
    // poll() is web.server's own function, sharing this page's
    // script scope — re-reads status.json immediately so a changed poll
    // interval/countdown shows right away instead of waiting up to 5s.
    if (typeof poll === 'function') poll();
    ikwToast(ikwI18n.t('Settings saved.'));
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
    const res = await fetch('/manual-login', {method: 'POST', headers: {'Content-Type': 'application/json'}});
    const data = await res.json();
    ikwToast(ikwI18n.t(data.message || 'Something went wrong.'));
  } catch (e) {
    ikwToast(ikwI18n.t('Could not reach the app.'));
  } finally {
    btn.disabled = false;
  }
});

// Getting a fresh session on demand lives in Settings now (next to Pair
// Google Messages Web), not here - see WIZARD_PAGE's #settings-relogin-btn.
// The dashboard toolbar used to have its own copy of this control; moved out
// since it's a "sometimes useful" action, not something that needs to be one
// click away on the main view for every visit.

// Headline becomes the pause/resume control here rather than in
// web.server, so the plain read-only dashboard (no /pause or
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
  // isPaused is web.server's own script-scoped variable, kept
  // current by its poll() loop (same cross-script visibility already
  // relied on below for `poll` itself).
  const resuming = isPaused;
  try {
    const res = await fetch(resuming ? '/resume' : '/pause', {method: 'POST', headers: {'Content-Type': 'application/json'}});
    const data = await res.json();
    // poll() (defined in web.server's own script, sharing this
    // page) re-reads the now-updated status.json and redraws the
    // headline/icon immediately, instead of waiting up to 5s for its
    // own interval to fire.
    if (typeof poll === 'function') await poll();
    ikwToast(ikwI18n.t(data.paused ? 'Paused — checking will stop until you resume.' : 'Resumed checking.'));
  } catch (e) {
    ikwToast(ikwI18n.t('Could not reach the app.'));
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
  input { width:100%; padding:0.72rem; margin:0.35rem 0; border-radius:7px; border:1px solid #555; background:#262626; color:#eee; }
  .reveal { position: relative; }
  .reveal input { padding-right: 2.7rem; }
  .reveal-btn { position:absolute; top:50%; right:0.35rem; transform:translateY(-50%); width:auto; padding:0.35rem;
    background:none; color:rgba(238,238,238,0.5); display:grid; place-items:center; }
  .reveal-btn:hover { background:none; color:var(--accent-soft); }
  .icon { width:18px; height:18px; display:block; }
  .methods { display:flex; gap:0.5rem; margin:1rem 0; }
  .methods button { background:#333; color:#eee; }
  .methods button.on { background:var(--accent); color:#1c1c1c; }
  #pz-fields { display:none; text-align:left; margin-bottom:0.8rem; }
  .pz-pairing-description { opacity: 0.65; font-size: 0.85rem; line-height: 1.4; margin: 0.1rem 0 0.35rem; }
  .secondary { margin-top:0.5rem; background:#333; color:#eee; }
  #hint { opacity: 0.65; font-size: 0.88rem; margin-top: 1.1rem; display: none; }
  #hint.show { display: block; }
  .booking-note { margin: 0 0 1.25rem; padding: 0.75rem 0.9rem; text-align: left; border: 1px solid rgba(157,194,172,0.38); border-radius: 8px; background: rgba(106,156,124,0.12); color: #d7eadf; font-size: 0.88rem; line-height: 1.45; }
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
  <p class="lead">Choose how the notifier should authenticate. Profil Zaufany can recover expired sessions automatically after setup.</p>
  <div class="methods"><button id="method-pz" class="on">Profil Zaufany</button><button id="method-mobywatel">mObywatel</button></div>
  <div id="pz-fields" style="display:block">
    <label for="pz-username">Profil Zaufany username</label>
    <div class="reveal">
      <input id="pz-username" type="password" autocomplete="username">
      <button type="button" class="reveal-btn" id="reveal-pz-username" aria-label="Show or hide Profil Zaufany username"></button>
    </div>
    <label for="pz-password">Profil Zaufany password</label><input id="pz-password" type="password" autocomplete="current-password">
    <div class="pz-pairing-description">Required for automatic Profil Zaufany login: pairing lets the app read the one-time SMS code from Google Messages.</div>
    <button class="secondary" id="pair-messages" type="button">Pair Google Messages Web</button>
    <div id="messages-status"></div>
  </div>
  <button id="login-btn">Log in with Profil Zaufany</button>
  <div id="hint">A Chrome window should open — scan the QR code in the mObywatel app. This page
  continues on its own once you're logged in.</div>
  <div id="error"></div>
  <a href="/setup" id="skip">Skip and enter my PKK number manually</a>
</div>
<script>
const loginBtn = document.getElementById('login-btn');
const loginHint = document.getElementById('hint');
const loginError = document.getElementById('error');
const LOGIN_EYE = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>';
const LOGIN_EYE_OFF = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.9 4.24A9.1 9.1 0 0 1 12 4c6.5 0 10 8 10 8a18 18 0 0 1-2.16 3.19M6.6 6.6A18 18 0 0 0 2 12s3.5 7 10 7a9 9 0 0 0 5.4-1.6"/><path d="m2 2 20 20"/></svg>';
const loginPzUsername = document.getElementById('pz-username');
const loginPzUsernameReveal = document.getElementById('reveal-pz-username');
function syncLoginPzUsernameReveal() { loginPzUsernameReveal.innerHTML = loginPzUsername.type === 'password' ? LOGIN_EYE : LOGIN_EYE_OFF; }
loginPzUsernameReveal.addEventListener('click', () => {
  loginPzUsername.type = loginPzUsername.type === 'password' ? 'text' : 'password';
  syncLoginPzUsernameReveal();
});
syncLoginPzUsernameReveal();
let loginMethod = 'profil_zaufany';
const pzFields = document.getElementById('pz-fields');
function setMethod(method) {
  loginMethod = method; pzFields.style.display = method === 'profil_zaufany' ? 'block' : 'none';
  document.getElementById('method-mobywatel').classList.toggle('on', method === 'mobywatel');
  document.getElementById('method-pz').classList.toggle('on', method === 'profil_zaufany');
  loginBtn.textContent = method === 'profil_zaufany' ? 'Log in with Profil Zaufany' : 'Log in with mObywatel';
}
document.getElementById('method-mobywatel').onclick = () => setMethod('mobywatel');
document.getElementById('method-pz').onclick = () => setMethod('profil_zaufany');
document.getElementById('pair-messages').onclick = async () => {
  const d = await (await fetch('/pair-google-messages', {method:'POST', headers:{'Content-Type':'application/json'}})).json();
  document.getElementById('messages-status').textContent = d.ok ? 'Google Messages Web opened for pairing.' : 'Could not open Google Messages Web.';
};
loginBtn.addEventListener('click', async () => {
  loginBtn.disabled = true;
  loginError.classList.remove('show');
  try {
    const res = await fetch('/login-start', {method: 'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
      login_method:loginMethod, pz_username:document.getElementById('pz-username').value,
      pz_password:document.getElementById('pz-password').value
    })});
    const data = await res.json();
    if (!data.ok || data.action === 'launch_failed' || data.action === 'no_chromium_browser') {
      throw new Error(ikwI18n.t(data.message || 'Could not open Chrome — try the manual option below.'));
    }
    if (data.action === 'already_running' && confirm(ikwI18n.t('A QR login is already open. Close it and restart login?'))) {
      const restartRes = await fetch('/relogin-restart', {method: 'POST', headers: {'Content-Type': 'application/json'}});
      const restartData = await restartRes.json();
      if (restartData.action !== 'restart_launched') {
        throw new Error(ikwI18n.t(restartData.message || 'Could not restart the QR login.'));
      }
    }
    loginHint.classList.add('show');
    loginBtn.textContent = loginMethod === 'profil_zaufany' ? 'Authenticating automatically...' : 'Waiting for QR scan...';
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
        setMethod(loginMethod);
        loginHint.classList.remove('show');
        loginError.textContent = ikwI18n.t("Login didn't complete — the Chrome window may have been closed. Try again.");
        loginError.classList.add('show');
      }
    }, 2000);
  } catch (e) {
    loginBtn.disabled = false;
    loginError.textContent = ikwI18n.t(e.message);
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
  #wiz-close-btn { display: none; position: absolute; top: 0.1rem; right: 0; width: 2.2rem; height: 2.2rem;
    border-radius: 999px; background: rgba(24,24,24,0.9); color: #eee; border: 1px solid rgba(255,255,255,0.18);
    box-shadow: 0 3px 12px rgba(0,0,0,0.45); font-size: 1.2rem; line-height: 1; cursor: pointer;
    align-items: center; justify-content: center; }
  #wiz-close-btn:hover { background: rgba(36,36,36,0.95); border-color: rgba(255,255,255,0.32); }
  h1 { font-size: 1.6rem; margin-bottom: 0.2rem; }
  p.lead { opacity: 0.75; margin-top: 0; margin-bottom: 2rem; }
  .booking-note { display: flex; align-items: flex-start; gap: 0.75rem; margin: 0 0 1.25rem; padding: 0.75rem 0.9rem; border: 1px solid rgba(157,194,172,0.38); border-radius: 8px; background: rgba(106,156,124,0.12); color: #d7eadf; font-size: 0.88rem; line-height: 1.45; }
  .booking-note[hidden] { display: none; }
  .booking-note-text { flex: 1; }
  .booking-note-dismiss { flex: none; padding: 0.2rem 0.45rem; border: 1px solid rgba(157,194,172,0.45); border-radius: 5px; background: transparent; color: var(--accent-soft); cursor: pointer; font: inherit; font-size: 0.8rem; white-space: nowrap; }
  .booking-note-dismiss:hover { background: rgba(106,156,124,0.16); }
  .booking-note-dismiss:focus-visible { outline: 2px solid var(--accent-soft); outline-offset: 2px; }
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
  .reveal input, .reveal select { margin-bottom: 0; padding-right: 2.5rem; }
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
  #auto-confirm-row { transition: opacity 0.15s; }
  #auto-confirm-row.disabled { opacity: 0.4; pointer-events: none; }

  /* preferred time-of-day dual-handle slider. The two overlaid range inputs'
     thumbs use -webkit-appearance/appearance: none (see the shared
     input[type=range]::-webkit-slider-thumb/::-moz-range-thumb rule above),
     which makes each browser position the thumb's *center* linearly across
     the full 0%-100% width of the input's own box — unlike a themed native
     thumb, which browsers keep fully inside the track automatically. With
     the thumb 16px wide, that puts half of it (8px) outside the box at
     both the 0 and 24 extremes. The track, fill, and inputs are all inset
     by that same 8px (half the thumb width) via explicit left/right offsets
     — not container padding, which absolutely positioned children ignore
     (their containing block is the padding *edge*, i.e. as if the padding
     weren't there) — so the thumbs land flush with the track ends instead
     of overhanging past them.
     The inputs' own `top` is also set to match .dual-range-track's `top`
     (11px), not 0: the shared ::-webkit-slider-thumb rule's margin-top:-6px
     centers the thumb on the *input's own* 4px-tall box, not on wherever
     the separately-drawn visible track div happens to sit — with top:0
     that math centers the thumb 11px above the visible track instead of on
     it. */
  .dual-range { position: relative; height: 26px; margin: 0.5rem 0 0.4rem; }
  .dual-range-track { position: absolute; top: 11px; left: 8px; right: 8px; height: 4px;
    border-radius: 999px; background: #3d3d3d; }
  .dual-range-fill { position: absolute; top: 11px; height: 4px; border-radius: 999px;
    background: var(--accent); }
  .dual-range input[type=range] { position: absolute; top: 11px; left: 8px; width: calc(100% - 16px);
    margin: 0; background: none; pointer-events: none; }
  .dual-range input[type=range]::-webkit-slider-runnable-track { background: none; }
  .dual-range input[type=range]::-moz-range-track { background: none; }
  .dual-range input[type=range]::-webkit-slider-thumb { pointer-events: auto; }
  .dual-range input[type=range]::-moz-range-thumb { pointer-events: auto; }

  /* custom date picker */
  .datepick { position: relative; margin-bottom: 0.3rem; }
  .datepick-input { cursor: pointer; margin-bottom: 0 !important; }
  .datepick-input.has-clear { padding-right: 2.5rem; }
  .datepick-clear { display: none; position: absolute; top: 50%; right: 0.35rem; transform: translateY(-50%);
    width: 2rem; height: 2rem; padding: 0; border: none; border-radius: 6px; background: transparent;
    color: rgba(238,238,238,0.55); cursor: pointer; font-size: 1.15rem; line-height: 1; }
  .datepick-clear.visible { display: block; }
  .datepick-clear:hover { background: #333; color: var(--accent-soft); }
  .datepick-clear:focus-visible { outline: 2px solid var(--accent-soft); outline-offset: 1px; }
  .datepick + .hint { margin-top: 0.45rem; }
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
  #card { position: relative; }
  .ikw-language-switch { position: absolute; top: 0.15rem; right: 3rem; display: inline-flex; align-items: center; gap: 0.28rem; font-size: 0.78rem; }
  .ikw-language-switch button { width: auto; margin: 0; padding: 0.22rem 0.35rem; border: 0; border-radius: 5px; background: transparent; color: #aaa; cursor: pointer; font: inherit; }
  .ikw-language-switch button.active { background: rgba(106,156,124,0.28); color: #fff; font-weight: 700; }
  .ikw-language-switch button:focus-visible { outline: 2px solid var(--accent-soft); outline-offset: 2px; }
</style>
</head>
<body>
<div id="card">
  <button id="wiz-close-btn" type="button" title="Back to dashboard" aria-label="Back to dashboard">&times;</button>
  <h1 id="page-title">Set up info-kierowca notifier</h1>
  <p class="lead" id="page-lead">This runs entirely on your machine — nothing but info-kierowca.pl ever sees your PKK number or session.</p>

  <div id="error"></div>

  <form id="form">
    <fieldset>
      <legend>Authentication</legend>
      <label for="login_method">Authentication method</label>
      <select id="login_method"><option value="profil_zaufany">Profil Zaufany</option><option value="mobywatel">mObywatel</option></select>
      <button type="button" class="cat-more" id="settings-relogin-btn">Get new session now</button>
      <div class="hint" id="settings-relogin-status"></div>
      <div id="settings-pz-fields" style="display:none">
        <label for="settings-pz-username">Profil Zaufany username</label>
        <div class="reveal">
          <input id="settings-pz-username" type="password" autocomplete="username">
          <button type="button" class="reveal-btn" id="reveal-pz-username-settings" aria-label="Show or hide Profil Zaufany username"></button>
        </div>
        <label for="settings-pz-password">Profil Zaufany password</label><input id="settings-pz-password" type="password" autocomplete="new-password" placeholder="Leave blank to keep the saved password">
        <div class="hint" id="password-status">No Profil Zaufany password is saved. Enter it to enable automatic login.</div>
        <div class="hint">Required for automatic Profil Zaufany login: pairing lets the app read the one-time SMS code from Google Messages.</div>
        <button type="button" class="cat-more" id="settings-pair-messages">Pair Google Messages Web</button>
        <div class="hint" id="settings-messages-status"></div>
        <div class="divider"></div>
        <div class="toggle-row">
          <div class="toggle-text">
            <div class="tt-title">Run automatic Profil Zaufany login in the background</div>
            <div class="tt-sub">Starts Chrome headlessly so no window pops up. Google Messages must already be paired.</div>
          </div>
          <div class="switch" id="headless_pz_login" role="switch" aria-checked="false" tabindex="0"></div>
        </div>
      </div>
    </fieldset>
    <fieldset>
      <legend>Exam &amp; centers</legend>
      <div id="pkk-auto-block" style="display:none;">
        <!-- 2+ profiles: masked "...last4 - code" dropdown, with a reveal toggle
             for the whole option list. See the PKK profile picker script below. -->
        <div id="pkk-select-block" style="display:none;">
          <label for="pkk-profile-select">Your PKK profile</label>
          <div class="reveal">
            <select id="pkk-profile-select"></select>
            <button type="button" class="reveal-btn" id="reveal-pkk-select" aria-label="Show or hide PKK number"></button>
          </div>
        </div>
        <!-- Exactly 1 profile: the #profile_number reveal input (moved in from
             pkk-manual-block below, not duplicated) shown read-only, plus a plain
             non-clickable category label - the category is bound to this profile,
             not user-chosen. -->
        <div id="pkk-single-block" style="display:none;">
          <label for="profile_number">PKK number</label>
          <div class="hint" id="pkk-single-category"></div>
        </div>
        <button type="button" class="cat-more" id="pkk-manual-link">Enter manually instead</button>
      </div>

      <div id="pkk-manual-block">
        <label for="profile_number">PKK number</label>
        <div class="reveal" id="pkk-reveal">
          <input type="text" id="profile_number" autocomplete="off" required>
          <button type="button" class="reveal-btn" id="reveal-pkk" aria-label="Show or hide PKK number"></button>
        </div>

        <label id="pkk-manual-cat-label">License category</label>
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

      <label for="center-search">WORD centers to watch (__CENTER_COUNT__ nationwide)</label>
      <div class="combobox">
        <input type="text" id="center-search" placeholder="Click to browse all centers, or type to filter..." autocomplete="off">
        <div id="center-dropdown"></div>
      </div>
      <div id="selected-centers"></div>
      <div class="center-count" id="center-count"></div>
    </fieldset>

    <fieldset>
      <legend>Alerts</legend>
      <div class="booking-note" id="booking-note" role="note">
        <span class="booking-note-text">Before continuing, you need an existing booked exam. This app changes the date of that booking; it does not create a new booking.</span>
        <button type="button" class="booking-note-dismiss" id="dismiss-booking-note" aria-label="Dismiss booking prerequisite message">Got it</button>
      </div>
      <label for="current_slot_date_display">Required: date of the booking to reschedule</label>
      <div class="datepick" id="datepick">
        <input type="text" class="datepick-input" id="current_slot_date_display" placeholder="Select a date" readonly required>
        <input type="hidden" id="current_slot_date">
        <div class="calendar" id="calendar"></div>
      </div>
      <div class="hint">Enter the date of the existing booking that you want the app to reschedule.</div>

      <label for="search_start_date_display" style="margin-top:1rem;">Earliest acceptable exam date (optional)</label>
      <div class="datepick" id="search-start-datepick">
        <input type="text" class="datepick-input has-clear" id="search_start_date_display" placeholder="Select a date" readonly>
        <button type="button" class="datepick-clear" id="clear-search-start-date" aria-label="Clear earliest acceptable date" title="Clear earliest acceptable date">&times;</button>
        <input type="hidden" id="search_start_date">
        <div class="calendar" id="search-start-calendar"></div>
      </div>
      <div class="hint">Ignore slots before this date. Leave blank to search from today; the site searches at most 31 days ahead.</div>

      <div class="freq-head" style="margin-top:1rem;">
        <label for="time_from_slider">Preferred time of day</label>
        <span class="freq-value" id="time-window-label">All day</span>
      </div>
      <div class="dual-range" id="time-window-slider">
        <div class="dual-range-track"></div>
        <div class="dual-range-fill" id="time-window-fill"></div>
        <input type="range" id="time_from_slider" min="0" max="24" step="1" value="0">
        <input type="range" id="time_to_slider" min="0" max="24" step="1" value="24">
      </div>
      <input type="hidden" id="earliest_slot_hour" value="0">
      <input type="hidden" id="latest_slot_hour" value="24">
      <div class="hint" style="margin-top:-0.15rem;">A slot outside this window won't trigger an alert or open the reschedule browser. Checking and the dashboard still show everything found.</div>

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
            <!-- Value set from NTFY_TOPIC in the script below, not
                 substituted into this attribute - see render_wizard(). -->
            <input type="password" id="ntfy_topic" readonly>
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
          <div class="tt-title">Open my booking when a slot beats your booked date</div>
          <div class="tt-sub">Opens a logged-in browser at your booking's "change date" screen. You still pick the date and confirm yourself.</div>
        </div>
        <div class="switch on" id="auto_open_browser" role="switch" aria-checked="true" tabindex="0"></div>
      </div>

      <div class="divider"></div>

      <div class="toggle-row">
        <div class="toggle-text">
          <div class="tt-title">Experimental: auto-select the matching slot</div>
          <div class="tt-sub">Unverified against the live site. Also expands the matching date group and picks the exact exam type/time on the change-date screen, then goes to the summary/review screen. Still stops there — nothing is submitted.</div>
        </div>
        <div class="switch" id="auto_select_slot" role="switch" aria-checked="false" tabindex="0"></div>
      </div>
      <div class="toggle-row" id="auto-confirm-row">
        <div class="toggle-text">
          <div class="tt-title" style="color:#d98c8c;">Experimental: auto-confirm the reservation change</div>
          <div class="tt-sub">Requires the toggle above. Submits the actual date change with no review step — the one action in this project that can't be undone by closing the browser. Only turn this on once you've watched auto-select pick the right slot reliably.</div>
        </div>
        <div class="switch" id="auto_confirm_reschedule" role="switch" aria-checked="false" tabindex="0"></div>
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
const NTFY_TOPIC = __NTFY_TOPIC_JSON__;
const KNOWN_IDS = new Set(CENTERS.map(c => c.id));
const loginMethodSelect = document.getElementById('login_method');
const settingsPzFields = document.getElementById('settings-pz-fields');
function updateAuthFields() { settingsPzFields.style.display = loginMethodSelect.value === 'profil_zaufany' ? 'block' : 'none'; }
loginMethodSelect.addEventListener('change', updateAuthFields);
updateAuthFields();
document.getElementById('settings-pair-messages').onclick = async () => {
  const d=await (await fetch('/pair-google-messages',{method:'POST', headers:{'Content-Type':'application/json'}})).json();
  document.getElementById('settings-messages-status').textContent=d.ok?'Google Messages Web opened for pairing.':'Could not open Google Messages Web.';
};
// Moved out of the dashboard toolbar (see web.server.py's #session-expiry
// and the app module's old TOOLBAR_HTML wiring) - same /relogin-now +
// /relogin-restart flow, just living in Settings instead of one click away
// on the main view, since forcing a fresh login is a "sometimes useful"
// action rather than a routine one. Not scoped to either login_method: a
// stuck mObywatel session is exactly as real a reason to reach for this as
// a Profil Zaufany one.
const settingsReloginBtn = document.getElementById('settings-relogin-btn');
const settingsReloginStatus = document.getElementById('settings-relogin-status');
settingsReloginBtn.addEventListener('click', async () => {
  if (!confirm(t('Open Chrome for a fresh QR login now? This replaces your current session.'))) return;
  settingsReloginBtn.disabled = true;
  try {
    const res = await fetch('/relogin-now', {method: 'POST', headers: {'Content-Type': 'application/json'}});
    const data = await res.json();
    settingsReloginStatus.textContent = t(data.message || 'Something went wrong.');
    if (data.action === 'already_running' && confirm(t('A QR login is already open. Close it and restart login?'))) {
      const restartRes = await fetch('/relogin-restart', {method: 'POST', headers: {'Content-Type': 'application/json'}});
      const restartData = await restartRes.json();
      settingsReloginStatus.textContent = t(restartData.message || 'Something went wrong.');
    }
  } catch (e) {
    settingsReloginStatus.textContent = t('Could not reach the app.');
  } finally {
    settingsReloginBtn.disabled = false;
  }
});
// True when this page is loaded inside the dashboard's Settings modal
// (see TOOLBAR_HTML's #ikw-settings-frame) rather than as its own top-level
// page (first-run /setup, or a direct /settings visit) — same-origin, so
// postMessage is just the cleanest way to hand control back to the parent
// rather than assuming direct window.parent access always stays safe.
const IKW_EMBEDDED = window.parent !== window;
// A browser-local acknowledgement, separate from account/config data. Reset
// account intentionally keeps it: dismissing explanatory copy cannot make a
// later setup or booking action less safe.
const BOOKING_PREREQUISITE_DISMISSED_KEY = 'info-kierowca-notifier-dismissed-booking-prerequisite';
ikwI18n.installSwitcher(document.getElementById('card'));
const t = (text) => ikwI18n.t(text);
const bookingNote = document.getElementById('booking-note');
if (localStorage.getItem(BOOKING_PREREQUISITE_DISMISSED_KEY) === '1') bookingNote.hidden = true;
document.getElementById('dismiss-booking-note').addEventListener('click', () => {
  localStorage.setItem(BOOKING_PREREQUISITE_DISMISSED_KEY, '1');
  bookingNote.hidden = true;
});
const centerLabelHeading = document.querySelector('label[for="center-search"]');
centerLabelHeading.textContent = ikwI18n.lang() === 'pl'
  ? `Ośrodki WORD do obserwowania (${CENTERS.length} w kraju)`
  : `WORD centers to watch (${CENTERS.length} nationwide)`;
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
  centerCount.innerHTML = ikwI18n.lang() === 'pl' ? `Obserwujesz <b>${n}</b> z ${MAX_CENTERS} ośrodków pod kątem wolnych terminów.` : `Watching <b>${n}</b> of ${MAX_CENTERS} centers for open slots.`;
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
  closeDropdown();
  searchInput.blur();
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
wireSwitch(document.getElementById('headless_pz_login'));
wireSwitch(document.getElementById('auto_open_browser'));

// auto_confirm_reschedule requires auto_select_slot — dim/disable it (and
// force it off) whenever auto_select_slot is off, same dependent-field
// pattern as applyNtfyDim() above. It also gets its own click handler
// instead of a plain wireSwitch(), since turning it ON needs a confirm()
// gate first: unlike every other toggle on this page, this one lets the app
// submit a real reservation change with no human review step, so a misclick
// shouldn't be able to enable it silently.
const autoSelectSlotSwitch = document.getElementById('auto_select_slot');
const autoConfirmSwitch = document.getElementById('auto_confirm_reschedule');
const autoConfirmRow = document.getElementById('auto-confirm-row');
function applyAutoConfirmDim() {
  const enabled = autoSelectSlotSwitch.classList.contains('on');
  autoConfirmRow.classList.toggle('disabled', !enabled);
  if (!enabled) setSwitch(autoConfirmSwitch, false);
}
wireSwitch(autoSelectSlotSwitch, applyAutoConfirmDim);
function toggleAutoConfirm() {
  if (!autoSelectSlotSwitch.classList.contains('on')) return;  // covers keyboard activation while dimmed
  const turningOn = !autoConfirmSwitch.classList.contains('on');
  if (turningOn && !confirm(t(
    "This lets the app automatically click through and submit a real reservation date change "
    + "the moment it finds a matching slot — no review step, and it can't be undone by closing "
    + "the browser. Are you sure?"
  ))) return;
  setSwitch(autoConfirmSwitch, turningOn);
}
autoConfirmSwitch.addEventListener('click', toggleAutoConfirm);
autoConfirmSwitch.addEventListener('keydown', (e) => {
  if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); toggleAutoConfirm(); }
});

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
  pollIntervalLabel.textContent = ikwI18n.lang() === 'pl' ? `Co ${fmtInterval(seconds)}` : `Every ${fmtInterval(seconds)}`;
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

// ---- preferred time-of-day dual-handle slider ----
// Two overlapping native range inputs (0-24, hour granularity) rather than a
// single custom-built slider: input[type=range]'s own track/background is
// set to none via CSS so only its thumb is visible, and pointer-events is
// none on the input itself but auto on just the thumb (::-webkit-slider-thumb/
// ::-moz-range-thumb) — the standard trick for two draggable handles sharing
// one track without a pointer-capture library. A .dual-range-fill div drawn
// between them (position/width set below) gives the "selected range"
// highlight the single-slider's own runnable-track background normally
// would.
const timeFromSlider = document.getElementById('time_from_slider');
const timeToSlider = document.getElementById('time_to_slider');
const timeFromHidden = document.getElementById('earliest_slot_hour');
const timeToHidden = document.getElementById('latest_slot_hour');
const timeWindowFill = document.getElementById('time-window-fill');
const timeWindowLabel = document.getElementById('time-window-label');

function fmtHour(h) {
  return `${String(h).padStart(2, '0')}:00`;
}

function updateTimeWindow(movedSlider) {
  let from = Number(timeFromSlider.value);
  let to = Number(timeToSlider.value);
  // Keep at least a 1-hour gap between the two handles rather than letting
  // them cross or collapse to a zero-width (and so unmatchable-by-design)
  // window; whichever handle the user is actively dragging wins and pushes
  // the other one ahead of/behind it.
  if (to - from < 1) {
    if (movedSlider === timeToSlider) {
      from = to - 1;
      timeFromSlider.value = from;
    } else {
      to = from + 1;
      timeToSlider.value = to;
    }
  }
  timeFromHidden.value = from;
  timeToHidden.value = to;
  // Mirrors the CSS thumb inset above (8px each side, out of the track's
  // full width) so the fill bar's edges land under the actual thumb
  // centers rather than the raw 0%-100% hour fraction.
  const fromFrac = from / 24;
  const toFrac = to / 24;
  timeWindowFill.style.left = `calc(8px + (100% - 16px) * ${fromFrac})`;
  timeWindowFill.style.width = `calc((100% - 16px) * ${toFrac - fromFrac})`;
  timeWindowLabel.textContent =
    (from === 0 && to === 24) ? t('All day') : `${fmtHour(from)} – ${fmtHour(to)}`;
}

function setTimeWindow(fromHour, toHour) {
  timeFromSlider.value = Math.max(0, Math.min(23, fromHour ?? 0));
  timeToSlider.value = Math.max(1, Math.min(24, toHour ?? 24));
  updateTimeWindow();
}

timeFromSlider.addEventListener('input', () => updateTimeWindow(timeFromSlider));
timeToSlider.addEventListener('input', () => updateTimeWindow(timeToSlider));
updateTimeWindow();

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
  updatePkkSingleCategoryLabel();
}
// Keeps the read-only "License category: X" label (single-PKK-profile state,
// see the PKK profile picker below) in sync with selectedCategory - a plain
// text snapshot taken once at profile-apply time would otherwise go stale the
// moment EXISTING_CONFIG's own setCategory() call runs later on /settings.
function updatePkkSingleCategoryLabel() {
  const el = document.getElementById('pkk-single-category');
  if (!el || el.dataset.active !== '1') return;
  const c = CATEGORIES.find((cat) => cat.id === selectedCategory);
  el.textContent = `${t('License category')}: ${c ? (c.code || ('Cat ' + c.id)) : ''}`;
}
function setCatRestOpen(open) {
  catRest.classList.toggle('open', open);
  catMoreBtn.textContent = open ? t('Fewer categories') : t('More categories');
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
wireReveal(document.getElementById('settings-pz-username'), document.getElementById('reveal-pz-username-settings'));
const ntfyInput = document.getElementById('ntfy_topic');
ntfyInput.value = NTFY_TOPIC;
wireReveal(ntfyInput, document.getElementById('reveal-ntfy'));

// ---- PKK profile picker (prefilled after QR login / on Settings, see
// build_pkk_prefill). Three presentations depending on account profile count:
//   0 profiles  -> pkk-manual-block only (unchanged: editable field + pills)
//   1 profile   -> pkk-single-block: the *same* #profile_number reveal input
//                  (moved here, not duplicated), made read-only, plus a plain
//                  non-clickable category label - the category is bound to
//                  the profile, not user-chosen
//   2+ profiles -> pkk-select-block: a <select> of masked "...last4 - code"
//                  options with a reveal toggle that swaps every option's
//                  label at once (updating the closed <select>'s own display
//                  for free, since it reads the selected <option>'s text)
const PKK_PROFILES = __PKK_PROFILES_JSON__;
if (PKK_PROFILES.length) {
  const pkkAutoBlock = document.getElementById('pkk-auto-block');
  const pkkManualBlock = document.getElementById('pkk-manual-block');
  const pkkSelectBlock = document.getElementById('pkk-select-block');
  const pkkSingleBlock = document.getElementById('pkk-single-block');
  const pkkProfileSelect = document.getElementById('pkk-profile-select');
  const pkkManualLink = document.getElementById('pkk-manual-link');
  const pkkAutoLink = document.getElementById('pkk-auto-link');
  const pkkRevealWrap = document.getElementById('pkk-reveal');
  const pkkManualCatLabel = document.getElementById('pkk-manual-cat-label');
  const pkkSingleCategory = document.getElementById('pkk-single-category');
  const multiProfile = PKK_PROFILES.length > 1;

  function applyPkkProfile(p) {
    pkkInput.value = p.pkkNumber;
    setCategory(p.categoryId);
    const isTop = CATEGORIES.some((c) => c.id === p.categoryId && TOP_CATEGORY_CODES.includes(c.code));
    if (!isTop) expandCatRest();
  }

  function maskedPkk(num) {
    const last4 = num.slice(-4);
    return '•'.repeat(Math.max(0, num.length - last4.length)) + last4;
  }

  function showSingleProfile() {
    pkkSelectBlock.style.display = 'none';
    pkkSingleBlock.style.display = 'block';
    pkkSingleCategory.dataset.active = '1';
    pkkSingleBlock.insertBefore(pkkRevealWrap, pkkSingleCategory);
    pkkInput.readOnly = true;
    pkkInput.type = 'password';
    pkkSync();
    applyPkkProfile(PKK_PROFILES[0]);
  }

  let selectRevealed = false;
  function refreshSelectOptionLabels() {
    Array.from(pkkProfileSelect.options).forEach((opt, i) => {
      const p = PKK_PROFILES[i];
      opt.textContent = `${selectRevealed ? p.pkkNumber : maskedPkk(p.pkkNumber)} — ${p.categoryCode}`;
    });
  }

  function showSelectProfiles() {
    pkkSingleBlock.style.display = 'none';
    pkkSelectBlock.style.display = 'block';
    refreshSelectOptionLabels();
    applyPkkProfile(PKK_PROFILES[Number(pkkProfileSelect.value || 0)]);
  }

  pkkAutoBlock.style.display = 'block';
  pkkManualBlock.style.display = 'none';

  if (multiProfile) {
    PKK_PROFILES.forEach((p, i) => {
      const opt = document.createElement('option');
      opt.value = String(i);
      pkkProfileSelect.appendChild(opt);
    });
    const selectRevealBtn = document.getElementById('reveal-pkk-select');
    const syncSelectReveal = () => { selectRevealBtn.innerHTML = selectRevealed ? EYE_OFF : EYE; };
    syncSelectReveal();
    selectRevealBtn.addEventListener('click', () => {
      selectRevealed = !selectRevealed;
      refreshSelectOptionLabels();
      syncSelectReveal();
    });
    pkkProfileSelect.addEventListener('change', () => applyPkkProfile(PKK_PROFILES[Number(pkkProfileSelect.value)]));
    showSelectProfiles();
  } else {
    showSingleProfile();
  }

  pkkManualLink.addEventListener('click', () => {
    pkkAutoBlock.style.display = 'none';
    pkkManualBlock.style.display = 'block';
    pkkAutoLink.style.display = 'block';
    if (!multiProfile) {
      pkkSingleCategory.dataset.active = '0';
      pkkManualBlock.insertBefore(pkkRevealWrap, pkkManualCatLabel);
      pkkInput.readOnly = false;
    }
  });
  pkkAutoLink.addEventListener('click', () => {
    pkkAutoBlock.style.display = 'block';
    pkkManualBlock.style.display = 'none';
    pkkAutoLink.style.display = 'none';
    if (multiProfile) {
      showSelectProfiles();
    } else {
      showSingleProfile();
    }
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
function fmtDate(d) { return d.toLocaleDateString(ikwI18n.lang() === 'pl' ? 'pl-PL' : 'en-GB', {day: 'numeric', month: 'short', year: 'numeric'}); }
function sameDay(a, b) { return !!a && !!b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate(); }
function renderCalendar() {
  calendar.innerHTML = '';
  const head = document.createElement('div'); head.className = 'cal-head';
  const prev = document.createElement('button'); prev.type = 'button'; prev.className = 'cal-nav'; prev.textContent = '‹';
  const title = document.createElement('div'); title.className = 'cal-title'; title.textContent = calView.toLocaleDateString(ikwI18n.lang() === 'pl' ? 'pl-PL' : 'en-GB', {month: 'long', year: 'numeric'});
  const next = document.createElement('button'); next.type = 'button'; next.className = 'cal-nav'; next.textContent = '›';
  prev.addEventListener('click', (e) => { e.stopPropagation(); calView = new Date(calView.getFullYear(), calView.getMonth() - 1, 1); renderCalendar(); });
  next.addEventListener('click', (e) => { e.stopPropagation(); calView = new Date(calView.getFullYear(), calView.getMonth() + 1, 1); renderCalendar(); });
  head.appendChild(prev); head.appendChild(title); head.appendChild(next);
  calendar.appendChild(head);
  const grid = document.createElement('div'); grid.className = 'cal-grid';
  (ikwI18n.lang() === 'pl' ? ['pon','wt','śr','czw','pt','sob','nd'] : DOW).forEach((d) => { const c = document.createElement('div'); c.className = 'cal-dow'; c.textContent = d; grid.appendChild(c); });
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
      e.stopPropagation(); selectedDate = date; dpValue.value = isoOf(date); dpInput.value = fmtDate(date);
      updateSearchStartBound(); closeCalendar();
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

// Independent optional lower bound for eligible slots.  It deliberately uses
// the same calendar language as the required booking date, while limiting
// selection to the government site's known search horizon.
const sdpInput = document.getElementById('search_start_date_display');
const sdpValue = document.getElementById('search_start_date');
const sdpCalendar = document.getElementById('search-start-calendar');
const clearSearchStartDate = document.getElementById('clear-search-start-date');
const searchHorizonDate = new Date(todayDate);
searchHorizonDate.setDate(searchHorizonDate.getDate() + 31);
let sdpView = new Date(todayDate.getFullYear(), todayDate.getMonth(), 1);
let selectedSearchStartDate = null;
function latestSearchStartDate() {
  if (!selectedDate) return null;
  const dayBeforeBooking = new Date(selectedDate);
  dayBeforeBooking.setDate(dayBeforeBooking.getDate() - 1);
  return dayBeforeBooking < searchHorizonDate ? dayBeforeBooking : searchHorizonDate;
}
function clearSearchStart() {
  selectedSearchStartDate = null;
  sdpValue.value = '';
  sdpInput.value = '';
  clearSearchStartDate.classList.remove('visible');
  closeSearchStartCalendar();
}
function updateSearchStartBound() {
  const latest = latestSearchStartDate();
  sdpInput.disabled = !latest || latest < todayDate;
  if (selectedSearchStartDate && (!latest || selectedSearchStartDate > latest)) clearSearchStart();
}
function renderSearchStartCalendar() {
  sdpCalendar.innerHTML = '';
  const head = document.createElement('div'); head.className = 'cal-head';
  const prev = document.createElement('button'); prev.type = 'button'; prev.className = 'cal-nav'; prev.textContent = '‹';
  const title = document.createElement('div'); title.className = 'cal-title'; title.textContent = sdpView.toLocaleDateString(ikwI18n.lang() === 'pl' ? 'pl-PL' : 'en-GB', {month: 'long', year: 'numeric'});
  const next = document.createElement('button'); next.type = 'button'; next.className = 'cal-nav'; next.textContent = '›';
  prev.addEventListener('click', (e) => { e.stopPropagation(); sdpView = new Date(sdpView.getFullYear(), sdpView.getMonth() - 1, 1); renderSearchStartCalendar(); });
  next.addEventListener('click', (e) => { e.stopPropagation(); sdpView = new Date(sdpView.getFullYear(), sdpView.getMonth() + 1, 1); renderSearchStartCalendar(); });
  head.appendChild(prev); head.appendChild(title); head.appendChild(next); sdpCalendar.appendChild(head);
  const grid = document.createElement('div'); grid.className = 'cal-grid';
  (ikwI18n.lang() === 'pl' ? ['pon','wt','śr','czw','pt','sob','nd'] : DOW).forEach((d) => { const c = document.createElement('div'); c.className = 'cal-dow'; c.textContent = d; grid.appendChild(c); });
  const startOffset = (new Date(sdpView.getFullYear(), sdpView.getMonth(), 1).getDay() + 6) % 7;
  const daysInMonth = new Date(sdpView.getFullYear(), sdpView.getMonth() + 1, 0).getDate();
  const prevDays = new Date(sdpView.getFullYear(), sdpView.getMonth(), 0).getDate();
  const latest = latestSearchStartDate();
  for (let i = 0; i < startOffset; i++) { const cell = document.createElement('div'); cell.className = 'cal-day muted disabled'; cell.textContent = prevDays - startOffset + 1 + i; grid.appendChild(cell); }
  for (let d = 1; d <= daysInMonth; d++) {
    const date = new Date(sdpView.getFullYear(), sdpView.getMonth(), d);
    const cell = document.createElement('div'); cell.className = 'cal-day'; cell.textContent = d;
    if (!latest || date < todayDate || date > latest) cell.classList.add('disabled');
    if (sameDay(date, todayDate)) cell.classList.add('today');
    if (sameDay(date, selectedSearchStartDate)) cell.classList.add('selected');
    if (latest && date >= todayDate && date <= latest) cell.addEventListener('click', (e) => {
      e.stopPropagation(); selectedSearchStartDate = date; sdpValue.value = isoOf(date); sdpInput.value = fmtDate(date);
      clearSearchStartDate.classList.add('visible'); closeSearchStartCalendar();
    });
    grid.appendChild(cell);
  }
  sdpCalendar.appendChild(grid);
}
function openSearchStartCalendar() { if (selectedSearchStartDate) sdpView = new Date(selectedSearchStartDate.getFullYear(), selectedSearchStartDate.getMonth(), 1); renderSearchStartCalendar(); sdpCalendar.classList.add('open'); }
function closeSearchStartCalendar() { sdpCalendar.classList.remove('open'); }
sdpInput.addEventListener('click', () => { sdpCalendar.classList.contains('open') ? closeSearchStartCalendar() : openSearchStartCalendar(); });
sdpInput.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeSearchStartCalendar(); });
document.addEventListener('click', (e) => { if (!document.getElementById('search-start-datepick').contains(e.target)) closeSearchStartCalendar(); });
clearSearchStartDate.addEventListener('click', (e) => { e.stopPropagation(); clearSearchStart(); });
updateSearchStartBound();

renderSelected();

if (EXISTING_CONFIG) {
  loginMethodSelect.value = EXISTING_CONFIG.login_method || 'mobywatel';
  document.getElementById('settings-pz-username').value = EXISTING_CONFIG.pz_username || '';
  if (EXISTING_CONFIG.pz_credential_present) {
    document.getElementById('password-status').textContent = t('Password saved securely by your operating system; its value is never shown here.');
  }
  updateAuthFields();
  const pageTitle = document.getElementById('page-title');
  pageTitle.textContent = t('Settings');
  pageTitle.style.marginBottom = '1.6rem'; // replaces the gap the (now-hidden) lead paragraph used to provide
  document.getElementById('page-lead').style.display = 'none';
  document.getElementById('submit-btn').textContent = t('Save changes');

  // Only shown once a config already exists (i.e. this is /settings, not
  // first-run /setup) — there's no dashboard to go "back" to otherwise.
  const closeBtn = document.getElementById('wiz-close-btn');
  closeBtn.style.display = 'flex';
  closeBtn.addEventListener('click', () => { ikwGoDashboard('ikw-settings-close'); });

  pkkInput.value = EXISTING_CONFIG.profile_number || '';
  if (pkkInput.value) { pkkInput.type = 'password'; pkkSync(); }
  // The dropdown (2+ PKK profiles) otherwise stays visually on whichever
  // profile applyPkkProfile() picked at load (index 0) even though the lines
  // above just overwrote the actually-submitted number/category with the
  // saved config's own values - sync the visible selection so it doesn't
  // silently disagree with what Save would submit.
  if (PKK_PROFILES.length > 1) {
    const savedProfileIdx = PKK_PROFILES.findIndex((p) => p.pkkNumber === EXISTING_CONFIG.profile_number);
    if (savedProfileIdx >= 0) document.getElementById('pkk-profile-select').value = String(savedProfileIdx);
  }

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

  if (EXISTING_CONFIG.search_start_date) {
    const parts = EXISTING_CONFIG.search_start_date.split('-').map(Number);
    if (parts.length === 3 && parts.every((n) => !Number.isNaN(n))) {
      const configuredDate = new Date(parts[0], parts[1] - 1, parts[2]);
      if (configuredDate >= todayDate && configuredDate <= searchHorizonDate) {
        selectedSearchStartDate = configuredDate;
        sdpValue.value = EXISTING_CONFIG.search_start_date;
        sdpInput.value = fmtDate(configuredDate);
        clearSearchStartDate.classList.add('visible');
      }
    }
  }

  updateSearchStartBound();

  setPollIntervalSeconds(EXISTING_CONFIG.poll_interval_seconds || 60);
  setTimeWindow(EXISTING_CONFIG.earliest_slot_hour, EXISTING_CONFIG.latest_slot_hour);
  setSwitch(phoneAlertsSwitch, EXISTING_CONFIG.phone_alerts !== false);
  setSwitch(phoneAlertsReloginSwitch, EXISTING_CONFIG.phone_alerts_relogin !== false);
  setSwitch(document.getElementById('headless_pz_login'), EXISTING_CONFIG.headless_pz_login === true);
  setSwitch(document.getElementById('auto_open_browser'), EXISTING_CONFIG.auto_open_browser !== false);
  setSwitch(autoSelectSlotSwitch, EXISTING_CONFIG.auto_select_slot === true);
  setSwitch(autoConfirmSwitch, EXISTING_CONFIG.auto_confirm_reschedule === true);
  applyAutoConfirmDim();
  applyNtfyDim();

  // Nothing to reset on a fresh /setup with no saved config yet.
  document.getElementById('reset-account-block').style.display = 'block';
}

window.addEventListener('ikw-language-changed', () => {
  centerLabelHeading.textContent = ikwI18n.lang() === 'pl'
    ? `Ośrodki WORD do obserwowania (${CENTERS.length} w kraju)`
    : `WORD centers to watch (${CENTERS.length} nationwide)`;
  renderSelected();
  updatePollIntervalDisplay();
  updateTimeWindow();
  renderCalendar();
  renderSearchStartCalendar();
  if (EXISTING_CONFIG) {
    document.getElementById('page-title').textContent = t('Settings');
    document.getElementById('submit-btn').textContent = t('Save changes');
  }
});

document.getElementById('copy-ntfy').addEventListener('click', () => {
  navigator.clipboard.writeText('https://ntfy.sh/' + ntfyInput.value);
});

const testPushBtn = document.getElementById('test-push-btn');
const testPushStatus = document.getElementById('test-push-status');
testPushBtn.addEventListener('click', async () => {
  testPushBtn.disabled = true;
  testPushStatus.textContent = t('Sending...');
  try {
    const res = await fetch('/test-push', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({topic: ntfyInput.value}),
    });
    const data = await res.json();
    testPushStatus.textContent = data.ok ? t('Sent — check your phone.') : t(data.error || 'Failed to send.');
  } catch (e) {
    testPushStatus.textContent = t('Failed to send.');
  } finally {
    testPushBtn.disabled = false;
  }
});

const resetAccountBtn = document.getElementById('reset-account-btn');
resetAccountBtn.addEventListener('click', async () => {
  if (!confirm(t("This logs you out and clears your saved settings. You'll need to scan the QR code again. Continue?"))) return;
  resetAccountBtn.disabled = true;
  try {
    const response = await fetch('/reset-account', {method: 'POST', headers: {'Content-Type': 'application/json'}});
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || 'Reset failed');
    if (data.warning) alert(data.warning);
    // Always a full top-level navigation, even when embedded: reset clears
    // config.json and session.json, so what comes next is the login screen,
    // not just an updated settings form — there's no "back to dashboard" to
    // return to inside the modal.
    if (IKW_EMBEDDED) { window.parent.postMessage({ type: 'ikw-settings-reset' }, window.location.origin); }
    else { window.location.href = '/'; }
  } catch (e) {
    resetAccountBtn.disabled = false;
    alert(t('Reset failed — check the log.'));
  }
});

document.getElementById('form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById('error');
  errorEl.textContent = '';
  errorEl.classList.remove('show');
  try {
    const examTypes = selectedExamTypes();
    if (!examTypes.length) throw new Error(t('Pick at least one exam type.'));

    const orgIds = Array.from(selectedIds);
    if (!orgIds.length) throw new Error(t('Pick at least one WORD center.'));
    if (orgIds.length > MAX_CENTERS) throw new Error(`Pick at most ${MAX_CENTERS} WORD centers — the site's search only accepts ${MAX_CENTERS} at a time.`);

    const profileNumber = pkkInput.value.trim();
    if (!profileNumber) throw new Error(t('PKK number is required.'));

    const category = selectedCategory;
    if (!category) throw new Error(t('Pick a license category.'));

    const currentSlotDate = dpValue.value;
    if (!currentSlotDate) throw new Error(t('Pick the date of your current booked slot.'));

    const body = {
      login_method: loginMethodSelect.value,
      pz_username: document.getElementById('settings-pz-username').value.trim(),
      pz_password: document.getElementById('settings-pz-password').value,
      profile_number: profileNumber,
      organization_ids: orgIds,
      category: category,
      exam_types: examTypes,
      current_slot_date: currentSlotDate,
      search_start_date: sdpValue.value,
      poll_interval_seconds: parseInt(document.getElementById('poll_interval_seconds').value, 10),
      earliest_slot_hour: parseInt(timeFromHidden.value, 10),
      latest_slot_hour: parseInt(timeToHidden.value, 10),
      phone_alerts: switchOn('phone-alerts'),
      phone_alerts_relogin: switchOn('phone-alerts-relogin'),
      headless_pz_login: switchOn('headless_pz_login'),
      auto_open_browser: switchOn('auto_open_browser'),
      auto_select_slot: switchOn('auto_select_slot'),
      auto_confirm_reschedule: switchOn('auto_confirm_reschedule'),
      ntfy_topic: ntfyInput.value,
    };

    const res = await fetch('/setup', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || t('Save failed.'));
    if (data.warning) alert(t(data.warning));

    ikwGoDashboard('ikw-settings-saved');
  } catch (err) {
    errorEl.textContent = t(err.message);
    errorEl.classList.add('show');
  }
});
</script>
</body>
</html>
"""

# Keep the templates readable as complete HTML documents above while placing
# the shared localization bootstrap in each page's head before it is painted.
LOGIN_PAGE = LOGIN_PAGE.replace("<head>", "<head>" + LOCALIZATION_SCRIPT, 1)
WIZARD_PAGE = WIZARD_PAGE.replace("<head>", "<head>" + LOCALIZATION_SCRIPT, 1)
