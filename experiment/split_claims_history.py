"""Sort and split the raw claims export without cleaning its contents.

Submission dates are parsed as YYYY-MM-DD or US MM/DD/YYYY. Rows on the
cutoff date belong to the test set. Same-day rows retain source order;
this does not imply a known within-day adjudication order. Duplicate rows
and all original field values (including outcomes) are preserved.
"""

import argparse
import csv
from datetime import date
from pathlib import Path

from check_formatting import canonical
from paths import DATA_DIR, EXPERIMENT_DIR


def split_history(source, output_dir, cutoff):
    with source.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        if not fields or "submitted_date" not in fields:
            raise ValueError("Input must contain a submitted_date column")
        dated_rows = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed CSV row at line {reader.line_num}")
            try:
                submitted = date.fromisoformat(canonical("submitted_date", row["submitted_date"]))
            except ValueError as exc:
                raise ValueError(f"Invalid submitted_date at line {reader.line_num}: {row['submitted_date']!r}") from exc
            dated_rows.append((submitted, row))

    # Python's stable sort preserves input order for equal submission dates.
    dated_rows.sort(key=lambda item: item[0])
    outputs = {
        "claims_history_sorted.csv": [row for _, row in dated_rows],
        "sorted_prev_data.csv": [row for submitted, row in dated_rows if submitted < cutoff],
        "sorted_test_data.csv": [row for submitted, row in dated_rows if submitted >= cutoff],
    }
    if any((output_dir / name).resolve() == source.resolve() for name in outputs):
        raise ValueError("Output must not overwrite the source export")
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in outputs.items():
        with (output_dir / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    return {name: len(rows) for name, rows in outputs.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DATA_DIR / "claims_history.csv")
    parser.add_argument("--output-dir", type=Path, default=EXPERIMENT_DIR, help="Defaults to the experiment directory")
    parser.add_argument("--cutoff", type=date.fromisoformat, default=date(2026, 10, 1), help="ISO submission date; default: 2026-10-01")
    args = parser.parse_args()
    counts = split_history(args.input, args.output_dir, args.cutoff)
    print(f"Previous data: submitted before {args.cutoff}; test data: on or after {args.cutoff}")
    for name, count in counts.items():
        print(f"{name}: {count} rows")


if __name__ == "__main__":
    main()
