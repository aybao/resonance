"""Single-user demo session: data, rules, current claim batch, drafts. Used by the HTTP layer."""

import threading
from pathlib import Path

from . import drafts as D
from .ai import AIClient
from .examples import validate_scenario
from .history import accuracy, replay
from .normalize import CLAIM_FIELDS, normalize_claims, read_csv_text
from .pipeline import Dataset, export_csv, load_sample, run_batch, summarize
from .rules import RuleStore, baseline_store, full_store, validate_params

APP_DIR = Path(__file__).resolve().parent.parent
SAMPLE_DIR = APP_DIR / "sample_data"
STATE_DIR = APP_DIR / "state"


class Session:
    def __init__(self, state_dir=STATE_DIR, sample_dir=SAMPLE_DIR, ai=None, autoload=True):
        self.lock = threading.RLock()
        self.sample_dir = sample_dir
        self.rules_path = Path(state_dir) / "rules.json"
        self.rules = RuleStore(self.rules_path)
        if not self.rules.payers or self.rules.snapshot() == baseline_store().snapshot():
            self.rules = full_store(self.rules_path)  # Fresh full-app sessions include all supplied bulletins.
            self.rules.save()
        self.dataset = Dataset()
        self.ai = ai or AIClient()
        self.drafts = {}
        self.claims_header, self.raw_rows, self.edits, self.overrides, self.rows = [], [], {}, {}, []
        self._accuracy = None
        self.demo = None
        self._full_state = None
        if autoload:
            load_sample(self.dataset, self.sample_dir)

    def start_demo(self, as_of="2026-06-30"):
        from .replay_demo import HistoricalReplay
        from .rules import valid_date
        with self.lock:
            if not valid_date(as_of):
                raise ValueError("Demo date must be YYYY-MM-DD")
            if self.demo:
                self.demo.close()
            else:
                self._full_state = {k: getattr(self, k) for k in (
                    "rules", "dataset", "claims_header", "raw_rows", "edits", "overrides", "rows", "drafts", "_accuracy", "ai")}
            self.demo = HistoricalReplay(self, as_of)
            return self.status()

    def end_demo(self):
        with self.lock:
            if self.demo:
                self.demo.close()
                for key, value in self._full_state.items():
                    setattr(self, key, value)
                self.demo = None
                self._full_state = None
            return self.status()

    def advance_demo(self, until):
        with self.lock:
            if not self.demo:
                raise ValueError("Start a historical demo first")
            report = self.demo.advance(until)
            return dict(report=report, status=self.status(), results=self.results())

    # data -------------------------------------------------------------
    def status(self):
        return dict(ai_available=self.ai.available(), model=self.ai.model,
                    data=dict(authorizations=len(self.dataset.authorizations), evv_visits=len(self.dataset.visits),
                              history=len(self.dataset.history), problems=self.dataset.errors[:10]),
                    claims_loaded=len(self.raw_rows), payers=sorted(self.rules.payers),
                    acceptance_tests=len(self.rules.acceptance),
                    mode="demo" if self.demo else "full", demo=self.demo.status() if self.demo else None)

    def load_reference(self, kind, text):
        with self.lock:
            if self.demo:
                raise ValueError("Use the demo date control to advance reference data")
            info = self.dataset.load_text(kind, text)
            self._accuracy = None
            self._recheck()
            return info

    def load_sample_data(self):
        with self.lock:
            if self.demo:
                raise ValueError("Use the demo date control to advance reference data")
            load_sample(self.dataset, self.sample_dir)
            self._accuracy = None
            self._recheck()

    def upload_claims(self, text):
        with self.lock:
            header, raw = read_csv_text(text)
            normalize_claims(header, raw)  # raises on missing columns before replacing the batch
            self.claims_header, self.raw_rows, self.edits, self.overrides = header, raw, {}, {}
            self._recheck()
            return self.results()

    def load_sample_claims(self):
        return self.upload_claims((self.sample_dir / "claims_to_check.csv").read_text(encoding="utf-8-sig"))

    # claims -----------------------------------------------------------
    def _recheck(self):
        if not self.raw_rows:
            self.rows = []
            return
        if not self.dataset.ready():
            raise ValueError("Load authorizations and EVV visits first")
        effective = []
        for i, raw in enumerate(self.raw_rows):
            row = dict(raw)
            row.update(self.edits.get(i + 2, {}))
            effective.append(row)
        records = normalize_claims(self.claims_header, effective)
        for rec, raw in zip(records, self.raw_rows):
            rec["raw"] = dict(raw)  # source values stay as uploaded
            rec["edits"] = self.edits.get(rec["line"], {})
        self.rows = run_batch(records, self.dataset, self.rules, self.overrides)
        for r, rec in zip(self.rows, records):
            r["edits"] = rec["edits"]

    def results(self):
        return dict(summary=summarize(self.rows), rows=self.rows)

    def find(self, claim_id, line=None):
        for r in self.rows:
            if (line is not None and r["line"] == line) or (line is None and r["claim_id"] == claim_id):
                return r
        raise KeyError("Unknown claim")

    def edit_claim(self, line, fields):
        """Apply a user edit (e.g. accept a proposed fix) to one row, then re-check the batch."""
        with self.lock:
            self.find(None, line)
            bad = [f for f in fields if f not in CLAIM_FIELDS]
            if bad:
                raise ValueError("Unknown field(s): " + ", ".join(bad))
            self.edits.setdefault(line, {}).update({k: str(v) for k, v in fields.items()})
            self._recheck()
            return self.results()

    def revert_edits(self, line):
        with self.lock:
            self.edits.pop(line, None)
            self._recheck()
            return self.results()

    def set_override(self, claim_id, status, codes, note):
        with self.lock:
            row = self.find(claim_id)
            if status in (None, ""):
                self.overrides.pop(claim_id, None)
            else:
                if status not in ("paid", "denied", "manual_review"):
                    raise ValueError("status must be paid, denied or manual_review")
                codes = codes or []
                if status == "denied" and not codes:
                    raise ValueError("A denied classification needs at least one denial code")
                if status != "denied":
                    codes = []
                self.overrides[claim_id] = dict(status=status, denial_codes=sorted(codes), note=note or "")
            self._recheck()
            return self.results()

    def export(self):
        with self.lock:
            return export_csv(self.rows, self.claims_header)

    def accuracy(self):
        with self.lock:
            if self._accuracy is None:
                self._accuracy = accuracy(replay(self.dataset, self.rules))
            return self._accuracy

    # rules ------------------------------------------------------------
    def rules_view(self):
        return dict(payers={pid: dict(name=p["name"], versions=self.rules.versions(pid)) for pid, p in self.rules.payers.items()},
                    acceptance=self.rules.acceptance, updates=self.rules.updates)

    def reset_rules_to_baseline(self):
        with self.lock:
            self.rules = baseline_store(self.rules.path)
            self.rules.save()
            self._accuracy = None
            self._recheck()

    def draft_view(self, draft, notes=()):
        return dict(draft=draft, evaluation=D.evaluate(draft, self.rules, self.dataset), notes=list(notes))

    def create_draft(self, payload):
        with self.lock:
            d = D.new_draft(payload.get("payer_id", ""), payload.get("effective_from", ""), payload.get("params") or {},
                            payload.get("source", ""), payload.get("summary", ""), payload.get("payer_name", ""))
            d["code"] = payload.get("code")
            notes = []
            errs = validate_params(d["params"])
            if errs:
                raise ValueError("; ".join(errs))
            if payload.get("message"):
                notes = D.chat(d, self.rules, self.dataset, self.ai, payload["message"])
            else:
                D.refresh_boundary_examples(d, self.rules)
            self.drafts[d["id"]] = d
            return self.draft_view(d, notes)

    def draft(self, draft_id):
        try:
            draft = self.drafts[draft_id]
            if draft["status"] != "draft":
                raise ValueError("This rule version is already accepted; start a new update")
            return draft
        except KeyError:
            raise KeyError("Unknown draft") from None

    def chat_draft(self, draft_id, message):
        with self.lock:
            d = self.draft(draft_id)
            return self.draft_view(d, D.chat(d, self.rules, self.dataset, self.ai, message))

    def edit_draft(self, draft_id, payload):
        with self.lock:
            d = self.draft(draft_id)
            if "params" in payload:
                errs = validate_params(payload["params"])
                if errs:
                    raise ValueError("; ".join(errs))
                d["params"] = payload["params"]
                d["decisions"] = {}
            if "code" in payload:
                from .code_rules import validate_code
                validate_code(payload["code"])
                d["code"] = payload["code"]
                d["proposal_errors"] = []
                d["decisions"] = {}
            for k in ("payer_id", "payer_name", "effective_from", "source", "summary"):
                if k in payload:
                    d[k] = payload[k]
            D.refresh_boundary_examples(d, self.rules)
            return self.draft_view(d)

    def add_example(self, draft_id, scenario):
        with self.lock:
            d = self.draft(draft_id)
            s = dict(scenario, id=f"user-{len(d['examples'])}", origin="user")
            if "payer" not in s.get("claim", {}) and d.get("payer_id"):
                s["payer_id"] = d["payer_id"]
            errs = validate_scenario(s)
            if errs:
                raise ValueError("; ".join(errs))
            d["examples"].append(s)
            return self.draft_view(d)

    def decide(self, draft_id, payload):
        with self.lock:
            d = self.draft(draft_id)
            D.decide(d, payload.get("example_id"), payload.get("decision"), payload.get("codes"), payload.get("note", ""))
            return self.draft_view(d)

    def activate(self, draft_id, reason=""):
        with self.lock:
            d = self.draft(draft_id)
            version = D.activate(d, self.rules, self.dataset, reason)
            self._accuracy = None
            self._recheck()
            return dict(version=version, rules=self.rules_view())
