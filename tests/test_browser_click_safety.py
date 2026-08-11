import contextlib
import io
import json
import shutil
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from info_kierowca_notifier.auth import session as auto_refresh_session
from info_kierowca_notifier.auth import providers as auth_providers
from info_kierowca_notifier.booking import reschedule as open_logged_in_browser
from info_kierowca_notifier.browser import clicking


class BrowserClickSafetyTests(unittest.TestCase):
    def test_shared_helpers_reject_sensitive_or_unrelated_controls(self):
        js = clicking.CLICKABLE_HELPERS_JS
        for expected in (
            "tag === 'header'", "tag === 'footer'", "tag === 'nav'",
            "logo|footer|header|navigation|nav|language",
            "__ikw_hasExcludedText", "załóż konto", "nie pamiętam hasła",
            "login.gov.pl", "__ikw_pageIsKnownError", "known_error_page",
            r"\b(wstecz", "new URL(href, location.href)", "__ikw_safeMatchedText",
            "[redacted sensitive text]",
        ):
            self.assertIn(expected, js)

    def _run_click_scenario(self, elements, label, *, known_error=False):
        if not shutil.which("node"):
            self.skipTest("Node.js is required for browser-helper behavior tests")
        harness = r"""
const clicked = [];
function makeElement(spec, parent) {
  const el = {
    tagName: (spec.tag || 'DIV').toUpperCase(), id: spec.id || '',
    className: spec.className || '', innerText: spec.text || '',
    textContent: spec.text || '', parentElement: parent || null,
    href: spec.href || '', disabled: !!spec.disabled,
    offsetWidth: spec.hidden ? 0 : 10, offsetHeight: spec.hidden ? 0 : 10,
    getClientRects: () => spec.hidden ? [] : [1],
    getAttribute: function(name) {
      if (name === 'href') return this.href;
      if (name === 'role') return spec.role || '';
      if (name === 'aria-label') return spec.ariaLabel || '';
      if (name === 'aria-disabled') return spec.ariaDisabled || '';
      return '';
    },
    click: () => clicked.push(spec.name || spec.text || spec.tag || 'element')
  };
  el._cursor = spec.cursor || '';
  return el;
}
const specs = SCENARIO.elements;
const made = {};
function build(name) {
  if (made[name]) return made[name];
  const spec = specs[name];
  return made[name] = makeElement(spec, spec.parent ? build(spec.parent) : null);
}
Object.keys(specs).forEach(build);
global.window = {getComputedStyle: el => ({
  display: 'block', visibility: 'visible', opacity: '1', cursor: el._cursor
})};
global.location = {origin: 'https://login.gov.pl', pathname: '/auth', hostname: 'login.gov.pl', href: 'https://login.gov.pl/auth?otp=123456'};
global.document = {
  body: makeElement({tag: 'body', text: SCENARIO.knownError ? 'Strona błędu' : ''}),
  querySelector: () => SCENARIO.knownError ? {} : null,
  querySelectorAll: () => (SCENARIO.candidates || Object.keys(specs)).map(name => made[name])
};
RESULT = __ikw_clickByText(SCENARIO.label, 'button, a, [role="button"]', false, false);
console.log(JSON.stringify({result: RESULT, clicked}));
"""
        scenario = {"elements": elements, "label": label, "knownError": known_error}
        script = (
            "const SCENARIO = " + json.dumps(scenario, ensure_ascii=False) + ";\n" +
            clicking.CLICKABLE_HELPERS_JS + "\n" + harness
        )
        completed = subprocess.run(
            ["node", "-e", script], check=True, capture_output=True, text=True
        )
        return json.loads(completed.stdout)

    def test_login_button_is_not_rejected_by_sibling_recovery_links(self):
        result = self._run_click_scenario({
            "form": {"tag": "form", "text": "Zaloguj się Nie pamiętam hasła? Załóż konto"},
            "login": {"tag": "button", "text": "Zaloguj się", "parent": "form", "name": "login"},
            "forgot": {"tag": "a", "text": "Nie pamiętam hasła?", "parent": "form"},
            "create": {"tag": "a", "text": "Załóż konto", "parent": "form"},
        }, "Zaloguj się")
        self.assertTrue(result["result"]["clicked"])
        self.assertEqual(result["clicked"], ["login"])

    def test_dangerous_textual_control_is_rejected(self):
        result = self._run_click_scenario({
            "forgot": {"tag": "a", "text": "Nie pamiętam hasła?", "name": "forgot"},
        }, "Nie pamiętam hasła")
        self.assertEqual(result["result"]["reason"], "not_found")
        self.assertEqual(result["clicked"], [])

    def test_header_and_footer_controls_are_rejected(self):
        for region in ("header", "footer"):
            with self.subTest(region=region):
                result = self._run_click_scenario({
                    "region": {"tag": region, "text": "Zaloguj się"},
                    "login": {"tag": "button", "text": "Zaloguj się", "parent": "region"},
                }, "Zaloguj się")
                self.assertEqual(result["result"]["reason"], "not_found")

    def test_unrelated_gov_link_is_rejected_but_login_gov_is_eligible(self):
        unrelated = self._run_click_scenario({
            "link": {"tag": "a", "text": "Zaloguj się", "href": "https://www.gov.pl/help"},
        }, "Zaloguj się")
        self.assertEqual(unrelated["result"]["reason"], "not_found")
        login = self._run_click_scenario({
            "link": {"tag": "a", "text": "Zaloguj się", "href": "https://login.gov.pl/auth", "name": "login"},
        }, "Zaloguj się")
        self.assertTrue(login["result"]["clicked"])

    def test_distinct_matching_controls_are_ambiguous(self):
        result = self._run_click_scenario({
            "first": {"tag": "button", "text": "Zaloguj przez bank"},
            "second": {"tag": "button", "text": "Zaloguj przez aplikację"},
        }, "Zaloguj")
        self.assertEqual(result["result"]["reason"], "ambiguous_match")
        self.assertEqual(result["clicked"], [])

    def test_matching_descendants_resolving_to_one_button_are_deduplicated(self):
        result = self._run_click_scenario({
            "button": {"tag": "button", "text": "Zaloguj się", "name": "button"},
            "label": {"tag": "span", "text": "Zaloguj się", "parent": "button"},
        }, "Zaloguj się")
        self.assertTrue(result["result"]["clicked"])
        self.assertEqual(result["clicked"], ["button"])

    def test_known_error_page_remains_non_clickable(self):
        result = self._run_click_scenario({
            "login": {"tag": "button", "text": "Zaloguj się"},
        }, "Zaloguj się", known_error=True)
        self.assertEqual(result["result"]["reason"], "known_error_page")
        self.assertEqual(result["clicked"], [])

    def test_browser_diagnostics_redact_sensitive_text_and_query_strings(self):
        result = self._run_click_scenario({
            "otp": {"tag": "button", "text": "OTP 123456", "href": "https://login.gov.pl/next?otp=123456"},
        }, "OTP")
        diagnostics = result["result"]
        self.assertEqual(diagnostics["matched_text"], "[redacted sensitive text]")
        self.assertEqual(diagnostics["page_url"], "https://login.gov.pl/auth")
        self.assertEqual(diagnostics["href"], "https://login.gov.pl/next")

    def test_auto_click_returns_structured_diagnostics_only_when_clicked(self):
        clicked = {
            "clicked": True, "requested_label": "gov.pl", "matched_text": "Zaloguj przez gov.pl",
            "page_host": "login.gov.pl", "tag": "BUTTON", "element_id": "chooser",
            "element_class": "tile", "href": "",
        }
        with patch.object(auto_refresh_session.cdp_client, "evaluate_in_page", return_value=clicked):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                result = auto_refresh_session.try_auto_click("127.0.0.1", 9333, target="correct-tab")
        self.assertEqual(result, clicked)
        output = stream.getvalue()
        self.assertIn("host='login.gov.pl'", output)
        self.assertNotIn("cookie", output.lower())

    def test_sensitive_matched_text_is_redacted_before_returning_or_logging(self):
        result = clicking.sanitize_click_diagnostics(
            {
                "clicked": True, "matched_text": "PKK 12345678901",
                "page_url": "https://login.gov.pl/path?otp=123456",
                "href": "https://login.gov.pl/next?pesel=12345678901",
            }
        )
        self.assertEqual(result["matched_text"], "[redacted sensitive text]")
        self.assertEqual(result["page_url"], "https://login.gov.pl/path")
        self.assertEqual(result["href"], "https://login.gov.pl/next")

    def test_safe_matched_text_is_preserved(self):
        result = clicking.sanitize_click_diagnostics(
            {"clicked": True, "matched_text": "Aplikacja mObywatel"}
        )
        self.assertEqual(result["matched_text"], "Aplikacja mObywatel")

    def test_auto_click_returns_diagnostics_without_printing_for_non_click_outcome(self):
        # try_auto_click() now surfaces every completed eval's diagnostics
        # (not just a successful click) so a stuck run can be told apart
        # from a CDP connection that never worked at all — see
        # wait_for_cookies()'s heartbeat log. It still prints nothing itself
        # for a non-click outcome; only wait_for_cookies decides whether/how
        # often to log that.
        diagnostics = {"clicked": False, "reason": "ambiguous_match"}
        with patch.object(
            auto_refresh_session.cdp_client, "evaluate_in_page", return_value=diagnostics
        ):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                result = auto_refresh_session.try_auto_click("127.0.0.1", 9333, target="correct-tab")
        self.assertEqual(result, diagnostics)
        self.assertEqual(stream.getvalue(), "")

    def test_auto_click_still_abstains_on_eval_exception(self):
        with patch.object(
            auto_refresh_session.cdp_client, "evaluate_in_page", side_effect=RuntimeError("boom"),
        ):
            self.assertIsNone(auto_refresh_session.try_auto_click("127.0.0.1", 9333, target="correct-tab"))

    def test_click_logic_js_carries_windows_stall_diagnostics(self):
        # See CLAUDE.md's auth/session.py bullet: viewport/DPR/consent-overlay
        # fields and the observer_injected marker exist specifically so a
        # future stuck report can distinguish "DOM genuinely never matches"
        # from a Windows-specific rendering/registration difference, instead
        # of guessing again the way the ruled-out --lang=pl-PL theory did.
        js = auto_refresh_session.CLICK_LOGIC_JS
        for expected in (
            "__ikw_pageDiag", "viewport_width", "viewport_height",
            "device_pixel_ratio", "observer_injected", "consent_overlay_present",
            "__ikw_withPageDiag",
        ):
            self.assertIn(expected, js)
        self.assertIn("__ikw_diag.observer_injected = true", auto_refresh_session.AUTO_CLICK_OBSERVER_JS)

    def test_wait_for_cookies_stops_forwarding_once_final_target_reached(self):
        # Task: navigating back after the auto-click has reached its final
        # target (targets[0], "Aplikacja mObywatel") must never trigger
        # another auto-click — not even via the Python-side fallback poll,
        # which is a mechanism entirely separate from the injected
        # observer's own (origin-scoped) sessionStorage stop-flag.
        chrome = Mock()
        chrome.poll.side_effect = [None, None, None, None, 1]
        click_results = [
            {"clicked": False, "reason": "not_found"},
            {
                "clicked": True,
                "requested_label": auto_refresh_session.AUTO_CLICK_TARGETS[0],
                "matched_text": "Aplikacja mObywatel", "page_host": "login.gov.pl",
                "tag": "DIV", "element_id": "", "element_class": "", "href": "",
            },
        ]
        with patch.object(
            auto_refresh_session.cdp_client, "fetch_cookies", side_effect=RuntimeError("mid-navigation"),
        ), patch.object(
            auto_refresh_session, "try_auto_click", side_effect=click_results,
        ) as click_mock, patch.object(
            auto_refresh_session, "remove_observer_script",
        ) as remove_mock, patch.object(
            auto_refresh_session.time, "sleep", return_value=None,
        ):
            result = auto_refresh_session.wait_for_cookies(
                "127.0.0.1", 9333, None, chrome, target="tab", observer_identifier="script-1",
            )
        self.assertIsNone(result)
        # Exactly the two calls above happen; two further loop iterations run
        # (chrome_proc.poll() keeps returning None) but must not call
        # try_auto_click again once the final target's been reached.
        self.assertEqual(click_mock.call_count, 2)
        remove_mock.assert_called_once_with("127.0.0.1", 9333, "tab", "script-1")

    def test_wait_for_cookies_latches_on_observer_reaching_target_first(self):
        # The injected observer can reach targets[0] before our own 0.5s
        # fallback poll does; __ikw_stopped()'s 'stopped' reason is how the
        # fallback finds out, and must be treated the same as clicking it
        # ourselves — including unregistering the observer script.
        chrome = Mock()
        chrome.poll.side_effect = [None, None, 1]
        with patch.object(
            auto_refresh_session.cdp_client, "fetch_cookies", side_effect=RuntimeError("mid-navigation"),
        ), patch.object(
            auto_refresh_session, "try_auto_click",
            side_effect=[{"clicked": False, "reason": "stopped"}],
        ) as click_mock, patch.object(
            auto_refresh_session, "remove_observer_script",
        ) as remove_mock, patch.object(
            auto_refresh_session.time, "sleep", return_value=None,
        ):
            auto_refresh_session.wait_for_cookies(
                "127.0.0.1", 9333, None, chrome, target="tab", observer_identifier="script-9",
            )
        self.assertEqual(click_mock.call_count, 1)
        remove_mock.assert_called_once_with("127.0.0.1", 9333, "tab", "script-9")

    def test_register_and_navigate_returns_registration_identifier(self):
        target = SimpleNamespace(id="target-1", websocket_url="ws://127.0.0.1:9333/devtools/x")

        def fake_cdp_call(sock, req_id, method, params=None):
            if method == "Page.addScriptToEvaluateOnNewDocument":
                return {"identifier": "script-123"}
            return {}

        socket_cm = Mock()
        socket_cm.__enter__ = Mock(return_value=object())
        socket_cm.__exit__ = Mock(return_value=False)
        with patch.object(
            auto_refresh_session.cdp_client, "get_page_target", return_value=target,
        ), patch.object(
            auto_refresh_session.cdp_client, "cdp_socket", return_value=socket_cm,
        ), patch.object(
            auto_refresh_session.cdp_client, "cdp_call", side_effect=fake_cdp_call,
        ) as call_mock:
            identifier = auto_refresh_session.register_and_navigate(
                "127.0.0.1", 9333, target, "https://info-kierowca.pl/login", "// script"
            )
        self.assertEqual(identifier, "script-123")
        methods_called = [call.args[2] for call in call_mock.call_args_list]
        self.assertEqual(
            methods_called,
            ["Page.enable", "Page.addScriptToEvaluateOnNewDocument", "Page.navigate"],
        )

    def test_remove_observer_script_is_a_noop_without_an_identifier(self):
        with patch.object(auto_refresh_session.cdp_client, "get_page_target") as get_target:
            auto_refresh_session.remove_observer_script("127.0.0.1", 9333, "tab", None)
        get_target.assert_not_called()

    def test_remove_observer_script_calls_cdp_remove_and_swallows_failures(self):
        target = SimpleNamespace(id="target-1", websocket_url="ws://127.0.0.1:9333/devtools/x")
        socket_cm = Mock()
        socket_cm.__enter__ = Mock(return_value=object())
        socket_cm.__exit__ = Mock(return_value=False)
        with patch.object(
            auto_refresh_session.cdp_client, "get_page_target", return_value=target,
        ), patch.object(
            auto_refresh_session.cdp_client, "cdp_socket", return_value=socket_cm,
        ), patch.object(
            auto_refresh_session.cdp_client, "cdp_call",
        ) as call_mock:
            auto_refresh_session.remove_observer_script("127.0.0.1", 9333, target, "script-123")
        call_mock.assert_called_once_with(
            call_mock.call_args[0][0], 1, "Page.removeScriptToEvaluateOnNewDocument",
            {"identifier": "script-123"},
        )
        # A dead/stale target must not raise out of this best-effort helper.
        with patch.object(
            auto_refresh_session.cdp_client, "get_page_target",
            side_effect=auto_refresh_session.cdp_client.StaleTargetError("gone"),
        ):
            auto_refresh_session.remove_observer_script("127.0.0.1", 9333, target, "script-123")

    def test_browser_click_js_uses_conservative_shared_helper(self):
        js = open_logged_in_browser.click_text_js("Zmień termin")
        self.assertIn("__ikw_clickByText", js)
        self.assertIn("__ikw_isExcludedControl", js)
        self.assertIn("known_error_page", js)
        self.assertNotIn(".click();\n    return true", js)

    def test_enabled_click_requires_exact_label_and_enabled_control(self):
        js = open_logged_in_browser.click_enabled_button_js("Przejdź do podsumowania")
        self.assertIn("true, true", js)
        self.assertIn("aria-disabled", js)

    def test_polling_only_accepts_a_confirmed_click(self):
        with patch.object(
            open_logged_in_browser.cdp_client,
            "evaluate_in_page",
            return_value={"clicked": False, "reason": "ambiguous_match"},
        ), patch.object(open_logged_in_browser.time, "monotonic", side_effect=[0, 21]):
            self.assertFalse(open_logged_in_browser._poll_until_truthy("host", 1, "js", timeout=20))

    def _run_pz_chooser(self, specs):
        if not shutil.which("node"):
            self.skipTest("Node.js is required for PZ chooser behavior tests")
        script = r"""
const specs = SCENARIO;
const clicked = [];
const made = {};
function build(index) {
  if (made[index]) return made[index];
  const spec = specs[index];
  const el = made[index] = {
    innerText: spec.text || '', textContent: spec.text || '',
    parentElement: null,
    matches: selector => selector.includes(spec.tag || 'button') ||
      (spec.role === 'button' && selector.includes('[role="button"]')),
    getBoundingClientRect: () => spec.hidden ? {width:0,height:0} : {width:10,height:10},
    click: () => clicked.push(spec.name || String(index))
  };
  if (spec.parent !== undefined) el.parentElement = build(spec.parent);
  return el;
}
specs.forEach((_spec, index) => build(index));
global.getComputedStyle = el => ({visibility: el.hidden ? 'hidden' : 'visible', display:'block'});
global.document = {querySelectorAll: () => specs.map((_spec, index) => build(index))};
const result = (CHOOSER)(['profil zaufany']);
console.log(JSON.stringify({result, clicked}));
"""
        completed = subprocess.run(
            ["node", "-e", "const SCENARIO=" + json.dumps(specs) + ";\nconst CHOOSER=" +
             auth_providers.CLICK_FUNCTION + ";\n" + script],
            check=True, capture_output=True, text=True,
        )
        return json.loads(completed.stdout)

    def test_pz_chooser_exact_match_outranks_substring(self):
        result = self._run_pz_chooser([
            {"tag": "button", "text": "Profil Zaufany", "name": "exact"},
            {"tag": "button", "text": "Pomoc: Profil Zaufany", "name": "substring"},
        ])
        self.assertEqual(result, {"result": {"status": "clicked"}, "clicked": ["exact"]})

    def test_pz_chooser_abstains_from_two_plausible_controls(self):
        result = self._run_pz_chooser([
            {"tag": "button", "text": "Wybierz Profil Zaufany"},
            {"tag": "button", "text": "Zaloguj: Profil Zaufany"},
        ])
        self.assertEqual(result["result"]["status"], "ambiguous")
        self.assertEqual(result["clicked"], [])

    def test_pz_chooser_deduplicates_nested_controls_and_ignores_hidden(self):
        result = self._run_pz_chooser([
            {"tag": "button", "text": "Profil Zaufany", "name": "outer"},
            {"tag": "span", "role": "button", "text": "Profil Zaufany", "parent": 0},
            {"tag": "button", "text": "Profil Zaufany", "hidden": True},
        ])
        self.assertEqual(result["result"]["status"], "clicked")
        self.assertEqual(result["clicked"], ["outer"])


if __name__ == "__main__":
    unittest.main()
