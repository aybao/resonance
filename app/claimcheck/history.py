"""Replay adjudicated history against a rule set (observed-outcome ledger).

Used for (a) an accuracy readout on recorded outcomes and (b) showing which historical
outcomes would change under a proposed rule version. Labels never enter the checker.
"""

from .engine import State, check_claim
from .normalize import CLAIM_FIELDS


def replay(dataset, rules):
    state, rows = State(), []
    for h in dataset.history:
        r = check_claim(h, state, dataset.ctx, rules)
        rows.append(dict(claim={k: h.get(k, "") for k in CLAIM_FIELDS}, claim_id=h["claim_id"], payer=h["payer"], date_of_service=h["date_of_service"],
                         actual_status=h["status"], actual_code=h["denial_code"], remark=h.get("remark", ""),
                         status=r["status"], codes=r["denial_codes"], error=r.get("rule_error")))
        state.record(h, h["status"])
    return rows


def matches(row):
    if row["actual_status"] == "denied":
        return row["status"] == "denied" and row["actual_code"] in row["codes"]
    return row["status"] == "paid" and not row["codes"]


def accuracy(rows):
    n = len(rows)
    ok = sum(matches(r) for r in rows)
    denied = [r for r in rows if r["actual_status"] == "denied"]
    return dict(claims=n, status_and_code_set_correct=ok, accuracy=ok / n if n else None,
                actual_denied=len(denied), actual_denied_code_detected=sum(matches(r) for r in denied),
                missed_denials=sum(r["status"] == "paid" for r in denied),
                false_alarms=sum(r["actual_status"] == "paid" and r["status"] == "denied" for r in rows),
                manual_review=sum(r["status"] == "manual_review" for r in rows))


def impact(dataset, old_rules, new_rules, limit=25):
    """Historical claims whose predicted outcome differs between two rule sets."""
    a, b = replay(dataset, old_rules), replay(dataset, new_rules)
    changed = []
    for x, y in zip(a, b):
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
