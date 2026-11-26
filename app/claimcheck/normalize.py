"""Sensible, auditable normalisation and validation of claim rows.

Source values are never modified: each row keeps its raw values, the cleaned
values and a list of cell-level changes (for highlighting). Unknown values are
flagged, never guessed. Slash dates mean US MM/DD/YYYY.
"""

import csv
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

CLAIM_FIELDS = ("claim_id agency_id payer patient_id service_code date_of_service submitted_date "
                "units auth_id modifier frequency_code original_claim_id billed_amount").split()
HISTORY_EXTRA = ["status", "paid_amount", "denial_code", "remark"]
AUTH_FIELDS = "auth_id patient_id payer_id service_code start_date end_date units_authorized".split()
EVV_FIELDS = "visit_id caregiver_id patient_id service_code clock_in clock_out verification_status".split()

ID_PATTERNS = {
    "claim_id": r"C\d{6}", "original_claim_id": r"C\d{6}", "agency_id": r"AG\d{3}",
    "patient_id": r"PT\d{4}", "auth_id": r"AUTH\d{5}", "visit_id": r"V\d{6}", "caregiver_id": r"CG\d{4}",
}
PAYER_ALIASES = {
    "py01": "PY01", "northwind health plan": "PY01", "northwind hp": "PY01", "northwind": "PY01",
    "py02": "PY02", "harbor medicaid plan": "PY02", "harbor medicaid": "PY02", "harbor": "PY02",
    "py03": "PY03", "summit care partners": "PY03", "summit care": "PY03", "summit": "PY03",
}
ENUMS = {
    "service_code": {"T1019", "T1005", "S5125"},
    "modifier": {"U1", "U2", "U3"},
    "frequency_code": {"1", "7"},
    "status": {"paid", "denied", "pending"},
    "verification_status": {"verified", "rejected", "manual_edit_pending"},
    "denial_code": {f"D{i:02}" for i in range(1, 11)} | {"D99"},
}
DATES = {"date_of_service", "submitted_date", "start_date", "end_date"}
OPTIONAL = {"auth_id", "modifier", "original_claim_id", "denial_code", "remark"}


def canonical(field, value):
    """Return a validated canonical string or raise ValueError."""
    value = value.strip()
    if field in {"payer", "payer_id"}:
        key = re.sub(r"\s+", " ", value).casefold()
        if key not in PAYER_ALIASES:
            raise ValueError("unknown payer alias")
        return PAYER_ALIASES[key]
    if field in ID_PATTERNS:
        value = value.upper()
        if not re.fullmatch(ID_PATTERNS[field], value):
            raise ValueError("invalid identifier format")
    elif field in DATES:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            fmt = "%Y-%m-%d"
        elif re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", value):
            fmt = "%m/%d/%Y"
        else:
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
        if not re.fullmatch(r"\d+(?:\.\d{1,2})?", value) or amount < 0:
            raise ValueError("expected nonnegative amount with at most two decimals")
    elif field in ENUMS:
        if field in {"modifier", "service_code", "denial_code"}:
            value = value.upper()
        if field in {"status", "verification_status"}:
            value = value.lower()
        if field == "status" and value in {"unknown", "unresolved", "submitted"}:
            value = "pending"
        if value not in ENUMS[field]:
            raise ValueError("unsupported value")
    return value


def read_csv_text(text):
    handle = io.StringIO(text.lstrip("﻿"), newline="")
    reader = csv.DictReader(handle)
    rows = []
    for raw in reader:
        if None in raw or any(v is None for v in raw.values()):
            raise ValueError(f"Wrong number of CSV fields at line {reader.line_num}")
        rows.append(raw)
    return reader.fieldnames or [], rows


def normalize_claims(fieldnames, rows):
    """Return one record per input row.

    record = {line, raw, clean, changes[{field,before,after}], issues[{field,severity,message}],
              duplicate_of}
    `severity == "error"` means the row must be corrected before it can be checked.
    """
    missing = [f for f in CLAIM_FIELDS if f not in fieldnames]
    if missing:
        raise ValueError("Missing required column(s): " + ", ".join(missing))
    records, seen = [], {}
    for index, raw in enumerate(rows):
        record = dict(line=index + 2, raw=dict(raw), clean={}, changes=[], issues=[], duplicate_of=None)
        for field in CLAIM_FIELDS:
            value = raw.get(field) or ""
            if not value.strip():
                record["clean"][field] = ""
                if field not in OPTIONAL:
                    record["issues"].append(dict(field=field, severity="error", message="Required value is blank"))
                continue
            try:
                cleaned = canonical(field, value)
            except ValueError as exc:
                record["clean"][field] = value
                record["issues"].append(dict(field=field, severity="error", message=f"{exc}: {value!r}"))
                continue
            record["clean"][field] = cleaned
            if cleaned != value:
                record["changes"].append(dict(field=field, before=value, after=cleaned))
        clean = record["clean"]
        if clean["frequency_code"] == "7" and not clean["original_claim_id"]:
            record["issues"].append(dict(field="original_claim_id", severity="error",
                                         message="Corrected claim (frequency 7) needs the original claim ID"))
        if clean["date_of_service"] and clean["submitted_date"] and not any(
                i["field"] in {"date_of_service", "submitted_date"} for i in record["issues"]):
            if clean["submitted_date"] < clean["date_of_service"]:
                record["issues"].append(dict(field="submitted_date", severity="error",
                                             message="Submitted before the date of service"))
        records.append(record)
    # Identical rows are exports of one claim; same ID with different values conflicts.
    by_id = {}
    for record in records:
        cid = record["clean"]["claim_id"]
        if not cid:
            continue
        if cid in by_id:
            first = by_id[cid]
            if first["clean"] == record["clean"]:
                record["duplicate_of"] = first["line"]
            else:
                for r in (first, record):
                    if not any(i["field"] == "claim_id" and "conflict" in i["message"] for i in r["issues"]):
                        r["issues"].append(dict(field="claim_id", severity="error",
                                                message=f"Claim ID reused with different values (lines {first['line']} and {record['line']})"))
        else:
            by_id[cid] = record
    return records


def normalize_reference_rows(rows, fields):
    """Clean authorizations / EVV / history rows. Returns (clean_rows, errors)."""
    clean_rows, errors = [], []
    for i, raw in enumerate(rows, 2):
        clean = {}
        for field in fields + [f for f in HISTORY_EXTRA if f in raw]:
            value = raw.get(field) or ""
            if field == "remark":
                clean[field] = value
            elif field == "status" and not value.strip():
                clean[field] = "pending"
            elif not value.strip():
                clean[field] = ""
                if field not in OPTIONAL and field not in {"paid_amount"}:
                    errors.append(f"line {i}: {field} blank")
            else:
                try:
                    clean[field] = canonical(field, value)
                except ValueError as exc:
                    clean[field] = value
                    errors.append(f"line {i}: {field}: {exc}")
        clean_rows.append(clean)
    return clean_rows, errors
