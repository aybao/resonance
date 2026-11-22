"""Upload -> normalise -> validate -> check against rules and history -> flag -> export."""

import csv
import io
import json
from collections import defaultdict

from .engine import Context, PendingReview, State, check_claim
from .normalize import (AUTH_FIELDS, CLAIM_FIELDS, EVV_FIELDS, HISTORY_EXTRA, normalize_claims,
                        normalize_reference_rows, read_csv_text)

FIXABLE = ("auth_id", "modifier")  # fields for which the engine may propose a value


class Dataset:
    """Reference data a batch is checked against: authorizations, EVV visits, claim history."""

    def __init__(self):
        self.authorizations, self.visits, self.history = [], [], []
        self.errors = []
        self._ctx = None

    def load_text(self, kind, text):
        fields_by_kind = {"authorizations": AUTH_FIELDS, "evv": EVV_FIELDS,
                          "history": CLAIM_FIELDS + HISTORY_EXTRA}
        fields = fields_by_kind[kind]
        header, rows = read_csv_text(text)
        missing = [f for f in fields if f not in header]
        if missing:
            raise ValueError(f"{kind}: missing column(s) {', '.join(missing)}")
        clean, errors = normalize_reference_rows(rows, fields)
        # identical exported rows are one record
        unique, seen = [], set()
        for r in clean:
            key = tuple(r.items())
            if key not in seen:
                seen.add(key)
                unique.append(r)
        if kind == "history":
            unique = [r for r in unique if r.get("status") in ("paid", "denied")]
            unique.sort(key=lambda r: r["submitted_date"])
        setattr(self, {"authorizations": "authorizations", "evv": "visits", "history": "history"}[kind], unique)
        self.errors = [f"{kind}: {e}" for e in errors][:50] + [e for e in self.errors if not e.startswith(kind + ":")]
        self._ctx = None
        return dict(rows=len(unique), problems=len(errors))

    @property
    def ctx(self):
        if self._ctx is None:
            self._ctx = Context(self.authorizations, self.visits)
        return self._ctx

    def ready(self):
        return bool(self.authorizations and self.visits)

    def base_state(self):
        state = State()
        for r in self.history:
            state.record(r, r["status"])
        return state


def load_sample(dataset, directory):
    for kind, name in (("authorizations", "authorizations.csv"), ("evv", "evv_visits.csv"),
                       ("history", "claims_history.csv")):
        dataset.load_text(kind, (directory / name).read_text(encoding="utf-8-sig"))


def run_batch(records, dataset, rules, overrides=None):
    """Check normalised records in submission order. Returns a list of result rows (input order).

    overrides: {claim_id: {status, denial_codes, note}} — user corrections. A user decision
    replaces the prediction, and the ledger follows the user's decision.
    """
    overrides = overrides or {}
    ctx = dataset.ctx
    valid = [r for r in records if not r["duplicate_of"] and not any(i["severity"] == "error" for i in r["issues"])]
    pending = PendingReview(ctx)
    for r in records:  # rows with unresolved input are undecided: never assumed paid
        if not r["duplicate_of"] and any(i["severity"] == "error" for i in r["issues"]):
            pending.add(r["clean"])
    state = dataset.base_state()
    outputs = {}
    for r in sorted(valid, key=lambda r: (r["clean"]["submitted_date"], r["line"])):
        claim = r["clean"]
        base = check_claim(claim, state, ctx, rules)
        review = pending.assess(claim, base) if base["evidence"] else dict(status=base["status"], balance_uncertain=False)
        outputs[r["line"]] = (base, review)
        # Uncheckable claims are registered as submitted but never assumed paid.
        decision = overrides.get(claim["claim_id"], {}).get("status") or review["status"]
        if decision in ("check_previous_claims", "manual_review"):
            pending.add(claim)
        state.record(claim, "paid" if decision == "paid" else "other")
    rows = []
    for r in records:
        claim = r["clean"]
        errors = [i for i in r["issues"] if i["severity"] == "error"]
        row = dict(line=r["line"], claim_id=claim["claim_id"], raw=r["raw"], clean=claim, changes=r["changes"],
                   issues=r["issues"], duplicate_of=r["duplicate_of"], proposed_changes={}, failures=[], warnings=[],
                   evidence={}, balance_uncertain=False, unresolved_prior_claim_ids=[], balance_range=None,
                   previous_claims_action="", rule_version=None)
        if r["duplicate_of"]:
            row.update(status="duplicate_row", denial_codes=[], summary=f"Repeat of line {r['duplicate_of']}; not checked twice")
        elif errors:
            row.update(status="manual_review", denial_codes=[], category="must_correct",
                       summary="Input cannot be checked until corrected: " + "; ".join(f"{i['field']}: {i['message']}" for i in errors))
        else:
            base, review = outputs[r["line"]]
            row.update(base)
            row.update(status=review["status"], balance_uncertain=review["balance_uncertain"],
                       unresolved_prior_claim_ids=review.get("unresolved_prior_claim_ids", []),
                       balance_range=review.get("balance_range"),
                       previous_claims_action=review.get("previous_claims_action", ""))
            row["summary"] = base["remark"]
            if review["status"] == "check_previous_claims":
                row["summary"] = "Authorization balance uncertain: it fits only if earlier unresolved claims are not paid"
            row["denial_codes"] = list(base["denial_codes"])
        row["category"] = _category(row)
        row["predicted_status"] = row["status"]
        row["predicted_codes"] = list(row["denial_codes"])
        ov = overrides.get(claim["claim_id"])
        row["override"] = ov
        if ov:
            row["status"] = ov["status"] or row["status"]
            row["denial_codes"] = ov["denial_codes"] if ov.get("denial_codes") is not None else row["denial_codes"]
        rows.append(row)
    return rows


