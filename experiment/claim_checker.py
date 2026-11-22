"""Documented-rules baseline. Run: python3 claim_checker.py

Outputs test_result.csv, claim_metrics.json, and reproducible cleaning artifacts.
Main predictions use actual pre-cutoff history and predicted test outcomes only.
Historical test outcomes are used separately for scoring and an explicitly
labelled observed-prior-state diagnostic. No test-label-based rule fitting.
"""

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from check_formatting import CLAIM_FIELDS, audit, canonical
from clean_data import clean_file
from pending_claims import PendingAuthorizationReview
from paths import DATA_DIR, EXPERIMENT_DIR, ARTIFACTS_DIR


SOURCES = {
    "D01": "PY01 4.2; PY02 Visit verification; PY03 9.1; billing_basics",
    "D02": "PY01 4.5; PY02 Authorizations; PY03 9.2; billing_basics",
    "D03": "PY01 6.1 + bulletin 2026-07; PY03 9.3",
    "D04": "PY02 Modifiers + bulletin 2026-08",
    "D05": "Payer duplicate sections + billing_basics corrected-claim exception",
    "D06": "PY02 Daily maximum",
    "D07": "PY01 4.5; PY02 Authorizations; PY03 9.2",
    "D08": "PY01 4.5; PY02 Authorizations; PY03 9.2",
    "D09": "PY01 4.3; PY02 Visit verification; PY03 9.1; billing_basics",
}


def load_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def claim_input(row):
    """Explicit allowlist excludes status, paid_amount, denial_code and remark."""
    return {field: row.get(field, "") for field in CLAIM_FIELDS}


def normalized_input(row):
    result = {}
    for field, value in claim_input(row).items():
        try:
            result[field] = canonical(field, value) if value.strip() else ""
        except ValueError:
            result[field] = value
    return result


class State:
    def __init__(self):
        self.auth_used = Counter()
        self.daily_paid = Counter()
        self.submissions = defaultdict(list)
        self.by_id = {}

    @staticmethod
    def duplicate_key(claim):
        return tuple(claim[k] for k in ("payer", "patient_id", "service_code", "date_of_service"))

    @staticmethod
    def daily_key(claim):
        return tuple(claim[k] for k in ("payer", "patient_id", "date_of_service"))

    def record(self, claim, status):
        self.submissions[self.duplicate_key(claim)].append(claim["claim_id"])
        self.by_id[claim["claim_id"]] = claim
        if status == "paid":
            self.auth_used[claim["auth_id"]] += int(claim["units"])
            self.daily_paid[self.daily_key(claim)] += int(claim["units"])


