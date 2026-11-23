#!/usr/bin/env python3
"""Run the dated bulletin replay, or present it one stage at a time. Isolated state; no AI cost by default."""
import argparse
import json
from pathlib import Path
import tempfile

from claimcheck.ai import AIClient
from claimcheck.demo_fixtures import bulletin_text, fixture_ai
from claimcheck.session import Session
from claimcheck.drafts import decide


def accept_bulletin(session, payer, live=False, interactive=False):
    session.ai = AIClient() if live else fixture_ai(payer)
    view = session.create_draft({'message': bulletin_text(payer)})
    draft = view['draft']
    ev = view['evaluation']
    if ev['problems']:
        raise ValueError('; '.join(ev['problems']))
    for ex in ev['examples']:
        if not ex['changed']:
            continue
        print(f"  {ex['title']}: {ex['before']} -> {ex['actual']['codes']}")
        if not live:
            from claimcheck.examples import run_scenario
            from claimcheck.rules import full_store
            # Independent source-transcribed parameter rules also judge generated boundaries.
            expected = run_scenario(ex, full_store())['codes']
            if expected != ex['actual']['codes']:
                raise ValueError('Bulletin code differs from the source-transcribed reference rules')
        if not live and ex['expected_codes'] is not None and sorted(ex['expected_codes']) != ex['actual']['codes']:
            # Fixture expectations were transcribed from the source; never approve code just because it ran.
            raise ValueError('Bulletin fixture result differs from the independent expected code set')
        if not live:
            decide(draft, ex['id'], 'set', codes=ex['actual']['codes'])
    if live and input('Accept this rule update and its displayed examples? [y/N] ').lower() != 'y':
        raise ValueError('Live proposal not accepted; raise an issue or revise it in the web chat')
    return session.activate(draft['id'])


def run_replay(session, interactive=False, live=False):
    session.start_demo()
    reports = []
    for date, next_bulletin in [('2026-07-31', 'PY01'), ('2026-08-31', 'PY02'), ('2027-01-31', None)]:
        if interactive:
            input(f'Press Enter to advance and check claims through {date}…')
        report = session.advance_demo(date)['report']
        reports.append(report)
        print(f"{date}: {report['claims']} claims, {report['accuracy']:.2%} agreement, "
              f"{report['known_exceptions']} known exceptions, {report['unexpected_mismatches']} unexpected mismatches")
        for row in report['mismatches']:
            print(f"  {row['claim_id']}: {row['status']} {row['codes']} vs recorded "
                  f"{row['actual_status']} {row['actual_code']} — {row['exception_reason'] or row['remark']}")
        if next_bulletin:
            if interactive:
                input(f'Press Enter to propose and review the {next_bulletin} bulletin…')
            accept_bulletin(session, next_bulletin, live=live, interactive=interactive)
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interactive', action='store_true', help='Pause between stages for a guided demo')
    parser.add_argument('--live-ai', action='store_true', help='Call Claude and review and accept each rule update once (requires key)')
    parser.add_argument('--report', type=Path, help='Write all stage reports and every mismatch as JSON')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='claimcheck-replay-') as tmp:
        session = Session(state_dir=tmp)
        try:
            reports = run_replay(session, interactive=args.interactive, live=args.live_ai)
            if args.report:
                args.report.write_text(json.dumps(reports, indent=2) + '\n')
            return 0 if all(r['passed'] for r in reports) else 1
        finally:
            session.end_demo()


if __name__ == '__main__':
    raise SystemExit(main())
