"""Flag issues that deterministic cleaning cannot resolve.

Run: python3 check_formatting.py [--data-dir PATH] [--output PATH]
Use --claims PATH to check just that claims file and supporting authorization/EVV
files, excluding the held-out claims. Exit 1 means malformed data. Safe formatting
differences are accepted in memory; run clean_data.py to persist their fixes.
CSV line numbers include the header. No source data is changed.
"""

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from paths import DATA_DIR, EXPERIMENT_DIR


CLAIM_FIELDS = "claim_id agency_id payer patient_id service_code date_of_service submitted_date units auth_id modifier frequency_code original_claim_id billed_amount".split()
SCHEMAS = {
    "claims_history.csv": CLAIM_FIELDS + "status paid_amount denial_code remark".split(),
    "claims_to_check.csv": CLAIM_FIELDS,
    "authorizations.csv": "auth_id patient_id payer_id service_code start_date end_date units_authorized".split(),
    "evv_visits.csv": "visit_id caregiver_id patient_id service_code clock_in clock_out verification_status".split(),
}
ID_PATTERNS = {
    "claim_id": r"C\d{6}", "original_claim_id": r"C\d{6}",
    "agency_id": r"AG\d{3}", "patient_id": r"PT\d{4}",
    "auth_id": r"AUTH\d{5}", "visit_id": r"V\d{6}", "caregiver_id": r"CG\d{4}",
}
PAYERS = {
    "PY01": "PY01", "Northwind Health Plan": "PY01", "NORTHWIND HP": "PY01",
    "PY02": "PY02", "Harbor Medicaid Plan": "PY02", "Harbor Medicaid": "PY02",
    "PY03": "PY03", "Summit Care Partners": "PY03", "SUMMIT CARE": "PY03",
}
ENUMS = {
    "service_code": {"T1019", "T1005", "S5125"},
    "modifier": {"U1", "U2", "U3"}, "frequency_code": {"1", "7"},
    "status": {"paid", "denied"},
    "verification_status": {"verified", "rejected", "manual_edit_pending"},
    "denial_code": {f"D{i:02}" for i in range(1, 11)} | {"D99"},
}
DATES = {"date_of_service", "submitted_date", "start_date", "end_date"}
OPTIONAL = {"auth_id", "modifier", "original_claim_id", "denial_code", "remark"}


def canonical(field, value):
    """Return a validated canonical string, or raise ValueError.

    Slash dates explicitly mean MM/DD/YYYY for this synthetic US export.
    Unknown aliases and date formats are rejected rather than guessed.
    """
    value = value.strip()
    if field in {"payer", "payer_id"}:
        if value not in PAYERS:
            raise ValueError("unknown payer alias")
        return PAYERS[value]
    if field in ID_PATTERNS:
        if not re.fullmatch(ID_PATTERNS[field], value):
            raise ValueError("invalid identifier format")
    elif field in DATES:
        fmt = "%Y-%m-%d" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else "%m/%d/%Y"
        if fmt == "%m/%d/%Y" and not re.fullmatch(r"\d{2}/\d{2}/\d{4}", value):
            raise ValueError("expected YYYY-MM-DD or MM/DD/YYYY")
        return datetime.strptime(value, fmt).date().isoformat()
    elif field in {"clock_in", "clock_out"}:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", value):
            raise ValueError("expected YYYY-MM-DD HH:MM")
        return datetime.strptime(value, "%Y-%m-%d %H:%M").strftime("%Y-%m-%d %H:%M")
    elif field in {"units", "units_authorized"}:
        if not re.fullmatch(r"\d+", value) or int(value) <= 0:
            raise ValueError("expected positive integer")
        return str(int(value))
    elif field in {"billed_amount", "paid_amount"}:
        try:
            amount = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("expected nonnegative monetary amount") from exc
        if not re.fullmatch(r"\d+(?:\.\d{1,2})?", value) or not amount.is_finite() or amount < 0:
            raise ValueError("expected nonnegative amount with at most two decimal places")
    elif field in ENUMS:
        if field in {"modifier", "service_code"}:
            value = value.upper()
        if value not in ENUMS[field]:
            raise ValueError("unsupported value for supplied data contract")
    return value


