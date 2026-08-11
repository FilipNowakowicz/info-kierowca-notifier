"""Behavioural tests for the two DOM checks that stand between a slot hit and
an irreversible booking change: select_slot_js() and verify_summary_js().

Both are pure browser-side matching, so they run here against a small
JS-object DOM under Node — the same approach tests/test_browser_click_safety.py
already uses for the shared click helpers, and the only way to catch "matched
the wrong row" without a live site. The scenarios are written from the bugs a
security review and a code review found on 2026-08-11: matching that ignored
the date and the exam centre entirely, and a summary check that read the whole
page instead of the modal.
"""
import json
import shutil
import subprocess
import unittest

from info_kierowca_notifier.booking import reschedule as browser

# One element per name; `parent` names another entry. `text` is that element's
# own text — innerText is composed from the subtree, as in a real browser.
HARNESS = r"""
const clicked = [];
const specs = SCENARIO.elements;
const made = {};
function textOf(el) {
  return [el.ownText].concat(el.children.map(textOf)).filter(Boolean).join('\n');
}
function descendants(el) {
  return el.children.reduce((acc, c) => acc.concat([c], descendants(c)), []);
}
function matchesSelector(el, sel) {
  return String(sel).split(',').map(s => s.trim()).filter(Boolean).some(part => {
    let m = part.match(/^\[([a-z-]+)(?:="([^"]*)")?\]$/);
    if (m) {
      const v = el.getAttribute(m[1]);
      return m[2] === undefined ? !!v : v === m[2];
    }
    m = part.match(/^([a-z]+)\[([a-z-]+)="([^"]*)"\]$/);
    if (m) return el.tagName === m[1].toUpperCase() && el.getAttribute(m[2]) === m[3];
    return el.tagName === part.toUpperCase();
  });
}
function makeElement(name, spec) {
  const el = {
    name,
    tagName: (spec.tag || 'DIV').toUpperCase(),
    id: spec.id || '',
    className: spec.className || '',
    ownText: spec.text || '',
    children: [],
    parentElement: null,
    href: spec.href || '',
    disabled: !!spec.disabled,
    offsetWidth: spec.hidden ? 0 : 10,
    offsetHeight: spec.hidden ? 0 : 10,
    getClientRects: () => spec.hidden ? [] : [1],
    getAttribute(n) {
      if (n === 'href') return this.href;
      if (n === 'role') return spec.role || '';
      if (n === 'aria-modal') return spec.ariaModal || '';
      if (n === 'aria-label') return spec.ariaLabel || '';
      if (n === 'aria-disabled') return spec.ariaDisabled || '';
      if (n === 'type') return spec.type || '';
      return '';
    },
    contains(other) {
      let cur = other;
      while (cur) { if (cur === this) return true; cur = cur.parentElement; }
      return false;
    },
    querySelectorAll(sel) { return descendants(this).filter(e => matchesSelector(e, sel)); },
  };
  Object.defineProperty(el, 'innerText', {get: () => textOf(el)});
  Object.defineProperty(el, 'textContent', {get: () => textOf(el)});
  el.click = () => clicked.push(name);
  el._cursor = spec.cursor || 'pointer';
  return el;
}
Object.keys(specs).forEach(name => { made[name] = makeElement(name, specs[name]); });
Object.keys(specs).forEach(name => {
  const parent = specs[name].parent;
  if (parent) { made[name].parentElement = made[parent]; made[parent].children.push(made[name]); }
});
const roots = Object.keys(specs).filter(n => !specs[n].parent).map(n => made[n]);
const body = makeElement('body', {tag: 'body'});
roots.forEach(r => { r.parentElement = body; body.children.push(r); });
const storage = {};
global.window = {
  getComputedStyle: el => ({
    display: el && el._hidden ? 'none' : 'block', visibility: 'visible',
    opacity: '1', cursor: el ? el._cursor : ''
  }),
  sessionStorage: {
    getItem: k => (k in storage ? storage[k] : null),
    setItem: (k, v) => { storage[k] = String(v); },
    removeItem: k => { delete storage[k]; },
  },
};
global.location = {
  origin: 'https://info-kierowca.pl', pathname: '/cases',
  hostname: 'info-kierowca.pl', href: 'https://info-kierowca.pl/cases'
};
global.document = {
  body,
  querySelector: () => null,
  querySelectorAll: sel => [body].concat(descendants(body)).filter(e => matchesSelector(e, sel)),
};
const RESULTS = EXPRESSIONS.map(expression => eval(expression));
console.log(JSON.stringify({result: RESULTS[RESULTS.length - 1], results: RESULTS, clicked}));
"""


def _run(expressions, elements):
    """Evaluate one JS expression (or several in sequence, sharing page state)."""
    if not shutil.which("node"):
        raise unittest.SkipTest("Node.js is required for browser-matching behaviour tests")
    if isinstance(expressions, str):
        expressions = [expressions]
    script = (
        "const SCENARIO = " + json.dumps({"elements": elements}, ensure_ascii=False) + ";\n"
        "const EXPRESSIONS = " + json.dumps(list(expressions)) + ";\n"
        + HARNESS
    )
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)


