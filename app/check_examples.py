#!/usr/bin/env python3
"""On-demand execution/validation of a JSON draft and its example rows. No AI call."""
import argparse
import json
from pathlib import Path
from claimcheck.drafts import evaluate, new_draft, refresh_boundary_examples
from claimcheck.pipeline import Dataset, load_sample
from claimcheck.rules import RuleStore, full_store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('draft', type=Path, help='JSON draft with payer_id, effective_from, code and examples')
    parser.add_argument('--rules', type=Path, help='Accepted rules snapshot; defaults to all supplied rules')
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).resolve().parent / 'sample_data')
    args = parser.parse_args()
    store = RuleStore(args.rules) if args.rules else full_store()
    ds = Dataset()
    load_sample(ds, args.data_dir)
    raw = json.loads(args.draft.read_text())
    d = new_draft()
    d.update(raw)
    # Assign IDs and validate even when reading raw examples rather than an API draft.
    from claimcheck.examples import validate_scenario
    problems = []
    for i, ex in enumerate(d['examples']):
        ex.setdefault('id', f'file-{i}')
        ex.setdefault('payer_id', d['payer_id'])
        problems.extend(validate_scenario(ex))
    if problems:
        print(json.dumps({'problems': problems}, indent=2))
        return 1
    refresh_boundary_examples(d, store)
    result = evaluate(d, store, ds)
    print(json.dumps(result, indent=2))
    return 1 if result['problems'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
