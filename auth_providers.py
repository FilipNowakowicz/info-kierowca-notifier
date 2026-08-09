"""Authentication providers used by the generic relogin helper."""
import enum
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import cdp_client
from sms_provider import OTP_RE


class PZState(enum.Enum):
    START = "START"
    OPEN_LOGIN = "OPEN_LOGIN"
    SELECT_IDENTITY_PROVIDER = "SELECT_IDENTITY_PROVIDER"
    SELECT_PROFIL_ZAUFANY = "SELECT_PROFIL_ZAUFANY"
    WAIT_FOR_CREDENTIAL_FORM = "WAIT_FOR_CREDENTIAL_FORM"
    SUBMIT_CREDENTIALS = "SUBMIT_CREDENTIALS"
    WAIT_FOR_SMS_CHALLENGE = "WAIT_FOR_SMS_CHALLENGE"
    WAIT_FOR_SMS = "WAIT_FOR_SMS"
    SUBMIT_SMS = "SUBMIT_SMS"
    WAIT_FOR_AUTH_REDIRECT = "WAIT_FOR_AUTH_REDIRECT"
    WAIT_FOR_INFO_KIEROWCA_SESSION = "WAIT_FOR_INFO_KIEROWCA_SESSION"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


@dataclass(frozen=True)
class AuthenticationFailure(Exception):
    reason: str
    state: PZState

    def __str__(self):
        return f"{self.reason} in {self.state.value}"


DEFAULT_TIMEOUTS = {
    PZState.SELECT_IDENTITY_PROVIDER: 20,
    PZState.SELECT_PROFIL_ZAUFANY: 20,
    PZState.WAIT_FOR_CREDENTIAL_FORM: 25,
    PZState.WAIT_FOR_SMS_CHALLENGE: 25,
    PZState.WAIT_FOR_SMS: 120,
    PZState.WAIT_FOR_AUTH_REDIRECT: 40,
    PZState.WAIT_FOR_INFO_KIEROWCA_SESSION: 20,
}

# PZePUAP can timestamp an SMS shortly before the browser renders its challenge.
# Codes already visible before credential submission are separately drained into
# ``used_codes``, so this tolerance covers delivery/render skew without allowing
# a previously observed code to be reused.
PZ_SMS_ATTEMPT_TOLERANCE_SECONDS = 20


