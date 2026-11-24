"""Dated, isolated historical replay shared by the web demo and CLI.

Submission date is the availability proxy: this dataset has no adjudication date.
Batch outcomes are withheld until predictions for the entire batch are complete.
"""
import copy
import csv
import io
import json
from pathlib import Path
import tempfile

from .history import accuracy, matches
from .normalize import CLAIM_FIELDS
from .pipeline import Dataset, load_sample
from .rules import baseline_store, valid_date

DEFAULT_START = '2026-04-30'
DEMO_DIR = Path(__file__).resolve().parents[2] / 'demo-data'
EXCEPTIONS_PATH = DEMO_DIR / 'replay_exceptions.json'


def claims_csv(rows):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=CLAIM_FIELDS, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


class HistoricalReplay:
    def __init__(self, session, start=DEFAULT_START):
        if not valid_date(start):
            raise ValueError('Demo start date must be YYYY-MM-DD')
        self.session = session
        self.source = Dataset()
        load_sample(self.source, DEMO_DIR / 'reference-data')
        self.as_of = start
        self.start = start
        self.last_report = None
        self.tmp = tempfile.TemporaryDirectory(prefix='claimcheck-demo-')
        session.rules = baseline_store(Path(self.tmp.name) / 'rules.json')
        session.rules.save()
        session.dataset = self.context_at(start, start)
        session.claims_header, session.raw_rows, session.edits, session.overrides, session.rows = [], [], {}, {}, []
        session.drafts = {}
        session._accuracy = None

    def context_at(self, history_until, reference_until):
        ds = Dataset()
        ds.authorizations = copy.deepcopy([r for r in self.source.authorizations if r['start_date'] <= reference_until])
        ds.visits = copy.deepcopy([r for r in self.source.visits if r['clock_in'][:10] <= reference_until])
        ds.history = copy.deepcopy([r for r in self.source.history if r['submitted_date'] <= history_until])
        return ds

    def status(self):
        history_before = min((r['clean'].get('submitted_date', '') for r in self.session.rows), default=None)
        ds = self.session.dataset
        return dict(as_of=self.as_of, start=self.start, last_report=self.last_report,
                    reference_data_as_of=self.as_of, prediction_history_before=history_before,
                    history_from=min((r['submitted_date'] for r in ds.history), default=None),
                    evv_from=min((r['clock_in'][:10] for r in ds.visits), default=None),
                    authorizations_from=min((r['start_date'] for r in ds.authorizations), default=None),
                    end=max((r['submitted_date'] for r in self.source.history), default=self.as_of),
                    availability_note='Submission date proxies outcome availability; labels are revealed only after the entire batch is predicted.')

    def advance(self, until):
        if not valid_date(until) or until <= self.as_of:
            raise ValueError('Advance to a date after the current demo date (YYYY-MM-DD)')
        self.as_of = until
        self.session.dataset = self.context_at(until, until)
        self.session._accuracy = None
        self.last_report = None
        self.session.claims_header, self.session.raw_rows = [], []
        self.session.edits, self.session.overrides, self.session.rows = {}, {}, []
        return None

    def compare(self, predictions):
        labels = {r['claim_id']: r for r in self.source.history}
        if not predictions or any(p['claim_id'] not in labels for p in predictions):
            self.last_report = None
            return
        batch = [labels[p['claim_id']] for p in predictions]
        until = max(r['submitted_date'] for r in batch)
        self.last_report = self.report(predictions, batch,
            min(r['submitted_date'] for r in batch), until)

    def replay(self, until):
        if not valid_date(until) or until <= self.as_of:
            raise ValueError('Advance to a date after the current demo date (YYYY-MM-DD)')
        batch = [r for r in self.source.history if self.as_of < r['submitted_date'] <= until]
        if not batch:
            raise ValueError('No recorded claims in that period; choose a later date')
        session = self.session
        # The complete prediction pass receives no labels for this batch.
        session.dataset = self.context_at(self.as_of, until)
        session._accuracy = None
        predictions = session.upload_claims(claims_csv(batch))['rows']
        report = self.report(predictions, batch, self.as_of, until)
        self.last_report = report
        self.as_of = until
        return report

    def report(self, predictions, batch, from_date, until):
        labels = {r['claim_id']: r for r in batch}
        rows = []
        known = json.loads(EXCEPTIONS_PATH.read_text()) if EXCEPTIONS_PATH.exists() else {}
        for pred in predictions:
            actual = labels[pred['claim_id']]
            row = dict(claim=dict(pred['clean']), claim_id=pred['claim_id'], payer=pred['clean']['payer'],
                       date_of_service=pred['clean']['date_of_service'], actual_status=actual['status'],
                       actual_code=actual['denial_code'], remark=actual.get('remark', ''),
                       status=pred['status'], codes=pred['denial_codes'])
            exception = known.get(row['claim_id'])
            row['known_exception'] = bool(exception and exception.get('replay_period_end') == until
                and exception['actual_status'] == row['actual_status']
                and exception['actual_code'] == row['actual_code']
                and exception['predicted_status'] == row['status']
                and exception['predicted_codes'] == row['codes'])
            row['exception_reason'] = exception['reason'] if row['known_exception'] else ''
            rows.append(row)
        mismatches = [r for r in rows if not matches(r)]
        report = dict(from_date=from_date, through_date=until, **accuracy(rows),
            mismatches=mismatches, unexplained_d99=sum(r['actual_status'] == 'denied' and r['actual_code'] == 'D99' for r in mismatches), known_exceptions=sum(r['known_exception'] for r in mismatches),
            unexpected_mismatches=sum(not r['known_exception'] for r in mismatches),
            passed=all(r['known_exception'] for r in mismatches))
        return report

    def close(self):
        self.tmp.cleanup()
