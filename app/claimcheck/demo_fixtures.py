"""Repeatable bulletin responses with independently specified source-based examples.

Used by the historical test/demo CLI instead of a paid, nondeterministic AI call.
Never used as a fallback for real requests.
"""
from pathlib import Path

BULLETINS = Path(__file__).resolve().parents[2] / 'Resonance - graduate preparation data' / 'payers'
NORTHWIND_CODE = '''def apply_rule(claim, context, failures):
    if claim['payer'] != 'PY01' or claim['date_of_service'] < '2026-08-01':
        return failures
    result = [f for f in failures if f['code'] != 'D03']
    if context['filing_days'] > 45:
        result.append({'code': 'D03', 'meaning': 'Timely filing limit exceeded',
            'reason': 'Submitted ' + str(context['filing_days']) + ' days after service; limit is 45',
            'suggestion': 'Review payer exceptions; do not change actual dates.'})
    return result
'''
HARBOR_CODE = '''def apply_rule(claim, context, failures):
    if claim['payer'] != 'PY02' or claim['date_of_service'] < '2026-09-01' or claim['service_code'] != 'T1019':
        return failures
    result = [f for f in failures if f['code'] != 'D04']
    expected = 'U3' if weekday(claim['date_of_service']) >= 5 else 'U2'
    if claim['modifier'] != expected:
        result.append({'code': 'D04', 'meaning': 'Missing or invalid modifier',
            'reason': 'Expected modifier ' + expected + ' for T1019 on this day',
            'suggestion': 'Confirm modifier ' + expected + '; follow corrected-claim procedures if submitted.'})
    return result
'''


def bulletin_text(payer):
    name = 'PY01_northwind_bulletin_2026-07.md' if payer == 'PY01' else 'PY02_harbor_bulletin_2026-08.md'
    return (BULLETINS / name).read_text()


def bulletin_proposal(payer):
    def example(title, day, sub, mod, codes):
        return dict(title=title, claim=dict(payer=payer, service_code='T1019', date_of_service=day, submitted_date=sub,
                    units=4, modifier=mod, frequency_code='1'), context=dict(evv_minutes=[60]), expected_codes=codes)
    if payer == 'PY01':
        return dict(code=NORTHWIND_CODE,
            summary='45-day Northwind filing limit', source_quote=bulletin_text(payer),
            assistant_message='Northwind allows 45 days for service from 1 August 2026.',
            examples=[example('40 days after August service', '2026-08-01', '2026-09-10', '', []),
                      example('Exactly 45 days', '2026-08-01', '2026-09-15', '', []),
                      example('46 days', '2026-08-01', '2026-09-16', '', ['D03']),
                      example('July service still has 30 days', '2026-07-31', '2026-09-09', '', ['D03'])])
    return dict(code=HARBOR_CODE,
        summary='Weekend T1019 modifier U3', source_quote=bulletin_text(payer),
        assistant_message='Harbor weekend T1019 uses U3 from 1 September; weekdays retain U2.',
        examples=[example('September weekend with U2', '2026-09-05', '2026-09-05', 'U2', ['D04']),
                  example('September weekend with U3', '2026-09-05', '2026-09-05', 'U3', []),
                  example('September weekday with U2', '2026-09-07', '2026-09-07', 'U2', []),
                  example('August weekend still requires U2', '2026-08-29', '2026-08-29', 'U3', ['D04'])])


def fixture_ai(payer):
    from .ai import AIClient
    return AIClient(transport=lambda payload: {'content': [{'type': 'tool_use', 'input': bulletin_proposal(payer)}]})
