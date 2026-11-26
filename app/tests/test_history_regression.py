"""Runtime history replay: strict recorded exceptions, real counts and no label leakage."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from claimcheck.drafts import evaluate, new_draft, refresh_boundary_examples
from claimcheck.history import compare_regression, regression_report
from claimcheck.pipeline import Dataset, load_sample
from claimcheck.rules import full_store, baseline_store
from tests.test_drafts import SAMPLE
from tests.test_pipeline import dataset, claim_row


class HistoricalRegression(unittest.TestCase):
    def test_rule_update_evaluation_runs_historical_regression(self):
        ds = Dataset()
        load_sample(ds, SAMPLE)
        base = full_store()
        same = compare_regression(ds, base, full_store())
        self.assertEqual((same['new_failure_count'], same['resolved_count']), (0, 0))
        self.assertTrue(same['before']['passed'] and same['after']['passed'])
        # A harmful update (1-day filing limit) must surface newly failing recorded outcomes.
        draft = new_draft('PY01', '2026-08-01', {'timely_filing_days': 1}, source='test')
        refresh_boundary_examples(draft, base)
        ev = evaluate(draft, base, ds)
        hr = ev['historical_regression']
        self.assertGreater(hr['new_failure_count'], 0)
        self.assertFalse(hr['after']['passed'])
        self.assertTrue(all(f['payer'] == 'PY01' and f['date_of_service'] >= '2026-08-01' for f in hr['new_failures']))
        self.assertEqual(ev['review']['new_historical_failures'], hr['new_failure_count'])

    def test_exception_contract_is_strict_and_demo_periods_remain_scoped(self):
        from claimcheck.history import exception_matches
        row = dict(actual_status='denied', actual_code='D99', status='denied',
                   codes=['D09', 'D02'], remark='Original remark', error=None)
        expected = dict(actual_status='denied', actual_code='D99', predicted_status='denied',
                        predicted_codes=['D02', 'D09'], recorded_remark='Original remark',
                        replay_period_end='2026-07-31', additional_replay_period_ends=['2026-08-31'])
        self.assertTrue(exception_matches(row, expected))
        self.assertTrue(exception_matches(row, expected, '2026-08-31'))
        self.assertFalse(exception_matches(row, expected, '2026-09-30'))
        self.assertFalse(exception_matches(dict(row, remark='Changed remark'), expected))
        self.assertFalse(exception_matches(dict(row, error='Rule failed'), expected))
        unable = dict(expected, predicted_status='unable_to_check', predicted_codes=[])
        self.assertFalse(exception_matches(dict(row, status='unable_to_check', codes=[]), unable))

    def fixture(self, auth='AUTH00001', code='D99'):
        claim = claim_row(cid='C000101', dos='2026-07-02', sub='2026-07-06', units='1', auth=auth)
        ds = dataset([claim + ['denied', '0', code, 'Reviewed source remark']])
        expected = {'C000101': dict(actual_status='denied', actual_code=code,
                    predicted_status='paid', predicted_codes=[], reason='Inspected missing contract',
                    recorded_remark='Reviewed source remark', replay_period_end='2027-01-31')}
        return ds, expected

    def report(self, ds, expected, rules=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'exceptions.json'
            path.write_text(json.dumps(expected))
            before = path.read_bytes()
            result = regression_report(ds, rules or baseline_store(), path)
            self.assertEqual(before, path.read_bytes())
            return result

    def test_full_history_is_green_only_with_existing_recorded_exceptions(self):
        ds = Dataset()
        load_sample(ds, SAMPLE)
        result = regression_report(ds, full_store())
        self.assertGreater(result['total'], 1000)
        self.assertEqual(result['total'], len(ds.history))
        self.assertTrue(result['passed'])
        self.assertGreater(result['known_exceptions'], 0)
        self.assertEqual(result['matched'] + result['known_exceptions'], result['total'])
        self.assertLess(result['accuracy'], 1)

    def test_missing_exception_file_does_not_excuse_mismatches(self):
        ds, _ = self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            result = regression_report(ds, baseline_store(), Path(tmp) / 'missing.json')
        self.assertFalse(result['passed'])
        self.assertEqual(result['failed'], 1)
        self.assertEqual(result['known_exceptions'], 0)

    def test_recorded_exception_matches_exact_outcomes_codes_and_remark(self):
        ds, expected = self.fixture()
        result = self.report(ds, expected)
        self.assertTrue(result['passed'])
        self.assertEqual(result['matched'], 0)
        self.assertEqual(result['known_exceptions'], 1)
        self.assertEqual(result['exceptions'][0]['exception_reason'], 'Inspected missing contract')
        for field, value in [('actual_status', 'paid'), ('actual_code', 'D10'),
                             ('predicted_status', 'denied'), ('predicted_codes', ['D10']),
                             ('recorded_remark', 'A different case')]:
            with self.subTest(field=field):
                changed = copy.deepcopy(expected)
                changed['C000101'][field] = value
                self.assertEqual(self.report(ds, changed)['failed'], 1)

    def test_changed_known_exception_prediction_is_a_new_failure(self):
        ds, expected = self.fixture()
        rules = baseline_store()
        rules.add_update("""def apply_rule(claim, context, failures):
    failures.append({'code':'D10','meaning':'Test rule','reason':'Changed rule','suggestion':'Review'})
    return failures