class Checker:
    def __init__(self, authorizations, visits):
        self.authorizations = {r["auth_id"]: r for r in authorizations}
        self.evv = defaultdict(lambda: {"minutes": 0.0, "verified_ids": [], "other_ids": []})
        for visit in visits:
            start = datetime.fromisoformat(visit["clock_in"])
            end = datetime.fromisoformat(visit["clock_out"])
            key = (visit["patient_id"], visit["service_code"], start.date().isoformat())
            entry = self.evv[key]
            if visit["verification_status"] == "verified":
                entry["minutes"] += (end - start).total_seconds() / 60
                entry["verified_ids"].append(visit["visit_id"])
            else:
                entry["other_ids"].append(visit["visit_id"])

    def check(self, claim, state, formatting_errors=()):
        # The public interface accepts only claim fields; outcomes cannot affect it.
        claim = claim_input(claim)
        failures, warnings, patches = [], [], []

        def fail(code, reason, action):
            failures.append(dict(code=code, reason=reason, suggestion=action, source=SOURCES[code]))

        if formatting_errors:
            return dict(status="manual_review", denial_codes=[], remark="Input cannot be checked reliably",
                        failures=[], warnings=list(formatting_errors), proposed_changes={}, evidence={})
        try:
            dos = date.fromisoformat(claim["date_of_service"])
            submitted = date.fromisoformat(claim["submitted_date"])
            units = int(claim["units"])
        except (ValueError, KeyError):
            return dict(status="manual_review", denial_codes=[], remark="Missing or invalid date/units",
                        failures=[], warnings=["Resolve dates and units; no payer code can be inferred"], proposed_changes={}, evidence={})
        payer = claim["payer"]
        if payer not in {"PY01", "PY02", "PY03"}:
            return dict(status="manual_review", denial_codes=[], remark="Unsupported payer",
                        failures=[], warnings=["Obtain the payer's rules"], proposed_changes={}, evidence={})
        if payer == "PY03":
            warnings.append("Summit agency contract requirements are not supplied; D10 cannot be determined")
        duplicate_ids = state.submissions[State.duplicate_key(claim)]
        if claim["frequency_code"] == "1" and duplicate_ids:
            fail("D05", f"Earlier submission exists: {duplicate_ids[0]}",
                 "Reconcile the earlier claim. If correcting it, review frequency 7 and the original claim reference; do not submit another new claim.")
        if claim["frequency_code"] == "7":
            original = state.by_id.get(claim["original_claim_id"])
            if original is None or State.duplicate_key(original) != State.duplicate_key(claim):
                warnings.append("Correction reference missing, not yet submitted, or for a different claim context; payer code unspecified")

        matching_auths = [a for a in self.authorizations.values()
                          if a["payer_id"] == payer and a["patient_id"] == claim["patient_id"]
                          and a["service_code"] == claim["service_code"]
                          and a["start_date"] <= claim["date_of_service"] <= a["end_date"]]
        auth = self.authorizations.get(claim["auth_id"])
        valid_auth = auth is not None and auth in matching_auths
        remaining = None
        if not claim["auth_id"]:
            fail("D07", "Prior authorization number missing", "Confirm and enter the authorization covering this patient, payer, service and date.")
        elif not valid_auth:
            fail("D08", "Authorization not found or not valid for payer, patient, service or date", "Check the authorization reference and coverage; obtain payer confirmation if necessary.")
        if not valid_auth and len(matching_auths) == 1:
            candidate = matching_auths[0]
            if state.auth_used[candidate["auth_id"]] + units <= int(candidate["units_authorized"]):
                patches.append(("auth_id", candidate["auth_id"], "Unique matching authorization with sufficient predicted balance; confirm before use"))
        if valid_auth:
            remaining = int(auth["units_authorized"]) - state.auth_used[auth["auth_id"]]
            if units > remaining:
                fail("D02", f"Billed {units} units; authorization has {remaining} remaining",
                     "Reconcile prior payments and authorization balance; request additional authorization if appropriate. Do not reduce documented care automatically.")

        filing_limit = (45 if dos >= date(2026, 8, 1) else 30) if payer == "PY01" else (60 if payer == "PY03" else None)
        filing_days = (submitted - dos).days
        if filing_limit is not None and filing_days > filing_limit:
            fail("D03", f"Submitted {filing_days} days after service; limit is {filing_limit}",
                 "Review any payer exception or appeal route. Do not change the actual submission or service date.")
        if payer == "PY02" and claim["service_code"] == "T1019":
            expected = "U3" if dos >= date(2026, 9, 1) and dos.weekday() >= 5 else "U2"
            if claim["modifier"] != expected:
                fail("D04", f"Modifier {claim['modifier'] or '(missing)'}; expected {expected}",
                     f"Use modifier {expected}; if already submitted, follow the corrected-claim workflow.")
                patches.append(("modifier", expected, "Explicit Harbor modifier rule for this service date"))

        evv = self.evv[(claim["patient_id"], claim["service_code"], claim["date_of_service"])]
        minutes = evv["minutes"]
        evv_units = int(minutes // 15) + int(payer == "PY01" and minutes % 15 >= 8)
        if not evv["verified_ids"]:
            fail("D01", "No verified EVV visit for patient, service and clock-in date",
                 "Locate the visit or resolve verification with the coordinator/aggregator; do not mark a visit verified automatically.")
        elif units > evv_units:
            fail("D09", f"Billed {units} units; {minutes:g} verified minutes support {evv_units}",
                 f"Reconcile billed care with EVV. At most {evv_units} units are supported by the current records; changing units and amount requires review.")
        daily_before = state.daily_paid[State.daily_key(claim)]
        if payer == "PY02" and daily_before + units > 32:
            fail("D06", f"Paid daily total {daily_before} plus {units} billed units exceeds 32",
                 "Review all services paid for this member and date; confirm any exception with the payer.")

        # Alphabetical order is solely for reproducible serialization, not priority.
        failures.sort(key=lambda f: f["code"])
        # Known denials remain predictions even when there are additional warnings.
        status = "denied" if failures else ("manual_review" if any(w.startswith("Correction reference") for w in warnings) else "paid")
        return dict(status=status, denial_codes=sorted({f["code"] for f in failures}),
                    remark=" | ".join(f["reason"] for f in failures) if failures else ("No documented rule violation detected" if status == "paid" else "Correction requires review"),
                    failures=failures, warnings=warnings,
                    proposed_changes={field: dict(value=value, reason=reason) for field, value, reason in patches},
                    evidence=dict(verified_minutes=minutes, evv_supported_units=evv_units,
                                  verified_visit_ids=evv["verified_ids"], unverified_visit_ids=evv["other_ids"],
                                  authorization_remaining=remaining, daily_paid_before=daily_before,
                                  filing_days=filing_days, filing_limit=filing_limit,
                                  prior_submission_ids=list(duplicate_ids)))


def outcome_matches(actual_status, actual_code, predicted_status, predicted_codes):
    """A denial matches by membership; a paid claim must pass without failures."""
    if actual_status == "denied":
        return predicted_status == "denied" and bool(actual_code) and actual_code in predicted_codes
    return actual_status == "paid" and predicted_status == "paid" and not predicted_codes


def score(rows):
    total = len(rows)
    labelled = [r for r in rows if r["actual_status"] in {"paid", "denied"}]
    n = len(labelled)
    correct_status = sum(r["actual_status"] == r["predicted_status"] for r in labelled)
    matched = sum(outcome_matches(r["actual_status"], r["actual_denial_code"], r["predicted_status"], json.loads(r["predicted_denial_codes"])) for r in labelled)
    denied = [r for r in labelled if r["actual_status"] == "denied"]
    paid = [r for r in labelled if r["actual_status"] == "paid"]
    return dict(total=total, labelled=n, status_correct=correct_status,
                status_accuracy=correct_status / n if n else None,
                always_paid_baseline=len(paid) / n if n else None,
                status_and_code_set_correct=matched, status_and_code_set_accuracy=matched / n if n else None,
                actual_denied=len(denied), actual_paid=len(paid),
                denial_code_correct=sum(outcome_matches(r["actual_status"], r["actual_denial_code"], r["predicted_status"], json.loads(r["predicted_denial_codes"])) for r in denied),
                denial_code_accuracy=sum(outcome_matches(r["actual_status"], r["actual_denial_code"], r["predicted_status"], json.loads(r["predicted_denial_codes"])) for r in denied) / len(denied) if denied else None,
                denial_recall=sum(r["predicted_status"] == "denied" for r in denied) / len(denied) if denied else None,
                denied_reason_detected=sum(r["actual_denial_code"] in json.loads(r["predicted_denial_codes"]) for r in denied),
                additional_unconfirmed_codes=sum(len(set(json.loads(r["predicted_denial_codes"])) - {r["actual_denial_code"]}) for r in denied),
                missed_denials=sum(r["predicted_status"] == "paid" for r in denied),
                false_alarms=sum(r["predicted_status"] == "denied" for r in paid),
                manual_review=sum(r["predicted_status"] == "manual_review" for r in rows),
                error_categories=dict(Counter(r["error_category"] for r in labelled if r["error_category"])),
                confusion=dict(Counter(f"{r['actual_denial_code'] or r['actual_status']} -> {r['predicted_denial_codes']} ({r['predicted_status']})" for r in labelled)))


def error_category(actual, result, diagnostic):
    if outcome_matches(actual["status"], actual["denial_code"], result["status"], result["denial_codes"]):
        return ""
    if result["status"] == "manual_review":
        return "unresolved_input"
    if actual["denial_code"] in {"D10", "D99"}:
        return "contract_or_unspecified_denial"
    if result["status"] != diagnostic["status"] or result["denial_codes"] != diagnostic["denial_codes"]:
        return "state_sensitive_mismatch"
    return "rule_or_data_disagreement"


def run(data_dir, artifacts, output, metrics_path, splits_dir=None):
    # The supplied pack and generated chronological splits live separately.
    splits_dir = splits_dir or data_dir
    artifacts.mkdir(parents=True, exist_ok=True)
    clean_paths, cleaning = {}, {}
    for name in ("sorted_prev_data.csv", "sorted_test_data.csv", "claims_to_check.csv", "authorizations.csv", "evv_visits.csv"):
        target = artifacts / name
        source_dir = splits_dir if name.startswith("sorted_") else data_dir
        cleaning[name] = clean_file(source_dir / name, target, target.with_suffix(".cleaning.json"))
        clean_paths[name] = target
    # Supporting context errors cannot be assigned to a single claim reliably.
    reports = {name: audit(artifacts, clean_paths[name]) for name in ("sorted_prev_data.csv", "sorted_test_data.csv", "claims_to_check.csv")}
    (artifacts / "validation.json").write_text(json.dumps(reports, indent=2) + "\n")
    for report in reports.values():
        context_errors = [i for i in report["issues"] if i["severity"] == "error" and i["file"] in {"authorizations.csv", "evv_visits.csv"}]
        if context_errors:
            raise ValueError("Invalid supporting context; see validation.json")
    if reports["sorted_prev_data.csv"]["severity_counts"].get("error"):
        raise ValueError("Resolve historical errors before constructing balances; see validation.json")
    if reports["claims_to_check.csv"]["severity_counts"].get("error"):
        raise ValueError("Resolve pending-claim errors before estimating exposure; see validation.json")
    prior = load_csv(clean_paths["sorted_prev_data.csv"])
    test = load_csv(clean_paths["sorted_test_data.csv"])
    prior.sort(key=lambda r: r["submitted_date"])
    # Unknown submission dates cannot be placed in a replay without guessing.
    date_errors = [i for i in reports["sorted_test_data.csv"]["issues"] if i["severity"] == "error" and i["field"] == "submitted_date"]
    if date_errors:
        raise ValueError("Resolve test submission dates before chronological replay")
    test.sort(key=lambda r: r["submitted_date"])
    if prior and test and prior[-1]["submitted_date"] >= test[0]["submitted_date"]:
        raise ValueError("Prior history must precede test history strictly")
    checker = Checker(load_csv(clean_paths["authorizations.csv"]), load_csv(clean_paths["evv_visits.csv"]))
    pending_review = PendingAuthorizationReview(
        load_csv(clean_paths["claims_to_check.csv"]),
        load_csv(clean_paths["authorizations.csv"]),
        {r["claim_id"] for r in prior + test},
    )
    predicted_state, observed_state = State(), State()
    for row in prior:
        predicted_state.record(claim_input(row), row["status"])
        observed_state.record(claim_input(row), row["status"])

    # Resolve line-based quality errors before sorting, without using labels.
    unsorted_test = load_csv(clean_paths["sorted_test_data.csv"])
    errors_by_id = defaultdict(list)
    for item in reports["sorted_test_data.csv"]["issues"]:
        if item["severity"] == "error" and item["file"] == "sorted_test_data.csv" and item["line"] >= 2:
            errors_by_id[unsorted_test[item["line"] - 2]["claim_id"]].append(item["message"])
    results = []
    for row in test:
        claim = claim_input(row)
        result = checker.check(claim, predicted_state, errors_by_id[claim["claim_id"]])
        diagnostic = checker.check(claim, observed_state, errors_by_id[claim["claim_id"]])
        # Decision layer only: don't assume unresolved claims were paid or change
        # the baseline replay's ledger. Assessment does not receive test outcomes.
        review = pending_review.assess(claim, result)
        # Both forecasts are made before the current label is read for scoring.
        actual = dict(status=row.get("status", ""), denial_code=row.get("denial_code", ""), remark=row.get("remark", ""))
        proposed = dict(claim)
        proposed.update({field: change["value"] for field, change in result["proposed_changes"].items()})
        record = dict(claim, predicted_status=result["status"], predicted_denial_codes=json.dumps(result["denial_codes"]),
                      predicted_remark=result["remark"],
                      failed_checks=json.dumps(result["failures"]), warnings=json.dumps(result["warnings"]),
                      suggested_actions=" | ".join(f["suggestion"] for f in result["failures"]),
                      proposed_changes=json.dumps(result["proposed_changes"]), proposed_claim=json.dumps(proposed),
                      fixed_auth_id=proposed["auth_id"], fixed_modifier=proposed["modifier"],
                      proposal_requires_review=bool(result["proposed_changes"]), evidence=json.dumps(result["evidence"]),
                      observed_prior_status=diagnostic["status"], observed_prior_denial_codes=json.dumps(diagnostic["denial_codes"]),
                      actual_status=actual["status"], actual_denial_code=actual["denial_code"], actual_remark=actual["remark"],
                      actual_paid_amount=row.get("paid_amount", ""),
                      status_correct=actual["status"] == result["status"],
                      code_correct=outcome_matches(actual["status"], actual["denial_code"], result["status"], result["denial_codes"]),
                      error_category=error_category(actual, result, diagnostic))
        record.update({key: json.dumps(value) if isinstance(value, list) else value for key, value in review.items()})
        results.append(record)
        # Uncheckable claims are registered as submitted but never assumed paid.
        if claim.get("units", "").isdigit():
            predicted_state.record(claim, result["status"])
            observed_state.record(claim, actual["status"])

    # Restore a row per original test-export row, including repeated export rows.
    # The latter share a prediction and never consume state twice.
    by_id = {r["claim_id"]: r for r in results}
    if len(by_id) != len(results):
        raise ValueError("Conflicting test IDs cannot be expanded safely; see validation.json")
    expanded, seen = [], set()
    for line, raw in enumerate(load_csv(splits_dir / "sorted_test_data.csv"), 2):
        clean = normalized_input(raw)
        row = dict(by_id[clean["claim_id"]], source_line=line, export_duplicate=clean["claim_id"] in seen)
        expanded.append(row)
        seen.add(clean["claim_id"])
    if expanded:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(expanded[0]))
            writer.writeheader()
            writer.writerows(expanded)
    diagnostic_rows = [dict(r, predicted_status=r["observed_prior_status"], predicted_denial_codes=r["observed_prior_denial_codes"]) for r in results]
    diagnostic_score = score(diagnostic_rows)
    # Error categories are a main-replay analysis; don't present them as if
    # independently diagnosed for the observed-prior experiment.
    diagnostic_score.pop("error_categories")
    reviewed = [r for r in results if r["decision"] == "check_previous_claims"]
    resolved = [r for r in results if r["decision"] in {"paid", "denied"}]
    review_metrics = dict(
        reviewed=len(reviewed), review_rate=len(reviewed) / len(results) if results else None,
        reviewed_actual_denied=sum(r["actual_status"] == "denied" for r in reviewed),
        reviewed_actual_paid=sum(r["actual_status"] == "paid" for r in reviewed),
        reviewed_actual_codes=dict(Counter(r["actual_denial_code"] or "paid" for r in reviewed)),
        balance_uncertainty_flags=sum(r["authorization_balance_uncertain"] for r in results),
        remaining_payable_missed_denials=sum(r["decision"] == "paid" and r["actual_status"] == "denied" for r in results),
        false_definite_denials=sum(r["decision"] == "denied" and r["actual_status"] == "paid" for r in results),
        automatic_decisions=len(resolved),
        automatic_decision_coverage=len(resolved) / len(results) if results else None,
        correct_automatic_code_matches=sum(r["code_correct"] for r in resolved),
        automatic_code_match_rate=sum(r["code_correct"] for r in resolved) / len(resolved) if resolved else None,
        correct_automatic_code_matches_all_claims=sum(r["code_correct"] for r in resolved) / len(results) if results else None,
        note="Review is an abstention, not a correctly predicted denial. Main metrics remain baseline forecasts; decision is the operational gate.",
    )
    metrics = dict(evaluation="Denied: recorded code is in predicted set. Paid: predicted paid with an empty set. Manual review never counts as a match.",
                   main=score(results), observed_prior_diagnostic=diagnostic_score,
                   pending_claims_review=review_metrics,
                   by_payer={payer: score([r for r in results if r["payer"] == payer]) for payer in ("PY01", "PY02", "PY03")},
                   by_actual_code={code or "paid": score([r for r in results if r["actual_denial_code"] == code]) for code in sorted({r["actual_denial_code"] for r in results})},
                   output_rows=len(expanded), unique_test_rows=len(results),
                   proposed_corrections=sum(bool(json.loads(r["proposed_changes"])) for r in results),
                   multiple_failures=sum(len(json.loads(r["predicted_denial_codes"])) > 1 for r in results),
                   cleaning={name: {k: v for k, v in report.items() if k not in {"changes", "removed_duplicates", "unresolved"}} for name, report in cleaning.items()})
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n")
    print(json.dumps(metrics["main"], indent=2))
    print(f"Results: {output}; metrics: {metrics_path}")
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="Original supplied data pack")
    parser.add_argument("--splits-dir", type=Path, default=EXPERIMENT_DIR, help="Directory containing sorted_prev_data.csv and sorted_test_data.csv")
    parser.add_argument("--artifacts", type=Path, default=ARTIFACTS_DIR)
    parser.add_argument("--output", type=Path, default=EXPERIMENT_DIR / "test_result.csv")
    parser.add_argument("--metrics", type=Path, default=EXPERIMENT_DIR / "claim_metrics.json")
    args = parser.parse_args()
    run(args.data_dir, args.artifacts, args.output, args.metrics, args.splits_dir)


if __name__ == "__main__":
    main()
