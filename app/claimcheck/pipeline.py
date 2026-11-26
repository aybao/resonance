"""Upload -> normalise -> validate -> check against rules and history -> flag -> export."""

import csv
import copy
import io
import json
from collections import defaultdict
from datetime import datetime

from .engine import Context, PendingReview, State, check_claim
from .normalize import (AUTH_FIELDS, CLAIM_FIELDS, EVV_FIELDS, HISTORY_EXTRA, canonical, normalize_claims,
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


def claim_references(claim, ctx, visits_by_key):
    """Referenced authorization (even if wrong) plus related coverage and same-day EVV."""
    key = (claim.get("payer"), claim.get("patient_id"), claim.get("service_code"))
    candidates = list(ctx.auth_index.get(key, []))
    selected = ctx.authorizations.get(claim.get("auth_id"))
    if selected is not None and selected not in candidates:
        candidates.insert(0, selected)
    authorizations = []
    for auth in candidates:
        issues = [label for field, claim_field, label in (
            ("payer_id", "payer", "Payer does not match"),
            ("patient_id", "patient_id", "Patient does not match"),
            ("service_code", "service_code", "Service does not match")) if auth[field] != claim.get(claim_field)]
        if not auth["start_date"] <= claim.get("date_of_service", "") <= auth["end_date"]:
            issues.append("Service date is outside coverage")
        authorizations.append(dict(auth, referenced=auth["auth_id"] == claim.get("auth_id"), issues=issues))
    authorizations.sort(key=lambda a: not a["referenced"])
    visits = [dict(v, duration_minutes=(datetime.strptime(v["clock_out"], "%Y-%m-%d %H:%M") -
                                       datetime.strptime(v["clock_in"], "%Y-%m-%d %H:%M")).total_seconds() / 60)
              for v in visits_by_key.get((claim.get("patient_id"), claim.get("service_code"), claim.get("date_of_service")), [])]
    return dict(evv_visits=visits, authorizations=authorizations)


def _trusted_authorizations(authorizations):
    """Conflicting IDs and invalid reference rows cannot justify automatic edits."""
    grouped = defaultdict(list)
    for auth in authorizations:
        grouped[auth.get("auth_id")].append(auth)
    trusted = []
    for rows in grouped.values():
        if any(r != rows[0] for r in rows):
            continue
        auth = rows[0]
        try:
            if any(not auth.get(f) or canonical(f, auth[f]) != auth[f] for f in AUTH_FIELDS):
                continue
            if auth["start_date"] > auth["end_date"]:
                continue
        except (ValueError, TypeError):
            continue
        trusted.append(auth)
    return trusted


def _fill_payers(records, ctx):
    """Infer blank payers from coverage, then rerun validation/duplicate detection."""
    for r in records:
        r["automatic_fixes"] = []
        c = r["clean"]
        if c["payer"] or "payer" in r.get("edits", {}):
            continue
        if any(i["field"] in {"patient_id", "service_code", "date_of_service"} for i in r["issues"]):
            continue
        matches = [a for a in ctx.authorizations.values()
                   if a["patient_id"] == c["patient_id"] and a["service_code"] == c["service_code"]
                   and a["start_date"] <= c["date_of_service"] <= a["end_date"]]
        if c["auth_id"]:
            # A supplied reference must agree; do not infer around a conflicting ID.
            matches = [a for a in matches if a["auth_id"] == c["auth_id"]]
        payers = {a["payer_id"] for a in matches}
        if len(payers) == 1:
            payer = payers.pop()
            r["automatic_fixes"].append(dict(field="payer", before="", after=payer,
                reason="Authorization coverage identifies one payer for this patient, service and date",
                source_ids=sorted(a["auth_id"] for a in matches)))
            c["payer"] = payer
    validated = normalize_claims(CLAIM_FIELDS, [r["clean"] for r in records])
    for r, checked in zip(records, validated):
        r.update(clean=checked["clean"], issues=checked["issues"], duplicate_of=checked["duplicate_of"])


def _apply_supported_fixes(record, state, ctx, rules, pending, trusted):
    """Apply engine-supported fields, retaining explicit edits and uncertain balances."""
    claim = record["clean"]
    result = check_claim(claim, state, ctx, rules)
    # Modifier first: the final authorization decision uses the corrected claim.
    for field in ("modifier", "auth_id"):
        patch = result["proposed_changes"].get(field)
        if not patch or field in record.get("edits", {}):
            continue
        if field == "auth_id" and patch["value"] not in trusted.authorizations:
            continue
        candidate = dict(claim, **{field: patch["value"]})
        checked = check_claim(candidate, state, ctx, rules)
        if checked.get("rule_error"):
            continue
        if field == "auth_id" and pending.assess(candidate, checked)["balance_uncertain"]:
            continue
        record["automatic_fixes"].append(dict(field=field, before=claim[field], after=patch["value"],
            reason=patch["reason"].replace("; confirm before use", ""), source_ids=[patch["value"]] if field == "auth_id" else [],
            rule_version=result["rule_version"]))
        claim[field] = patch["value"]
        record["issues"] = [i for i in record["issues"] if i["field"] != field]
        result = checked
    return result


def run_batch(records, dataset, rules, overrides=None, *, autofix=True):
    """Check normalised records in submission order. Returns a list of result rows (input order).

    overrides: {claim_id: {status, denial_codes, note}} — user corrections. A user decision
    replaces the prediction, and the ledger follows the user's decision.
    """
    overrides = overrides or {}
    records = copy.deepcopy(records)
    ctx = dataset.ctx
    trusted = Context(_trusted_authorizations(dataset.authorizations), []) if autofix else None
    if autofix:
        _fill_payers(records, trusted)
    else:
        for r in records:
            r["automatic_fixes"] = []
    visits_by_key = defaultdict(list)
    for visit in dataset.visits:
        visits_by_key[(visit["patient_id"], visit["service_code"], visit["clock_in"][:10])].append(visit)
    def blocking(r):
        return any(i["severity"] == "error" and (not autofix or i["field"] not in FIXABLE) for i in r["issues"])
    valid = [r for r in records if not r["duplicate_of"] and not blocking(r)]
    pending = PendingReview(ctx)
    for r in records:  # rows with unresolved input are undecided: never assumed paid
        if not r["duplicate_of"] and blocking(r):
            pending.add(r["clean"])
    state = dataset.base_state()
    submissions = defaultdict(list)
    for historical in dataset.history:
        submissions[State.duplicate_key(historical)].append(dict(
            source="history", line=None, status=historical["status"],
            claim={f: historical.get(f, "") for f in CLAIM_FIELDS}))
    outputs = {}
    for r in sorted(valid, key=lambda r: (r["clean"]["submitted_date"], r["line"])):
        claim = r["clean"]
        base = _apply_supported_fixes(r, state, ctx, rules, pending, trusted) if autofix else check_claim(claim, state, ctx, rules)
        if any(i["severity"] == "error" for i in r["issues"]):
            pending.add(claim)
            continue
        base["duplicate_submissions"] = (copy.deepcopy(submissions[State.duplicate_key(claim)])
                                         if "D05" in base["denial_codes"] else [])
        review = pending.assess(claim, base) if base["evidence"] else dict(status=base["status"], balance_uncertain=False)
        outputs[r["line"]] = (base, review)
        # Uncheckable claims are registered as submitted but never assumed paid.
        decision = overrides.get(claim["claim_id"], {}).get("status") or review["status"]
        if decision in ("check_previous_claims", "manual_review"):
            pending.add(claim)
        state.record(claim, "paid" if decision == "paid" else "other")
        submissions[State.duplicate_key(claim)].append(dict(
            source="batch", line=r["line"], status=decision, claim=dict(claim)))
    rows = []
    for r in records:
        claim = r["clean"]
        errors = [i for i in r["issues"] if i["severity"] == "error"]
        row = dict(line=r["line"], claim_id=claim["claim_id"], raw=r["raw"], clean=claim, changes=r["changes"],
                   automatic_fixes=r["automatic_fixes"],
                   duplicate_submissions=[],
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
        row["reference_data"] = claim_references(claim, ctx, visits_by_key)
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
                autofixed_rows=sum(bool(r["automatic_fixes"]) for r in rows),
                overridden=sum(bool(r["override"]) for r in rows))


RESULT_COLUMNS = ["predicted_status", "predicted_denial_codes", "reasons", "suggested_actions", "proposed_changes",
                  "automatic_fixes", "checked_claim",
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
            automatic_fixes=json.dumps(r["automatic_fixes"]), checked_claim=json.dumps(r["clean"]),
            input_issues="; ".join(f"{i['field']}: {i['message']}" for i in r["issues"]),
            user_edits="; ".join(f"{k}={v!r}" for k, v in (r.get("edits") or {}).items()),
            warnings=" | ".join(r["warnings"]), user_status=ov.get("status", ""),
            user_denial_codes=";".join(ov.get("denial_codes") or []) if ov else "", user_note=ov.get("note", ""))
        writer.writerow(line)
    return out.getvalue()


def parse_claims_csv(text):
    header, rows = read_csv_text(text)
    return header, normalize_claims(header, rows)
