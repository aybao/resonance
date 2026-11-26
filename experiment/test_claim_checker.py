import unittest

from claim_checker import Checker, State, claim_input, outcome_matches, score


class ClaimCheckerTests(unittest.TestCase):
    def make(self, payer="PY01", dos="2026-09-05", minutes=120, units="8", **overrides):
        claim = dict(claim_id="C000001", agency_id="AG001", payer=payer,
                     patient_id="PT0001", service_code="T1019", date_of_service=dos,
                     submitted_date=dos, units=units, auth_id="AUTH10001",
                     modifier="U3" if payer == "PY02" and dos >= "2026-09-01" else "U2",
                     frequency_code="1", original_claim_id="", billed_amount="40")
        claim.update(overrides)
        auth = dict(auth_id="AUTH10001", patient_id="PT0001", payer_id=payer,
                    service_code="T1019", start_date="2026-06-01", end_date="2026-11-30", units_authorized="100")
        from datetime import datetime, timedelta
        start = datetime.fromisoformat(dos + " 08:00")
        visit = dict(visit_id="V000001", patient_id="PT0001", service_code="T1019",
                     clock_in=str(start), clock_out=str(start + timedelta(minutes=minutes)), verification_status="verified")
        return claim, auth, visit

    def check(self, claim, auth, visits, state=None):
        return Checker([auth], visits).check(claim, state or State())

    def codes(self, result):
        return {f["code"] for f in result["failures"]}

    def test_rounding_boundaries(self):
        for payer, minutes, denied in [("PY01", 22, True), ("PY01", 23, False), ("PY02", 29, True), ("PY03", 30, False)]:
            with self.subTest(payer=payer, minutes=minutes):
                claim, auth, visit = self.make(payer=payer, minutes=minutes, units="2")
                self.assertEqual("D09" in self.codes(self.check(claim, auth, [visit])), denied)

    def test_filing_versions_and_exact_deadlines(self):
        from datetime import date, timedelta
        for payer, dos, limit in [("PY01", "2026-07-31", 30), ("PY01", "2026-08-01", 45), ("PY03", "2026-09-05", 60)]:
            claim, auth, visit = self.make(payer=payer, dos=dos)
            for extra in (0, 1):
                claim["submitted_date"] = str(date.fromisoformat(dos) + timedelta(days=limit + extra))
                self.assertEqual("D03" in self.codes(self.check(claim, auth, [visit])), bool(extra))
        claim, auth, visit = self.make(payer="PY02", submitted_date="2027-01-01")
        self.assertNotIn("D03", self.codes(self.check(claim, auth, [visit])))

    def test_harbor_modifier_effective_date_weekend_and_weekday(self):
        for dos, expected in [("2026-08-30", "U2"), ("2026-09-05", "U3"), ("2026-09-07", "U2")]:
            claim, auth, visit = self.make(payer="PY02", dos=dos, modifier="")
            result = self.check(claim, auth, [visit])
            self.assertIn("D04", self.codes(result))
            self.assertEqual(result["proposed_changes"]["modifier"]["value"], expected)

    def test_midnight_and_aggregate_before_rounding(self):
        claim, auth, first = self.make(payer="PY03", units="2")
        first.update(clock_in="2026-09-05 23:50", clock_out="2026-09-06 00:10")
        second = dict(first, visit_id="V000002", clock_in="2026-09-05 10:00", clock_out="2026-09-05 10:10")
        result = self.check(claim, auth, [first, second])
        self.assertEqual(result["evidence"]["verified_minutes"], 30)
        self.assertEqual(result["status"], "paid")

    def test_pending_and_rejected_visits_are_not_evidence(self):
        claim, auth, visit = self.make()
        for status in ("manual_edit_pending", "rejected"):
            visit["verification_status"] = status
            self.assertIn("D01", self.codes(self.check(claim, auth, [visit])))

    def test_auth_matching_missing_and_exhausted_balance(self):
        claim, auth, visit = self.make(auth_id="")
        result = self.check(claim, auth, [visit])
        self.assertIn("D07", self.codes(result))
        self.assertEqual(result["proposed_changes"]["auth_id"]["value"], "AUTH10001")
        claim["auth_id"] = auth["auth_id"]
        for field, bad in [("patient_id", "PT0002"), ("payer_id", "PY02"), ("service_code", "T1005"), ("end_date", "2026-09-04")]:
            self.assertIn("D08", self.codes(self.check(claim, dict(auth, **{field: bad}), [visit])))
        state = State()
        state.auth_used[auth["auth_id"]] = 92
        self.assertNotIn("D02", self.codes(self.check(claim, auth, [visit], state)))
        state.auth_used[auth["auth_id"]] = 93
        self.assertIn("D02", self.codes(self.check(claim, auth, [visit], state)))

    def test_denied_submission_is_duplicate_but_correction_is_exempt(self):
        claim, auth, visit = self.make()
        state = State()
        state.record(claim, "denied")
        self.assertEqual(state.auth_used[auth["auth_id"]], 0)
        new = dict(claim, claim_id="C000002")
        self.assertIn("D05", self.codes(self.check(new, auth, [visit], state)))
        new.update(frequency_code="7", original_claim_id=claim["claim_id"])
        self.assertNotIn("D05", self.codes(self.check(new, auth, [visit], state)))

    def test_harbor_daily_total_across_services(self):
        claim, auth, visit = self.make(payer="PY02", units="8")
        state = State()
        earlier = dict(claim, claim_id="C000002", service_code="T1005", units="25")
        state.record(earlier, "paid")
        self.assertIn("D06", self.codes(self.check(claim, auth, [visit], state)))
        state.daily_paid[State.daily_key(claim)] = 24
        self.assertNotIn("D06", self.codes(self.check(claim, auth, [visit], state)))

    def test_outcomes_do_not_affect_predictions_and_proposals_do_not_mutate_claim(self):
        claim, auth, visit = self.make(payer="PY02", modifier="")
        original = dict(claim)
        result = self.check(claim, auth, [visit])
        poisoned = dict(claim, status="denied", denial_code="D99", remark="Secret answer", paid_amount="0")
        self.assertEqual(self.check(poisoned, auth, [visit]), result)
        self.assertEqual(claim, original)
        self.assertNotIn("status", claim_input(poisoned))

    def test_unresolvable_input_is_error_not_fabricated_denial(self):
        claim, auth, visit = self.make()
        result = Checker([auth], [visit]).check(claim, State(), ["Invalid patient ID"])
        self.assertEqual(result["status"], "unable_to_check")
        self.assertEqual(result["denial_codes"], [])

    def test_multiple_failures_are_a_set_and_match_any_reported_code(self):
        claim, auth, visit = self.make(payer="PY02", modifier="", auth_id="")
        visit["verification_status"] = "rejected"
        result = self.check(claim, auth, [visit])
        self.assertEqual(result["denial_codes"], ["D01", "D04", "D07"])
        for code in result["denial_codes"]:
            self.assertTrue(outcome_matches("denied", code, result["status"], result["denial_codes"]))
        self.assertFalse(outcome_matches("denied", "D02", result["status"], result["denial_codes"]))
        self.assertTrue(outcome_matches("paid", "", "paid", []))
        self.assertFalse(outcome_matches("paid", "", "denied", ["D01"]))
        self.assertFalse(outcome_matches("paid", "", "unable_to_check", []))
        self.assertFalse(outcome_matches("denied", "", "denied", ["D01"]))
        import json
        rows = [dict(actual_status="denied", actual_denial_code="D07", predicted_status="denied",
                     predicted_denial_codes=json.dumps(result["denial_codes"]), error_category=""),
                dict(actual_status="paid", actual_denial_code="", predicted_status="paid",
                     predicted_denial_codes="[]", error_category="")]
        metrics = score(rows)
        self.assertEqual(metrics["status_and_code_set_accuracy"], 1)
        self.assertEqual(metrics["additional_unconfirmed_codes"], 2)

    def test_pipeline_preserves_export_rows_and_never_uses_test_labels_for_main_state(self):
        import csv
        import io
        from contextlib import redirect_stdout
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from check_formatting import SCHEMAS
        from claim_checker import load_csv, run

        first, auth, visit = self.make(dos="2026-10-01")
        auth["units_authorized"] = "12"
        second = dict(first, claim_id="C000002", date_of_service="2026-10-02", submitted_date="2026-10-02")
        next_visit = dict(visit, visit_id="V000002", clock_in="2026-10-02 08:00", clock_out="2026-10-02 10:00")
        first.update(status="denied", denial_code="D99", remark="Hidden", paid_amount="0")
        second.update(status="paid", denial_code="", remark="", paid_amount="40")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            def write(name, schema, rows):
                with (root / name).open("w", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=SCHEMAS[schema])
                    writer.writeheader()
                    writer.writerows(rows)
            write("sorted_prev_data.csv", "claims_history.csv", [])
            write("sorted_test_data.csv", "claims_history.csv", [first, first, second])
            pending = dict(claim_input(first), claim_id="C000003", date_of_service="2026-09-30", submitted_date="2026-09-30")
            write("claims_to_check.csv", "claims_to_check.csv", [pending])
            # Fixture EVV schema also requires caregiver ID.
            write("authorizations.csv", "authorizations.csv", [auth])
            write("evv_visits.csv", "evv_visits.csv", [dict(v, caregiver_id="CG0001", clock_in=v["clock_in"][:16], clock_out=v["clock_out"][:16]) for v in [visit, next_visit]])
            source_bytes = (root / "sorted_test_data.csv").read_bytes()
            with redirect_stdout(io.StringIO()):
                run(root, root / "artifacts", root / "result.csv", root / "metrics.json")
            self.assertEqual((root / "sorted_test_data.csv").read_bytes(), source_bytes)
            results = load_csv(root / "result.csv")
            self.assertEqual(len(results), 3)
            self.assertEqual([r["export_duplicate"] for r in results], ["False", "True", "False"])
            self.assertEqual(results[0]["predicted_status"], "paid")
            self.assertEqual(results[2]["predicted_denial_codes"], '["D02"]')
            self.assertEqual(results[2]["observed_prior_status"], "paid")
            first.update(status="paid", denial_code="", remark="Changed answer", paid_amount="40")
            write("sorted_test_data.csv", "claims_history.csv", [first, first, second])
            with redirect_stdout(io.StringIO()):
                run(root, root / "artifacts2", root / "result2.csv", root / "metrics2.json")
            repeated = load_csv(root / "result2.csv")
            fields = ["predicted_status", "predicted_denial_codes", "predicted_remark", "evidence", "proposed_changes"]
            self.assertEqual([[r[f] for f in fields] for r in results], [[r[f] for f in fields] for r in repeated])


if __name__ == "__main__":
    unittest.main()
