#!/usr/bin/env python3
"""Check a claims CSV with approved parameter/code rules; export all codes and reasons."""
import argparse
from pathlib import Path
from claimcheck.pipeline import Dataset, load_sample, parse_claims_csv, run_batch, export_csv
from claimcheck.rules import RuleStore, full_store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('claims', type=Path)
    parser.add_argument('--rules', type=Path, help='Approved rules snapshot (defaults to all supplied rules)')
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).resolve().parent / 'sample_data')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    ds = Dataset()
    load_sample(ds, args.data_dir)
    header, records = parse_claims_csv(args.claims.read_text(encoding='utf-8-sig'))
    rules = RuleStore(args.rules) if args.rules else full_store()
    args.output.write_text(export_csv(run_batch(records, ds, rules), header))


if __name__ == '__main__':
    main()
