"""Novel rules execute, persist and fail closed without trusting model outcomes."""
import copy
from pathlib import Path
import tempfile
import unittest

from claimcheck.code_rules import apply_code, validate_code, RuleCodeError
from claimcheck.drafts import chat, evaluate, decide, activate, new_draft, refresh_boundary_examples, DraftError
from claimcheck.examples import run_scenario
from claimcheck.pipeline import Dataset
from claimcheck.rules import baseline_store, full_store, RuleStore
from tests.test_drafts import fake_ai

WHOLE_HOURS = '''def apply_rule(claim, context, failures):
    if claim['payer'] != 'PY03' or claim['date_of_service'] < '2026-09-01':
        return failures
    if int(claim['units']) % 4 != 0:
        failures.append({'code': 'D10', 'meaning': 'Whole-hour billing required',
            'reason': 'Units must be a multiple of four', 'suggestion': 'Review the supplied contract; do not alter documented care automatically.'})
    return failures
'''


def proposal(code=WHOLE_HOURS):
    return dict(assistant_message='The supplied test contract requires whole hours.', code=code, summary='Whole-hour billing', source_quote='Test contract: whole hours only',
                examples=[dict(title='Five units', claim=dict(payer='PY03', service_code='T1019', units=5, modifier='',
                    date_of_service='2026-09-01', submitted_date='2026-09-02'), context={'evv_minutes': [75]},
                    expected_codes=[])])  # Intentionally wrong model expectation.