class ProfilZaufanyProvider:
    def __init__(self, browser, sms_provider, username, password, *, monotonic=None,
                 wall_clock=None, sleep=None, timeouts=None, overall_timeout=240,
                 otp_settle_delay=5, otp_confirm_delay=0, logger=None):
        self.browser = browser
        self.sms_provider = sms_provider
        self.username = username
        self.password = password
        self.monotonic = monotonic or time.monotonic
        self.wall_clock = wall_clock or time.time
        self.sleep = sleep or time.sleep
        self.timeouts = dict(DEFAULT_TIMEOUTS)
        self.timeouts.update(timeouts or {})
        self.overall_timeout = overall_timeout
        self.otp_settle_delay = otp_settle_delay
        self.otp_confirm_delay = otp_confirm_delay
        self.logger = logger or (lambda message: print(message))
        self.state = PZState.START
        self.used_codes = set()
        self.cancelled = None
        self.started_at = None
        self._global_deadline = None

    def _transition(self, state):
        self.logger(f"PZ state: {self.state.value} -> {state.value}")
        self.state = state

    def _fail(self, reason):
        failed_at = self.state
        self._transition(PZState.FAILED)
        raise AuthenticationFailure(reason, failed_at)

    def _wait(self, state, probe, timeout_reason):
        self._transition(state)
        deadline = min(self._global_deadline, self.monotonic() + self.timeouts[state])
        while self.monotonic() < deadline:
            if self.cancelled and self.cancelled():
                self._fail("restart_requested")
            if self.browser.closed():
                self._fail("browser_closed")
            try:
                outcome = probe()
            except cdp_client.StaleTargetError:
                self._fail("auth_target_lost")
            except cdp_client.ExecutionContextLostError:
                # Cross-origin navigation replaces the target's JavaScript
                # context without replacing its target ID. Retry within this
                # state's existing deadline.
                outcome = None
            if outcome is not None:
                return outcome
            self.sleep(min(0.5, max(0, deadline - self.monotonic())))
        if self.monotonic() >= self._global_deadline:
            self._fail("global_deadline")
        self._fail(timeout_reason() if callable(timeout_reason) else timeout_reason)

    def _exclude_existing_sms_codes(self):
        """Snapshot recent codes before credentials can request a new SMS."""
        deadline = min(self._global_deadline, self.monotonic() + 5)
        while self.monotonic() < deadline:
            existing = self.sms_provider.get_latest_pz_code(
                self.started_at - 270, self.used_codes
            )
            if existing.status == "found" and existing.code:
                self.used_codes.add(existing.code)
                continue
            if existing.status == "switching":
                self.sleep(min(0.25, max(0, deadline - self.monotonic())))
                continue
            return

    def _bounded_pause(self, seconds):
        deadline = min(self._global_deadline,
                       self.monotonic() + max(0, seconds))
        while self.monotonic() < deadline:
            if self.cancelled and self.cancelled():
                self._fail("restart_requested")
            if self.browser.closed():
                self._fail("browser_closed")
            self.sleep(min(0.5, max(0, deadline - self.monotonic())))
        if self.monotonic() >= self._global_deadline:
            self._fail("global_deadline")

    def authenticate(self):
        self.started_at = self.wall_clock()
        self._global_deadline = self.monotonic() + self.overall_timeout
        self._transition(PZState.OPEN_LOGIN)
        if self.cancelled and self.cancelled():
            self._fail("restart_requested")
        self.browser.open_login()
        self._wait(PZState.SELECT_IDENTITY_PROVIDER, self.browser.select_identity_provider,
                   "identity_provider_timeout")
        self._wait(PZState.SELECT_PROFIL_ZAUFANY, self.browser.select_profil_zaufany,
                   "profil_zaufany_chooser_timeout")
        form = self._wait(PZState.WAIT_FOR_CREDENTIAL_FORM, self.browser.credential_form_status,
                          "credential_form_timeout")
        if form == "expired":
            self._fail("auth_transaction_expired")
        if form == "no_valid_profile":
            self._fail("profil_zaufany_inactive")
        if form != "ready":
            self._fail("unexpected_auth_page")

        # Wait through conversation switching and drain every recent candidate
        # before credentials can request a new SMS.
        self._exclude_existing_sms_codes()
        self._transition(PZState.SUBMIT_CREDENTIALS)
        credentials_submitted_at = self.wall_clock()
        self.browser.submit_credentials(self.username, self.password)
        challenge = self._wait(PZState.WAIT_FOR_SMS_CHALLENGE, self.browser.challenge_status,
                               "sms_challenge_timeout")
        if challenge == "invalid_credentials":
            self._fail("invalid_credentials")
        if challenge == "expired":
            self._fail("auth_transaction_expired")
        if challenge == "no_valid_profile":
            self._fail("profil_zaufany_inactive")
        if challenge != "sms":
            self._fail("unexpected_auth_page")
        stale_sms_seen = [False]
        def sms_probe():
            result = self.sms_provider.get_latest_pz_code(
                credentials_submitted_at, self.used_codes,
                tolerance_seconds=PZ_SMS_ATTEMPT_TOLERANCE_SECONDS,
            )
            if result.status == "found":
                return result
            if result.status in ("not_paired", "page_structure_unsupported"):
                self._fail("sms_provider_unavailable")
            if result.status in ("messages_target_lost", "messages_target_unavailable",
                                 "unexpected_messages_origin"):
                self._fail("messages_target_lost")
            if result.status == "stale_otp":
                stale_sms_seen[0] = True
            return None

        sms = self._wait(PZState.WAIT_FOR_SMS, sms_probe,
                         lambda: "stale_sms" if stale_sms_seen[0] else "sms_timeout")
        if not sms.code or not OTP_RE.fullmatch(sms.code):
            self._fail("stale_sms")
        self.used_codes.add(sms.code)
        self._transition(PZState.SUBMIT_SMS)
        self._bounded_pause(self.otp_settle_delay)
        self.browser.enter_otp(sms.code)
        self._bounded_pause(self.otp_confirm_delay)
        self.browser.confirm_otp(sms.code)
        redirect = self._wait(PZState.WAIT_FOR_AUTH_REDIRECT, self.browser.redirect_status,
                              "auth_redirect_timeout")
        if redirect == "otp_rejected":
            self._fail("otp_rejected")
        if redirect == "expired":
            self._fail("auth_transaction_expired")
        if redirect == "no_valid_profile":
            self._fail("profil_zaufany_inactive")
        if redirect != "redirected":
            self._fail("unexpected_auth_page")
        cookies = self._wait(PZState.WAIT_FOR_INFO_KIEROWCA_SESSION,
                             self.browser.session_cookies, "session_capture_timeout")
        self._transition(PZState.SUCCESS)
        return cookies


