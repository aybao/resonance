"""Small, explicit scenarios used to review rule updates and as versioned acceptance tests.

A scenario builds a tiny controlled world (one member, one authorization, a few EVV visits,
optional history) so the expected code set can be reasoned about independently of the engine.

scenario = {
  id, title,
  claim:   {payer, service_code, date_of_service, submitted_date, units, modifier, frequency_code?},
  context: {evv_minutes: [int,...], unverified_evv_minutes?: [int,...],
            auth: "valid"|"missing"|"wrong_service"|"wrong_patient"|"outside_dates" (default valid),
            auth_units_remaining?: int (default 1000), daily_paid_units?: int, prior_submission?: bool},
  expected_codes: [..] | null   # null = expectation not decided yet
}
"""

import copy
import re
from datetime import datetime, timedelta

from .engine import Context, State, check_claim
from .rules import DENIAL_CODES, SERVICES, valid_date

PATIENT, AUTH = "PT9999", "AUTH99999"
AUTH_STATES = {"valid", "missing", "wrong_service", "wrong_patient", "outside_dates"}


def validate_scenario(s):
    errors = []
    if not isinstance(s, dict):
        return ["scenario must be an object"]
    claim, ctx = s.get("claim"), s.get("context", {})
    if not isinstance(claim, dict) or not isinstance(ctx, dict):
        return ["scenario needs claim and context objects"]
    if claim.get("service_code") not in SERVICES:
        errors.append("claim.service_code invalid")
    for f in ("date_of_service", "submitted_date"):
        if not valid_date(claim.get(f)):
            errors.append(f"claim.{f} must be YYYY-MM-DD")
    if not isinstance(claim.get("units"), int) or isinstance(claim.get("units"), bool) or claim["units"] <= 0:
        errors.append("claim.units must be a positive integer")
    if claim.get("modifier", "") not in ("", "U1", "U2", "U3"):
        errors.append("claim.modifier must be '', U1, U2 or U3")
    if claim.get("frequency_code", "1") not in ("1", "7"):
        errors.append("claim.frequency_code must be 1 or 7")
    for key in ("evv_minutes", "unverified_evv_minutes"):
        v = ctx.get(key, [])
        if not isinstance(v, list) or not all(isinstance(m, int) and not isinstance(m, bool) and 0 < m <= 600 for m in v):
            errors.append(f"context.{key} must be a list of minutes (1-600)")
    if ctx.get("auth", "valid") not in AUTH_STATES:
        errors.append(f"context.auth must be one of {sorted(AUTH_STATES)}")
    for key in ("auth_units_remaining", "daily_paid_units"):
        v = ctx.get(key, 0)
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            errors.append(f"context.{key} must be a non-negative integer")
    if "prior_submission" in ctx and not isinstance(ctx["prior_submission"], bool):
        errors.append("context.prior_submission must be boolean")
    from .normalize import canonical
    for field in ("billed_amount", "agency_id"):
        if field in claim:
            try:
                canonical(field, str(claim[field]))
            except ValueError as exc:
                errors.append(f"claim.{field}: {exc}")
    ec = s.get("expected_codes")
    if ec is not None and not (isinstance(ec, list) and all(isinstance(c, str) and re.fullmatch(r"D\d{2}", c) for c in ec)):
        errors.append("expected_codes must be null or a list of denial codes")
    payer = claim.get("payer", s.get("payer_id"))
    if not isinstance(payer, str) or not re.fullmatch(r"PY\d{2}", payer):
        errors.append("claim.payer must be a payer ID such as PY01")
    return errors


def build_world(s):
    claim_in, c = s["claim"], s.get("context", {})
    dos = claim_in["date_of_service"]
    payer = claim_in.get("payer", s.get("payer_id"))
    claim = dict(claim_id="C999999", agency_id="AG999", payer=payer, patient_id=PATIENT,
                 service_code=claim_in["service_code"], date_of_service=dos,
                 submitted_date=claim_in["submitted_date"], units=str(claim_in["units"]),
                 auth_id="", modifier=claim_in.get("modifier", ""), frequency_code=claim_in.get("frequency_code", "1"),
                 original_claim_id="C999998" if claim_in.get("frequency_code") == "7" else "", billed_amount="1")
    for field in ("billed_amount", "agency_id"):
        if field in claim_in:
            claim[field] = str(claim_in[field])
    auth_state = c.get("auth", "valid")
    auths = []
    if auth_state != "missing":
        claim["auth_id"] = AUTH
        a = dict(auth_id=AUTH, patient_id=PATIENT, payer_id=payer, service_code=claim["service_code"],
                 start_date="2000-01-01", end_date="2099-12-31",
                 units_authorized=str(c.get("auth_units_remaining", 1000)))
        if auth_state == "wrong_service":
            a["service_code"] = next(x for x in sorted(SERVICES) if x != claim["service_code"])
        elif auth_state == "wrong_patient":
            a["patient_id"] = "PT9998"
        elif auth_state == "outside_dates":
            a["start_date"], a["end_date"] = "2000-01-01", (datetime.fromisoformat(dos) - timedelta(days=1)).date().isoformat()
        auths.append(a)
    visits, start = [], datetime.fromisoformat(dos + " 06:00")
    for i, (mins, status) in enumerate([(m, "verified") for m in c.get("evv_minutes", [])] +
                                       [(m, "manual_edit_pending") for m in c.get("unverified_evv_minutes", [])]):
        t0 = start + timedelta(hours=3 * i)
        visits.append(dict(visit_id=f"V99990{i}", caregiver_id="CG9999", patient_id=PATIENT,
                           service_code=claim["service_code"], clock_in=t0.strftime("%Y-%m-%d %H:%M"),
                           clock_out=(t0 + timedelta(minutes=mins)).strftime("%Y-%m-%d %H:%M"),
                           verification_status=status))
    state = State()
    if c.get("daily_paid_units"):
        state.daily_paid[State.daily_key(claim)] += c["daily_paid_units"]
    if c.get("prior_submission"):
        state.submissions[State.duplicate_key(claim)].append("C999990")
    if claim_in.get("frequency_code") == "7":
        state.by_id["C999998"] = dict(claim, claim_id="C999998", frequency_code="1", original_claim_id="")
    return claim, Context(auths, visits), state


