"""Claude API client for *rule maintenance only*. Claim checks never call AI.

The API key is read from the ANTHROPIC_API_KEY environment variable (or the
process-local value set via the UI, held in memory only). It is never logged,
returned by any endpoint or written to disk.
"""

import json
import os
import urllib.error
import urllib.request

from .rules import DENIAL_CODES, PARAMS_DOC

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"

SYSTEM = f"""You maintain deterministic payer rules for a home-care claim checker.
You never decide individual claims. You translate payer text and user requests into a rule
version in a fixed vocabulary, and propose small example scenarios for a human to review.

{PARAMS_DOC}

Denial codes: {json.dumps(DENIAL_CODES)}
Services: T1019 personal care, T1005 respite, S5125 attendant care. One unit = 15 minutes.
Payer ids look like PY01. A rule version applies to claims by DATE OF SERVICE on/after effective_from.

Rules for you:
- Put in `params` only the keys that CHANGE (or, for a new payer, all that the text defines). Omitted keys inherit.
- Use only the vocabulary above. Anything the text requires that cannot be expressed (e.g. unspecified
  contract terms) goes in params.notes, and you must say so in assistant_message. Never invent rules.
- effective_from: use the date stated in the source. For a payer's base manual with no date use 1900-01-01.
  If the date or payer is genuinely ambiguous, set clarifying_question and leave params empty.
- Provide 4-8 examples: one passing, one failing, boundary dates/values, one unaffected case.
  Scenario schema: claim {{service_code, date_of_service, submitted_date, units, modifier, frequency_code}},
  context {{evv_minutes:[..], auth: valid|missing|wrong_service|wrong_patient|outside_dates,
  auth_units_remaining, daily_paid_units, prior_submission}}, expected_codes [..] (ALL codes that should fire).
  Your expected_codes are proposals; the human confirms them.
- quote the source passage you relied on in source_quote."""

TOOL = {
    "name": "propose_rule_update",
    "description": "Propose a rule version and example scenarios for human review.",
    "input_schema": {
        "type": "object",
        "properties": {
            "assistant_message": {"type": "string", "description": "Short explanation for the user, incl. anything not modelled"},
            "clarifying_question": {"type": "string"},
            "payer_id": {"type": "string"},
            "payer_name": {"type": "string"},
            "effective_from": {"type": "string", "description": "YYYY-MM-DD, applies by date of service"},
            "params": {"type": "object"},
            "summary": {"type": "string"},
            "source_quote": {"type": "string"},
            "examples": {"type": "array", "items": {"type": "object", "properties": {
                "title": {"type": "string"}, "claim": {"type": "object"}, "context": {"type": "object"},
                "expected_codes": {"type": "array", "items": {"type": "string"}}},
                "required": ["title", "claim", "expected_codes"]}},
        },
        "required": ["assistant_message"],
    },
}


class AIUnavailable(RuntimeError):
    pass


class AIClient:
    def __init__(self, transport=None, model=None):
        self._key = None            # set only through set_key(); never exposed
        self.transport = transport  # injectable for tests: fn(payload) -> response dict
        self.model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)

    def set_key(self, key):
        self._key = key.strip() or None

    def available(self):
        return bool(self.transport or self._key or os.environ.get("ANTHROPIC_API_KEY"))

    def propose(self, messages, context_text):
        """messages: [{role, content}] chat so far. Returns the tool input dict."""
        if not self.available():
            raise AIUnavailable("No API key configured. Set ANTHROPIC_API_KEY, or edit rules manually.")
        payload = dict(model=self.model, max_tokens=4000, system=SYSTEM + "\n\nContext:\n" + context_text,
                       tools=[TOOL], tool_choice={"type": "tool", "name": TOOL["name"]}, messages=messages)
        response = self.transport(payload) if self.transport else self._post(payload)
        for block in response.get("content", []):
            if block.get("type") == "tool_use":
                return block["input"]
        raise AIUnavailable("The model returned no structured proposal")

    def _post(self, payload):
        key = self._key or os.environ.get("ANTHROPIC_API_KEY")
        req = urllib.request.Request(API_URL, data=json.dumps(payload).encode(), method="POST", headers={
            "content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise AIUnavailable(f"Claude API error {e.code}") from None  # body may echo request data
        except (urllib.error.URLError, TimeoutError):
            raise AIUnavailable("Could not reach the Claude API") from None