TARGET_DATE = "10/08/2026"
OTHER_DATE = "20/08/2026"
LABEL = "Egzamin praktyczny"
TIME = "12:30"
CENTER = "WORD Warszawa M/E Odolany"


def slot_page(*, second_group_date=OTHER_DATE, center_text="", second_center_text=""):
    """Two expanded date groups, each holding one identical-looking slot row."""
    return {
        "list": {"tag": "div"},
        "group1": {"tag": "section", "parent": "list"},
        "group1_header": {"tag": "div", "parent": "group1",
                          "text": f"{TARGET_DATE} {center_text}".strip()},
        "group1_row": {"tag": "div", "parent": "group1", "role": "button",
                       "text": f"{LABEL} {TIME}"},
        "group2": {"tag": "section", "parent": "list"},
        "group2_header": {"tag": "div", "parent": "group2",
                          "text": f"{second_group_date} {second_center_text}".strip()},
        "group2_row": {"tag": "div", "parent": "group2", "role": "button",
                       "text": f"{LABEL} {TIME}"},
    }


class SlotSelectionTests(unittest.TestCase):
    def select(self, elements, *, center=""):
        return _run(browser.select_slot_js(LABEL, TIME, TARGET_DATE, center), elements)

    def test_row_under_the_target_date_wins_over_an_identical_later_one(self):
        # The bug: matching was exam label + time across the whole document,
        # and the tie-break preferred the LAST equal-length match in document
        # order — i.e. the later date's row.
        result = self.select(slot_page())
        self.assertTrue(result["result"]["clicked"])
        self.assertEqual(result["clicked"], ["group1_row"])

    def test_target_date_group_missing_selects_nothing(self):
        elements = slot_page()
        elements["group1_header"]["text"] = "01/01/2027"
        result = self.select(elements)
        self.assertEqual(result["result"]["reason"], "date_group_not_found")
        self.assertEqual(result["clicked"], [])

    def test_two_identical_rows_inside_one_group_abstain(self):
        elements = slot_page()
        elements["group1_row_duplicate"] = {
            "tag": "div", "parent": "group1", "role": "button", "text": f"{LABEL} {TIME}",
        }
        result = self.select(elements)
        self.assertEqual(result["result"]["reason"], "ambiguous_slot_row")
        self.assertEqual(result["clicked"], [])

    def test_wrong_exam_centre_is_never_selected(self):
        result = self.select(
            slot_page(center_text="WORD Warszawa M/E Bemowo"), center=CENTER
        )
        self.assertEqual(result["result"]["reason"], "center_conflict")
        self.assertEqual(result["clicked"], [])

    def test_matching_exam_centre_is_selected_and_recorded(self):
        result = self.select(slot_page(center_text=CENTER), center=CENTER)
        self.assertTrue(result["result"]["clicked"])
        self.assertEqual(result["result"]["center_verdict"], "match")
        self.assertEqual(result["clicked"], ["group1_row"])

    def test_centre_absent_from_the_group_still_selects(self):
        # Selection is recoverable; only the confirm gate demands a positive
        # centre match. A picker that simply doesn't repeat the centre name in
        # the slot list must not make the feature unusable.
        result = self.select(slot_page(), center=CENTER)
        self.assertTrue(result["result"]["clicked"])
        self.assertEqual(result["result"]["center_verdict"], "unknown")

    def test_a_date_group_appearing_twice_abstains(self):
        result = self.select(slot_page(second_group_date=TARGET_DATE))
        self.assertEqual(result["result"]["reason"], "ambiguous_date_group")
        self.assertEqual(result["clicked"], [])


def summary_page(*, modal_datetime=f"{TARGET_DATE}, {TIME}", modal_label=LABEL,
                 modal_center="", page_center="", page_datetime=f"{TARGET_DATE}, {TIME}",
                 with_modal=True):
    """A summary modal over the date-picker page that is still mounted behind it."""
    elements = {
        "page": {"tag": "div"},
        "page_slot": {"tag": "div", "parent": "page",
                      "text": f"{LABEL} {page_datetime} {page_center}".strip()},
    }
    if with_modal:
        elements["modal"] = {"tag": "div", "role": "dialog", "ariaModal": "true"}
        elements["modal_body"] = {
            "tag": "div", "parent": "modal",
            "text": f"Potwierdź wybrany egzamin {modal_label} {modal_datetime} {modal_center}".strip(),
        }
    return elements


