"""Claude API client for *rule maintenance only*. Claim checks never call AI.

The API key is read only from the ANTHROPIC_API_KEY environment variable. It is
never logged, returned by any endpoint or written to disk.
"""

import json
import os
import urllib.error
import urllib.request

from pathlib import Path

from .rules import DENIAL_CODES

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"
SKILL_PATH = Path(__file__).resolve().parent.parent / "skills" / "payer-rule-updater" / "SKILL.md"
SYSTEM = SKILL_PATH.read_text() + "\n\nDenial code meanings: " + json.dumps(DENIAL_CODES)

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
            "params": {"type": "object", "description": "Optional legacy baseline parameter changes"},
            "code": {"type": "string", "description": "Complete Python apply_rule(claim, context, failures) implementation"},
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
        payload = dict(model=self.model, max_tokens=12000, system=system, tools=[TOOL],
                       tool_choice={"type": "auto"}, messages=[dict(m) for m in messages])
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
