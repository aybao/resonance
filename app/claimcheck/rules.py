"""Versioned payer rules.

Rules are plain data (a closed vocabulary the deterministic engine understands).
AI proposes versions in this format; code validates them; users approve them.
A version applies to claims whose *date of service* is on or after its
`effective_from`, until a later version of the same payer takes over.
"""

import copy
import json
import re
from datetime import date
from pathlib import Path

DENIAL_CODES = {
    "D01": "No verified EVV visit for the date of service",
    "D02": "Units exceed the authorization's remaining units",
    "D03": "Claim received after the payer's timely filing limit",
    "D04": "Missing or invalid modifier",
    "D05": "Duplicate claim",
    "D06": "Daily unit maximum exceeded",
    "D07": "Prior authorization number missing",
    "D08": "Authorization not valid for this patient, service or date of service",
    "D09": "Billed units exceed the EVV-verified time",
    "D10": "Payer contract-specific denial",
    "D99": "Other / referred for manual review",
}
SERVICES = {"T1019", "T1005", "S5125"}
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
ROUNDING = {"eight_minute": "8-minute rule: remaining 8+ minutes bill as one more unit",
            "full_units": "Only complete 15-minute units are billable"}

DEFAULT_PARAMS = {
    "evv": {"required": True, "rounding": "full_units"},
    "authorization": {"required": True},
    "timely_filing_days": None,
    "modifier_rules": [],
    "daily_max_units": None,
    "duplicate_check": True,
    "notes": [],
}
PARAMS_DOC = """params object (all keys optional; omitted keys keep the previous version's value):
  evv: {required: bool, rounding: "eight_minute" | "full_units"}
  authorization: {required: bool}      # missing number D07, invalid D08, over balance D02
  timely_filing_days: integer | null   # null = no limit; calendar days from date of service to submitted date
  modifier_rules: [{service_code: "T1019", days: ["sat","sun"] | null, modifier: "U2"}]
                  # a rule with `days` beats one with days=null for the same service
  daily_max_units: integer | null      # paid units per member per day across all services
  duplicate_check: bool
  notes: [string]                      # caveats shown as warnings, e.g. unmodelled contract terms"""


def validate_params(params):
    """Return a list of problems (empty if valid). Strict: unknown keys are rejected."""
    errors = []
    if not isinstance(params, dict):
        return ["params must be an object"]
    for key in params:
        if key not in DEFAULT_PARAMS:
            errors.append(f"unknown rule key {key!r}")
    evv = params.get("evv")
    if evv is not None:
        if not isinstance(evv, dict) or set(evv) - {"required", "rounding"}:
            errors.append("evv must be {required, rounding}")
        else:
            if not isinstance(evv.get("required", True), bool):
                errors.append("evv.required must be boolean")
            if evv.get("rounding", "full_units") not in ROUNDING:
                errors.append(f"evv.rounding must be one of {sorted(ROUNDING)}")
    auth = params.get("authorization")
    if auth is not None and (not isinstance(auth, dict) or set(auth) - {"required"}
                             or not isinstance(auth.get("required", True), bool)):
        errors.append("authorization must be {required: bool}")
    for key in ("timely_filing_days", "daily_max_units"):
        v = params.get(key)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v <= 0):
            errors.append(f"{key} must be a positive integer or null")
    if "duplicate_check" in params and not isinstance(params["duplicate_check"], bool):
        errors.append("duplicate_check must be boolean")
    notes = params.get("notes")
    if notes is not None and not (isinstance(notes, list) and all(isinstance(n, str) for n in notes)):
        errors.append("notes must be a list of strings")
    mods = params.get("modifier_rules")
    if mods is not None:
        if not isinstance(mods, list):
            errors.append("modifier_rules must be a list")
        else:
            for i, m in enumerate(mods):
                if not isinstance(m, dict) or set(m) - {"service_code", "days", "modifier"}:
                    errors.append(f"modifier_rules[{i}] must be {{service_code, days, modifier}}")
                    continue
                if m.get("service_code") not in SERVICES:
                    errors.append(f"modifier_rules[{i}].service_code must be one of {sorted(SERVICES)}")
                if not isinstance(m.get("modifier"), str) or not re.fullmatch(r"[A-Z0-9]{2}", m["modifier"]):
                    errors.append(f"modifier_rules[{i}].modifier must be a 2-character modifier")
                days = m.get("days")
                if days is not None and not (isinstance(days, list) and days and all(d in DAYS for d in days)):
                    errors.append(f"modifier_rules[{i}].days must be null or a list of {DAYS}")
    return errors


