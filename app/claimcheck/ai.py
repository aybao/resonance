"""Claude API client for *rule maintenance only*. Claim checks never call AI.

The API key is read only from the ANTHROPIC_API_KEY environment variable. It is
never logged, returned by any endpoint or written to disk.
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
Payer ids: PY01 Northwind Health Plan, PY02 Harbor Medicaid Plan, PY03 Summit Care Partners.
A rule version applies to claims by DATE OF SERVICE on/after effective_from (not submission date).

How the deterministic checker uses a rule version (so you know what each param does):
- Claim CSV columns: claim_id, agency_id, payer, patient_id, service_code, date_of_service, submitted_date,
  units, auth_id, modifier (U1/U2/U3 or blank), frequency_code (1 original, 7 corrected claim),
  original_claim_id, billed_amount. Dates arrive as YYYY-MM-DD or MM/DD/YYYY and are normalised first.
- Reference data: authorizations (auth_id, patient_id, payer_id, service_code, start_date, end_date,
  units_authorized), EVV visits (visit_id, caregiver_id, patient_id, service_code, clock_in, clock_out,
  verification_status verified|rejected|manual_edit_pending), and claim history with recorded outcomes.
- EVV: only verified visits for the same patient, service and date count; their minutes become billable
  units via evv.rounding (15 minutes = 1 unit). Billed units above that fail D09; no verified visit is D01.
- Authorization: missing auth_id D07; not matching patient/service/payer or outside its dates D08; units above
  what remains (units_authorized minus units already paid) D02.
- timely_filing_days: submitted_date minus date_of_service in calendar days; more than the limit is D03.
- modifier_rules: the claim's modifier must equal the required one for its service (and weekday if `days` is given), else D04.
- daily_max_units: paid units per member per day across services; exceeding it is D06.
- Duplicates (D05) are judged against claim history; corrected claims (frequency_code 7) are exempt.
- Several codes can fire on one claim; all are reported.

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
        self.transport = transport  # injectable for tests: fn(payload) -> response dict
        self.model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)

    def available(self):
        return bool(self.transport or os.environ.get("ANTHROPIC_API_KEY"))

    def propose(self, messages, context_text):
        """messages: [{role, content}] chat so far. Returns the tool input dict."""
        if not self.available():
            raise AIUnavailable("No API key configured. Set ANTHROPIC_API_KEY, or edit rules manually.")
        # This model rejects a forced tool_choice, so use auto and instruct it to always call the tool.
        system = SYSTEM + f"\n\nAlways answer by calling the {TOOL['name']} tool.\n\nContext:\n" + context_text
        payload = dict(model=self.model, max_tokens=8000, system=system, tools=[TOOL],
                       tool_choice={"type": "auto"}, messages=messages)
        response = self.transport(payload) if self.transport else self._post(payload)
        blocks = response.get("content", [])
        for block in blocks:
            if block.get("type") == "tool_use":
                return block["input"]
        text = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        if text:  # no proposal; show what the model said so the user can reply
            return dict(assistant_message=text)
        raise AIUnavailable("The model returned no structured proposal")

    def _post(self, payload):
        key = os.environ.get("ANTHROPIC_API_KEY")
        req = urllib.request.Request(API_URL, data=json.dumps(payload).encode(), method="POST", headers={
            "content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:  # error bodies are {"error": {"type", "message"}}; the key is never part of them
                detail = json.load(e)["error"]["message"]
            except Exception:
                detail = ""
            raise AIUnavailable(f"Claude API error {e.code}" + (f": {detail}" if detail else "")) from None
        except (urllib.error.URLError, TimeoutError):
            raise AIUnavailable("Could not reach the Claude API") from None
