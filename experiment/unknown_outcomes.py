"""Compare ways to handle unknown outcomes without fitting rules to held-out labels.

Uses the existing cleaned experiment artifacts. Run run_experiment.py first to
rebuild those artifacts from the original pack. Leaves the original baseline results unchanged.
"""
import argparse
from collections import Counter
import csv
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import random
from statistics import mean

from claim_checker import Checker, State, claim_input, load_csv, outcome_matches
from paths import ARTIFACTS_DIR, EXPERIMENT_DIR

POLICIES = ('ignore', 'assume_paid', 'predict')
SEEDS = (11, 23, 37, 51, 71)
FRACTIONS = (0.25, 0.5, 1.0)


def simulate(prior, targets, checker, authorizations, observed, policy, pending=(), pending_first=True):
    """Only visible earlier outcomes enter observed; target/hidden labels are absent.

    prior, targets and pending contain claim fields only. Historical submissions
    remain submissions even when their outcome is ignored. External pending rows
    are included as submissions only by the assume-paid and predict policies.
    """
    if policy not in POLICIES:
        raise ValueError('Unknown policy')
    prior, targets, pending = ([claim_input(r) for r in rows] for rows in (prior, targets, pending))
    known_ids = {r['claim_id'] for r in prior + targets}
    seen, external = set(known_ids), []
    for row in pending:
        if row['claim_id'] not in seen:
            external.append(row)
            seen.add(row['claim_id'])
    events = [(r['submitted_date'], 0, i, 'history', r) for i, r in enumerate(prior)]
    events += [(r['submitted_date'], 2, i, 'target', r) for i, r in enumerate(targets)]
    events += [(r['submitted_date'], 1 if pending_first else 3, i, 'pending', r) for i, r in enumerate(external)]
    state, predictions, unknown_predictions = State(), {}, {}
    for _, _, _, kind, row in sorted(events, key=lambda e: e[:3]):
        cid = row['claim_id']
        if kind == 'history' and cid in observed:
            state.record(row, observed[cid])
            continue
        if kind != 'target':
            if policy == 'predict':
                result = checker.check(row, state)
                unknown_predictions[cid] = dict(status=result['status'], codes=result['denial_codes'])
                state.record(row, result['status'])
            elif policy == 'assume_paid':
                state.record(row, 'paid')
            elif kind == 'history':
                state.record(row, 'unknown')
            continue
        result = checker.check(row, state)
        predictions[cid] = dict(status=result['status'], codes=result['denial_codes'],
                                authorization_remaining=result['evidence'].get('authorization_remaining'))
        state.record(row, result['status'])
    return predictions, unknown_predictions


def evaluate(predictions, labels, reference=None):
    total = len(predictions)
    def matched(cid, result):
        label = labels[cid]
        return outcome_matches(label['status'], label['denial_code'], result['status'], result['codes'])
    matches = sum(matched(cid, p) for cid, p in predictions.items())
    result = dict(total=total, status_correct=sum(p['status'] == labels[cid]['status'] for cid, p in predictions.items()),
                  code_matches=matches, code_accuracy=matches / total if total else None,
                  unable_to_check=sum(p['status'] == 'unable_to_check' for p in predictions.values()),
                  payable_denials=sum(p['status'] == 'paid' and labels[cid]['status'] == 'denied' for cid, p in predictions.items()),
                  false_denials=sum(p['status'] == 'denied' and labels[cid]['status'] == 'paid' for cid, p in predictions.items()),
                  d02_detected=sum(p['status'] == 'denied' and 'D02' in p['codes'] and labels[cid]['denial_code'] == 'D02' for cid, p in predictions.items()))
    if reference is not None:
        changed = [cid for cid, p in predictions.items() if (p['status'], p['codes']) != (reference[cid]['status'], reference[cid]['codes'])]
        result.update(changed=len(changed), gained_matches=sum(matched(cid, predictions[cid]) and not matched(cid, reference[cid]) for cid in changed),
                      lost_matches=sum(matched(cid, reference[cid]) and not matched(cid, predictions[cid]) for cid in changed))
    return result


