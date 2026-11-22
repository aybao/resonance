"""Rule behaviour from the source manuals, one explicit scenario each.

Expected code sets come from the payer documents, not from engine output.
"""
import unittest

from claimcheck.examples import run_scenario, run_examples, validate_scenario
from claimcheck.rules import baseline_store, validate_params, RuleStore

RULES = baseline_store()


def run(payer, dos="2026-07-15", sub=None, units=4, svc="T1019", mod="U2", ctx=None, freq="1"):
    sub = sub or dos
    s = dict(payer_id=payer, claim=dict(service_code=svc, date_of_service=dos, submitted_date=sub, units=units,
                                        modifier=mod, frequency_code=freq),
             context={"evv_minutes": [60], **(ctx or {})})
    assert not validate_scenario(s), validate_scenario(s)
    return run_scenario(s, RULES)["codes"]


class EVV(unittest.TestCase):
    def test_clean_claim_passes(self):
        for p in ("PY01", "PY02", "PY03"):
            self.assertEqual(run(p), [], p)

    def test_no_verified_visit_is_d01(self):
        self.assertEqual(run("PY01", ctx=dict(evv_minutes=[], unverified_evv_minutes=[60])), ["D01"])

    def test_visits_are_aggregated_before_rounding(self):
        # 10 + 10 + 10 = 30 minutes = 2 units, whereas per-visit rounding would give 0 (full units)
        self.assertEqual(run("PY02", units=2, ctx=dict(evv_minutes=[10, 10, 10])), [])
        self.assertEqual(run("PY02", units=3, ctx=dict(evv_minutes=[10, 10, 10])), ["D09"])

    def test_northwind_eight_minute_boundary(self):
        self.assertEqual(run("PY01", units=2, ctx=dict(evv_minutes=[22])), ["D09"])   # 7 spare minutes
        self.assertEqual(run("PY01", units=2, ctx=dict(evv_minutes=[23])), [])        # 8 spare minutes

    def test_other_payers_need_complete_units(self):
        for p in ("PY02", "PY03"):
            self.assertEqual(run(p, mod="U2" if p == "PY02" else "", units=2, ctx=dict(evv_minutes=[29])), ["D09"])
            self.assertEqual(run(p, mod="U2" if p == "PY02" else "", units=2, ctx=dict(evv_minutes=[30])), [])


class Authorization(unittest.TestCase):
    def test_missing_wrong_and_expired(self):
        self.assertEqual(run("PY03", ctx=dict(auth="missing")), ["D07"])
        for bad in ("wrong_service", "wrong_patient", "outside_dates"):
            self.assertEqual(run("PY03", ctx=dict(auth=bad)), ["D08"], bad)

    def test_balance_boundary(self):
        self.assertEqual(run("PY03", units=4, ctx=dict(auth_units_remaining=4)), [])
        self.assertEqual(run("PY03", units=4, ctx=dict(auth_units_remaining=3)), ["D02"])


class FilingAndModifiers(unittest.TestCase):
    def test_northwind_baseline_30_days(self):
        self.assertEqual(run("PY01", dos="2026-07-31", sub="2026-08-30"), [])            # day 30
        self.assertEqual(run("PY01", dos="2026-07-31", sub="2026-08-31"), ["D03"])       # day 31
        self.assertEqual(run("PY01", dos="2026-08-01", sub="2026-09-10"), ["D03"])       # 40 days, no later version loaded

    def test_summit_60_days_and_harbor_none(self):
        self.assertEqual(run("PY03", dos="2026-07-01", sub="2026-08-30"), [])
        self.assertEqual(run("PY03", dos="2026-07-01", sub="2026-08-31"), ["D03"])
        self.assertEqual(run("PY02", dos="2026-07-01", sub="2026-12-01"), [])

    def test_harbor_baseline_modifier_u2(self):
        self.assertEqual(run("PY02", dos="2026-08-29", mod="U2"), [])
        self.assertEqual(run("PY02", dos="2026-09-05", mod="U2"), [])
        self.assertEqual(run("PY02", dos="2026-09-05", mod="U3"), ["D04"])
        self.assertEqual(run("PY02", dos="2026-09-07", mod=""), ["D04"])

    def test_modifier_only_applies_to_harbor_t1019(self):
        self.assertEqual(run("PY01", mod=""), [])
        self.assertEqual(run("PY02", svc="T1005", mod=""), [])


