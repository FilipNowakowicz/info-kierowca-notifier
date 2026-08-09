"""Target-scoped Google Messages Web integration for PZePUAP OTPs."""
import re
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import cdp_client

MESSAGES_URL = "https://messages.google.com/web"
OTP_RE = re.compile(r"\b(\d{8})\b")
TIMESTAMP_RE = re.compile(
    r"(\d{2})\.(\d{2})\.(\d{4}),?\s*godz\.\s*(\d{2}):(\d{2}):(\d{2})",
    re.IGNORECASE,
)
WARSAW_TIMEZONE = ZoneInfo("Europe/Warsaw")


def allowed_messages_origin(url):
    parsed = urlparse(url)
    return parsed.scheme == "https" and (parsed.hostname or "").lower() == "messages.google.com"


@dataclass(frozen=True)
class SMSResult:
    status: str
    code: str = None
    timestamp: float = None


# Returns only the minimum candidate data. It never returns arbitrary message
# bodies or conversation text to Python.
MESSAGES_SCAN_FUNCTION = r"""
function() {
  function deep(selector, root) {
    root = root || document; var out = Array.from(root.querySelectorAll(selector));
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    while (walker.nextNode()) if (walker.currentNode.shadowRoot)
      out = out.concat(deep(selector, walker.currentNode.shadowRoot));
    return out;
  }
  var body = (document.body && document.body.innerText || '').toLowerCase();
  if (body.indexOf('pair with qr code') >= 0 || body.indexOf('paruj za pomocą kodu qr') >= 0)
    return {status:'not_paired', candidates:[]};
  var header = deep('[data-e2e-header-title] h2,[data-e2e-header-title],mws-header h2')
    .map(function(x){return x.innerText || ''}).join(' ').toLowerCase();
  var active = header.indexOf('pzepuap') >= 0 || header.indexOf('profil zaufany') >= 0;
  var conversations = deep('mws-conversation-list-item,a[data-e2e-conversation]');
  var pz = conversations.find(function(item) {
    var n = item.querySelector && item.querySelector('[data-e2e-conversation-name],.name');
    var t = (n && n.innerText || item.innerText || '').toLowerCase();
    return t.indexOf('pzepuap') >= 0 || t.indexOf('profil zaufany') >= 0;
  });
  if (!active && pz) {
    var link = pz.matches && pz.matches('a') ? pz : pz.querySelector && pz.querySelector('a');
    if (link && link.href) location.href = link.href; else pz.click();
    return {status:'switching', candidates:[]};
  }
  if (!active && !pz) return {status:'no_conversation', candidates:[]};
  var nodes = deep('mws-message-wrapper,[data-e2e-message-content],mws-text-message-part');
  var candidates = [];
  nodes.forEach(function(node) {
    var text = node.innerText || '';
    var m = text.match(/(\d{2}\.\d{2}\.\d{4}),?\s*godz\.\s*(\d{2}:\d{2}:\d{2}).*?Kod:\s*(\d{8})/i);
    if (m) candidates.push({date:m[1], time:m[2], code:m[3]});
  });
  return {status:candidates.length ? 'candidates' : 'no_current_otp', candidates:candidates};
}
"""


class GoogleMessagesWebProvider:
    def __init__(self, host, port, target=None, wall_clock=None):
        self.host = host
        self.port = port
        self.target = target
        self.wall_clock = wall_clock or time.time

    def find_or_create_target(self, create=True):
        if self.target is not None:
            return cdp_client.get_page_target(self.host, self.port, self.target.id)
        try:
            self.target = cdp_client.find_page_target(
                self.host, self.port, host_match="messages.google.com"
            )
        except cdp_client.TargetNotFoundError:
            if not create:
                raise
            self.target = cdp_client.create_page_target(self.host, self.port, MESSAGES_URL)
        return self.target

    @staticmethod
    def _timestamp(date_text, time_text):
        match = TIMESTAMP_RE.search(f"{date_text} godz. {time_text}")
        if not match:
            return None
        day, month, year, hour, minute, second = map(int, match.groups())
        try:
            return datetime(year, month, day, hour, minute, second,
                            tzinfo=WARSAW_TIMEZONE).timestamp()
        except (OverflowError, ValueError):
            return None

    def get_latest_pz_code(self, after_timestamp, used_codes=None,
                           tolerance_seconds=30):
        used_codes = used_codes or set()
        try:
            target = self.find_or_create_target(create=False)
            if not allowed_messages_origin(target.url):
                return SMSResult("unexpected_messages_origin")
            result = cdp_client.call_function_in_target(
                self.host, self.port, target, MESSAGES_SCAN_FUNCTION
            ) or {"status": "unsupported", "candidates": []}
        except cdp_client.StaleTargetError:
            return SMSResult("messages_target_lost")
        except cdp_client.TargetNotFoundError:
            return SMSResult("messages_target_unavailable")
        except Exception:
            return SMSResult("page_structure_unsupported")
        status = result.get("status", "page_structure_unsupported")
        if status != "candidates":
            return SMSResult(status)
        now = self.wall_clock()
        valid = []
        stale_found = False
        for candidate in result.get("candidates", []):
            code = candidate.get("code")
            if not isinstance(code, str) or not OTP_RE.fullmatch(code) or code in used_codes:
                continue
            stamp = self._timestamp(candidate.get("date", ""), candidate.get("time", ""))
            if stamp is None:
                continue
            if (stamp < after_timestamp - tolerance_seconds or
                    now - stamp > 300 or stamp > now + 30):
                stale_found = True
                continue
            valid.append((stamp, code))
        if not valid:
            return SMSResult("stale_otp" if stale_found else "no_current_otp")
        stamp, code = max(valid)
        return SMSResult("found", code=code, timestamp=stamp)
