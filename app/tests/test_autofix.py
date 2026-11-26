"""Supported automatic corrections, audit, ambiguity and sequential balances."""
import copy
import csv
import io
import json
import unittest

from claimcheck.normalize import CLAIM_FIELDS
from claimcheck.pipeline import parse_claims_csv, run_batch, export_csv
from claimcheck.rules import baseline_store
from tests.test_pipeline import claim_row, csv_text, dataset, AUTHS


def check(rows, ds=None, edits=None):
    _, records = parse_claims_csv(csv_text(rows))
    if edits:
        for record in records:
            record['edits'] = edits
    return run_batch(records, ds or dataset(), baseline_store())


class AutomaticFixes(unittest.TestCase):
    def test_missing_payer_from_supplied_authorization(self):
        row = check([claim_row(payer='')])[0]
        self.assertEqual(row['clean']['payer'], 'PY03')
        self.assertEqual(row['raw']['payer'], '')
        self.assertEqual(row['automatic_fixes'][0]['source_ids'], ['AUTH00001'])
        self.assertEqual(row['status'], 'paid')

    def test_missing_payer_and_auth_are_filled_together(self):
        row = check([claim_row(payer='', auth='')])[0]
        self.assertEqual((row['clean']['payer'], row['clean']['auth_id']), ('PY03', 'AUTH00001'))
        self.assertEqual({c['field'] for c in row['automatic_fixes']}, {'payer', 'auth_id'})
        self.assertEqual(row['denial_codes'], [])

    def test_ambiguous_payer_or_conflicting_reference_is_not_guessed(self):
        ds = dataset()
        ds.load_text('authorizations', AUTHS + 'AUTH00003,PT0001,PY01,T1005,2026-06-01,2026-12-31,100\n')
        row = check([claim_row(payer='', auth='')], ds)[0]
        self.assertEqual(row['category'], 'must_correct')
        self.assertFalse(row['automatic_fixes'])
        for changes in ({'payer': '', 'auth': 'AUTH00002'}, {'payer': 'Acme'}):
            row = check([claim_row(**changes)])[0]
            self.assertEqual(row['category'], 'must_correct')
            self.assertFalse(row['automatic_fixes'])

    def test_missing_invalid_and_wrong_authorizations_are_corrected(self):
        for auth in ('', 'bad-id', 'AUTH00002'):
            with self.subTest(auth=auth):
                row = check([claim_row(auth=auth)])[0]
                self.assertEqual(row['clean']['auth_id'], 'AUTH00001')
                self.assertEqual(row['raw']['auth_id'], auth)
                self.assertFalse(row['issues'])
                self.assertFalse(row['proposed_changes'])
                self.assertEqual(row['automatic_fixes'][0]['before'], auth)

    def test_ambiguous_or_insufficient_authorization_is_not_filled(self):
        ds = dataset()
        ds.load_text('authorizations', AUTHS + 'AUTH00003,PT0001,PY03,T1005,2026-06-01,2026-12-31,100\n')
        self.assertFalse(check([claim_row(auth='')], ds)[0]['automatic_fixes'])
        row = check([claim_row(auth='', units='11')])[0]
        self.assertEqual(row['clean']['auth_id'], '')
        self.assertIn('D07', row['denial_codes'])

    def test_modifiers_follow_payer_date_and_service_rules(self):
        row = check([claim_row(payer='PY02', pt='PT0002', svc='T1019', units='4', auth='AUTH00002', mod='INVALID')])[0]
        self.assertEqual(row['clean']['modifier'], 'U2')
        self.assertEqual(row['denial_codes'], [])
        self.assertEqual(row['automatic_fixes'][0]['field'], 'modifier')
        self.assertTrue(row['automatic_fixes'][0]['reason'])
        from claimcheck.rules import full_store
        for day, expected in [('2026-08-30', 'U2'), ('2026-09-05', 'U3'), ('2026-09-07', 'U2')]:
            _, records = parse_claims_csv(csv_text([claim_row(payer='PY02', pt='PT0002', svc='T1019',
                dos=day, sub=day, units='4', auth='AUTH00002')]))
            changed = run_batch(records, dataset(), full_store())[0]
            self.assertEqual(changed['clean']['modifier'], expected)


    def test_autofilled_claims_consume_balance_in_submission_order(self):
        rows = check([claim_row(cid='C000201', dos='2026-07-02', sub='2026-07-09', auth='', units='6'),
                      claim_row(cid='C000202', dos='2026-07-01', sub='2026-07-05', auth='', units='6')])
        self.assertEqual(rows[1]['clean']['auth_id'], 'AUTH00001')
        self.assertEqual(rows[0]['clean']['auth_id'], '')
        self.assertIn('D07', rows[0]['denial_codes'])

    def test_uncheckable_earlier_claim_does_not_create_a_balance_range(self):
        rows = check([claim_row(cid='C000301', freq='7', orig='C000099', units='8'),
                      claim_row(cid='C000302', dos='2026-07-02', sub='2026-07-06', auth='', units='3')])
        self.assertEqual(rows[0]['status'], 'unable_to_check')
        self.assertEqual(rows[1]['clean']['auth_id'], 'AUTH00001')
        self.assertNotIn('balance_range', rows[1])

    def test_explicit_user_edits_take_precedence(self):
        row = check([claim_row(auth='')], edits={'auth_id': ''})[0]
        self.assertEqual(row['clean']['auth_id'], '')
        self.assertIn('D07', row['denial_codes'])
        row = check([claim_row(payer='')], edits={'payer': ''})[0]
        self.assertEqual(row['clean']['payer'], '')
        self.assertEqual(row['category'], 'must_correct')

    def test_bad_or_conflicting_reference_data_cannot_justify_autofill(self):
        for extra in ('AUTH00001,PT0001,PY01,T1005,2026-06-01,2026-12-31,10\n',
                      'AUTH00001,PT0001,PY03,T1005,2026-06-01,2026-12-31,99\n'):
            ds = dataset()
            ds.load_text('authorizations', AUTHS + extra)
            row = check([claim_row(payer='')], ds)[0]
            self.assertFalse(row['automatic_fixes'])
        ds = dataset()
        ds.load_text('authorizations', AUTHS.replace('PY03', 'Unknown'))
        self.assertFalse(check([claim_row(payer='')], ds)[0]['automatic_fixes'])

    def test_duplicate_detection_and_export_preserve_originals(self):
        source = [claim_row(payer='', auth=''), claim_row(payer='', auth='')]
        _, records = parse_claims_csv(csv_text(source))
        original = copy.deepcopy(records)
        rows = run_batch(records, dataset(), baseline_store())
        self.assertEqual(records, original)
        self.assertEqual(rows[1]['category'], 'duplicate_row')
        exported = list(csv.DictReader(io.StringIO(export_csv(rows, CLAIM_FIELDS))))[0]
        self.assertEqual(exported['payer'], '')
        self.assertEqual(exported['auth_id'], '')
        self.assertEqual(json.loads(exported['checked_claim'])['auth_id'], 'AUTH00001')
        self.assertEqual(len(json.loads(exported['automatic_fixes'])), 2)
        again = run_batch(records, dataset(), baseline_store())
        self.assertEqual(rows, again)

    def test_units_amount_dates_and_unknown_patient_are_not_invented(self):
        row = check([claim_row(units='13')])[0]
        self.assertEqual(row['clean']['units'], '13')
        self.assertFalse(row['automatic_fixes'])
        row = check([claim_row(pt='', payer='', auth='')])[0]
        self.assertEqual(row['category'], 'must_correct')
        self.assertFalse(row['automatic_fixes'])
