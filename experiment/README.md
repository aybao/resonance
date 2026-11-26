# Claims-checking experiment

A Python prototype that cleans synthetic billing exports, checks documented payer
rules, predicts all applicable denial codes and uses provisional predictions for
earlier claims whose outcomes are unknown. It uses the Python standard library only: no packages, API
keys or AI calls are required. Verified with Python 3.14.4.

The original assignment data stays in `../Resonance - graduate preparation data/`.
Scripts and generated outputs live here in `experiment/`. The discussion of
findings is in [experiment_results.md](../plans/experiment_results.md).

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
Tests are a separate command and currently comprise **24 passing tests**.

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
| `claim_checker.py` | Cleans and validates the splits and context, replays claims, generates predictions/proposed fixes, forecasts unknown outcomes into the provisional ledger, and calculates metrics |
| `unknown_outcomes.py` | Compares ignoring, assuming payment and predicting unknown outcomes; tests external pending rows, random hidden historical outcomes and a 30-day backlog; writes separate metrics and predictions |
| `test_unknown_outcomes.py` | Tests the new replay’s temporal state, label isolation, error handling and reproducible outputs |
| `paths.py` | Supporting module: defines the supplied-data, experiment and artifact directories independently of the shell's working directory |
| `test_check_formatting.py` | Tests canonicalization, rejected formats, conflicting records, missing inputs and cleaning idempotence |
| `test_claim_checker.py` | Tests payer-rule boundaries, historical state, multi-code scoring, input/output preservation and independence from test labels |

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

# 4. Rebuild predictions, metrics and pipeline artifacts.
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
| `unknown_outcomes_metrics.json` | Unknown-outcome experiment | Three-policy comparisons, every deterministic mask, temporal-backlog results, input hashes and protocol |
| `unknown_outcomes_predictions.csv` | Unknown-outcome experiment | Per-target forecasts for all three external-pending policies; exposes false-denial cases |
| `claim_metrics.json` | Claim checker | Baseline metrics, observed-prior diagnostic, payer/code breakdowns, cleaning summaries and provisional historical forecasts |
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
| `failed_checks`, `suggested_actions`, `warnings`, `evidence` | Reasons, rule sources, actions and supporting minutes, visits, units, balances and filing intervals |
| `proposed_changes`, `proposed_claim`, `fixed_auth_id`, `fixed_modifier`, `proposal_requires_confirmation` | Suggested authorization/modifier changes; these require confirmation and do not alter the prediction inputs |
| `actual_*` | Recorded outcomes retained for evaluation, excluded from the prediction interface |
| `observed_prior_*` | Separate diagnostic forecast using observed earlier payments instead of predicted earlier test outcomes |
| `status_correct`, `code_correct`, `error_category` | Baseline evaluation; `code_correct` means the actual denial code is in the predicted set, or a paid claim was predicted paid with no failures |
| `source_line`, `export_duplicate` | Original test CSV line and repeated-export marker; duplicates share a prediction and never consume state twice |

JSON-valued columns can be read with `json.loads()` after loading the CSV with
`csv.DictReader`; standard CSV quoting protects commas within these values.

## Evaluation and limitations

The current pipeline interleaves earlier settled history, 546 unlabelled claims
and the target cohort. Earlier confirmed paid outcomes consume recorded units;
unknown outcomes and earlier target claims are predicted with the same checker.
Predicted paid claims reserve provisional units, while denied predictions consume
none. Every submitted claim still establishes duplicate history. Actual target
outcomes are used only for scoring, or for the separately labelled diagnostic.

The provisional model achieves **97.71% status accuracy** and **97.34% agreement
with recorded status/code outcomes** on 1,090 unique targets. It leaves 23 denials
predicted payable and produces **two false D02 denials on recorded paid claims**.
The diagnostic using observed earlier target payments produces 1,062 matches
versus the main run's 1,061. Missing contracts and correspondence remain unexplained.

`unknown_outcomes.py` separately compares ignore/all-paid/predicted policies.
With all 2,740 earlier outcomes hidden, later agreement remains 96.24%, compared
with 96.42% for the observed-history control without external pending claims.
A hidden September backlog reproduces that control's downstream forecasts exactly.
Neither test supplies ground truth for the 546 genuinely unlabelled rows.

Provisional usage remains distinct from confirmed payer payment. Dates without
adjudication timestamps and snapshot EVV availability are assumptions. This is
an inspected synthetic cohort, so validate future changes on a fresh cohort.
See [the full report](../plans/experiment_results.md) for methods, controls,
per-mask results, counterexamples and the proposed ledger policy.
