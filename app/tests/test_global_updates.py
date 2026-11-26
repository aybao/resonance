"""Applicability lives in code, so one accepted update can span payers and dates."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from claimcheck.ai import TOOL
from claimcheck.demo_fixtures import NORTHWIND_CODE, HARBOR_CODE, bulletin_proposal
from claimcheck.drafts import new_draft, chat, evaluate, activate
from claimcheck.examples import run_scenario, validate_scenario
from claimcheck.pipeline import Dataset
from claimcheck.rules import baseline_store, RuleStore
from tests.test_drafts import approved_activate, fake_ai, UPDATE
from tests.test_code_rules import WHOLE_HOURS

MULTI_CODE = (NORTHWIND_CODE.replace('def apply_rule(', 'def northwind(') + '\n' +
              HARBOR_CODE.replace('def apply_rule(', 'def harbor(') + '\n' +
              'def apply_rule(claim, context, failures):\n'
              '    return harbor(claim, context, northwind(claim, context, failures))\n')


def multi_proposal():
    return dict(code=MULTI_CODE, summary='Northwind filing and Harbor weekend update',
                assistant_message='Two payer changes with different service-date boundaries.',
                source_quote='Northwind July bulletin and Harbor August notice.',
                examples=copy.deepcopy(bulletin_proposal('PY01')['examples'] + bulletin_proposal('PY02')['examples']))


def scenario(payer, day, sub=None, modifier=''):
    return dict(claim=dict(payer=payer, service_code='T1019', date_of_service=day,
                submitted_date=sub or day, units=4, modifier=modifier), context=dict(evv_minutes=[60]))


class GlobalCodeUpdates(unittest.TestCase):
    def setUp(self):
        self.store = baseline_store()
        self.ds = Dataset()

    def test_contract_has_no_scope_fields_and_examples_carry_their_own_payer(self):
        properties = TOOL['input_schema']['properties']
        self.assertFalse({'payer_id', 'effective_from', 'payer_name', 'params'} & properties.keys())
        d = new_draft()
        ai = fake_ai(multi_proposal())
        chat(d, self.store, self.ds, ai, 'Apply both bulletins')
        self.assertEqual(len(ai.calls), 1)
        self.assertNotIn('payer_id', d)
        self.assertNotIn('effective_from', d)
        self.assertTrue(evaluate(d, self.store, self.ds)['can_activate'])
        self.assertFalse(validate_scenario(scenario('PY01', '2026-08-01')))
        bad = scenario('PY01', '2026-08-01')
        del bad['claim']['payer']
        self.assertTrue(validate_scenario(bad))

    def test_one_acceptance_applies_two_payers_and_dates_and_preserves_controls(self):
        d = new_draft()
        chat(d, self.store, self.ds, fake_ai(multi_proposal()), 'Apply both')
        before = self.store.snapshot()
        self.assertTrue(any(e['changed'] for e in evaluate(d, self.store, self.ds)['examples']))
        self.assertEqual(self.store.snapshot(), before)  # A proposal never changes accepted state.
        with tempfile.TemporaryDirectory() as tmp:
            self.store.path = Path(tmp) / 'rules.json'
            update = approved_activate(d, self.store, self.ds)
            store = RuleStore(self.store.path)
        self.assertEqual(len(store.updates), 1)
        self.assertEqual(update['code'], MULTI_CODE)
        self.assertNotIn('payer_id', update)
        self.assertNotIn('effective_from', update)
        cases = [
            (scenario('PY01', '2026-07-31', '2026-09-09'), ['D03']),
            (scenario('PY01', '2026-08-01', '2026-09-10'), []),
            (scenario('PY01', '2026-08-01', '2026-09-15'), []),
            (scenario('PY01', '2026-08-01', '2026-09-16'), ['D03']),
            (scenario('PY02', '2026-08-29', modifier='U3'), ['D04']),
            (scenario('PY02', '2026-09-05', modifier='U3'), []),
            (scenario('PY02', '2026-09-05', modifier='U2'), ['D04']),
            (scenario('PY02', '2026-09-07', modifier='U2'), []),
            (scenario('PY03', '2026-08-01', '2026-09-10'), []),
        ]
        for claim, expected in cases:
            with self.subTest(claim=claim):
                self.assertEqual(run_scenario(claim, store)['codes'], expected)
        self.assertEqual({t['payer_id'] for t in store.acceptance}, {'PY01', 'PY02'})
        self.assertTrue(all(t['basis'] == 'accepted_with_rule' for t in store.acceptance))

    def test_later_update_preserves_earlier_accepted_code_outside_its_own_date_condition(self):
        self.store.add_update(UPDATE['code'])  # 50 days from August.
        later = UPDATE['code'].replace('50', '40').replace('2026-08-01', '2026-10-01')
        self.store.add_update(later)
        self.assertEqual(run_scenario(scenario('PY01', '2026-09-01', '2026-10-16'), self.store)['codes'], [])
        self.assertEqual(run_scenario(scenario('PY01', '2026-10-01', '2026-11-15'), self.store)['codes'], ['D03'])
        self.assertEqual(run_scenario(scenario('PY03', '2026-10-01', '2026-11-15'), self.store)['codes'], [])

    def test_legacy_saved_code_is_retained_alongside_new_global_updates(self):
        self.store.add_version('PY03', '2026-09-01', {}, code=WHOLE_HOURS, source='Legacy test contract')
        old = self.store.snapshot()
        old.pop('updates')  # Exact older on-disk format.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'rules.json'
            path.write_text(json.dumps(old))
            store = RuleStore(path)
            store.add_update(NORTHWIND_CODE)
            store.save()
            loaded = RuleStore(path)
        claim = scenario('PY03', '2026-09-01')
        claim['claim']['units'] = 5
        claim['context']['evv_minutes'] = [75]
        self.assertEqual(run_scenario(claim, loaded)['codes'], ['D10'])
        self.assertEqual(run_scenario(scenario('PY01', '2026-08-01', '2026-09-10'), loaded)['codes'], [])
        self.assertEqual(loaded.resolve('PY03', '2026-09-01')['code'], WHOLE_HOURS)

    def test_followup_replaces_only_the_pending_update(self):
        d = new_draft()
        chat(d, self.store, self.ds, fake_ai(multi_proposal()), 'Both')
        chat(d, self.store, self.ds, fake_ai({**multi_proposal(), 'code': MULTI_CODE.replace('> 45', '> 60')}), 'Make Northwind 60 days')
        self.assertEqual(self.store.updates, [])
        approved_activate(d, self.store, self.ds)
        self.assertEqual(len(self.store.updates), 1)
        self.assertEqual(run_scenario(scenario('PY01', '2026-08-01', '2026-09-25'), self.store)['codes'], [])
