"""Apply deterministic, auditable CSV cleaning; never modify the source file.

Known aliases, whitespace, code case, and date formats use the same explicit
contract as check_formatting.py. Invalid values are preserved for that checker.
Only fully identical rows after canonicalization are deduplicated, never distinct
claim IDs or conflicting versions of an ID. No missing business data is inferred.
"""

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from check_formatting import SCHEMAS, canonical


def clean_file(source, output, report_path):
    if len({p.resolve() for p in (source, output, report_path)}) != 3:
        raise ValueError("Source, cleaned output and report must have distinct paths")
    changes, removed, unresolved = [], [], []
    rows, seen = [], {}
    input_rows = 0
    with source.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        if not fields or not any(len(fields) == len(schema) and set(fields) == set(schema) for schema in SCHEMAS.values()):
            raise ValueError("Headers do not match a supplied export schema; no output written")
        for raw in reader:
            input_rows += 1
            line = reader.line_num
            if None in raw or any(value is None for value in raw.values()):
                raise ValueError(f"Wrong CSV field count at line {line}; no output written")
            row = {}
            for field, value in raw.items():
                # Preserve free-text remarks exactly; they are not join keys.
                if field == "remark":
                    row[field] = value
                    continue
                try:
                    row[field] = canonical(field, value) if value.strip() else ""
                except ValueError as exc:
                    row[field] = value
                    unresolved.append(dict(line=line, field=field, value=value, reason=str(exc)))
                if row[field] != value:
                    changes.append(dict(line=line, field=field, before=value, after=row[field]))
            key = tuple(row[field] for field in fields)
            if key in seen:
                removed.append(dict(line=line, first_line=seen[key], record_id=row[fields[0]]))
            else:
                seen[key] = line
                rows.append(row)
    report = dict(source=str(source), output=str(output), input_rows=input_rows,
                  output_rows=len(rows), duplicates_removed=len(removed),
                  changed_cells_by_field=dict(Counter(item["field"] for item in changes)),
                  changed_source_rows=len({item["line"] for item in changes}),
                  unresolved_count=len(unresolved),
                  assumptions=["Slash dates mean US MM/DD/YYYY", "Keep first fully identical canonical row; preserve ordering", "Do not infer missing values or alter business outcomes"],
                  changes=changes, removed_duplicates=removed, unresolved=unresolved)
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    output = args.output or args.input.with_name(args.input.stem + "_cleaned.csv")
    report_path = args.report or output.with_suffix(".cleaning.json")
    report = clean_file(args.input, output, report_path)
    print(json.dumps({key: value for key, value in report.items() if key not in {"changes", "removed_duplicates", "unresolved"}}, indent=2))
    print(f"Audit trail: {report_path}")


if __name__ == "__main__":
    main()
