"""Rule-update workflow with a fake AI transport (no network, no key)."""
import json
import os
import unittest
from pathlib import Path

from claimcheck.ai import AIClient, AIUnavailable
from claimcheck.drafts import DraftError, activate, apply_ai_proposal, chat, decide, evaluate, new_draft, refresh_boundary_examples
from claimcheck.pipeline import Dataset, load_sample
from claimcheck.rules import RuleStore, baseline_store

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data"


def base_store():
    s = RuleStore()
    full = baseline_store()
    s.payers = {"PY01": {"name": "Northwind", "versions": [full.versions("PY01")[0]]}}
    return s


UPDATE = dict(
    assistant_message="Filing limit becomes 50 days for service on/after the effective date.",
    payer_id="PY01", effective_from="2026-08-01", params={"timely_filing_days": 50},
    summary="50-day filing", source_quote="Test fixture rule text: 50 calendar days",
    examples=[
        dict(title="40 days after service", claim=dict(service_code="T1019", date_of_service="2026-08-01",
             submitted_date="2026-09-10", units=4, modifier=""), context=dict(evv_minutes=[60]), expected_codes=[]),
        dict(title="Garbage example", claim=dict(service_code="NOPE"), context={}, expected_codes=[]),
    ])


def fake_ai(proposal):
    calls = []

    def transport(payload):
        calls.append(payload)
        return {"content": [{"type": "tool_use", "name": "propose_rule_update", "input": proposal}]}
    client = AIClient(transport=transport)
    client.calls = calls
    return client


