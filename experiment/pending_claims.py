"""Conservative authorization review gate; unknown outcomes are never payments.

The range is conditional on the baseline's reconstructed balance. Pending
claims may be planned rather than actually submitted: verify their submission
and adjudication, not just their payment. No labels or remarks are consumed.
"""

from collections import defaultdict


class PendingAuthorizationReview:
    def __init__(self, pending, authorizations, historical_ids=()):
        auths = {a["auth_id"]: a for a in authorizations}
        self.by_auth = defaultdict(list)
        seen = set(historical_ids)
        for row in pending:
            if row["claim_id"] in seen:
                continue
            seen.add(row["claim_id"])
            auth = auths.get(row["auth_id"])
            if not auth or not (
                auth["payer_id"] == row["payer"]
                and auth["patient_id"] == row["patient_id"]
                and auth["service_code"] == row["service_code"]
                and auth["start_date"] <= row["date_of_service"] <= auth["end_date"]
            ):
                continue
            self.by_auth[row["auth_id"]].append(row)

    def assess(self, claim, baseline):
        remaining = baseline["evidence"].get("authorization_remaining")
        earlier = [r for r in self.by_auth.get(claim["auth_id"], [])
                   if r["submitted_date"] < claim["submitted_date"]
                   and r["claim_id"] != claim["claim_id"]
                   and all(r[k] == claim[k] for k in ("payer", "patient_id", "service_code"))]
        units = sum(int(r["units"]) for r in earlier)
        lower = max(0, remaining - units) if remaining is not None else None
        uncertain = (remaining is not None and bool(earlier)
                     and lower < int(claim["units"]) <= remaining)
        decision = baseline["status"]
        if uncertain and decision == "paid":
            decision = "check_previous_claims"
        return dict(
            decision=decision,
            authorization_balance_uncertain=uncertain,
            potential_denial_codes=["D02"] if uncertain else [],
            unresolved_prior_claim_ids=[r["claim_id"] for r in earlier],
            unresolved_prior_units=units,
            authorization_balance_if_none_paid=remaining,
            authorization_balance_if_all_paid=lower,
            previous_claims_action=(
                "Confirm whether the listed earlier claims were submitted and paid; reconcile the payer balance before submitting this claim."
                if uncertain else ""),
        )