def run_scenario(s, rules):
    claim, ctx, state = build_world(s)
    r = check_claim(claim, state, ctx, rules)
    return dict(status=r["status"], codes=r["denial_codes"], reasons=[f["reason"] for f in r["failures"]],
                warnings=r["warnings"], error=r.get("rule_error"))


def run_examples(examples, rules):
    """Run scenarios; for each return actual vs expected."""
    out = []
    for s in examples:
        actual = run_scenario(s, rules)
        exp = s.get("expected_codes")
        out.append(dict(id=s.get("id"), title=s.get("title"), payer_id=s["claim"].get("payer", s.get("payer_id")), claim=s["claim"],
                        full_claim=build_world(s)[0], context=s.get("context", {}), expected_codes=exp, actual=actual,
                        confirmed=s.get("confirmed", False), basis=s.get("basis", ""), origin=s.get("origin", ""),
                        passed=None if exp is None else sorted(exp) == sorted(actual["codes"])))
    return out


def deterministic_boundary_examples(payer_id, effective_from, params):
    """Boundary scenarios derived mechanically from the rule parameters (no AI).

    They only *pose* cases; the expected outcome is for the user to confirm.
    """
    from datetime import date
    eff = date.fromisoformat(effective_from)
    from .rules import DAYS

    def modifier_for(service, day):
        rules_ = [m for m in params["modifier_rules"] if m["service_code"] == service]
        hit = [m for m in rules_ if m["days"] and DAYS[day.weekday()] in m["days"]] or [m for m in rules_ if not m["days"]]
        return hit[0]["modifier"] if hit else "U1"

    base_claim = dict(service_code="T1019", date_of_service=effective_from, submitted_date=effective_from, units=4,
                      modifier=modifier_for("T1019", eff))
    ctx = dict(evv_minutes=[60])
    ex = []

    def add(title, claim=None, context=None):
        ex.append(dict(id=f"auto-{len(ex) + 1}", title=title, payer_id=payer_id, origin="boundary",
                       claim={**base_claim, **(claim or {})}, context={**ctx, **(context or {})}, expected_codes=None))

    add("Unaffected baseline: everything in order")
    lim = params["timely_filing_days"]
    if lim:
        for days, label in ((lim, "exactly at"), (lim + 1, "one day past")):
            add(f"Filing {label} the {lim}-day limit",
                claim=dict(submitted_date=(eff + timedelta(days=days)).isoformat()))
        add("Day before this version takes effect (previous rules apply)",
            claim=dict(date_of_service=(eff - timedelta(days=1)).isoformat(),
                       submitted_date=(eff - timedelta(days=1) + timedelta(days=lim + 1)).isoformat()))
    if params["daily_max_units"]:
        cap = params["daily_max_units"]
        add(f"Daily total exactly {cap}", claim=dict(units=4), context=dict(daily_paid_units=cap - 4, evv_minutes=[60]))
        add(f"Daily total one over {cap}", claim=dict(units=4), context=dict(daily_paid_units=cap - 3, evv_minutes=[60]))
    for rule in params["modifier_rules"]:
        day = eff
        while rule["days"] and DAYS[day.weekday()] not in rule["days"]:
            day += timedelta(days=1)
        svc = rule["service_code"]
        iso = day.isoformat()
        add(f"{svc} on {iso} ({DAYS[day.weekday()]}) with the correct modifier",
            claim=dict(service_code=svc, date_of_service=iso, submitted_date=iso, modifier=modifier_for(svc, day)))
        add(f"{svc} on {iso} ({DAYS[day.weekday()]}) with no modifier",
            claim=dict(service_code=svc, date_of_service=iso, submitted_date=iso, modifier=""))
    add("EVV 7 verified minutes beyond a full unit", claim=dict(units=1), context=dict(evv_minutes=[22]))
    add("EVV 8 verified minutes beyond a full unit", claim=dict(units=2), context=dict(evv_minutes=[23]))
    return ex