class SummaryVerificationTests(unittest.TestCase):
    def verify(self, elements, *, center=""):
        return _run(
            browser.verify_summary_js(TARGET_DATE, TIME, LABEL, center), elements
        )["result"]

    def test_page_behind_the_modal_cannot_satisfy_the_check(self):
        # The bug: this read document.body, and the date-picker page behind the
        # modal is already showing the selected date group — so a modal showing
        # a DIFFERENT slot still verified.
        result = self.verify(summary_page(modal_datetime="20/08/2026, 09:00"))
        self.assertEqual(result["reason"], "summary_mismatch")

    def test_no_modal_at_all_fails_closed(self):
        result = self.verify(summary_page(with_modal=False))
        self.assertEqual(result["reason"], "summary_modal_not_found")

    def test_matching_modal_with_matching_centre_verifies(self):
        self.assertIs(
            self.verify(summary_page(modal_center=CENTER), center=CENTER), True
        )

    def test_matching_modal_naming_a_different_centre_refuses(self):
        result = self.verify(
            summary_page(modal_center="WORD Warszawa M/E Bemowo"), center=CENTER
        )
        self.assertEqual(result["reason"], "center_conflict")

    def test_centre_may_come_from_the_page_when_the_modal_omits_it(self):
        self.assertIs(
            self.verify(summary_page(page_center=CENTER), center=CENTER), True
        )

    def test_no_centre_anywhere_refuses_to_confirm(self):
        result = self.verify(summary_page(), center=CENTER)
        self.assertEqual(result["reason"], "center_unknown")

    def test_target_without_a_centre_cannot_auto_confirm(self):
        # Every hit dict notifier.py builds carries `word`, so this is the
        # hand-written --target-slot case: nothing to compare the page against,
        # and this is the last gate before an irreversible submit.
        result = self.verify(summary_page(modal_center=CENTER))
        self.assertEqual(result["reason"], "center_unverifiable")

    def test_modal_scope_still_decides_before_the_centre_is_considered(self):
        self.assertEqual(
            self.verify(
                summary_page(modal_label="Egzamin teoretyczny", modal_center=CENTER),
                center=CENTER,
            )["reason"],
            "summary_mismatch",
        )


class ConfirmMarkerTests(unittest.TestCase):
    """The in-page half of the double-submit guard, run as real JS.

    click_confirm_once()'s Python half is tested with a fake page in
    tests/test_reschedule_transaction.py; this checks the invariant it relies
    on — that the marker is already set the moment a click has fired, so a
    lost CDP response can never turn into a second submission.
    """

    CONFIRM = "Potwierdź i przejdź dalej"

    def page(self):
        return {
            "modal": {"tag": "div", "role": "dialog", "ariaModal": "true"},
            "confirm": {"tag": "button", "parent": "modal", "role": "button",
                        "text": self.CONFIRM},
        }

    def test_a_second_evaluation_refuses_to_click_again(self):
        js = browser.confirm_click_js(self.CONFIRM)
        out = _run([js, js], self.page())
        self.assertTrue(out["results"][0]["clicked"])
        self.assertFalse(out["results"][1]["clicked"])
        self.assertEqual(out["results"][1]["reason"], "already_clicked")
        self.assertEqual(out["clicked"], ["confirm"])

    def test_the_marker_reports_the_click_even_before_we_read_the_result(self):
        out = _run(
            [browser.confirm_click_js(self.CONFIRM), browser.confirm_marker_js("get")],
            self.page(),
        )
        self.assertIs(out["results"][1], True)

    def test_a_click_that_never_landed_leaves_the_marker_down(self):
        # Nothing to click -> the marker is cleared again, so the poll loop is
        # free to keep retrying until the button appears.
        out = _run(
            [browser.confirm_click_js(self.CONFIRM), browser.confirm_marker_js("get")],
            {"other": {"tag": "button", "role": "button", "text": "Anuluj"}},
        )
        self.assertFalse(out["results"][0]["clicked"])
        self.assertIs(out["results"][1], False)
        self.assertEqual(out["clicked"], [])

    def test_clearing_the_marker_re_arms_a_fresh_attempt(self):
        js = browser.confirm_click_js(self.CONFIRM)
        out = _run([js, browser.confirm_marker_js("clear"), js], self.page())
        self.assertTrue(out["results"][0]["clicked"])
        self.assertIs(out["results"][1], False)
        self.assertTrue(out["results"][2]["clicked"])


class WhitespaceCollapsingTests(unittest.TestCase):
    def test_button_label_split_across_lines_still_matches_exactly(self):
        # __ikw_text()'s collapsing regex was dead (`\\s` in a raw string), so
        # exact matching — which is what the summary and confirm buttons use —
        # failed on any label that wrapped. The harness composes innerText with
        # newlines between children, so this covers the fix end to end.
        elements = {
            "button": {"tag": "button", "role": "button"},
            "line1": {"tag": "span", "parent": "button", "text": "Potwierdź i"},
            "line2": {"tag": "span", "parent": "button", "text": "przejdź dalej"},
        }
        result = _run(browser.click_enabled_button_js("Potwierdź i przejdź dalej"), elements)
        self.assertTrue(result["result"]["clicked"])
        self.assertEqual(result["clicked"], ["button"])


if __name__ == "__main__":
    unittest.main()
