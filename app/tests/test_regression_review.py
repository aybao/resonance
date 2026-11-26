"""Execution gates, explicit semantic approval, stale reviews and runtime checks."""
import copy
from pathlib import Path
import tempfile
import unittest

from claimcheck.drafts import activate, chat, evaluate, new_draft, DraftError
from claimcheck.examples import regression_report
from claimcheck.pipeline import Dataset, load_sample
from claimcheck.session import Session
from tests.test_drafts import UPDATE, SAMPLE, base_store, fake_ai, approval, approved_activate


class RegressionReview(unittest.TestCase):
    def setUp(self):
        self.store, self.ds = base_store(), Dataset()

    def draft(self, proposal=None):
        d = new_draft()
        chat(d, self.store, self.ds, fake_ai(proposal or UPDATE), 'Apply supplied test source')
        return d

    def test_execution_failure_never_offers_semantic_approval_or_mutates_rules(self):
        d = self.draft({**UPDATE, 'code': 'def apply_rule(claim, context, failures):\n    return 42'})
        ev = evaluate(d, self.store, self.ds)
        self.assertEqual(ev['execution']['status'], 'failed')
        self.assertIn('return a list', ' '.join(ev['execution']['errors']))
        self.assertIsNone(ev['review'])
        before = self.store.snapshot()
        with self.assertRaises(DraftError):
            activate(d, self.store, self.ds, approve_synthetic=True, approve_history=True)
        self.assertEqual(before, self.store.snapshot())
        d = self.draft()
        d['examples'] = []
        ev = evaluate(d, self.store, self.ds)
        self.assertEqual(ev['execution']['status'], 'blocked')
        self.assertIsNone(ev['review'])

    def test_changed_synthetic_examples_need_one_explicit_group_approval(self):
        d = self.draft()
        ev = evaluate(d, self.store, self.ds)
        self.assertEqual(ev['execution']['status'], 'passed')
        self.assertGreater(ev['review']['synthetic_count'], 0)
        with self.assertRaisesRegex(DraftError, 'synthetic'):
            activate(d, self.store, self.ds, review_token=ev['review']['token'])
        with self.assertRaisesRegex(DraftError, 'synthetic'):
            activate(d, self.store, self.ds, review_token=ev['review']['token'], approve_synthetic='true')
        version = activate(d, self.store, self.ds, **approval(d, self.store, self.ds))
        self.assertTrue(version['review']['approved_synthetic'])
        self.assertFalse(d['decisions'])

    def test_history_requires_separate_approval_and_reason(self):
        load_sample(self.ds, SAMPLE)
        d = self.draft()
        ev = evaluate(d, self.store, self.ds)
        self.assertGreater(ev['review']['historical_count'], 0)
        with self.assertRaisesRegex(DraftError, 'historical'):
            activate(d, self.store, self.ds, review_token=ev['review']['token'], approve_synthetic=True)
        with self.assertRaisesRegex(DraftError, 'reason/source'):
            activate(d, self.store, self.ds, **approval(d, self.store, self.ds))
        v = approved_activate(d, self.store, self.ds, 'Retroactive payer correction in test source')
        self.assertEqual(len(v['review']['historical_changes']), ev['review']['historical_count'])
        self.assertIn('Retroactive', v['review']['reason'])

    def test_historical_examples_are_not_truncated(self):
        load_sample(self.ds, SAMPLE)
        d = self.draft()
        ev = evaluate(d, self.store, self.ds)
        self.assertGreater(ev['impact']['changed_count'], 25)
        self.assertEqual(len(ev['impact']['examples']), ev['impact']['changed_count'])
        self.assertTrue(all(c['date_of_service'] >= '2026-08-01' for c in ev['impact']['examples']))

    def test_approval_is_invalidated_by_revision_or_baseline_changes(self):
        d = self.draft()
        consent = approval(d, self.store, self.ds)
        d['code'] = d['code'].replace('> 50', '> 49')
        with self.assertRaisesRegex(DraftError, 'refresh'):
            activate(d, self.store, self.ds, **consent)
        consent = approval(d, self.store, self.ds)
        self.store.add_version('PY01', '2026-07-01', {'daily_max_units': 99})
        with self.assertRaisesRegex(DraftError, 'refresh'):
            activate(d, self.store, self.ds, **consent)

    def test_new_history_invalidates_previous_approval(self):
        d = self.draft()
        consent = approval(d, self.store, self.ds)
        load_sample(self.ds, SAMPLE)
        with self.assertRaisesRegex(DraftError, 'refresh'):
            activate(d, self.store, self.ds, 'Reviewed source', **consent)

    def test_saved_regressions_are_replaced_and_old_expectations_preserved(self):
        first = self.draft()
        approved_activate(first, self.store, self.ds)
        # Deliberately retain one saved case that is not in the next proposal.
        saved = copy.deepcopy(self.store.acceptance[0])
        saved['id'], saved['title'] = 'independent-saved-case', 'Previously reviewed independent case'
        self.store.acceptance.append(saved)
        second = self.draft({**UPDATE, 'code': UPDATE['code'].replace('50', '30')})
        ev = evaluate(second, self.store, self.ds)
        self.assertTrue(ev['regressions'])
        with self.assertRaisesRegex(DraftError, 'reason/source'):
            activate(second, self.store, self.ds, **approval(second, self.store, self.ds))
        approved_activate(second, self.store, self.ds, 'Payer corrected test source')
        self.assertTrue(saved.get('superseded_by'))
        self.assertTrue(any(t.get('supersedes') == saved['id'] for t in self.store.acceptance))
        self.assertEqual(regression_report(self.store)['failed'], 0)

    def test_runtime_regressions_run_again_for_claim_upload_and_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Session(state_dir=Path(tmp))
            session.rules = self.store
            approved_activate(self.draft(), self.store, self.ds)
            first = session.load_sample_claims()['regression_checks']
            self.assertGreater(first['total'], 0)
            self.assertEqual(first['failed'], 0)
            # Simulate an unexpected rule edit outside the approval workflow.
            self.store.updates[-1]['code'] = UPDATE['code'].replace('50', '30')
            session.edit_claim(session.rows[0]['line'], {})
            second = session.results()['regression_checks']
            self.assertGreater(second['failed'], 0)
            self.assertTrue(second['failures'])
            self.assertTrue(session.results()['rows'])
