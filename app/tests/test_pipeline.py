"""Normalisation, ledger/history behaviour and CSV round trip on small fixtures."""
import csv
import io
import unittest

from claimcheck.normalize import CLAIM_FIELDS, canonical, normalize_claims, read_csv_text
from claimcheck.pipeline import Dataset, export_csv, parse_claims_csv, run_batch
from claimcheck.rules import baseline_store

AUTHS = ("auth_id,patient_id,payer_id,service_code,start_date,end_date,units_authorized\n"
         "AUTH00001,PT0001,PY03,T1005,2026-06-01,2026-12-31,10\n"
         "AUTH00002,PT0002,PY02,T1019,2026-06-01,2026-12-31,100\n")
EVV = ("visit_id,caregiver_id,patient_id,service_code,clock_in,clock_out,verification_status\n"
       + "".join(f"V00000{i},CG0001,PT0001,T1005,2026-07-{d:02} 09:00,2026-07-{d:02} 12:00,verified\n"
                 for i, d in enumerate(range(1, 6), 1))
       + "V000010,CG0001,PT0002,T1019,2026-07-01 09:00,2026-07-01 10:00,verified\n")
HIST_HEAD = ",".join(CLAIM_FIELDS) + ",status,paid_amount,denial_code,remark\n"


def claim_row(cid="C000100", payer="PY03", pt="PT0001", svc="T1005", dos="2026-07-01", sub="2026-07-05",
              units="8", auth="AUTH00001", mod="", freq="1", orig=""):
    return [cid, "AG001", payer, pt, svc, dos, sub, units, auth, mod, freq, orig, "10.0"]


def csv_text(rows, head=CLAIM_FIELDS):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(head)
    w.writerows(rows)
    return out.getvalue()


def dataset(history_rows=()):
    ds = Dataset()
    ds.load_text("authorizations", AUTHS)
    ds.load_text("evv", EVV)
    ds.load_text("history", csv_text(history_rows, CLAIM_FIELDS + ["status", "paid_amount", "denial_code", "remark"]))
    return ds


def run(rows, ds=None, overrides=None, *, autofix=True):
    _, recs = parse_claims_csv(csv_text(rows))
    return run_batch(recs, ds or dataset(), baseline_store(), overrides, autofix=autofix)


class Normalisation(unittest.TestCase):
    def test_canonical_forms(self):
        self.assertEqual(canonical("payer", " northwind  HEALTH plan "), "PY01")
        self.assertEqual(canonical("date_of_service", "07/09/2026"), "2026-07-09")
        self.assertEqual(canonical("modifier", " u2 "), "U2")
        self.assertEqual(canonical("units", "08"), "8")

    def test_unknown_values_are_flagged_not_guessed(self):
        for field, value in (("payer", "Acme"), ("date_of_service", "13/45/2026"), ("service_code", "X1"),
                             ("date_of_service", "9 Jul 2026"), ("units", "-1"), ("units", "2.5")):
            with self.assertRaises(ValueError):
                canonical(field, value)

    def test_idempotent(self):
        _, recs = parse_claims_csv(csv_text([claim_row(payer="Summit Care", dos="07/01/2026", mod="u1")]))
        again = normalize_claims(CLAIM_FIELDS, [recs[0]["clean"]])
        self.assertEqual(again[0]["changes"], [])
        self.assertEqual(again[0]["clean"], recs[0]["clean"])

    def test_changes_recorded_and_raw_preserved(self):
        _, recs = parse_claims_csv(csv_text([claim_row(payer="Summit Care", dos="07/01/2026")]))
        r = recs[0]
        self.assertEqual({c["field"] for c in r["changes"]}, {"payer", "date_of_service"})
        self.assertEqual(r["raw"]["payer"], "Summit Care")

    def test_missing_column_rejected(self):
        with self.assertRaises(ValueError):
            normalize_claims(["claim_id"], [])

    def test_blank_required_and_correction_without_original(self):
        rows = run([claim_row(units=""), claim_row(cid="C000101", freq="7")])
        self.assertEqual([r["category"] for r in rows], ["must_correct", "must_correct"])

    def test_identical_rows_collapse_conflicting_rows_flagged(self):
        rows = run([claim_row(), claim_row(), claim_row(cid="C000101"), claim_row(cid="C000101", units="9")])
        self.assertEqual(rows[1]["category"], "duplicate_row")
        self.assertEqual([r["category"] for r in rows[2:]], ["must_correct", "must_correct"])

    def test_missing_auth_is_automatically_filled_when_unique(self):
        r = run([claim_row(auth="")])[0]
        self.assertEqual((r["category"], r["denial_codes"]), ("payable", []))
        self.assertEqual(r["automatic_fixes"][0]["after"], "AUTH00001")
        self.assertEqual(r["clean"]["auth_id"], "AUTH00001")
        self.assertEqual(r["raw"]["auth_id"], "")


