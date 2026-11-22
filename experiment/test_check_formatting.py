import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from check_formatting import CLAIM_FIELDS, SCHEMAS, audit, canonical
from clean_data import clean_file


class FormattingTests(unittest.TestCase):
    def test_explicit_normalizations(self):
        for field, raw, expected in [
            ("payer", "NORTHWIND HP", "PY01"),
            ("service_code", "T1019 ", "T1019"),
            ("modifier", " u3 ", "U3"),
            ("date_of_service", "09/01/2026", "2026-09-01"),
        ]:
            with self.subTest(field=field):
                self.assertEqual(canonical(field, raw), expected)

    def test_rejects_invalid_and_unknown_values(self):
        for field, raw in [
            ("payer", "Harbour"), ("date_of_service", "2026-02-30"),
            ("date_of_service", "31/08/2026"), ("units", "1.5"),
            ("units", "0"), ("paid_amount", "NaN"),
            ("billed_amount", "1.234"), ("frequency_code", "9"),
            ("clock_in", "2026-09-01 25:00"),
        ]:
            with self.subTest(field=field, value=raw):
                with self.assertRaises(ValueError):
                    canonical(field, raw)

    def test_business_omissions_duplicates_and_corrections(self):
        claim = dict.fromkeys(CLAIM_FIELDS, "")
        claim.update(claim_id="C000001", agency_id="AG001", payer="PY01",
                     patient_id="PT0001", service_code="T1019",
                     date_of_service="2026-09-01", submitted_date="2026-09-02",
                     units="4", frequency_code="1", billed_amount="20")
        conflict = dict(claim, units="8")
        correction = dict(claim, claim_id="C000002", frequency_code="7")
        malformed = dict(claim, claim_id="C000003", units="four")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name, fields in SCHEMAS.items():
                with (root / name).open("w", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader()
                    if name == "claims_to_check.csv":
                        writer.writerows([claim, claim, conflict, correction, malformed])
            report = audit(root)
        counts = report["issue_counts"]
        self.assertNotIn("duplicate_row", counts)
        self.assertNotIn("normalization", counts)
        self.assertEqual(counts["conflicting_id"], 1)
        self.assertEqual(counts["missing_correction_reference"], 1)
        self.assertEqual(counts["invalid_format"], 1)
        self.assertEqual(counts["missing_business_input"], 5)
        self.assertNotIn("missing_required", counts)

    def test_cleaning_preserves_conflicts_and_bad_values_and_is_idempotent(self):
        claim = dict.fromkeys(CLAIM_FIELDS, "")
        claim.update(claim_id="C000001", agency_id="AG001", payer="NORTHWIND HP",
                     patient_id="PT0001", service_code="T1019 ", modifier=" u2 ",
                     date_of_service="09/01/2026", submitted_date="09/02/2026",
                     units="4", frequency_code="1", billed_amount="20")
        equivalent = dict(claim, payer="PY01", service_code="T1019")
        conflict = dict(claim, units="four")
        distinct_claim = dict(claim, claim_id="C000002")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "raw.csv", root / "clean.csv"
            with source.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=CLAIM_FIELDS)
                writer.writeheader()
                writer.writerows([claim, equivalent, conflict, distinct_claim])
            original = source.read_bytes()
            report = clean_file(source, output, root / "report.json")
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(report["duplicates_removed"], 1)
            self.assertEqual(report["output_rows"], 3)
            self.assertEqual(report["unresolved_count"], 1)
            with output.open() as handle:
                cleaned = list(csv.DictReader(handle))
            self.assertEqual(cleaned[0]["date_of_service"], "2026-09-01")
            self.assertEqual(cleaned[0]["modifier"], "U2")
            self.assertEqual(cleaned[1]["units"], "four")
            self.assertEqual(cleaned[0]["auth_id"], "")
            again = root / "again.csv"
            second = clean_file(output, again, root / "again.json")
            self.assertEqual(output.read_bytes(), again.read_bytes())
            self.assertEqual(second["changed_cells_by_field"], {})
            self.assertEqual(second["duplicates_removed"], 0)


if __name__ == "__main__":
    unittest.main()
