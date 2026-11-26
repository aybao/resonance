# Historical replay exceptions

`replay_exceptions.json` records 121 specific discrepancies inspected during
implementation of the June monthly upload and three-stage demo (June 30 start; July 31, August 31,
January 31 period ends). These are limitations of this diagnostic, not corrected
claims or evidence of accurate predictions. The demo always reports all of them
as mismatches and includes them in the overall accuracy denominator.

- Seven June rows are unexplained D99 denials: C011567, C001044, C009944,
  C001155, C011706, C009183 and C015574. Their source remarks only refer to
  contacting the payer or unavailable correspondence. Their exact predicted
  and recorded outcomes are pinned for the June 30 batch. They still count as
  missed denials; documenting them does not improve accuracy.
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

The September monthly batch has also been checked independently using both
source-transcribed bulletin rules. Its 29 mismatches exactly match the previously
recorded claim outcomes and prediction code sets from the January-ending replay.
These entries list September 30 in `additional_replay_period_ends`. September
therefore has 29 documented differences, zero unexpected differences, 805/834
matches (96.52%), 21 missed denials and one false alarm. The 29 differences comprise
17 recorded D99 denials, 11 D10 contract denials, and C006764's D02 false alarm.
For C006764, predicting C003575 paid consumes nine units that the recorded denial
would not consume. The two bulletins do not resolve that missing-contract effect.

Each entry pins the claim ID, recorded status/code, predicted status/full code
set, and replay period (including any independently verified additional period ends). A changed prediction is not automatically excused by the
same claim ID. Other cohorts or starting dates can therefore reveal unexpected
mismatches and fail. The replay never writes or expands this file. Changes need
an explicit review of the source record, ledger and expected outcome.

The existing experiment used a different prediction horizon and provisional-history setup. These counts
must not be substituted for the results reported in [the experiment report](../plans/experiment_results.md).
This inspected dataset is suitable for regression checks and demonstration;
future generalization claims require a new holdout and missing payer evidence.
