# Claims-checking experiment

A Python prototype that cleans synthetic billing exports, checks documented payer
rules, predicts all applicable denial codes and flags uncertain authorization
balances for review. It uses the Python standard library only: no packages, API
keys or AI calls are required. Verified with Python 3.14.4.

The original assignment data stays in `../Resonance - graduate preparation data/`.
Scripts and generated outputs live here in `experiment/`. The discussion of
findings is in [experiment_results.md](../experiment_results.md).

## Run everything

From the repository root:

```bash
python3 experiment/run_experiment.py
python3 -m unittest discover -s experiment -p 'test_*.py' -v
```

Or from inside `experiment/`:

```bash
python3 run_experiment.py
python3 -m unittest discover -p 'test_*.py' -v
```

The runner rebuilds the chronological splits, standalone history-cleaning outputs,
formatting reports, prediction CSV, metrics, cleaned supporting artifacts and the separate
unknown-outcome comparison.
Rerunning replaces generated outputs; it leaves the supplied files unchanged.
Tests are a separate command and currently comprise **31 passing tests**.

Default paths are anchored to the scripts, so both working directories produce
the same files. Explicit path arguments are interpreted relative to your current
directory, as usual.

## Scripts

| Script | What it does |
|---|---|
| `run_experiment.py` | Runs the complete experiment from original inputs; accepts `--data-dir`, `--output-dir` and `--cutoff YYYY-MM-DD` |
| `split_claims_history.py` | Parses submission dates, sorts oldest first and splits history at 1 October 2026 by default; preserves original field values, outcomes and duplicates |
| `clean_data.py` | Cleans one CSV using explicit payer aliases, supported date formats, whitespace/case rules and identical-row deduplication; writes an audit trail; preserves unresolved invalid values |
| `check_formatting.py` | Reports problems safe cleaning cannot resolve, including invalid values, required fields, conflicting IDs and missing references; flags missing claim authorization numbers as business inputs |
| `claim_checker.py` | Cleans and validates the splits and context, replays claims, generates predictions/proposed fixes, applies the pending-claim review step, and calculates metrics |
| `unknown_outcomes.py` | Compares ignoring/assuming/predicting unknown outcomes against range review; tests external pending rows, random hidden historical outcomes and a 30-day backlog; writes separate metrics and predictions |
| `test_unknown_outcomes.py` | Tests the new replay’s temporal state, label isolation, review scoring and reproducible outputs |
| `pending_claims.py` | Supporting module: computes authorization exposure to earlier claims with unknown outcomes and returns `check_previous_claims` where appropriate |
| `paths.py` | Supporting module: defines the supplied-data, experiment and artifact directories independently of the shell's working directory |
| `test_check_formatting.py` | Tests canonicalization, rejected formats, conflicting records, missing inputs and cleaning idempotence |
| `test_claim_checker.py` | Tests payer-rule boundaries, historical state, multi-code scoring, input/output preservation and independence from test labels |
| `test_pending_claims.py` | Tests balance ranges, exact-fit thresholds, date filtering, authorization matching, deduplication and retention of definite denials |

## Run individual steps

The following commands assume you are inside `experiment/`:

```bash
# 1. Regenerate sorted raw history and the two chronological splits.
python3 split_claims_history.py

# 2. Clean earlier history into a separate file and generate its audit trail.
python3 clean_data.py sorted_prev_data.csv

# 3. Check that history against the original authorization/EVV context.
python3 check_formatting.py --claims sorted_prev_data_cleaned.csv --output prev_data_formatting_report.json

# Optional: inspect all original exports using the current checker.
python3 check_formatting.py

# 4. Rebuild predictions, review decisions, metrics and pipeline artifacts.
python3 claim_checker.py
```

The claim checker requires the split files from step 1; it runs its own cleaning
and validation, so steps 2–3 are useful for inspection but are not prerequisites.
The cleaner defaults to `<input_stem>_cleaned.csv` beside its input and a
`<output_stem>.cleaning.json` audit trail; use `--output` and `--report` to override.
The formatting checker exits with status 1 for errors; informational findings
and warnings alone do not cause failure. The pipeline stops for invalid supporting
context or history rather than building unreliable balances.

For separate experiment outputs, without replacing this run:

```bash
python3 run_experiment.py --output-dir /tmp/claims-experiment
```

`claim_checker.py` also accepts `--data-dir` for the original pack,
`--splits-dir` for the two split files, `--artifacts`, `--output` and `--metrics`.
Use `--help` on each command for its arguments.

## Outputs and their provenance

All paths in this table are relative to `experiment/`.