def audit(data_dir, claims_file=None):
    issues = []
    records = {}
    counts = {}

    def flag(file, line, field, kind, severity, value, message, suggested=None):
        item = dict(file=file, line=line, field=field, kind=kind,
                    severity=severity, value=value, message=message)
        if suggested is not None:
            item["suggested"] = suggested
        issues.append(item)

    inputs = {name: (data_dir / name, fields) for name, fields in SCHEMAS.items()}
    if claims_file is not None:
        inputs = {name: item for name, item in inputs.items() if not name.startswith("claims_")}
        with claims_file.open(newline="", encoding="utf-8-sig") as handle:
            header = next(csv.reader(handle), [])
        fields = SCHEMAS["claims_history.csv"] if "status" in header else CLAIM_FIELDS
        inputs[claims_file.name] = (claims_file, fields)

    for name, (path, fields) in inputs.items():
        records[name] = []
        counts[name] = 0
        if not path.exists():
            flag(name, 1, "", "missing_file", "error", "", "Required file is absent")
            continue
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or set(reader.fieldnames) != set(fields) or len(reader.fieldnames) != len(fields):
                flag(name, 1, "", "schema", "error", reader.fieldnames,
                     "Headers must match the supplied schema", fields)
                continue
            seen = {}
            for raw in reader:
                line = reader.line_num
                counts[name] += 1
                if None in raw or any(v is None for v in raw.values()):
                    flag(name, line, "", "row_shape", "error", raw, "Wrong number of CSV fields")
                    continue
                clean = {}
                for field, value in raw.items():
                    # Authorization ID is required in the authorization master, but
                    # may legitimately be absent on a claim that should be denied.
                    optional = field in OPTIONAL and not (name == "authorizations.csv" and field == "auth_id")
                    if not value.strip():
                        if not optional:
                            flag(name, line, field, "missing_required", "error", value, "Required value is blank")
                        clean[field] = ""
                        continue
                    try:
                        clean[field] = canonical(field, value)
                    except ValueError as exc:
                        flag(name, line, field, "invalid_format", "error", value, str(exc))
                        continue
                key_field = fields[0]
                key = clean.get(key_field)
                if key:
                    if key in seen:
                        first_line, first_clean = seen[key]
                        if clean != first_clean:
                            flag(name, line, key_field, "conflicting_id", "error", key,
                                 f"ID first occurs on line {first_line} with different values; cannot choose a record automatically")
                    else:
                        seen[key] = (line, clean)
                records[name].append((line, clean))

    auth_ids = {r.get("auth_id") for _, r in records["authorizations.csv"]} - {None, ""}
    claim_ids = {r.get("claim_id") for rows in records.values() for _, r in rows} - {None, ""}
    for name, rows in records.items():
        for line, row in rows:
            if "claim_id" in row:
                for field, known in (("auth_id", auth_ids), ("original_claim_id", claim_ids)):
                    if row.get(field) and row[field] not in known:
                        flag(name, line, field, "unresolved_reference", "warning", row[field], "Reference absent from supplied pack; investigate context")
                if row.get("frequency_code") == "7" and not row.get("original_claim_id"):
                    flag(name, line, "original_claim_id", "missing_correction_reference", "error", "", "Corrected claim requires original claim ID")
                if "auth_id" in row and not row["auth_id"]:
                    flag(name, line, "auth_id", "missing_business_input", "info", "", "Keep for authorization denial check; not a formatting failure")
            for start, end in (("start_date", "end_date"), ("clock_in", "clock_out"), ("date_of_service", "submitted_date")):
                if row.get(start) and row.get(end) and row[end] < row[start]:
                    flag(name, line, end, "invalid_chronology", "error", row[end], f"Precedes {start}")
    return {
        "contract": "Supplied synthetic export; ISO canonical dates; slash dates explicitly US month/day/year. No source data changed.",
        "row_counts": counts,
        "issue_counts": dict(Counter(i["kind"] for i in issues)),
        "severity_counts": dict(Counter(i["severity"] for i in issues)),
        "affected_rows": len({(i["file"], i["line"]) for i in issues}),
        "issues": issues,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output", type=Path, default=EXPERIMENT_DIR / "formatting_report.json")
    parser.add_argument("--claims", type=Path, help="Check this claims file only, plus authorization and EVV context")
    args = parser.parse_args()
    report = audit(args.data_dir, args.claims)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "issues"}, indent=2))
    print(f"Details: {args.output}")
    return int(report["severity_counts"].get("error", 0) > 0)


if __name__ == "__main__":
    raise SystemExit(main())