class MObywatelProvider:
    def __init__(self, wait_for_cookies):
        self.wait_for_cookies = wait_for_cookies

    def authenticate(self):
        return self.wait_for_cookies()


def allowed_pz_origin(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(
        host == suffix or host.endswith("." + suffix)
        for suffix in ("login.gov.pl", "pz.gov.pl")
    )


def allowed_auth_redirect(url):
    parsed = urlparse(url); host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(
        host == suffix or host.endswith("." + suffix)
        for suffix in ("gov.pl", "pwpw.pl", "info-kierowca.pl")
    )


def transient_initial_page(url):
    parsed = urlparse(url)
    return (not url or url == "about:blank" or
            (parsed.scheme == "chrome" and parsed.netloc == "newtab"))


CLICK_FUNCTION = r"""
function(labels) {
  var visible = function(e) { var r=e.getBoundingClientRect(); var s=getComputedStyle(e);
    return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none'; };
  var normalize = function(text) { return (text||'').replace(/\s+/g,' ').trim().toLowerCase(); };
  var controls = [];
  Array.from(document.querySelectorAll('button,a,[role="button"]')).forEach(function(node) {
    var control = node;
    while (control.parentElement &&
           control.parentElement.matches('button,a,[role="button"]')) {
      control = control.parentElement;
    }
    if (visible(control) && controls.indexOf(control) < 0) controls.push(control);
  });
  for (var i=0;i<labels.length;i++) {
    var label=normalize(labels[i]);
    var exact=controls.filter(function(e){return normalize(e.innerText||e.textContent)===label;});
    var matches=exact.length ? exact : controls.filter(function(e){
      return normalize(e.innerText||e.textContent).indexOf(label)>=0;
    });
    if (matches.length===1) { matches[0].click(); return {status:'clicked'}; }
    if (matches.length>1) return {status:'ambiguous'};
  }
  return {status:'not_found'};
}
"""

IDENTITY_PROVIDER_FUNCTION = r"""
function() {
  var visible = function(e) { var r=e.getBoundingClientRect(); var s=getComputedStyle(e);
    return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none'; };
  var cards = Array.from(document.querySelectorAll('mat-card.auth-card'));
  var govCard = cards.find(function(e) {
    var text=(e.innerText||e.textContent||'').trim().toLowerCase();
    return visible(e) && text.indexOf('login.gov.pl')>=0;
  });
  if (govCard) { govCard.click(); return {clicked:true}; }
  var controls = Array.from(document.querySelectorAll('button,a,[role="button"]'));
  var login = controls.find(function(e) {
    var text=(e.innerText||e.textContent||'').trim().toLowerCase();
    return visible(e) && text.indexOf('zaloguj się')>=0;
  });
  if (login) { login.click(); return null; }
  return null;
}
"""

FORM_STATUS_FUNCTION = r"""
function() {
  var body=(document.body && document.body.innerText || '').toLowerCase();
  if (body.indexOf('nie masz ważnego profilu zaufanego')>=0 ||
      body.indexOf('nie masz waznego profilu zaufanego')>=0) return 'no_valid_profile';
  if (document.querySelector('.alertPage,[class*="alertPage"],.wkProcessUsed') ||
      body.indexOf('został już użyty')>=0 || body.indexOf('wkprocessused')>=0) return 'expired';
  var u=document.querySelector('#username,input[formcontrolname="login"],input[name="login"],#login,input[data-testid="username-input"] input');
  var p=document.querySelector('#password,input[formcontrolname="password"],input[name="password"],input[data-testid="password-input"] input');
  return u && p ? 'ready' : null;
}
"""

PREPARE_CREDENTIAL_FIELD_FUNCTION = r"""
function(kind) {
  var u=document.querySelector('#username,input[formcontrolname="login"],input[name="login"],#login,input[data-testid="username-input"] input');
  var p=document.querySelector('#password,input[formcontrolname="password"],input[name="password"],input[data-testid="password-input"] input');
  if (!u || !p || u.disabled || p.disabled) return 'fields_missing';
  var field=kind === 'username' ? u : p;
  field.focus(); field.select();
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(field,'');
  field.dispatchEvent(new Event('input',{bubbles:true}));
  return 'ready';
}
"""

SUBMIT_CREDENTIALS_FUNCTION = r"""
function(username,password) {
  var u=document.querySelector('#username,input[formcontrolname="login"],input[name="login"],#login,input[data-testid="username-input"] input');
  var p=document.querySelector('#password,input[formcontrolname="password"],input[name="password"],input[data-testid="password-input"] input');
  if (!u || !p || u.disabled || p.disabled) return 'fields_missing';
  if (u.value !== username || p.value !== password) return 'value_mismatch';
  var form=u.form || p.form;
  if (u.form && p.form && u.form !== p.form) return 'form_mismatch';
  var root=form || document;
  var b=root.querySelector('button[data-testid="login-confirm-btn"],button[aria-label="Zaloguj się"],button[type="submit"].gds-button--primary');
  if (b && !b.disabled) b.click();
  else if (form && form.requestSubmit) form.requestSubmit();
  else return 'submit_missing';
  return 'submitted';
}
"""

CHALLENGE_STATUS_FUNCTION = r"""
function() {
  var body=(document.body && document.body.innerText || '').toLowerCase();
  if (body.indexOf('nie masz ważnego profilu zaufanego')>=0 ||
      body.indexOf('nie masz waznego profilu zaufanego')>=0) return 'no_valid_profile';
  if (document.querySelector('input[data-testid="sms-code-input"],#smsInput')) return 'sms';
  if (document.querySelector('.alertPage,[class*="alertPage"],.wkProcessUsed') || body.indexOf('został już użyty')>=0) return 'expired';
  if ((body.indexOf('nieprawidł')>=0 || body.indexOf('błędn')>=0) &&
      document.querySelector('#password,input[type="password"]')) return 'invalid_credentials';
  return null;
}
"""

PREPARE_OTP_FIELD_FUNCTION = r"""
function() {
  var i=document.querySelector('input[data-testid="sms-code-input"],#smsInput');
  if (!i || i.disabled) return 'field_missing';
  i.focus(); i.select();
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,'');
  i.dispatchEvent(new Event('input',{bubbles:true}));
  return 'ready';
}
"""

VERIFY_OTP_FUNCTION = r"""
function(code) {
  var i=document.querySelector('input[data-testid="sms-code-input"],#smsInput');
  return i && i.value === code ? 'ready' : 'value_mismatch';
}
"""

SUBMIT_OTP_FUNCTION = r"""
function(code) {
  var i=document.querySelector('input[data-testid="sms-code-input"],#smsInput');
  if (!i || i.value !== code) return 'value_mismatch';
  var form=i.form;
  var root=form || i.closest('[role="dialog"]') || document;
  var b=root.querySelector('button[data-testid="sms-code-submit-btn"],button[aria-label="Potwierdź"]');
  if (b && !b.disabled) b.click();
  else if (form && form.requestSubmit) form.requestSubmit();
  else return 'submit_missing';
  return 'submitted';
}
"""

REDIRECT_STATUS_FUNCTION = r"""
function() {
  var body=(document.body && document.body.innerText || '').toLowerCase();
  if (body.indexOf('nie masz ważnego profilu zaufanego')>=0 ||
      body.indexOf('nie masz waznego profilu zaufanego')>=0) return 'no_valid_profile';
  if (document.querySelector('input[data-testid="sms-code-input"],#smsInput')) {
    if (body.indexOf('nieprawidł')>=0 || body.indexOf('błędn')>=0) return 'otp_rejected';
    return null;
  }
  if (document.querySelector('.alertPage,[class*="alertPage"],.wkProcessUsed')) return 'expired';
  return location.hostname.endsWith('info-kierowca.pl') ? 'redirected' : null;
}
"""


class CDPProfilZaufanyBrowser:
    """All page operations retain and address one explicit auth target ID."""
    def __init__(self, host, port, target, chrome_proc, login_url):
        self.host, self.port, self.target = host, port, target
        self.chrome_proc, self.login_url = chrome_proc, login_url

    def closed(self):
        return self.chrome_proc.poll() is not None

    def _current(self):
        return cdp_client.get_page_target(self.host, self.port, self.target.id)

    def _call(self, function, arguments=None, require_pz_origin=False):
        current = self._current()
        if require_pz_origin and not allowed_pz_origin(current.url):
            raise AuthenticationFailure("unexpected_auth_page", PZState.FAILED)
        return cdp_client.call_function_in_target(
            self.host, self.port, self.target, function, arguments
        )

    def open_login(self):
        cdp_client.navigate_target(self.host, self.port, self.target, self.login_url)

    def select_identity_provider(self):
        current = self._current()
        if allowed_pz_origin(current.url):
            return "ready"
        if transient_initial_page(current.url):
            return None
        if not allowed_auth_redirect(current.url):
            raise AuthenticationFailure("unexpected_auth_page", PZState.SELECT_IDENTITY_PROVIDER)
        return self._call(IDENTITY_PROVIDER_FUNCTION)

    def select_profil_zaufany(self):
        current = self._current()
        if not allowed_pz_origin(current.url):
            if allowed_auth_redirect(current.url):
                return None
            raise AuthenticationFailure("unexpected_auth_page", PZState.SELECT_PROFIL_ZAUFANY)
        if self._call(FORM_STATUS_FUNCTION, require_pz_origin=True) == "ready":
            return "ready"
        result = self._call(CLICK_FUNCTION, [["profil zaufany"]], require_pz_origin=True)
        return "clicked" if result and result.get("status") == "clicked" else None

    def credential_form_status(self):
        return self._call(FORM_STATUS_FUNCTION, require_pz_origin=True)

    def submit_credentials(self, username, password):
        for kind, value in (("username", username), ("password", password)):
            result = self._call(PREPARE_CREDENTIAL_FIELD_FUNCTION, [kind], True)
            if result != "ready":
                raise AuthenticationFailure("credential_form_timeout", PZState.SUBMIT_CREDENTIALS)
            cdp_client.insert_text_in_target(
                self.host, self.port, self.target, value
            )
        result = self._call(SUBMIT_CREDENTIALS_FUNCTION, [username, password], True)
        if result != "submitted":
            raise AuthenticationFailure("credential_form_timeout", PZState.SUBMIT_CREDENTIALS)

    def challenge_status(self):
        return self._call(CHALLENGE_STATUS_FUNCTION, require_pz_origin=True)

    def enter_otp(self, code):
        if not OTP_RE.fullmatch(code):
            raise AuthenticationFailure("sms_challenge_timeout", PZState.SUBMIT_SMS)
        if self._call(PREPARE_OTP_FIELD_FUNCTION, require_pz_origin=True) != "ready":
            raise AuthenticationFailure("sms_challenge_timeout", PZState.SUBMIT_SMS)
        cdp_client.insert_text_in_target(self.host, self.port, self.target, code)
        if self._call(VERIFY_OTP_FUNCTION, [code], True) != "ready":
            raise AuthenticationFailure("sms_challenge_timeout", PZState.SUBMIT_SMS)

    def confirm_otp(self, code):
        if self._call(SUBMIT_OTP_FUNCTION, [code], True) != "submitted":
            raise AuthenticationFailure("sms_challenge_timeout", PZState.SUBMIT_SMS)

    def redirect_status(self):
        current = self._current()
        parsed = urlparse(current.url)
        if (parsed.hostname or "").endswith("info-kierowca.pl"):
            return "redirected"
        if (parsed.hostname or "").endswith("pz.gov.pl") and \
                parsed.path == "/ui/au/alert" and \
                "case=pzip-wk-login-no-profile" in parsed.query:
            return "no_valid_profile"
        if not allowed_auth_redirect(current.url):
            raise AuthenticationFailure("unexpected_auth_page", PZState.WAIT_FOR_AUTH_REDIRECT)
        if not allowed_pz_origin(current.url):
            return None
        return self._call(REDIRECT_STATUS_FUNCTION, require_pz_origin=True)

    def session_cookies(self):
        raw = cdp_client.fetch_cookies(self.host, self.port)
        cookies = cdp_client.extract_info_kierowca_cookies(raw)
        return cookies if cdp_client.COOKIE_NAMES <= cookies.keys() else None
