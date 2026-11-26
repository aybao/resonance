"""Deterministic claim checker driven by versioned rules. No AI, no labels."""

from collections import Counter, defaultdict
from datetime import date, datetime

from .normalize import CLAIM_FIELDS
from .rules import DAYS, DENIAL_CODES

SOURCE_HINT = {
    "D01": "EVV rule", "D02": "Authorization rule", "D03": "Timely filing rule", "D04": "Modifier rule",
    "D05": "Duplicate rule", "D06": "Daily maximum rule", "D07": "Authorization rule",
    "D08": "Authorization rule", "D09": "EVV units rule",
}


class State:
    """Ledger of submitted claims: paid units consume authorizations; all submissions make history."""

    def __init__(self):
        self.auth_used = Counter()
        self.daily_paid = Counter()
        self.provisional_auth_used = Counter()
        self.provisional_auth_claims = defaultdict(list)
        self.submissions = defaultdict(list)
        self.by_id = {}

    @staticmethod
    def duplicate_key(c):
        return (c["payer"], c["patient_id"], c["service_code"], c["date_of_service"])

    @staticmethod
    def daily_key(c):
        return (c["payer"], c["patient_id"], c["date_of_service"])

    def record(self, claim, status, provisional=False):
        self.submissions[self.duplicate_key(claim)].append(claim["claim_id"])
        self.by_id[claim["claim_id"]] = claim
        if status == "paid" and str(claim.get("units", "")).isdigit():
            self.auth_used[claim["auth_id"]] += int(claim["units"])
            self.daily_paid[self.daily_key(claim)] += int(claim["units"])
            if provisional:
                self.provisional_auth_used[claim["auth_id"]] += int(claim["units"])
                self.provisional_auth_claims[claim["auth_id"]].append(claim["claim_id"])


class Context:
    """Authorizations and EVV visits, indexed for lookup."""

    def __init__(self, authorizations, visits):
        self.authorizations = {a["auth_id"]: a for a in authorizations}
        self.auth_index = defaultdict(list)
        for a in authorizations:
            self.auth_index[(a["payer_id"], a["patient_id"], a["service_code"])].append(a)
        self.evv = defaultdict(lambda: {"minutes": 0.0, "verified_ids": [], "other_ids": []})
        for v in visits:
            start = datetime.strptime(v["clock_in"], "%Y-%m-%d %H:%M")
            end = datetime.strptime(v["clock_out"], "%Y-%m-%d %H:%M")
            entry = self.evv[(v["patient_id"], v["service_code"], start.date().isoformat())]
            if v["verification_status"] == "verified":
                entry["minutes"] += (end - start).total_seconds() / 60
                entry["verified_ids"].append(v["visit_id"])
            else:
                entry["other_ids"].append(v["visit_id"])


