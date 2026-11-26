"""Replay adjudicated history against a rule set (observed-outcome ledger).

Used for (a) an accuracy readout on recorded outcomes and (b) showing which historical
outcomes would change under a proposed rule version. Labels never enter the checker.
"""

import json
from pathlib import Path

from .engine import State, check_claim
from .normalize import CLAIM_FIELDS

EXCEPTIONS_PATH = Path(__file__).resolve().parents[2] / "demo-data" / "replay_exceptions.json"


def replay(dataset, rules):
    state, rows = State(), []
    for h in dataset.history:
        claim = {k: h.get(k, "") for k in CLAIM_FIELDS}
        r = check_claim(claim, state, dataset.ctx, rules)
        rows.append(dict(claim={k: h.get(k, "") for k in CLAIM_FIELDS}, claim_id=h["claim_id"], payer=h["payer"], date_of_service=h["date_of_service"],
                         actual_status=h["status"], actual_code=h["denial_code"], remark=h.get("remark", ""),
                         status=r["status"], codes=r["denial_codes"], error=r.get("rule_error")))
        state.record(claim, h["status"] if h["status"] in ("paid", "denied") else r["status"],
                     provisional=h["status"] not in ("paid", "denied"))
    return rows


def regression_report(dataset, rules, exceptions_path=None):
    """Check available original history; only pinned, inspected differences are allowed.

    This uses observed earlier outcomes rather than a pending-batch ledger, so
    exception cohort end dates do not apply. Actual and predicted outcomes, the
    full predicted code set and any saved recorded remark must match exactly.
    """
    path = Path(exceptions_path) if exceptions_path is not None else EXCEPTIONS_PATH
    known = json.loads(path.read_text()) if path.exists() else {}
    history = replay(dataset, rules)
    rows = [r for r in history if r["actual_status"] in ("paid", "denied")]
    pending = [r for r in history if r["actual_status"] not in ("paid", "denied")]
    pending_errors = sum(r["status"] == "unable_to_check" for r in pending)
    failures, exceptions = [], []
    matched = 0
    for row in rows:
        if not row.get("error") and matches(row):
            matched += 1
            continue
        expected = known.get(row["claim_id"])
        allowed = bool(expected and not row.get("error")
                       and expected["actual_status"] == row["actual_status"]
                       and expected["actual_code"] == row["actual_code"]
                       and expected["predicted_status"] == row["status"]
                       and sorted(expected["predicted_codes"]) == sorted(row["codes"])
                       and ("recorded_remark" not in expected or expected["recorded_remark"] == row["remark"]))
        row["exception_reason"] = expected["reason"] if allowed else ""
        (exceptions if allowed else failures).append(row)
    return dict(total=len(rows), pending=len(pending), pending_predictions=pending, pending_errors=pending_errors, matched=matched, known_exceptions=len(exceptions),
                failed=len(failures), passed=False if pending_errors else (not failures if rows else None),
                failures=failures, exceptions=exceptions,
                accuracy=matched / len(rows) if rows else None,
                basis="Original historical claims using earlier recorded outcomes")


def matches(row):
    if row["actual_status"] not in ("paid", "denied"):
        return False
    if row["actual_status"] == "denied":
        return row["status"] == "denied" and row["actual_code"] in row["codes"]
    return row["status"] == "paid" and not row["codes"]


def accuracy(rows):
    rows = [r for r in rows if r["actual_status"] in ("paid", "denied")]
    n = len(rows)
    ok = sum(matches(r) for r in rows)
    denied = [r for r in rows if r["actual_status"] == "denied"]
    return dict(claims=n, status_and_code_set_correct=ok, accuracy=ok / n if n else None,
                actual_denied=len(denied), actual_denied_code_detected=sum(matches(r) for r in denied),
                missed_denials=sum(r["status"] == "paid" for r in denied),
                false_alarms=sum(r["actual_status"] == "paid" and r["status"] == "denied" for r in rows),
                unable_to_check=sum(r["status"] == "unable_to_check" for r in rows))


def impact(dataset, old_rules, new_rules, limit=None):
    """Historical claims whose predicted outcome differs between two rule sets."""
    a, b = replay(dataset, old_rules), replay(dataset, new_rules)
    changed = []
    for x, y in zip(a, b):
        if x["actual_status"] not in ("paid", "denied"):
            continue
        if (x["status"], x["codes"]) != (y["status"], y["codes"]):
            changed.append(dict(claim=x["claim"], claim_id=x["claim_id"], payer=x["payer"], date_of_service=x["date_of_service"],
                                before=dict(status=x["status"], codes=x["codes"]),
                                after=dict(status=y["status"], codes=y["codes"]),
                                actual=dict(status=x["actual_status"], code=x["actual_code"]),
                                now_correct=matches(y) and not matches(x), now_wrong=matches(x) and not matches(y)))
    return dict(changed_count=len(changed), now_correct=sum(c["now_correct"] for c in changed),
                now_wrong=sum(c["now_wrong"] for c in changed), examples=changed[:limit],
                accuracy_before=accuracy(a), accuracy_after=accuracy(b),
                errors=sorted(set(r["error"] for r in b if r.get("error"))))
