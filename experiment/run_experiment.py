"""Rebuild splits, cleaning reports, predictions and metrics from original data."""

import argparse
import json
from datetime import date
from pathlib import Path

from check_formatting import audit
from claim_checker import run
from clean_data import clean_file
from paths import DATA_DIR, EXPERIMENT_DIR
from split_claims_history import split_history


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=EXPERIMENT_DIR)
    parser.add_argument("--cutoff", type=date.fromisoformat, default=date(2026, 10, 1))
    args = parser.parse_args()
    print(split_history(args.data_dir / "claims_history.csv", args.output_dir, args.cutoff))
    cleaned = args.output_dir / "sorted_prev_data_cleaned.csv"
    clean_file(args.output_dir / "sorted_prev_data.csv", cleaned, cleaned.with_suffix(".cleaning.json"))
    for name, claims in (("formatting_report.json", None), ("prev_data_formatting_report.json", cleaned)):
        report = audit(args.data_dir, claims)
        (args.output_dir / name).write_text(json.dumps(report, indent=2) + "\n")
        if report["severity_counts"].get("error"):
            raise SystemExit(f"Resolve errors in {args.output_dir / name}")
    run(args.data_dir, args.output_dir / "claim_checker_artifacts",
        args.output_dir / "test_result.csv", args.output_dir / "claim_metrics.json", args.output_dir)


if __name__ == "__main__":
    main()
