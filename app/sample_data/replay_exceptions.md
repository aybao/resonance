# Historical replay exceptions

`replay_exceptions.json` records 114 specific discrepancies inspected during
implementation of the three-stage demo (June 30 start; July 31, August 31,
January 31 period ends). These are limitations of this diagnostic, not corrected
claims or evidence of accurate predictions. The demo always reports all of them
as mismatches and includes them in the overall accuracy denominator.

- 101 rows have recorded D10/D99 denials referring to unavailable contracts,
  amendments or unspecified correspondence. The supplied manuals do not define
  their complete expected denial conditions.
- 12 rows have recorded D02 denials not reproduced by the available authorization
  balances and earlier recorded payments. They need payer balance/pending-claim
  reconciliation; no authorization limit or claim value was changed to fit them.
  Two also receive a definite D03 under the supplied filing rule.
- C006764 is a known sequential false alarm: C003575's unexplained contract denial
  is predicted paid, consuming nine units. This leaves 13 instead of 22 units for
  C006764's 20-unit claim. It passes with observed prior outcomes, but this demo
  deliberately predicts the complete batch without observing its labels.

Each entry pins the claim ID, recorded status/code, predicted status/full code
set, and replay period. A changed prediction is not automatically excused by the
same claim ID. Other cohorts or starting dates can therefore reveal unexpected
mismatches and fail. The replay never writes or expands this file. Changes need
an explicit review of the source record, ledger and expected outcome.

The existing experiment used a different split and review setup. These counts
must not be substituted for the results reported in `experiment_results.md`.
This inspected dataset is suitable for regression checks and demonstration;
future generalization claims require a new holdout and missing payer evidence.