class Workflow(unittest.TestCase):
    def setUp(self):
        self.store, self.ds = base_store(), Dataset()
        load_sample(self.ds, SAMPLE)

    def drafted(self):
        d = new_draft()
        notes = chat(d, self.store, self.ds, fake_ai(UPDATE), "Rule text ...")
        return d, notes

    def test_chat_builds_validated_draft_and_drops_bad_examples(self):
        d, notes = self.drafted()
        self.assertEqual((d["payer_id"], d["effective_from"], d["params"]), ("PY01", "2026-08-01", {"timely_filing_days": 50}))
        self.assertTrue(any("Garbage example" in n for n in notes))
        self.assertEqual(len([e for e in d["examples"] if e["origin"] == "ai"]), 1)
        self.assertTrue(any(e["origin"] == "boundary" for e in d["examples"]))
        self.assertEqual([m["role"] for m in d["messages"]], ["user", "assistant"])

    def test_invalid_ai_rules_are_not_applied(self):
        d = new_draft(payer_id="PY01", effective_from="2026-08-01")
        notes = apply_ai_proposal(d, dict(params={"timely_filing_days": "soon"}))
        self.assertEqual(d["params"], {})
        self.assertTrue(any("invalid rules" in n for n in notes))

    def test_clarifying_question_leaves_draft_empty(self):
        d = new_draft()
        chat(d, self.store, self.ds, fake_ai(dict(assistant_message="Which payer?", clarifying_question="Which payer?")), "x")
        self.assertEqual(d["params"], {})
        self.assertIn("Question: Which payer?", d["messages"][-1]["content"])
        self.assertFalse(evaluate(d, self.store, self.ds)["can_activate"])

    def test_activation_requires_user_decisions_and_matching_results(self):
        d, _ = self.drafted()
        ev = evaluate(d, self.store, self.ds)
        self.assertFalse(ev["can_activate"])
        self.assertGreater(ev["undecided"], 0)
        with self.assertRaises(DraftError):
            activate(d, self.store, self.ds)
        # AI's own expectation is a proposal until confirmed; confirming a wrong one blocks activation
        wrong = next(e for e in d["examples"] if e["origin"] == "ai")
        decide(d, wrong["id"], "set", ["D03"])
        for e in d["examples"]:
            if e["id"] != wrong["id"]:
                decide(d, e["id"], "set", next(r for r in ev["examples"] if r["id"] == e["id"])["actual"]["codes"])
        ev = evaluate(d, self.store, self.ds)
        self.assertFalse(ev["can_activate"])
        self.assertTrue(any("differ" in p for p in ev["problems"]))
        decide(d, wrong["id"], "confirm")
        self.assertTrue(evaluate(d, self.store, self.ds)["can_activate"])

    def test_activate_saves_version_and_acceptance_tests_and_changes_outcomes_by_date(self):
        d, _ = self.drafted()
        ev = evaluate(d, self.store, self.ds)
        for r in ev["examples"]:
            decide(d, r["id"], "set", r["actual"]["codes"])
        before = self.store.resolve("PY01", "2026-08-01")["params"]["timely_filing_days"]
        v = activate(d, self.store, self.ds)
        self.assertEqual((before, v["params"]["timely_filing_days"]), (30, 50))
        self.assertEqual(self.store.resolve("PY01", "2026-07-31")["params"]["timely_filing_days"], 30)
        self.assertGreater(len(self.store.acceptance), 3)
        self.assertTrue(all(t["basis"] == "user_confirmed" and t["effective_from"] == "2026-08-01" for t in self.store.acceptance))

    def test_impact_on_history_shows_changed_outcomes_for_affected_dates_only(self):
        d, _ = self.drafted()
        imp = evaluate(d, self.store, self.ds)["impact"]
        self.assertGreater(imp["changed_count"], 0)
        for c in imp["examples"]:
            self.assertEqual(c["payer"], "PY01")
            self.assertGreaterEqual(c["date_of_service"], "2026-08-01")
            self.assertIn("D03", c["before"]["codes"])
            self.assertNotIn("D03", c["after"]["codes"])

    def test_regression_against_saved_tests_needs_a_reason(self):
        d, _ = self.drafted()
        for r in evaluate(d, self.store, self.ds)["examples"]:
            decide(d, r["id"], "set", r["actual"]["codes"])
        activate(d, self.store, self.ds)
        # a later change that flips the confirmed 50-day behaviour on the same effective date
        d2 = new_draft("PY01", "2026-08-01", {"timely_filing_days": 30}, source="Mistaken edit")
        refresh_boundary_examples(d2, self.store)
        ev = evaluate(d2, self.store, self.ds)
        self.assertTrue(ev["regressions"])
        for r in ev["examples"]:
            decide(d2, r["id"], "set", r["actual"]["codes"])
        with self.assertRaises(DraftError):
            activate(d2, self.store, self.ds)
        activate(d2, self.store, self.ds, regression_reason="Payer corrected the document (ref 123)")
        self.assertTrue(any(t.get("superseded_reason") for t in self.store.acceptance))

    def test_unclear_examples_are_recorded_not_saved_as_tests(self):
        d = new_draft("PY03", "2026-10-01", {"daily_max_units": 24}, source="Phone call")
        refresh_boundary_examples(d, self.store)
        self.store.payers["PY03"] = {"name": "Summit", "versions": [baseline_store().versions("PY03")[0]]}
        ev = evaluate(d, self.store, self.ds)
        for i, r in enumerate(ev["examples"]):
            if i == 0:
                decide(d, r["id"], "unclear", note="ask payer")
            else:
                decide(d, r["id"], "set", r["actual"]["codes"])
        v = activate(d, self.store, self.ds)
        self.assertEqual(len(v["open_questions"]), 1)
        self.assertEqual(len(self.store.acceptance), len(ev["examples"]) - 1)

    def test_persistence_round_trip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            s = baseline_store(Path(tmp) / "rules.json")
            s.save()
            self.assertEqual(RuleStore(Path(tmp) / "rules.json").snapshot(), s.snapshot())


class Client(unittest.TestCase):
    def test_no_key_means_unavailable_and_error_is_explicit(self):
        import os
        old = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            c = AIClient()
            self.assertFalse(c.available())
            with self.assertRaises(AIUnavailable):
                c.propose([{"role": "user", "content": "x"}], "")
        finally:
            if old:
                os.environ["ANTHROPIC_API_KEY"] = old

    def test_request_is_forced_tool_call_with_context_and_no_key_in_payload(self):
        ai = fake_ai(dict(assistant_message="ok"))
        ai.propose([{"role": "user", "content": "hi"}], "ctx")
        payload = ai.calls[0]
        self.assertEqual(payload["tool_choice"], {"type": "auto"})
        self.assertEqual(payload["tools"][0]["name"], "propose_rule_update")
        self.assertIn("ctx", payload["system"])
        self.assertIn("units_authorized", payload["system"])  # CSV formats are described
        key = os.environ.get("ANTHROPIC_API_KEY")
        if key:
            self.assertNotIn(key, json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
