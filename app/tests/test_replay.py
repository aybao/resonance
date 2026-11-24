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
        def inspect(records, ds, rules, overrides):
            self.assertTrue(all(row['submitted_date'] < '2026-06-01' for row in ds.history))
            self.assertTrue(all('status' not in row['clean'] for row in records))
            seen.extend(records)
            return original(records, ds, rules, overrides)
        with patch.object(module, 'run_batch', inspect):
            result = self.session.advance_demo('2026-06-30')
            self.assertFalse(seen)
            self.assertIsNone(result['report'])
            self.assertEqual(result['results']['rows'], [])
            csv = (DEMO_DIR / 'claim-checking-csvs' / 'claims_2026-06-01_to_2026-06-30.csv').read_text()
            self.session.upload_claims(csv)
        self.assertEqual(len(seen), 361)
        self.assertEqual(self.session.demo.last_report['claims'], 361)
        self.session.advance_demo('2026-07-31')
        self.assertIsNone(self.session.demo.last_report)
        self.assertEqual(self.session.rows, [])
        self.assertTrue(any(r['submitted_date'] > '2026-06-30' for r in self.session.dataset.history))

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