def valid_date(text):
    try:
        date.fromisoformat(text)
        return True
    except (TypeError, ValueError):
        return False


class RuleStore:
    """payer_id -> {name, versions:[{version, effective_from, params, source, summary, approved_at}]}.

    Also holds user-confirmed acceptance examples (versioned tests).
    """

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.payers = {}
        self.acceptance = []
        if self.path and self.path.exists():
            data = json.loads(self.path.read_text())
            self.payers, self.acceptance = data["payers"], data.get("acceptance", [])

    # persistence -------------------------------------------------------
    def save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.snapshot(), indent=2) + "\n")

    def snapshot(self):
        return {"payers": copy.deepcopy(self.payers), "acceptance": copy.deepcopy(self.acceptance)}

    def restore(self, snap):
        self.payers, self.acceptance = copy.deepcopy(snap["payers"]), copy.deepcopy(snap["acceptance"])

    # lookup ------------------------------------------------------------
    def versions(self, payer_id):
        return sorted(self.payers.get(payer_id, {}).get("versions", []), key=lambda v: v["effective_from"])

    def resolve(self, payer_id, service_date):
        """Return the version in force for the date of service, or None."""
        found = None
        for v in self.versions(payer_id):
            if v["effective_from"] <= service_date:
                found = v
        return found

    def full_params(self, payer_id, effective_from, proposed):
        """Merge `proposed` over the version in force at effective_from (or defaults)."""
        base = self.resolve(payer_id, effective_from)
        merged = copy.deepcopy(base["params"] if base else DEFAULT_PARAMS)
        merged.update(copy.deepcopy(proposed))
        return merged

    # mutation ----------------------------------------------------------
    def add_version(self, payer_id, effective_from, params, source="", summary="", payer_name=None, approved_at=None):
        if not valid_date(effective_from):
            raise ValueError("effective_from must be YYYY-MM-DD")
        params = self.full_params(payer_id, effective_from, params) if payer_id in self.payers else \
            {**copy.deepcopy(DEFAULT_PARAMS), **copy.deepcopy(params)}
        errors = validate_params(params)
        if errors:
            raise ValueError("; ".join(errors))
        payer = self.payers.setdefault(payer_id, {"name": payer_name or payer_id, "versions": []})
        if payer_name:
            payer["name"] = payer_name
        payer["versions"] = [v for v in payer["versions"] if v["effective_from"] != effective_from]
        number = max([v["version"] for v in payer["versions"]], default=0) + 1
        version = dict(version=number, effective_from=effective_from, params=params,
                       source=source, summary=summary, approved_at=approved_at)
        payer["versions"].append(version)
        payer["versions"].sort(key=lambda v: v["effective_from"])
        return version


def baseline_store(path=None):
    """Baseline rules transcribed from the three payers' base manuals only. Later changes go through the update workflow."""
    store = RuleStore(path)
    base = "1900-01-01"
    store.add_version("PY01", base, dict(
        evv={"required": True, "rounding": "eight_minute"}, authorization={"required": True},
        timely_filing_days=30, modifier_rules=[], daily_max_units=None, duplicate_check=True, notes=[]),
        source="Northwind Provider Manual 4.2, 4.3, 4.5, 6.1, 6.4", summary="Northwind manual", payer_name="Northwind Health Plan")
    store.add_version("PY02", base, dict(
        evv={"required": True, "rounding": "full_units"}, authorization={"required": True},
        timely_filing_days=None, modifier_rules=[{"service_code": "T1019", "days": None, "modifier": "U2"}],
        daily_max_units=32, duplicate_check=True, notes=[]),
        source="Harbor Billing Guide: Visit verification, Authorizations, Modifiers, Daily maximum, Filing limits, Duplicates",
        summary="Harbor billing guide", payer_name="Harbor Medicaid Plan")
    store.add_version("PY03", base, dict(
        evv={"required": True, "rounding": "full_units"}, authorization={"required": True},
        timely_filing_days=60, modifier_rules=[], daily_max_units=None, duplicate_check=True,
        notes=["Summit agency contract requirements (section 9.5) are not supplied; D10 cannot be determined."]),
        source="Summit Network Provider Handbook sections 9.1-9.5", summary="Summit handbook", payer_name="Summit Care Partners")
    return store
