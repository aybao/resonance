"""Unknown-outcome simulation: temporal state, label isolation and honest scoring."""
import contextlib
import copy
import csv
import io
from pathlib import Path
import tempfile
import unittest

from claim_checker import Checker
from unknown_outcomes import evaluate, run, simulate


def fixture():
    def claim(cid, day, units):
        return dict(claim_id=cid, agency_id='AG001', payer='PY03', patient_id='PT0001',
                    service_code='T1019', date_of_service=day, submitted_date=day, units=str(units),
                    auth_id='AUTH10001', modifier='', frequency_code='1', original_claim_id='', billed_amount='10')
    prior = claim('C000001', '2026-09-01', 8)
    target = claim('C000002', '2026-10-01', 5)
    auth = dict(auth_id='AUTH10001', patient_id='PT0001', payer_id='PY03', service_code='T1019',
                start_date='2026-06-01', end_date='2026-12-31', units_authorized='10')
    visits = [dict(visit_id=f'V00000{i}', patient_id='PT0001', service_code='T1019',
                   clock_in=f'{day} 09:00', clock_out=f'{day} 12:00', verification_status='verified')
              for i, day in enumerate(('2026-09-01', '2026-10-01'), 1)]
    return prior, target, [auth], visits


class UnknownOutcomeTests(unittest.TestCase):
    def test_predicted_paid_unknown_consumes_balance(self):
        prior, target, auths, visits = fixture()
        model = Checker(auths, visits)
        ignored, _ = simulate([prior], [target], model, auths, {}, 'ignore')
        predicted, hidden = simulate([prior], [target], model, auths, {}, 'predict')
        self.assertEqual(ignored[target['claim_id']]['status'], 'paid')
        self.assertEqual(hidden[prior['claim_id']]['status'], 'paid')
        self.assertEqual(predicted[target['claim_id']]['codes'], ['D02'])
        self.assertEqual(predicted[target['claim_id']]['authorization_remaining'], 2)

    def test_predicted_denial_does_not_consume_units(self):
        prior, target, auths, visits = fixture()
        visits[0]['verification_status'] = 'rejected'
        model = Checker(auths, visits)
        predicted, hidden = simulate([prior], [target], model, auths, {}, 'predict')
        assumed, _ = simulate([prior], [target], model, auths, {}, 'assume_paid')
        self.assertEqual(hidden[prior['claim_id']]['status'], 'denied')
        self.assertEqual(predicted[target['claim_id']]['status'], 'paid')
        self.assertIn('D02', assumed[target['claim_id']]['codes'])

    def test_future_pending_claims_do_not_affect_earlier_targets(self):
        prior, target, auths, visits = fixture()
        prior.update(date_of_service='2026-11-01', submitted_date='2026-11-01')
        result, _ = simulate([], [target], Checker(auths, visits), auths, {}, 'predict', [prior])
        self.assertEqual(result[target['claim_id']]['status'], 'paid')

    def test_only_explicitly_visible_outcomes_affect_state(self):
        prior, target, auths, visits = fixture()
        model = Checker(auths, visits)
        original = copy.deepcopy([prior, target])
        first, _ = simulate([prior], [target], model, auths, {}, 'predict')
        prior.update(status='denied', denial_code='D99', paid_amount='0', remark='Hidden answer')
        target.update(status='paid', denial_code='', remark='Target answer')
        poisoned, _ = simulate([prior], [target], model, auths, {}, 'predict')
        self.assertEqual(first, poisoned)
        visible, _ = simulate([prior], [target], model, auths, {prior['claim_id']: 'denied'}, 'predict')
        self.assertEqual(visible[target['claim_id']]['status'], 'paid')
        self.assertEqual(first[target['claim_id']]['status'], 'denied')
        self.assertEqual(original[0]['units'], prior['units'])

    def test_external_duplicate_ids_are_not_counted_twice(self):
        prior, target, auths, visits = fixture()
        result, unknown = simulate([], [target], Checker(auths, visits), auths, {}, 'predict', [prior, prior, target])
        self.assertEqual(len(unknown), 1)
        self.assertEqual(result[target['claim_id']]['authorization_remaining'], 2)

    def test_same_day_order_sensitivity_is_explicit(self):
        prior, target, auths, visits = fixture()
        prior['submitted_date'] = target['submitted_date']
        early, _ = simulate([], [target], Checker(auths, visits), auths, {}, 'predict', [prior])
        late, _ = simulate([], [target], Checker(auths, visits), auths, {}, 'predict', [prior], pending_first=False)
        self.assertIn('D02', early[target['claim_id']]['codes'])
        self.assertEqual(late[target['claim_id']]['status'], 'paid')

    def test_denied_historical_claims_still_establish_submissions(self):
        prior, target, auths, visits = fixture()
        target['date_of_service'] = prior['date_of_service']
        result, _ = simulate([prior], [target], Checker(auths, visits), auths,
                             {prior['claim_id']: 'denied'}, 'ignore')
        self.assertIn('D05', result[target['claim_id']]['codes'])

    def test_results_are_reproducible_and_inputs_remain_unchanged(self):
        prior, target, auths, visits = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifacts = root / 'inputs'
            artifacts.mkdir()
            records = {'sorted_prev_data.csv': [dict(prior, status='paid', denial_code='')],
                       'sorted_test_data.csv': [dict(target, status='denied', denial_code='D02')],
                       'claims_to_check.csv': [dict(prior, claim_id='C000003')],
                       'authorizations.csv': auths, 'evv_visits.csv': visits}
            for name, rows in records.items():
                with (artifacts / name).open('w', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)
            before = {p.name: p.read_bytes() for p in artifacts.iterdir()}
            with contextlib.redirect_stdout(io.StringIO()):
                first = run(artifacts, root / 'first')
                second = run(artifacts, root / 'second')
            self.assertEqual(first, second)
            self.assertEqual(first['observed_history_control']['code_matches'], 1)
            for name in ('unknown_outcomes_metrics.json', 'unknown_outcomes_predictions.csv'):
                self.assertEqual((root / 'first' / name).read_bytes(), (root / 'second' / name).read_bytes())
            self.assertEqual(before, {p.name: p.read_bytes() for p in artifacts.iterdir()})


if __name__ == '__main__':
    unittest.main()