class Ledger(unittest.TestCase):
    def hist(self, cid, status, units="4", sub="2026-07-02", dos="2026-07-01", code=""):
        return claim_row(cid=cid, units=units, sub=sub, dos=dos) + [status, "1", code, ""]

    def test_paid_history_consumes_authorization_denied_does_not(self):
        ds = dataset([self.hist("C000001", "paid", "6", dos="2026-07-02"),
                      self.hist("C000002", "denied", "9", dos="2026-07-03", code="D01")])
        ok = run([claim_row(units="4", dos="2026-07-01")], ds)[0]
        self.assertEqual(ok["denial_codes"], [])                    # 10 - 6 = 4 left, exact fit
        over = run([claim_row(units="5", dos="2026-07-01")], ds)[0]
        self.assertEqual(over["denial_codes"], ["D02"])

    def test_denied_history_still_makes_duplicates(self):
        ds = dataset([self.hist("C000002", "denied", "2", dos="2026-07-01", code="D01")])
        self.assertEqual(run([claim_row(units="2")], ds)[0]["denial_codes"], ["D05"])
        self.assertEqual(run([claim_row(units="2", freq="7", orig="C000002")], ds)[0]["denial_codes"], [])

    def test_batch_claims_consume_in_submission_order(self):
        # file order is reversed; the earlier submission takes the balance first
        rows = run([claim_row(cid="C000201", dos="2026-07-02", sub="2026-07-09", units="6"),
                    claim_row(cid="C000202", dos="2026-07-01", sub="2026-07-05", units="6")])
        by = {r["claim_id"]: r for r in rows}
        self.assertEqual(by["C000202"]["denial_codes"], [])
        self.assertEqual(by["C000201"]["denial_codes"], ["D02"])

    def test_decided_earlier_claims_use_the_ledger_without_double_counting(self):
        a = claim_row(cid="C000301", dos="2026-07-01", sub="2026-07-05", units="8")
        b = claim_row(cid="C000302", dos="2026-07-02", sub="2026-07-06", units="2")
        rows = run([a, b])
        self.assertEqual([r["status"] for r in rows], ["paid", "paid"])  # 8 + 2 = exactly 10
        c = claim_row(cid="C000303", dos="2026-07-03", sub="2026-07-07", units="3")
        self.assertEqual(run([a, c])[1]["denial_codes"], ["D02"])        # definite denial retained

    def test_uncheckable_input_is_distinct_from_payable_and_denied_results(self):
        # a correction whose original is unknown cannot be decided: never assumed paid
        undecided = claim_row(cid="C000310", dos="2026-07-01", sub="2026-07-05", units="8", freq="7", orig="C000099")
        fits_anyway = claim_row(cid="C000311", dos="2026-07-02", sub="2026-07-06", units="2")
        needs_check = claim_row(cid="C000312", dos="2026-07-03", sub="2026-07-06", units="3", mod="")
        rows = run([undecided, fits_anyway, needs_check])
        self.assertEqual(rows[0]["status"], "unable_to_check")
        self.assertEqual(rows[1]["status"], "paid")                      # uncheckable rows do not invent paid units
        self.assertEqual(rows[2]["status"], "paid")    # no range-based claim result
        self.assertNotIn("balance_range", rows[2])
        later = claim_row(cid="C000313", dos="2026-07-04", sub="2026-07-01", units="1")
        # submitted before the undecided claim (and before its own DOS is invalid): must be corrected
        self.assertEqual(run([undecided, later])[1]["category"], "must_correct")

    def test_unknown_historical_outcomes_use_provisional_predictions(self):
        for status in ('pending', 'unknown', ''):
            with self.subTest(status=status):
                history = self.hist('C000001', status, units='8')
                ds = dataset([history])
                row = run([claim_row(units='4', dos='2026-07-02', sub='2026-07-06')], ds)[0]
                self.assertEqual(ds.history[0]['status'], 'pending')
                self.assertEqual(row['denial_codes'], ['D02'])
                self.assertEqual(row['evidence']['authorization_predicted_usage'], 8)
                self.assertEqual(row['evidence']['authorization_provisional_claim_ids'], ['C000001'])
                self.assertNotIn('balance_range', row)

    def test_pending_predicted_denial_does_not_consume_capacity(self):
        ds = dataset([self.hist('C000001', 'pending', units='13')])
        row = run([claim_row(units='4', dos='2026-07-02', sub='2026-07-06')], ds)[0]
        self.assertEqual(row['status'], 'paid')
        self.assertEqual(row['evidence']['authorization_remaining'], 10)
        duplicate = run([claim_row(units='1')], ds)[0]
        self.assertIn('D05', duplicate['denial_codes'])

    def test_same_day_unknown_history_and_final_outcomes_update_projected_usage(self):
        pending = self.hist('C000001', 'pending', units='8', sub='2026-07-05')
        ds = dataset([pending])
        target = claim_row(units='4', dos='2026-07-02', sub='2026-07-05')
        self.assertEqual(run([target], ds)[0]['denial_codes'], ['D02'])
        settled = list(pending)
        settled[-4], settled[-2] = 'denied', 'D01'
        from claimcheck.normalize import HISTORY_EXTRA
        ds.load_text('history', csv_text([settled], CLAIM_FIELDS + HISTORY_EXTRA))
        self.assertEqual(run([target], ds)[0]['status'], 'paid')

    def test_user_override_changes_ledger(self):
        rows = [claim_row(cid="C000401", dos="2026-07-01", sub="2026-07-05", units="8"),
                claim_row(cid="C000402", dos="2026-07-02", sub="2026-07-06", units="8")]
        before = run(rows)
        self.assertEqual(before[1]["denial_codes"], ["D02"])
        after = run(rows, overrides={"C000401": dict(status="denied", denial_codes=["D99"], note="payer said so")})
        self.assertEqual(after[1]["denial_codes"], [])
        self.assertEqual(after[0]["status"], "denied")
        self.assertEqual(after[0]["predicted_status"], "paid")  # original prediction retained beside override

    def test_outcomes_in_history_never_influence_a_batch_claim_directly(self):
        ds = dataset([self.hist("C000009", "denied", "1", dos="2026-07-04", code="D99")])
        self.assertEqual(run([claim_row(dos="2026-07-01")], ds)[0]["denial_codes"], [])