def inputs(artifacts):
    rows = {name: load_csv(artifacts / name) for name in ('sorted_prev_data.csv', 'sorted_test_data.csv',
             'claims_to_check.csv', 'authorizations.csv', 'evv_visits.csv')}
    prior, targets = rows['sorted_prev_data.csv'], rows['sorted_test_data.csv']
    if not prior or not targets:
        raise ValueError('This experiment requires nonempty earlier-history and target splits')
    if len({r['claim_id'] for r in prior + targets}) != len(prior) + len(targets):
        raise ValueError('Resolve repeated/conflicting history IDs before replay')
    if max(r['submitted_date'] for r in prior) >= min(r['submitted_date'] for r in targets):
        raise ValueError('Earlier history must strictly precede the evaluation split')
    labels = {r['claim_id']: {'status': r['status'], 'denial_code': r['denial_code']} for r in prior + targets}
    return ([claim_input(r) for r in prior], [claim_input(r) for r in targets],
            [claim_input(r) for r in rows['claims_to_check.csv']], rows['authorizations.csv'], rows['evv_visits.csv'], labels)


def run(artifacts=ARTIFACTS_DIR, output_dir=EXPERIMENT_DIR):
    prior, targets, pending, auths, visits, labels = inputs(artifacts)
    checker = Checker(auths, visits)
    visible = {r['claim_id']: labels[r['claim_id']]['status'] for r in prior}
    reference, _ = simulate(prior, targets, checker, auths, visible, 'ignore')
    main, main_predictions = {}, {}
    for policy in POLICIES:
        predictions, unknown = simulate(prior, targets, checker, auths, visible, policy, pending)
        main[policy] = evaluate(predictions, labels, reference)
        if policy == 'predict':
            main[policy]['unlabelled_forecasts'] = dict(Counter(p['status'] for p in unknown.values()))
        main_predictions[policy] = predictions
    late, _ = simulate(prior, targets, checker, auths, visible, 'predict', pending, pending_first=False)
    main['predict_pending_after_same_day_targets'] = evaluate(late, labels, reference)

    sensitivity = []
    ids = sorted(visible)
    for fraction in FRACTIONS:
        for seed in SEEDS[:1] if fraction == 1 else SEEDS:
            mask = set(random.Random(seed).sample(ids, round(len(ids) * fraction)))
            observed = {cid: outcome for cid, outcome in visible.items() if cid not in mask}
            for policy in POLICIES:
                predictions, unknown = simulate(prior, targets, checker, auths, observed, policy)
                record = dict(fraction=fraction, seed=seed, hidden=len(mask), policy=policy,
                              mask_sha256=hashlib.sha256('\n'.join(sorted(mask)).encode()).hexdigest(),
                              **evaluate(predictions, labels, reference))
                if policy == 'predict':
                    record['hidden_status_correct'] = sum(p['status'] == labels[cid]['status'] for cid, p in unknown.items())
                    record['hidden_code_matches'] = sum(outcome_matches(labels[cid]['status'], labels[cid]['denial_code'], p['status'], p['codes']) for cid, p in unknown.items())
                sensitivity.append(record)
    # A temporal backlog is more plausible than random missingness alone. Its
    # window uses dates only; no outcome or prediction chooses the hidden cases.
    backlog_start = (date.fromisoformat(max(r['submitted_date'] for r in prior)) - timedelta(days=29)).isoformat()
    backlog_ids = {r['claim_id'] for r in prior if r['submitted_date'] >= backlog_start}
    backlog = {}
    for policy in POLICIES:
        observed = {cid: outcome for cid, outcome in visible.items() if cid not in backlog_ids}
        predictions, unknown = simulate(prior, targets, checker, auths, observed, policy)
        backlog[policy] = evaluate(predictions, labels, reference)
        if policy == 'predict':
            backlog[policy]['hidden_status_correct'] = sum(p['status'] == labels[cid]['status'] for cid, p in unknown.items())
            backlog[policy]['hidden_code_matches'] = sum(outcome_matches(labels[cid]['status'], labels[cid]['denial_code'], p['status'], p['codes']) for cid, p in unknown.items())
    aggregates = []
    for fraction in FRACTIONS:
        for policy in POLICIES:
            group = [r for r in sensitivity if r['fraction'] == fraction and r['policy'] == policy]
            fields = ('code_matches', 'code_accuracy', 'false_denials', 'payable_denials', 'changed', 'gained_matches', 'lost_matches')
            aggregates.append(dict(fraction=fraction, policy=policy, runs=len(group),
                **{field: dict(mean=mean(r[field] for r in group), minimum=min(r[field] for r in group), maximum=max(r[field] for r in group)) for field in fields}))

    protocol = dict(prior=len(prior), targets=len(targets), unlabelled_pending=len(pending),
                    cutoff=min(r['submitted_date'] for r in targets), fractions=FRACTIONS, seeds=SEEDS,
                    backlog_start=backlog_start, backlog_hidden=len(backlog_ids),
                    source_files_sha256={name: hashlib.sha256((artifacts / name).read_bytes()).hexdigest()
                        for name in ('sorted_prev_data.csv', 'sorted_test_data.csv', 'claims_to_check.csv', 'authorizations.csv', 'evv_visits.csv')},
                    notes=['No rules or thresholds were fitted; existing Checker and payer documents are unchanged.',
                           'All evaluation and hidden labels are separated from simulate; only visible prior statuses enter the ledger.',
                           'Whole previous history is masked at 100%; it is run once because every seed gives the same mask.',
                           'External pending rows have no outcomes; their own forecast accuracy cannot be measured.',
                           'External rows are included before same-day targets; a reverse-order prediction sensitivity is also reported.',
                           'History is treated as submitted; external pending submission itself is an assumption in predict/assume-paid.',
                           'Earlier target outcomes are always predicted.',
                           'All source data and original experiment metrics/results remain unchanged.'])
    metrics = dict(protocol=protocol, observed_history_control=evaluate(reference, labels),
                   external_pending_comparison=main, masked_history_runs=sensitivity, masked_history_summary=aggregates,
                   recent_backlog_comparison=backlog)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / 'unknown_outcomes_metrics.json').write_text(json.dumps(metrics, indent=2) + '\n')
    with (output_dir / 'unknown_outcomes_predictions.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, lineterminator='\n', fieldnames=['claim_id','actual_status','actual_denial_code'] +
            [f'{policy}_{field}' for policy in POLICIES for field in ('status','codes','authorization_remaining')])
        writer.writeheader()
        for claim in targets:
            cid = claim['claim_id']
            row = dict(claim_id=cid, actual_status=labels[cid]['status'], actual_denial_code=labels[cid]['denial_code'])
            for policy, predictions in main_predictions.items():
                for field, value in predictions[cid].items():
                    row[f'{policy}_{field}'] = json.dumps(value) if isinstance(value, list) else value
            writer.writerow(row)
    print(f"Unknown-outcome comparison: {len(targets)} labelled targets; {len(pending)} unlabelled claims")
    for policy, result in main.items():
        print(f"  {policy}: {result['code_matches']}/{result['total']} forecast matches; "
              f"{result['false_denials']} false denials")
    print(f"Recent backlog: {len(backlog_ids)} hidden outcomes from {backlog_start}")
    for policy, result in backlog.items():
        print(f"  {policy}: {result['code_matches']}/{result['total']} forecast matches; "
              f"{result['false_denials']} false denials")
    print(f"Full metrics and paired predictions written to {output_dir}")
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, default=ARTIFACTS_DIR)
    parser.add_argument('--output-dir', type=Path, default=EXPERIMENT_DIR)
    args = parser.parse_args()
    run(args.artifacts, args.output_dir)


if __name__ == '__main__':
    main()
