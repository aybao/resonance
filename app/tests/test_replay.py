"""Time-limited demo state, leakage prevention, source bulletins and explicit exceptions."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from claimcheck.replay_demo import claims_csv
from claimcheck.session import Session
from claimcheck.rules import baseline_store
from replay_history import run_replay, accept_bulletin


class HistoricalDemo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.session = Session(state_dir=self.tmp.name)

    def tearDown(self):
        self.session.end_demo()
        self.tmp.cleanup()

    def test_demo_prunes_history_and_restores_full_state_without_writing_it(self):
        full_rules = self.session.rules.snapshot()
        full_history = copy.deepcopy(self.session.dataset.history)
        path = Path(self.tmp.name) / 'rules.json'
        saved = path.read_bytes()
        self.session.start_demo()
        self.assertEqual(self.session.status()['mode'], 'demo')
        self.assertTrue(all(r['submitted_date'] <= '2026-06-30' for r in self.session.dataset.history))
        self.assertTrue(all(v['clock_in'][:10] <= '2026-06-30' for v in self.session.dataset.visits))
        self.assertEqual(self.session.rules.resolve('PY01', '2026-09-01')['params']['timely_filing_days'], 30)
        accept_bulletin(self.session, 'PY01')
        self.assertEqual(path.read_bytes(), saved)
        self.session.end_demo()
        self.assertEqual(self.session.rules.snapshot(), full_rules)
        self.assertEqual(self.session.dataset.history, full_history)

    def test_advance_updates_data_and_upload_withholds_batch_outcomes(self):
        self.session.start_demo()
        self.assertEqual(self.session.demo.as_of, '2026-04-30')
        from claimcheck import session as module
        from claimcheck.replay_demo import DEMO_DIR
        original = module.run_batch
        seen = []
        def inspect(records, ds, rules, overrides, **kwargs):
            self.assertTrue(all(row['submitted_date'] < '2026-06-01' for row in ds.history))
            self.assertTrue(all('status' not in row['clean'] for row in records))
            if kwargs.get("autofix", True):
                seen.extend(records)
            return original(records, ds, rules, overrides, **kwargs)
        with patch.object(module, 'run_batch', inspect):
            result = self.session.advance_demo('2026-06-30')
            self.assertFalse(seen)
            self.assertIsNone(result['report'])
            self.assertEqual(result['results']['rows'], [])
            self.assertTrue(all(r['submitted_date'] < '2026-06-01' for r in self.session.dataset.history))
            history_ids = {r['claim_id'] for r in self.session.dataset.history}
            june_ids = {r['claim_id'] for r in self.session.demo.source.history if r['submitted_date'].startswith('2026-06')}
            self.assertTrue(history_ids.isdisjoint(june_ids))
            csv = (DEMO_DIR / 'claim-checking-csvs' / 'claims_2026-06-01_to_2026-06-30.csv').read_text()
            self.session.upload_claims(csv)
        self.assertEqual(len(seen), 361)
        self.assertEqual(self.session.demo.last_report['claims'], 361)
        report = self.session.demo.last_report
        self.assertEqual(report['unexplained_d99'], 7)
        self.assertEqual(report['known_exceptions'], 7)
        self.assertEqual(report['unexpected_mismatches'], 0)
        self.assertAlmostEqual(report['accuracy'], 354 / 361)
        self.assertTrue(all('Unexplained historical D99 denial' in r['exception_reason'] for r in report['mismatches']))
        status = self.session.status()['demo']
        self.assertEqual(status['as_of'], '2026-06-30')
        self.assertEqual(status['reference_data_as_of'], '2026-06-30')
        self.assertEqual(status['prediction_history_before'], '2026-06-02')
        self.session.advance_demo('2026-07-31')
        self.assertIsNone(self.session.demo.last_report)
        self.assertEqual(self.session.rows, [])
        self.assertTrue(all(r['submitted_date'] < '2026-07-01' for r in self.session.dataset.history))
        july_history_ids = {r['claim_id'] for r in self.session.dataset.history}
        self.assertTrue(june_ids.issubset(july_history_ids))
        self.assertEqual(self.session.status()['demo']['pending_month'], '2026-07')

    def test_rule_acceptance_recomputes_live_report_and_false_alarms(self):
        from claimcheck.replay_demo import DEMO_DIR
        self.session.start_demo()
        self.session.advance_demo('2026-09-30')
        csv = (DEMO_DIR / 'claim-checking-csvs' / 'claims_2026-09-01_to_2026-09-30.csv').read_text()
        before = self.session.upload_claims(csv)
        self.assertEqual(before['demo_report']['false_alarms'], 26)
        self.assertEqual(self.session.find('C005332')['denial_codes'], ['D03'])
        accept_bulletin(self.session, 'PY01')
        after = self.session.results()
        self.assertEqual(after['demo_report']['false_alarms'], 9)
        self.assertEqual(after['demo_report'], self.session.demo.last_report)
        self.assertEqual(self.session.find('C005332')['status'], 'paid')
        self.assertEqual(self.session.find('C005332')['denial_codes'], [])
        # The source outcome is used for comparison, not to set the prediction.
        original_predictions = [(r['claim_id'], r['status'], r['denial_codes']) for r in after['rows']]
        actual = next(r for r in self.session.demo.source.history if r['claim_id'] == 'C005332')
        actual['status'], actual['denial_code'] = 'denied', 'D99'
        self.session._recheck()
        self.assertEqual(original_predictions, [(r['claim_id'], r['status'], r['denial_codes']) for r in self.session.rows])
        self.assertEqual(self.session.results()['demo_report']['false_alarms'], 9)
        self.assertEqual(self.session.results()['demo_report']['missed_denials'], after['demo_report']['missed_denials'] + 1)

    def test_september_both_bulletins_match_verified_investigation(self):
        from claimcheck.replay_demo import DEMO_DIR
        from claimcheck.rules import full_store
        self.session.start_demo()
        self.session.advance_demo('2026-09-30')
        self.session.rules = full_store(self.session.rules.path)
        text = (DEMO_DIR / 'claim-checking-csvs' / 'claims_2026-09-01_to_2026-09-30.csv').read_text()
        report = self.session.upload_claims(text)['demo_report']
        self.assertEqual(report['status_and_code_set_correct'], 805)
        self.assertEqual(report['claims'], 834)
        self.assertEqual(report['known_exceptions'], 29)
        self.assertEqual(report['unexpected_mismatches'], 0)
        self.assertEqual(report['false_alarms'], 1)
        self.assertEqual(report['missed_denials'], 21)
        self.assertTrue(report['passed'])
        # Removing the bulletins changes real predictions, not the baseline.
        self.session.rules = baseline_store()
        self.session._recheck()
        changed = self.session.demo.last_report
        self.assertGreater(changed['unexpected_mismatches'], 0)
        self.assertFalse(changed['passed'])
        self.assertEqual(changed['false_alarms'], 26)

    def test_rejects_backwards_dates_and_reference_replacement(self):
        self.session.start_demo()
        for date in ['invalid', '2026-04-29', '2026-04-30']:
            with self.assertRaises(ValueError):
                self.session.advance_demo(date)
        with self.assertRaises(ValueError):
            self.session.load_sample_data()
        with self.assertRaises(ValueError):
            self.session.load_reference('history', 'anything')

    def test_empty_and_invalid_advances_preserve_current_state(self):
        self.session.start_demo()
        before = self.session.dataset
        with self.assertRaises(ValueError):
            self.session.advance_demo('2026-06-31')
        self.assertIs(self.session.dataset, before)

    def test_complete_replay_passes_only_with_exact_documented_exceptions(self):
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            reports = run_replay(self.session)
        self.assertTrue(all(r['passed'] for r in reports), [(r['through_date'], r['unexpected_mismatches']) for r in reports])
        self.assertTrue(all(r['accuracy'] < 1 for r in reports))
        self.assertTrue(all(len(r['mismatches']) == r['known_exceptions'] for r in reports))
        # Removing one known exception must cause a real failure, not silently whitelist it.
        from claimcheck import replay_demo
        original = json.loads(replay_demo.EXCEPTIONS_PATH.read_text())
        first = reports[0]['mismatches'][0]['claim_id']
        original.pop(first)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'exceptions.json'
            path.write_text(json.dumps(original))
            self.session.start_demo('2026-06-30')
            with patch.object(replay_demo, 'EXCEPTIONS_PATH', path):
                report = self.session.demo.replay('2026-07-31')
            self.assertFalse(report['passed'])
            self.assertEqual(report['unexpected_mismatches'], 1)

    def test_pristine_old_baseline_migrates_to_full_but_custom_rules_are_preserved(self):
        path = Path(self.tmp.name) / 'legacy'
        rules = baseline_store(path / 'rules.json')
        rules.save()
        self.assertEqual(Session(state_dir=path).rules.resolve('PY01', '2026-08-01')['params']['timely_filing_days'], 45)
        rules.add_version('PY01', '2026-07-01', {'timely_filing_days': 35}, source='User memo')
        rules.save()
        self.assertEqual(Session(state_dir=path).rules.resolve('PY01', '2026-08-01')['params']['timely_filing_days'], 35)