class ExecutableRules(unittest.TestCase):
    def setUp(self):
        self.store, self.ds = baseline_store(), Dataset()

    def drafted(self, code=WHOLE_HOURS):
        d = new_draft()
        chat(d, self.store, self.ds, fake_ai(proposal(code)), 'Test contract says whole hours only')
        return d

    def test_novel_rule_actual_output_overrides_incorrect_ai_prediction(self):
        d = self.drafted()
        ev = evaluate(d, self.store, self.ds)
        ex = next(e for e in ev['examples'] if e['title'] == 'Five units')
        self.assertEqual(ex['before'], [])
        self.assertEqual(ex['actual']['codes'], ['D10'])
        self.assertEqual(ex['expected_codes'], [])
        self.assertTrue(ev['can_activate'])
        decide(d, ex['id'], 'confirm')
        self.assertFalse(evaluate(d, self.store, self.ds)['can_activate'])
        decide(d, ex['id'], 'set', ['D10'])
        for e in evaluate(d, self.store, self.ds)['examples']:
            if e['changed'] and not e['decision']:
                decide(d, e['id'], 'set', e['actual']['codes'])
        self.assertTrue(evaluate(d, self.store, self.ds)['can_activate'])
        with tempfile.TemporaryDirectory() as tmp:
            self.store.path = Path(tmp) / 'rules.json'
            activate(d, self.store, self.ds)
            loaded = RuleStore(self.store.path)
        scenario = copy.deepcopy(proposal()['examples'][0])
        scenario['payer_id'] = 'PY03'
        self.assertEqual(run_scenario(scenario, loaded)['codes'], ['D10'])
        scenario['claim'].update(date_of_service='2026-08-31', submitted_date='2026-08-31')
        self.assertEqual(run_scenario(scenario, loaded)['codes'], [])
        scenario['claim']['payer'] = 'PY01'
        self.assertEqual(run_scenario(scenario, loaded)['codes'], [])

    def test_revision_compares_with_accepted_rules_and_makes_one_call(self):
        d = self.drafted()
        ai = fake_ai(proposal(WHOLE_HOURS.replace('% 4', '% 2')))
        chat(d, self.store, self.ds, ai, 'Use half hours instead')
        self.assertEqual(len(ai.calls), 1)
        ex = next(e for e in evaluate(d, self.store, self.ds)['examples'] if e['title'] == 'Five units')
        self.assertEqual(ex['before'], [])
        self.assertEqual(ex['actual']['codes'], ['D10'])
        self.assertEqual(d['decisions'], {})

    def test_invalid_code_and_runtime_failure_block_activation(self):
        for code in ['import os', 'def apply_rule(claim, context, failures):\n    return 3',
                     'def apply_rule(claim, context, failures):\n    return claim["missing"]',
                     'def apply_rule(claim, context, failures):\n    while True:\n        pass']:
            with self.subTest(code=code):
                ev = evaluate(self.drafted(code), self.store, self.ds)
                self.assertFalse(ev['can_activate'])
                self.assertTrue(ev['problems'])

    def test_io_and_introspection_are_rejected(self):
        for body in ['return claim.__class__', 'import os\n    return failures', 'return open("secret")']:
            code = 'def apply_rule(claim, context, failures):\n    ' + body
            with self.assertRaises(RuleCodeError):
                apply_code(code, {}, {}, [])

    def test_worker_does_not_mutate_host_inputs_and_preserves_other_failures(self):
        failures = [dict(code='D07', meaning='Auth missing', reason='No auth', suggestion='Confirm auth')]
        claim = {'payer': 'PY03', 'date_of_service': '2026-09-01', 'units': '5'}
        result = apply_code(WHOLE_HOURS, claim, {}, failures)
        self.assertEqual([f['code'] for f in result], ['D07', 'D10'])
        self.assertEqual(len(failures), 1)
        self.assertEqual(claim, {'payer': 'PY03', 'date_of_service': '2026-09-01', 'units': '5'})

    def test_full_app_includes_dated_bulletins(self):
        rules = full_store()
        self.assertEqual(rules.resolve('PY01', '2026-07-31')['params']['timely_filing_days'], 30)
        self.assertEqual(rules.resolve('PY01', '2026-08-01')['params']['timely_filing_days'], 45)
        mods = rules.resolve('PY02', '2026-09-01')['params']['modifier_rules']
        self.assertEqual(next(m['modifier'] for m in mods if m['days']), 'U3')

    def test_code_context_excludes_recorded_labels(self):
        ai = fake_ai(proposal())
        self.ds.history = [dict(claim_id='C000001', status='SECRET_LABEL', denial_code='SECRET_CODE', units='4')]
        d = new_draft()
        chat(d, self.store, self.ds, ai, 'test')
        self.assertNotIn('SECRET_LABEL', ai.calls[0]['system'])
        self.assertNotIn('SECRET_CODE', ai.calls[0]['system'])

    def test_malformed_code_examples_block_acceptance(self):
        update = proposal()
        update['examples'].append(dict(title='Bad units', claim=dict(service_code='T1019', units='five',
            date_of_service='2026-09-01', submitted_date='2026-09-02'), context={}))
        d = new_draft()
        chat(d, self.store, self.ds, fake_ai(update), 'test')
        ev = evaluate(d, self.store, self.ds)
        self.assertFalse(ev['can_activate'])
        self.assertTrue(any('units' in p for p in ev['problems']))

    def test_memory_failure_does_not_damage_app_or_next_call(self):
        with self.assertRaises(RuleCodeError):
            apply_code('def apply_rule(claim, context, failures):\n    return list(range(1000000000))', {}, {}, [])
        self.assertEqual(apply_code('def apply_rule(claim, context, failures):\n    return failures', {}, {}, []), [])

    def test_execution_error_on_history_blocks_acceptance(self):
        from claimcheck.pipeline import load_sample
        ds = Dataset()
        load_sample(ds, Path(__file__).resolve().parent.parent / 'sample_data')
        code = "def apply_rule(claim, context, failures):\n    if claim['patient_id'] != 'PT9999':\n        return 7\n    return failures"
        d = self.drafted(code)
        ev = evaluate(d, self.store, ds)
        self.assertTrue(ev['impact']['errors'])
        self.assertFalse(ev['can_activate'])