def evv_units(minutes, rounding):
    whole = int(minutes // 15)
    if rounding == "eight_minute" and minutes % 15 >= 8:
        whole += 1
    return whole


def _blank_result(status, remark, warnings=()):
    return dict(status=status, denial_codes=[], remark=remark, failures=[], warnings=list(warnings),
                proposed_changes={}, evidence={}, rule_version=None)


def check_claim(claim, state, ctx, rules):
    """Check one cleaned claim. `rules` is a RuleStore. Returns a result dict."""
    claim = {f: claim.get(f, "") for f in CLAIM_FIELDS}  # outcomes can never leak in
    try:
        dos = date.fromisoformat(claim["date_of_service"])
        submitted = date.fromisoformat(claim["submitted_date"])
        units = int(claim["units"])
    except (ValueError, KeyError):
        return _blank_result("unable_to_check", "Missing or invalid date/units")
    version = rules.resolve(claim["payer"], claim["date_of_service"])
    if version is None:
        return _blank_result("unable_to_check", "No approved rules for this payer on the date of service",
                             ["Add rules for this payer and date in the Rules tab"])
    p = version["params"]
    failures, warnings, patches = [], list(p.get("notes", [])), []

    def fail(code, reason, action):
        failures.append(dict(code=code, meaning=DENIAL_CODES[code], reason=reason, suggestion=action,
                             rule=f"{claim['payer']} v{version['version']} (from {version['effective_from']})"))

    dup_ids = state.submissions[State.duplicate_key(claim)]
    if p["duplicate_check"] and claim["frequency_code"] == "1" and dup_ids:
        fail("D05", f"Earlier submission exists: {dup_ids[0]}",
             "Reconcile the earlier claim; if correcting it, use frequency 7 with the original claim ID.")
    if claim["frequency_code"] == "7":
        original = state.by_id.get(claim["original_claim_id"])
        if original is None or State.duplicate_key(original) != State.duplicate_key(claim):
            warnings.append("Correction reference missing, not yet submitted, or for a different claim; payer code unspecified")

    remaining = None
    if p["authorization"]["required"]:
        matching = [a for a in ctx.auth_index[(claim["payer"], claim["patient_id"], claim["service_code"])]
                    if a["start_date"] <= claim["date_of_service"] <= a["end_date"]]
        auth = ctx.authorizations.get(claim["auth_id"])
        valid = auth is not None and auth in matching
        if not claim["auth_id"]:
            fail("D07", "Prior authorization number missing",
                 "Confirm and enter the authorization covering this patient, payer, service and date.")
        elif not valid:
            fail("D08", "Authorization not found or not valid for payer, patient, service or date",
                 "Check the authorization reference and coverage; obtain payer confirmation if necessary.")
        if not valid and len(matching) == 1:
            cand = matching[0]
            if state.auth_used[cand["auth_id"]] + units <= int(cand["units_authorized"]):
                patches.append(("auth_id", cand["auth_id"],
                                "Unique matching authorization with sufficient predicted balance; confirm before use"))
        if valid:
            remaining = int(auth["units_authorized"]) - state.auth_used[auth["auth_id"]]
            if units > remaining:
                fail("D02", f"Billed {units} units; authorization has {remaining} projected units remaining",
                     "Reconcile prior payments and balance; request more authorization if appropriate. Do not reduce documented care automatically.")

    days = (submitted - dos).days
    limit = p["timely_filing_days"]
    if limit is not None and days > limit:
        fail("D03", f"Submitted {days} days after service; limit is {limit}",
             "Check any payer exception or appeal route. Do not change the actual submission or service date.")

    mod_rules = [m for m in p["modifier_rules"] if m["service_code"] == claim["service_code"]]
    specific = [m for m in mod_rules if m["days"] and DAYS[dos.weekday()] in m["days"]]
    chosen = (specific or [m for m in mod_rules if not m["days"]] or [None])[0]
    if chosen and claim["modifier"] != chosen["modifier"]:
        fail("D04", f"Modifier {claim['modifier'] or '(missing)'}; expected {chosen['modifier']}",
             f"Use modifier {chosen['modifier']}; if already submitted, follow the corrected-claim workflow.")
        patches.append(("modifier", chosen["modifier"], "Explicit payer modifier rule for this service date"))

    evv = ctx.evv[(claim["patient_id"], claim["service_code"], claim["date_of_service"])]
    minutes = evv["minutes"]
    supported = evv_units(minutes, p["evv"]["rounding"])
    if p["evv"]["required"]:
        if not evv["verified_ids"]:
            fail("D01", "No verified EVV visit for patient, service and clock-in date",
                 "Locate the visit or resolve verification with the coordinator; do not mark a visit verified automatically.")
        elif units > supported:
            fail("D09", f"Billed {units} units; {minutes:g} verified minutes support {supported}",
                 f"At most {supported} units are supported by current EVV; confirm documented care before changing units or amounts.")

    daily_before = state.daily_paid[State.daily_key(claim)]
    cap = p["daily_max_units"]
    if cap is not None and daily_before + units > cap:
        fail("D06", f"Projected daily usage {daily_before} plus {units} billed units exceeds {cap}",
             "Check all services paid for this member and date; confirm any exception with the payer.")

    transforms = ([version["code"]] if version.get("code") else []) + [u["code"] for u in rules.updates]
    if transforms:
        from .code_rules import apply_code, RuleCodeError
        # Sanitized claims and computed context only: labels and live state never enter rule code.
        context = dict(verified_minutes=minutes, evv_supported_units=supported,
                       authorization_remaining=remaining, daily_paid_before=daily_before,
                       filing_days=days, prior_submission_ids=list(dup_ids),
                       authorizations=[dict(a) for a in ctx.auth_index[(claim["payer"], claim["patient_id"], claim["service_code"])]],
                       prior_claims=[{f: c.get(f, "") for f in CLAIM_FIELDS}
                                     for c in state.by_id.values()
                                     if c["payer"] == claim["payer"] and c["patient_id"] == claim["patient_id"]])
        original_failures = {f["code"]: dict(f) for f in failures}
        try:
            for source in transforms:
                failures = apply_code(source, claim, context, failures)
        except RuleCodeError as exc:
            result = _blank_result("unable_to_check", "Approved rule could not execute; fix the rule to check this claim", [str(exc)])
            result["rule_error"] = str(exc)
            return result
        for failure in failures:
            failure["rule"] = (f"{claim['payer']} v{version['version']} (from {version['effective_from']})"
                               + ("; accepted updates " + ", ".join(str(u["version"]) for u in rules.updates) if rules.updates else ""))
        # Remove baseline field suggestions whose corresponding failure was removed by code.
        codes = {f["code"] for f in failures}
        unchanged_modifier = any(f["code"] == "D04" and f["reason"] == original_failures.get("D04", {}).get("reason")
                                 and f["suggestion"] == original_failures.get("D04", {}).get("suggestion") for f in failures)
        patches = [p for p in patches if (p[0] == "auth_id" and codes & {"D07", "D08"})
                   or (p[0] == "modifier" and unchanged_modifier)]

    failures.sort(key=lambda f: f["code"])  # alphabetical for stable output, not priority
    corr_warning = any(w.startswith("Correction reference") for w in warnings)
    status = "denied" if failures else ("unable_to_check" if corr_warning else "paid")
    return dict(
        status=status, denial_codes=[f["code"] for f in failures],
        remark=" | ".join(f["reason"] for f in failures) if failures else
        ("No documented rule violation detected" if status == "paid" else "Original claim reference is missing or does not match"),
        failures=failures, warnings=warnings,
        proposed_changes={f: dict(value=v, reason=r) for f, v, r in patches},
        evidence=dict(verified_minutes=minutes, evv_supported_units=supported,
                      verified_visit_ids=evv["verified_ids"], unverified_visit_ids=evv["other_ids"],
                      authorization_remaining=remaining, daily_paid_before=daily_before,
                      authorization_predicted_usage=state.provisional_auth_used[claim["auth_id"]],
                      authorization_provisional_claim_ids=list(state.provisional_auth_claims[claim["auth_id"]]),
                      filing_days=days, filing_limit=limit, prior_submission_ids=list(dup_ids)),
        rule_version=version["version"])
