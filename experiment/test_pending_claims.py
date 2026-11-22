import unittest

from pending_claims import PendingAuthorizationReview


class PendingReviewTests(unittest.TestCase):
    def setUp(self):
        self.claim = dict(claim_id="C000001", auth_id="AUTH10001", payer="PY01",
                          patient_id="PT0001", service_code="T1019", units="7",
                          date_of_service="2026-10-01", submitted_date="2026-10-10")
        self.pending = dict(self.claim, claim_id="C000002", units="18", submitted_date="2026-10-09")
        self.auth = dict(auth_id="AUTH10001", payer_id="PY01", patient_id="PT0001",
                         service_code="T1019", start_date="2026-09-01", end_date="2026-11-30")
        self.baseline = dict(status="paid", evidence=dict(authorization_remaining=24))

    def assess(self, pending=None, baseline=None, known=()):
        gate = PendingAuthorizationReview(pending if pending is not None else [self.pending], [self.auth], known)
        return gate.assess(self.claim, baseline or self.baseline)

    def test_balance_interval_and_possible_not_confirmed_code(self):
        result = self.assess()
        self.assertEqual(result["decision"], "check_previous_claims")
        self.assertEqual(result["authorization_balance_if_none_paid"], 24)
        self.assertEqual(result["authorization_balance_if_all_paid"], 6)
        self.assertEqual(result["potential_denial_codes"], ["D02"])
        self.assertEqual(result["unresolved_prior_claim_ids"], ["C000002"])
        self.assertEqual(self.baseline["status"], "paid")

    def test_exact_balance_is_allowed(self):
        result = self.assess([dict(self.pending, units="17")])
        self.assertEqual(result["decision"], "paid")
        self.assertFalse(result["authorization_balance_uncertain"])

    def test_same_day_and_future_claims_are_excluded(self):
        for submitted in ("2026-10-10", "2026-10-11"):
            with self.subTest(submitted=submitted):
                self.assertEqual(self.assess([dict(self.pending, submitted_date=submitted)])["decision"], "paid")

    def test_duplicates_and_already_known_claims_not_counted_twice(self):
        self.assertEqual(self.assess([self.pending, self.pending])["unresolved_prior_units"], 18)
        self.assertEqual(self.assess(known=[self.pending["claim_id"]])["decision"], "paid")

    def test_wrong_context_and_invalid_coverage_are_excluded(self):
        for field, value in (("payer", "PY02"), ("patient_id", "PT0002"),
                             ("service_code", "T1005"), ("auth_id", "AUTH99999"),
                             ("date_of_service", "2026-08-01")):
            with self.subTest(field=field):
                self.assertEqual(self.assess([dict(self.pending, **{field: value})])["decision"], "paid")

    def test_definite_denial_and_missing_balance_are_not_overridden(self):
        result = self.assess(baseline=dict(self.baseline, status="denied"))
        self.assertEqual(result["decision"], "denied")
        self.assertTrue(result["authorization_balance_uncertain"])
        result = self.assess(baseline=dict(status="manual_review", evidence={}))
        self.assertEqual(result["decision"], "manual_review")
        self.assertFalse(result["authorization_balance_uncertain"])


if __name__ == "__main__":
    unittest.main()
