"""Committed cohort results must reproduce from original source files, not stale artifacts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


class CommittedResults(unittest.TestCase):
    def test_original_data_rebuild_matches_committed_metrics_and_predictions(self):
        with tempfile.TemporaryDirectory() as tmp:
            rebuilt = Path(tmp)
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
            result = subprocess.run([sys.executable, str(ROOT / 'run_experiment.py'), '--output-dir', tmp],
                                    cwd=ROOT.parent, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = json.loads((ROOT / 'claim_metrics.json').read_text())
            actual = json.loads((rebuilt / 'claim_metrics.json').read_text())
            # Cleaning records include destination paths, which necessarily differ.
            for metrics in (expected, actual):
                for report in metrics['cleaning'].values():
                    report.pop('source', None)
                    report.pop('output', None)
            self.assertEqual(actual, expected,
                'Cohort metrics changed; inspect the source/rules before updating the committed result.')
            for name in ('unknown_outcomes_metrics.json', 'test_result.csv', 'unknown_outcomes_predictions.csv'):
                self.assertEqual((rebuilt / name).read_bytes(), (ROOT / name).read_bytes(), name)
            self.assertNotIn('pending_claims_review', actual)
            self.assertEqual(actual['main']['status_and_code_set_correct'], 1061)
            self.assertEqual(actual['main']['false_alarms'], 2)


if __name__ == '__main__':
    unittest.main()