""")
        result = self.report(ds, expected, rules)
        self.assertFalse(result['passed'])
        self.assertEqual(result['failures'][0]['codes'], ['D10'])
        self.assertEqual(result['known_exceptions'], 0)

    def test_execution_error_cannot_be_accepted_as_a_recorded_exception(self):
        ds, expected = self.fixture()
        expected['C000101'].update(predicted_status='unable_to_check', predicted_codes=[])
        rules = baseline_store()
        rules.add_update('def apply_rule(claim, context, failures):\n    return 42')
        result = self.report(ds, expected, rules)
        self.assertEqual(result['failed'], 1)
        self.assertTrue(result['failures'][0]['error'])

    def test_empty_history_is_neutral(self):
        result = self.report(dataset(), {})
        self.assertEqual(result['total'], 0)
        self.assertIsNone(result['passed'])
        self.assertIsNone(result['accuracy'])

    def test_history_is_checked_before_autofix_and_can_detect_its_recorded_code(self):
        ds, _ = self.fixture(auth='', code='D07')
        result = self.report(ds, {})
        self.assertTrue(result['passed'])
        self.assertEqual(result['matched'], 1)
        self.assertEqual(ds.history[0]['auth_id'], '')

    def test_pending_history_is_forecast_but_excluded_from_outcome_agreement(self):
        ds, _ = self.fixture()
        ds.history[0]['status'], ds.history[0]['denial_code'] = 'pending', ''
        result = self.report(ds, {})
        self.assertEqual(result['total'], 0)
        self.assertEqual(result['pending'], 1)
        self.assertIsNone(result['passed'])
        self.assertEqual(result['pending_predictions'][0]['status'], 'paid')
        self.assertEqual(ds.history[0]['status'], 'pending')
        rules = baseline_store()
        rules.add_update('def apply_rule(claim, context, failures):\n    return 42')
        failed = self.report(ds, {}, rules)
        self.assertEqual(failed['pending_errors'], 1)
        self.assertFalse(failed['passed'])


    def test_current_labels_do_not_enter_checking_or_exempt_failed_rules(self):
        from claimcheck import history
        ds, _ = self.fixture()
        actual = history.check_claim
        def check(claim, state, ctx, rules):
            self.assertNotIn('status', claim)
            self.assertNotIn('denial_code', claim)
            self.assertNotIn('remark', claim)
            return actual(claim, state, ctx, rules)
        with patch.object(history, 'check_claim', check):
            result = self.report(ds, {})
        self.assertEqual(result['failed'], 1)
        ds.history[0]['status'], ds.history[0]['denial_code'] = 'paid', ''
        self.assertEqual(self.report(ds, {})['matched'], 1)