| Output | Produced by | Meaning |
|---|---|---|
| `claims_history_sorted.csv` | Splitter | All **3,862** original history rows sorted by submission date; no cleaning |
| `sorted_prev_data.csv` | Splitter | **2,765** original rows submitted before 1 October 2026; used to initialize history |
| `sorted_test_data.csv` | Splitter | **1,097** original rows submitted on or after the cutoff; kept as the raw evaluation split |
| `sorted_prev_data_cleaned.csv` | Cleaner, or full runner | **2,740** unique, normalized earlier-history rows, for standalone inspection |
| `sorted_prev_data_cleaned.cleaning.json` | Cleaner, or full runner | Per-cell before/after values, duplicate source lines, summary counts and unresolved invalid values |
| `formatting_report.json` | Formatting checker, or full runner | Current unresolved-issue audit across the original history, pending claims, authorizations and EVV exports; safe normalization differences are not listed |
| `prev_data_formatting_report.json` | Scoped formatting checker, or full runner | Unresolved-issue audit of the standalone cleaned earlier history plus original authorization/EVV context |
| `test_result.csv` | Claim checker | **1,097** result rows, preserving the original test row order; **1,090** unique claims are evaluated, with seven repeated export rows marked |
| `unknown_outcomes_metrics.json` | Unknown-outcome experiment | Four-policy comparisons, every deterministic mask, temporal-backlog results, input hashes and protocol |
| `unknown_outcomes_predictions.csv` | Unknown-outcome experiment | Per-target forecasts/decisions for all four external-pending policies; exposes false-denial cases |
| `claim_metrics.json` | Claim checker | Baseline metrics, observed-prior diagnostic, payer/code breakdowns, cleaning summaries and separate pending-claim review metrics |
| `claim_checker_artifacts/*.csv` | Claim checker via cleaner | Cleaned copies of both splits, pending claims, authorizations and EVV actually used by the pipeline |
| `claim_checker_artifacts/*.cleaning.json` | Claim checker via cleaner | Cleaning audit for each pipeline input |
| `claim_checker_artifacts/validation.json` | Claim checker via formatting checker | Validation reports for earlier history, test history and pending claims, each with authorization/EVV context |

Scoped reference checking only sees the selected claims file: an unresolved
`original_claim_id` warning may refer to an ID in another split rather than a
missing record in the complete pack. It is a context warning, not a D08 denial.

## Reading `test_result.csv`

| Columns | Interpretation |
|---|---|
| Original claim column names | Normalized claim inputs, before proposed business corrections |
| `predicted_status`, `predicted_denial_codes`, `predicted_remark` | Baseline forecast and all applicable documented failures; codes are a JSON array representing a set, e.g. `["D01", "D03"]`; paid claims have `[]` |
| `failed_checks`, `suggested_actions`, `warnings`, `evidence` | Reasons, rule sources, actions, uncertainty and supporting minutes, visits, units, balances and filing intervals |
| `decision` | Operational result: `paid`, `denied`, `manual_review`, or `check_previous_claims`; use this to find claims needing review even when the baseline forecast says paid |
| `authorization_balance_uncertain`, `potential_denial_codes` | Conditional authorization risk; a possible D02 is kept separate from definite baseline failures |
| `unresolved_prior_claim_ids`, `unresolved_prior_units`, `authorization_balance_if_none_paid`, `authorization_balance_if_all_paid`, `previous_claims_action` | Earlier pending claims, their exposure, the conditional balance range and what to verify |
| `proposed_changes`, `proposed_claim`, `fixed_auth_id`, `fixed_modifier`, `proposal_requires_review` | Suggested authorization/modifier changes; these require review and do not alter the prediction inputs |
| `actual_*` | Recorded outcomes retained for evaluation, excluded from the prediction interface |
| `observed_prior_*` | Separate diagnostic forecast using observed earlier payments instead of predicted earlier test outcomes |
| `status_correct`, `code_correct`, `error_category` | Baseline evaluation; `code_correct` means the actual denial code is in the predicted set, or a paid claim was predicted paid with no failures |
| `source_line`, `export_duplicate` | Original test CSV line and repeated-export marker; duplicates share a prediction and never consume state twice |

JSON-valued columns can be read with `json.loads()` after loading the CSV with
`csv.DictReader`; standard CSV quoting protects commas within these values.

## Evaluation and limitations

The baseline seeds balances with actual earlier paid claims, then uses predicted
test outcomes to update authorization usage and daily totals. Paid and denied
submissions both establish duplicate history. Bulletin versions use service
dates, while replay ordering and the split use submission dates. Safe formatting
fixes are applied automatically; business changes remain review proposals.

Baseline status accuracy is **96.97%**; status-and-code-set accuracy is **96.42%**.
The review step refers **14 unique claims**: ten actual D02 denials and four paid
claims, leaving **23** actual denials still marked payable. These referrals are
abstentions, not correct denial predictions. `main` in the metrics describes the
baseline, while `pending_claims_review` describes operational review and coverage.

Pending claims are planned/unconfirmed submissions, so the balance range is
conditional on the baseline ledger and deliberately conservative. Same-day
pending exposure is excluded because the within-day order is unknown. No Summit
contract amendment is inferred or implemented, and unspecified D99 outcomes
remain unexplained. This test split has been inspected during development;
subsequent results are exploratory. Full findings and recommendations are in
[the experiment report](../experiment_results.md).

## Unknown-result claims: updated recommendation

Run `python3 experiment/unknown_outcomes.py` from the repository root to reuse the
validated artifacts, or run the full runner to rebuild everything. Defaults and
`--artifacts`/`--output-dir` work independently of the shell's current directory.

The existing model can supply provisional outcomes for earlier unresolved
submissions. Including forecasts for the 546 unlabelled pending rows raises later
status/code agreement from 96.42% to 97.34%, while introducing two false denials.
With all 2,740 earlier historical outcomes hidden, later agreement is 96.24%,
compared with the observed-history control's 96.42%. A hidden September backlog
reproduces the control's downstream forecasts exactly.

This supports a simpler provisional predicted ledger for the prototype, updating
the original broad review recommendation. It does not establish actual submission
or payment, validate the 546 unknown outcomes themselves, or prove greater safety
than human review. Full methods, comparisons, counterexamples and limits are in
[section 8 of the report](../experiment_results.md#8-use-provisional-predictions-for-earlier-unknown-outcomes).
The web app has not been changed by this experiment.