class DailyAndDuplicates(unittest.TestCase):
    def test_harbor_daily_maximum(self):
        self.assertEqual(run("PY02", units=4, ctx=dict(daily_paid_units=28)), [])
        self.assertEqual(run("PY02", units=4, ctx=dict(daily_paid_units=29)), ["D06"])
        self.assertEqual(run("PY01", units=4, ctx=dict(daily_paid_units=40)), [])

    def test_duplicate_and_corrected_claim_exemption(self):
        self.assertEqual(run("PY03", ctx=dict(prior_submission=True)), ["D05"])
        self.assertEqual(run("PY03", freq="7", ctx=dict(prior_submission=True)), [])

    def test_all_failures_are_reported(self):
        codes = run("PY02", dos="2026-09-05", sub="2026-09-05", units=40, mod="",
                    ctx=dict(evv_minutes=[], daily_paid_units=0, prior_submission=True, auth="missing"))
        self.assertEqual(codes, ["D01", "D04", "D05", "D06", "D07"])


class VersionsAndValidation(unittest.TestCase):
    def test_resolution_by_service_date(self):
        self.assertEqual(RULES.resolve("PY01", "2026-07-31")["params"]["timely_filing_days"], 30)
        self.assertEqual(RULES.resolve("PY01", "2026-08-01")["params"]["timely_filing_days"], 30)  # baseline only
        s = baseline_store()
        s.add_version("PY01", "2026-08-01", {"timely_filing_days": 50})
        self.assertEqual(s.resolve("PY01", "2026-07-31")["params"]["timely_filing_days"], 30)
        self.assertEqual(s.resolve("PY01", "2026-08-01")["params"]["timely_filing_days"], 50)
        self.assertIsNone(RuleStore().resolve("PY01", "2026-08-01"))

    def test_unknown_payer_needs_review_not_paid(self):
        s = dict(payer_id="PY09", claim=dict(service_code="T1019", date_of_service="2026-07-01",
                                             submitted_date="2026-07-02", units=1), context={})
        self.assertEqual(run_scenario(s, RULES)["status"], "manual_review")

    def test_params_validation_rejects_unknown_and_bad_values(self):
        self.assertTrue(validate_params({"surprise": 1}))
        self.assertTrue(validate_params({"timely_filing_days": -1}))
        self.assertTrue(validate_params({"evv": {"rounding": "banker"}}))
        self.assertTrue(validate_params({"modifier_rules": [{"service_code": "X", "modifier": "u2", "days": ["xyz"]}]}))
        self.assertEqual(validate_params({"timely_filing_days": None, "daily_max_units": 10}), [])

    def test_add_version_merges_and_replaces_same_date(self):
        s = baseline_store()
        v = s.add_version("PY03", "2026-10-01", {"timely_filing_days": 90})
        self.assertEqual(v["params"]["evv"]["rounding"], "full_units")  # inherited
        self.assertEqual(s.resolve("PY03", "2026-09-30")["params"]["timely_filing_days"], 60)
        s.add_version("PY03", "2026-10-01", {"timely_filing_days": 75})
        self.assertEqual(len([x for x in s.versions("PY03") if x["effective_from"] == "2026-10-01"]), 1)
        with self.assertRaises(ValueError):
            s.add_version("PY03", "2026-11-01", {"daily_max_units": 0})

    def test_expectations_are_compared_as_code_sets(self):
        ex = [dict(id="a", payer_id="PY03", claim=dict(service_code="T1005", date_of_service="2026-07-01",
                                                       submitted_date="2026-10-01", units=1),
                   context=dict(evv_minutes=[60]), expected_codes=["D03"])]
        self.assertTrue(run_examples(ex, RULES)[0]["passed"])


if __name__ == "__main__":
    unittest.main()