class Export(unittest.TestCase):
    def test_original_columns_preserved_with_results_appended(self):
        text = csv_text([claim_row(payer="Summit Care", dos="07/01/2026", sub="2026-07-05")]).replace("billed_amount", "billed_amount,extra")
        text = text.replace("10.0", "10.0,keepme")
        head, recs = parse_claims_csv(text)
        rows = run_batch(recs, dataset(), baseline_store())
        out = list(csv.DictReader(io.StringIO(export_csv(rows, head))))[0]
        self.assertEqual(out["payer"], "Summit Care")
        self.assertEqual(out["date_of_service"], "07/01/2026")
        self.assertEqual(out["extra"], "keepme")
        self.assertEqual(out["predicted_status"], "paid")
        self.assertIn("payer", out["normalisation_changes"])


class ClaimReferenceDetails(unittest.TestCase):
    def test_duplicate_reference_links_to_earlier_processed_batch_line(self):
        rows = run([claim_row(cid='C000202', sub='2026-07-06', units='1'),
                    claim_row(cid='C000201', sub='2026-07-05', units='1')])
        prior = rows[0]['duplicate_submissions']
        self.assertIn('D05', rows[0]['denial_codes'])
        self.assertEqual(len(prior), 1)
        self.assertEqual((prior[0]['source'], prior[0]['line'], prior[0]['claim']['claim_id']),
                         ('batch', 3, 'C000201'))
        self.assertFalse(rows[1]['duplicate_submissions'])

    def test_historical_duplicate_retains_actual_outcome_and_claim_details(self):
        for status, code in [('paid', ''), ('denied', 'D01')]:
            history = claim_row(cid='C000001', units='1') + [status, '1', code, 'Original outcome']
            row = run([claim_row(units='1')], dataset([history]))[0]
            prior = row['duplicate_submissions'][0]
            self.assertEqual(prior['source'], 'history')
            self.assertIsNone(prior['line'])
            self.assertEqual(prior['status'], status)
            self.assertEqual(prior['claim']['date_of_service'], '2026-07-01')
            self.assertEqual(prior['claim']['submitted_date'], '2026-07-05')
            self.assertEqual(prior['claim']['claim_id'], 'C000001')

    def test_duplicate_references_distinguish_history_from_batch_with_same_id(self):
        history = claim_row(cid='C000001', units='1') + ['paid', '1', '', '']
        rows = run([claim_row(cid='C000001', units='1'), claim_row(cid='C000002', units='1')], dataset([history]))
        prior = rows[1]['duplicate_submissions']
        self.assertEqual([s['source'] for s in prior], ['history', 'batch'])
        self.assertEqual([s['line'] for s in prior], [None, 2])

    def test_related_records_are_scoped_to_claim_and_balance_is_from_its_check(self):
        ds = dataset()
        rows = run([claim_row(cid='C000201', units='4'),
                    claim_row(cid='C000202', dos='2026-07-02', units='5')], ds)
        first, second = rows
        self.assertEqual(first['evidence']['authorization_remaining'], 10)
        self.assertEqual(second['evidence']['authorization_remaining'], 6)
        for row in rows:
            refs = row['reference_data']
            self.assertEqual([a['auth_id'] for a in refs['authorizations']], ['AUTH00001'])
            self.assertTrue(refs['authorizations'][0]['referenced'])
            self.assertEqual(refs['authorizations'][0]['issues'], [])
            self.assertEqual(len(refs['evv_visits']), 1)
            visit = refs['evv_visits'][0]
            self.assertEqual(visit['patient_id'], row['clean']['patient_id'])
            self.assertEqual(visit['service_code'], row['clean']['service_code'])
            self.assertEqual(visit['clock_in'][:10], row['clean']['date_of_service'])
            self.assertEqual(visit['duration_minutes'], 180)

    def test_wrong_referenced_authorization_is_shown_with_mismatches_and_alternative(self):
        row = run([claim_row(auth='AUTH00002')], autofix=False)[0]
        auths = row['reference_data']['authorizations']
        self.assertEqual([a['auth_id'] for a in auths], ['AUTH00002', 'AUTH00001'])
        self.assertTrue(auths[0]['referenced'])
        self.assertEqual(set(auths[0]['issues']), {'Payer does not match', 'Patient does not match', 'Service does not match'})
        self.assertFalse(auths[1]['referenced'])
        self.assertEqual(auths[1]['issues'], [])

    def test_unverified_and_overnight_visits_remain_visible_on_clock_in_date(self):
        ds = dataset()
        ds.load_text('evv', EVV +
            'V000020,CG0002,PT0001,T1005,2026-07-01 23:00,2026-07-02 01:00,manual_edit_pending\n')
        rows = run([claim_row(cid='C000301', units='1'),
                    claim_row(cid='C000302', dos='2026-07-02', units='1')], ds)
        visits = rows[0]['reference_data']['evv_visits']
        self.assertEqual(len(visits), 2)
        overnight = next(v for v in visits if v['visit_id'] == 'V000020')
        self.assertEqual(overnight['duration_minutes'], 120)
        self.assertEqual(overnight['verification_status'], 'manual_edit_pending')
        self.assertEqual(rows[0]['evidence']['verified_minutes'], 180)
        self.assertNotIn('V000020', [v['visit_id'] for v in rows[1]['reference_data']['evv_visits']])

    def test_missing_references_have_empty_visits_and_useful_related_coverage(self):
        row = run([claim_row(dos='2026-07-20', sub='2026-07-20', auth='AUTH99999')], autofix=False)[0]
        refs = row['reference_data']
        self.assertEqual(refs['evv_visits'], [])
        self.assertTrue(refs['authorizations'])
        self.assertFalse(any(a['referenced'] for a in refs['authorizations']))


if __name__ == "__main__":
    unittest.main()