def _category(row):
    if row["duplicate_of"]:
        return "duplicate_row"
    if row["status"] == "manual_review" and any(i["severity"] == "error" for i in row["issues"]):
        return "must_correct"
    return {"denied": "denied", "check_previous_claims": "review", "manual_review": "review", "paid": "payable"}[row["status"]]


def summarize(rows):
    cats = defaultdict(int)
    for r in rows:
        cats[r["category"]] += 1
    codes = defaultdict(int)
    for r in rows:
        for c in r["denial_codes"]:
            codes[c] += 1
    return dict(total=len(rows), by_category=dict(cats), by_code=dict(sorted(codes.items())),
                formatted_rows=sum(bool(r["changes"]) for r in rows),
                overridden=sum(bool(r["override"]) for r in rows))


RESULT_COLUMNS = ["predicted_status", "predicted_denial_codes", "reasons", "suggested_actions", "proposed_changes",
                  "normalisation_changes", "user_edits", "input_issues", "warnings", "user_status", "user_denial_codes", "user_note"]


def export_csv(rows, original_fieldnames):
    """Original columns exactly as uploaded, followed by result columns."""
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=list(original_fieldnames) + [c for c in RESULT_COLUMNS if c not in original_fieldnames])
    writer.writeheader()
    for r in rows:
        ov = r["override"] or {}
        line = dict(r["raw"])
        line.update(
            predicted_status=r["predicted_status"], predicted_denial_codes=";".join(r["predicted_codes"]),
            reasons=" | ".join(f["reason"] for f in r["failures"]) or r["summary"],
            suggested_actions=" | ".join([f["suggestion"] for f in r["failures"]] + ([r["previous_claims_action"]] if r["previous_claims_action"] else [])),
            proposed_changes=json.dumps({k: v["value"] for k, v in r["proposed_changes"].items()}) if r["proposed_changes"] else "",
            normalisation_changes="; ".join(f"{c['field']}: {c['before']!r}->{c['after']!r}" for c in r["changes"]),
            input_issues="; ".join(f"{i['field']}: {i['message']}" for i in r["issues"]),
            user_edits="; ".join(f"{k}={v!r}" for k, v in (r.get("edits") or {}).items()),
            warnings=" | ".join(r["warnings"]), user_status=ov.get("status", ""),
            user_denial_codes=";".join(ov.get("denial_codes") or []) if ov else "", user_note=ov.get("note", ""))
        writer.writerow(line)
    return out.getvalue()


def parse_claims_csv(text):
    header, rows = read_csv_text(text)
    return header, normalize_claims(header, rows)
