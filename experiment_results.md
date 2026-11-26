# Rules-based claims experiment

Scripts and generated outputs are in `experiment/`; the original assignment data
remains in `Resonance - graduate preparation data/`. See
[the experiment README](experiment/README.md) for file descriptions and run options.

## 1. Clean and validate the data

**Purpose:** Make equivalent records match reliably without inventing missing information.

**Applied:** Standardize payer aliases, ISO dates, whitespace and code case; interpret slash dates as US MM/DD/YYYY; remove identical export rows while preserving distinct submissions and conflicting records.

| Formatting fix | Earlier history: changed cells | Test split: changed cells |
|---|---:|---:|
| Payer aliases → PY01/PY02/PY03 | 678 | 253 |
| Service dates → YYYY-MM-DD | 324 | 133 |
| Submission dates → YYYY-MM-DD | 309 | 114 |
| Trim service codes | 125 | 51 |
| Trim modifiers and standardize case | 92 | 42 |
| **Total changed cells** | **1,528** | **593** |
| **Source rows with at least one change** | **1,255** | **482** |

“Earlier history: changed cells” counts individual field values corrected in claims submitted before 1 October 2026, while “Test split: changed cells” counts those corrected on or after that date; one row can have several corrections, and these counts include rows subsequently removed as duplicates.

**Cleaning methods:**

- **Payer aliases:** Use an explicit lookup table mapping the supplied names and abbreviations to PY01, PY02 or PY03; unknown names are flagged rather than guessed.
- **Dates:** Parse the two supported formats, YYYY-MM-DD and US MM/DD/YYYY, then write valid dates as YYYY-MM-DD.
- **Service codes:** Remove leading/trailing whitespace and convert to uppercase, then validate against T1019, T1005 and S5125.
- **Modifiers:** Remove leading/trailing whitespace and convert to uppercase, then validate nonblank values against U1, U2 and U3; selecting the correct modifier for a claim is a separate payer-rule check.
- **Duplicates:** Compare every field after normalization and retain the first fully identical row, preserving distinct claim IDs and conflicting records.

| Row-level operation | Earlier history | Test split |
|---|---:|---:|
| Original rows | 2,765 | 1,097 |
| Identical canonical rows removed | 25 | 7 |
| Unique rows retained | 2,740 | 1,090 |
| Missing authorization numbers retained | 63 | 21 |

**Outcomes:**

- No other unresolved formatting errors were found under the implemented checks.
- All **780 authorization records** and **5,076 EVV records** passed without formatting repairs.
- Rows are deduplicated only when every field matches after canonicalization; different claim IDs remain separate, and conflicting versions of an ID require review.

**Implemented as a result:** Separate cleaned copies and audit trails; a checker for unresolved issues; missing business information stays available for denial checks rather than being filled automatically.

`clean_data.py` preserves ordering, remarks and business outcomes, and leaves
invalid values for `check_formatting.py`. The checker flags invalid formats,
missing required fields, conflicting IDs, unresolved references, missing
correction references and reversed date ranges. Unsupported schemas and malformed
CSV row lengths stop cleaning.

## 2. Apply the documented payer rules

**Purpose:** Establish how much of the payer's behaviour can be explained with explicit rules.

| Scope | Rules applied |
|---|---|
| All payers | Verified EVV; authorization identity, coverage and remaining units; duplicate submissions; corrected-claim exemption |
| Northwind | Eight-minute rounding; 30-day filing limit before 1 August service dates, 45 days thereafter |
| Harbor | Complete 15-minute units; 32 paid units per member/day across services; T1019 modifier U2, changing to U3 on weekends from 1 September; no filing limit |
| Summit | Complete 15-minute units; 60-day filing limit; warning that additional contract requirements are unavailable |

| Code | Implemented failure condition |
|---|---|
| D01 | No verified EVV for the patient, service and clock-in date |
| D02 | Billed units exceed the referenced authorization's remaining units |
| D03 | Submission is beyond the applicable filing limit |
| D04 | Harbor T1019 modifier is missing or incorrect for the service date |
| D05 | A new claim duplicates an earlier submission for payer, patient, service and date |
| D06 | Harbor's previously paid daily units plus this claim exceed 32 |
| D07 | Authorization number is missing |
| D08 | Authorization is unknown or mismatched to payer, patient, service or coverage dates |
| D09 | Billed units exceed units supported by verified EVV time |

Authorization coverage dates are inclusive. Filing at exactly the limit is
allowed. Northwind rounds a remainder of **8 minutes up**, but **7 minutes
down**; Harbor and Summit discard partial 15-minute units. Frequency `7`
claims are exempt from duplicate denial and carry an original-claim reference.
Claims exceeding a balance or daily maximum are denied in full.

**Edge cases:** Aggregate visits before rounding; assign overnight visits to their clock-in date; count denied submissions when checking duplicates; deduct units only for paid claims.

**Implemented as a result:** Return **every applicable denial code**, its evidence and a suggested action, rather than guessing which failure the payer prioritizes.

## 3. Replay claims chronologically

**Purpose:** Test future claims using only information available from earlier submissions.

**Applied:** Split by submission date at **1 October 2026**, initialize balances from earlier actual payments, then update test balances using predicted outcomes; select bulletin versions by service date.

The replay maintains three pieces of state:

| State | Key | Update |
|---|---|---|
| Authorization usage | Authorization ID | Add units only when paid |
| Daily paid units | Payer, patient, service date | Add units across services only when paid |
| Prior submissions | Payer, patient, service, service date | Record paid and denied submissions |

Predictions use only the claim input columns; `status`, `denial_code`,
`paid_amount` and `remark` are excluded. In the diagnostic replay, each actual
outcome updates state only after that claim has been predicted.

**Outcomes:** All **1,090 unique test claims** received predictions; a separate replay using observed prior payments produced the same status and code sets.

**Implemented as a result:** Exclude actual outcomes and remarks from prediction inputs; process export duplicates once; preserve all **1,097 original test rows** in the output.

**Limitation:** **84 test submission dates** contain multiple unique claims;
same-day order is assumed from source order. Processing each claim before the
next and using snapshot EVV verification are assumptions because adjudication,
payment and verification-acceptance timestamps are absent.

## 4. Evaluate the predictions

**Purpose:** Measure whether the checker detects the denial actually reported by the payer.

**Applied:** A denial matches when its reported code appears in the predicted set; a paid claim matches only with a paid prediction and an empty set.

| Outcome | Result |
|---|---:|
| Correct paid/denied status | **1,057 / 1,090 (96.97%)** |
| Correct status and matching code set | **1,051 / 1,090 (96.42%)** |
| Reported denial code detected | **348 / 387 (89.92%)** |
| Recorded denials identified as denied, regardless of code | **354 / 387 (91.47%)** |
| Missed denials | **33 / 387 (8.53%)** |
| False alarms on paid claims | **0 / 703** |
| Always-predict-paid baseline | **64.50%** status accuracy |

| Payer | Unique claims | Correct status | Correct status and code set |
|---|---:|---:|---:|
| Northwind | 398 | 389 (97.74%) | 387 (97.24%) |
| Harbor | 404 | 393 (97.28%) | 393 (97.28%) |
| Summit | 288 | 275 (95.49%) | 271 (94.10%) |

Metrics exclude export duplicates and include **703 paid claims** and **387
denials**. Manual-review predictions do not count as matches; there were none
on this test split. Remarks explain the detected rules and are not scored
against the payer's exact wording.

**Edge cases:** **57 claims** have multiple failures; set matching accepts **37 cases** previously penalized for selecting a different primary code.

**Implemented as a result:** Store `predicted_denial_codes` as a set serialized as a JSON array, such as `["D01", "D03"]`; `code_correct` uses membership. The **70 additional codes** beyond reported denial reasons remain unconfirmed by the single-code labels.

## 5. Understand remaining errors and propose fixes

**Purpose:** Separate missing rules and evidence from issues we can safely help a billing user resolve.

| Remaining mismatch | Count | Response |
|---|---:|---|
| Contract-specific D10 | 10 | Flag missing Summit contracts; obtain amendments before implementing their scope and dates |
| Unspecified/manual-review D99 | 17 | Obtain payer correspondence; do not invent a rule |
| Authorization-balance D02 | 12 | Investigate opening balances, missing transactions and processing order |

Of the ten D10 cases, six were predicted paid and four failed another rule.
Of the twelve unexplained D02 cases, ten were predicted paid and two failed
late-filing checks. All seventeen D99 cases were predicted paid. Missing
transactions, opening balances or inconsistent synthetic labels are possible
explanations for the D02 discrepancies, not confirmed causes.

**Example:** C011708 bills **7 units** with **24 reconstructed units remaining**, but history reports D02; observed-prior replay does not explain the discrepancy.

**Outcomes:** **39 code-set mismatches** remain; the **33 missed denials** comprise 17 D99, six D10 and ten D02 cases.

**Implemented as a result:** Propose corrections on **54 claims**: **42 authorization references** with a unique matching record and sufficient predicted balance, plus **12 Harbor modifiers**. Proposals require review and may resolve only part of a claim's failures; they have not been validated through resubmission.

| Proposed fix | Claims | Evidence required |
|---|---:|---|
| Replace or add authorization reference | 42 | Exactly one matching payer/patient/service/date authorization, with sufficient predicted balance |
| Correct Harbor T1019 modifier | 12 | Explicit weekday/weekend rule for that service date |

The original cleaned input stays in the ordinary claim columns; `proposed_claim`,
`proposed_changes`, `fixed_auth_id` and `fixed_modifier` hold the candidate edits.
Predictions describe the claim **before** those edits. Units, billed amounts,
dates and EVV verification status are not changed to force a pass.

**Next:** Obtain missing contracts and balance evidence, then evaluate improvements on a fresh holdout because this split has now been inspected.

## 6. Demonstrate and verify

**Purpose:** Make each prediction reproducible and easy to inspect with an assessor.

**Applied:** `test_result.csv` contains cleaned inputs, all predicted codes, explanations, evidence, proposed corrections and separate actual outcomes; `claim_metrics.json` contains detailed metrics.

| Artifact | Technical detail |
|---|---|
| `test_result.csv` | One row per original test row; `export_duplicate` marks repeats; `failed_checks` records reasons, source references and actions; `evidence` records visit IDs, minutes, supported units, balance, daily total and filing interval |
| `claim_metrics.json` | Overall, payer-level and actual-code-level metrics, plus actual-code-to-predicted-set counts |
| `claim_checker_artifacts/*.cleaning.json` | Every changed cell, removed duplicate and unresolved invalid value |
| `claim_checker_artifacts/validation.json` | Unresolved data-quality findings for both splits and their context |

**Outcomes:** **22 tests pass**, covering rule boundaries, chronological state, duplicate handling, cleaning, pending-claim review and independence from test labels; source files remain unchanged.

Tests cover 7/8-minute rounding, filing deadlines and bulletin dates, Harbor
weekdays/weekends, midnight attribution, aggregation before rounding,
pending/rejected EVV, authorization matching and exhaustion, daily limits,
corrected claims, multi-code membership and cleaning idempotence. The integration
test verifies one output per export row and that changing test outcomes cannot
change main-run predictions or their state.

**Run:**

```bash
python3 experiment/run_experiment.py
python3 -m unittest discover -s experiment -p 'test_*.py' -v
```

## 7. Earlier claims with unknown outcomes

**Purpose:** Identify claims that appear payable only because earlier unresolved
claims have not been included in authorization usage.

**Issue:** `claims_to_check.csv` contains claims with submission dates earlier
than some test claims, but no outcomes; ignoring them can overstate remaining
authorization units, while assuming they were paid can create false denials.

**Changed logic:** Retain the existing prediction as a baseline and add an
operational decision of `check_previous_claims` when an otherwise payable claim
would exceed its authorization if relevant earlier unresolved claims were paid.
Show the earlier claim IDs and the balance with and without their units; keep
D02 as a possible code rather than a confirmed denial. Existing definite
denials retain their decision, with any additional balance uncertainty noted.

**Implementation:** `pending_claims.py` runs inside `claim_checker.py` after
the ordinary checks and before the current actual outcome is read:

1. Clean and validate all **546 pending claims**; this fixes **328 cells** and
   removes no duplicates. Keep the original file unchanged.
2. Find distinct pending claims with a strictly earlier submission date and
   the same authorization, payer, patient and service; require authorization
   coverage of the pending claim's service date. Exclude IDs already represented
   in historical/test records to avoid double counting.
3. Let **R** be the baseline remaining balance and **U** the earlier pending
   units. Compare this claim's units with the range **max(0, R − U) to R**.
4. If the claim fits R but not the lower balance, flag uncertainty; change a
   baseline `paid` decision to `check_previous_claims`, keeping baseline codes
   separate from `potential_denial_codes = ["D02"]`.

`test_result.csv` now includes `decision`, `authorization_balance_uncertain`,
the two balances, unresolved claim IDs/units, potential codes and the suggested
verification action. This is a review step, not an assertion that scheduled
claims were submitted or paid. The baseline forecast ledger stays unchanged:
referring a claim for review does not establish that it will remain unpaid.

**Evaluation:** Counts below use the same **1,090 unique test claims** and are
exploratory because their errors had already been inspected.

| Measure | Baseline | With previous-claim review |
|---|---:|---:|
| Claims sent to this additional review | 0 | **14 (1.28%)** |
| Recorded denials still marked payable | 33 | **23** |
| Previously missed D02 cases sent to review | 0 / 10 | **10 / 10** |
| Recorded paid claims unnecessarily sent to review | 0 | **4 / 703 (0.57%)** |
| False definite denials on paid claims | 0 | **0** |
| Claims receiving an automatic paid/denied decision | 1,090 (100%) | **1,076 (98.72%)** |
| Matching status/code set among automatic decisions | 1,051 / 1,090 (96.42%) | **1,047 / 1,076 (97.30%)** |

Ten of the fourteen reviewed claims were actual D02 denials (**71.43%**).
The step intercepts **30.30% of the 33 missed denials**, leaving seventeen D99
and six D10 cases still marked payable. Another six already-denied claims carry
balance-uncertainty flags but keep their definite denial decision.

Review is **not** counted as a correct prediction: automatic correct code-set
matches across *all* claims become **1,047 / 1,090 (96.06%)**, with fourteen
abstentions. The original baseline prediction accuracy remains 96.42%; the
benefit is fewer risky claims proceeding without review, not a demonstrated
increase in unconditional prediction accuracy. Metrics are stored separately
under `pending_claims_review` in `claim_metrics.json`.

| Example | Units billed | Balance if no pending claims paid | Balance if all paid | Outcome |
|---|---:|---:|---:|---|
| C011708 | 7 | 24 | 6 | Actual D02; now review |
| C012372 | 8 | 14 | 6 | Actually paid; extra review |
| C005201 | 20 | 32 | 0 | Actual D02; now review |

**Edge cases and limits:** Same-day pending claims are excluded because ordering
is unknown; later claims cannot affect an earlier assessment. Pending claims
may themselves fail checks or compete for the same balance, so summing their
units deliberately gives a conservative exposure estimate. The range is
conditional on the baseline's earlier test predictions, not a verified payer
balance or a complete simulation of every possible outcome. This change covers
authorization exposure only, not pending effects on duplicate or daily-limit
checks. It does not implement any inferred Summit rule.

**Validation:** Six new tests cover the balance threshold, exact-fit allowance,
same-day/future exclusion, duplicate and known-ID exclusion, authorization
matching, and retention of definite denials. The integration test confirms
baseline predictions and review decisions remain independent of test labels.

**Original recommendation (updated by section 8):** Keep this as an explicit review warning with claim IDs and
the balance range; verify submission/payment status or the payer's current
balance before allowing the claim to proceed. The observed trade-off—ten
intercepted denials for four extra paid-claim reviews—is promising, but should
be checked on a fresh holdout before adoption. Do not automatically deny these
claims or report review referrals as prediction successes.


## 8. Use provisional predictions for earlier unknown outcomes

**Updated recommendation:** For the prototype, use the existing deterministic
checker to predict earlier submitted claims whose results are unknown, and use
those predictions in a **provisional ledger**. Predicted paid claims reserve
units; predicted denied claims remain submissions but do not consume units.
Keep their real outcome marked unknown, and expose when a later decision depends
on predicted usage. This is a reasonable simpler default than the broad
range-based review gate proposed in section 7. It is not evidence that predictions
are confirmed payments, or that every resulting denial is correct.

A complete uncertainty model would need reliable submission, adjudication,
payment, correction and reconciliation events, including their timing. Those
are missing from the supplied data. The current balance-range calculation is computationally simple. The difficulty
is assigning realistic meaning to those bounds when submission and resolution
events are missing, and extending them across corrections and dependencies.
Predicting unknown results supplies one auditable working estimate. The experiment
below tests that alternative rather than assuming it works.
The app has not been changed by this experiment.

### Method and safeguards

`experiment/unknown_outcomes.py` reuses the existing `Checker` without changing
rules, thresholds, source claims, authorization limits or the original results.
All methods use the same **2,740 earlier claims**, **1,090 unique later test
claims**, authorizations and EVV. The original **1 October 2026** split is retained.
The observed-earlier-history control reproduces **1,051/1,090 (96.42%)** matches.

The simulator accepts claim fields and explicitly visible earlier statuses only.
Hidden outcomes, all target outcomes, denial codes and remarks are separated from
prediction and read only for scoring after replay. Earlier target claims update
state using predictions throughout. Paid and denied historical submissions both
remain available for duplicate checks. No proposed business corrections are
applied. A match uses the same criterion as the original experiment: paid with no
failures, or denied with the recorded single code present in the predicted set.

Four policies are compared: ignore unknown payment usage; assume all unknown
claims paid; predict them with the existing checker; and the previous balance-range
review gate. Review is an abstention and is never credited as a correct denial.

### A. Revisit the 546 genuinely unlabelled claims

Interleave `claims_to_check.csv` with the labelled timeline and predict each
unknown claim from the state available before it. The prediction policy forecasts
**383 paid and 163 denied**. Their own accuracy cannot be measured because their
outcomes are absent. The measurable question is whether including those forecasts
improves predictions for the **1,090 later labelled claims**.

| Measure | Ignore pending (original baseline) | Assume all paid | Predict unknown outcomes | Previous range review |
|---|---:|---:|---:|---:|
| Correct automatic status/code matches, out of all 1,090 | 1,051 (96.42%) | 1,060 (97.25%) | **1,061 (97.34%)** | 1,047 (96.06%) |
| Automatic decisions | 1,090 | 1,090 | **1,090** | 1,076 |
| Claims sent to review | 0 | 0 | **0** | 14 |
| Recorded denials still marked payable | 33 | 23 | **23** | 23 |
| False denials on recorded paid claims | 0 | 3 | **2** | 0 |
| Recorded D02 codes detected | 15 / 27 | 27 / 27 | **27 / 27** | 15 / 27 (plus 10 referrals) |
| Recorded paid claims sent to review | 0 | 0 | **0** | 4 |

The prediction policy changes **17** forecasts: **12** gain the recorded denial
code, **2** lose a previously correct match, and **3** remain correct with an
additional unconfirmed D02. Ten previously payable D02 denials become denied;
the other two D02 improvements add the correct code to claims already denied for
late filing. This is a net gain of **10 matches**, or **0.92 percentage points**,
over ignoring the unknown claims. Status-only accuracy becomes **1,065/1,090
(97.71%)**, compared with 96.97% originally.

The counterexamples matter: **C012372** and **C012381** were recorded paid but
become D02 under the predicted ledger. The previous range gate would send both
to review. Thus prediction reduces review workload and improves aggregate
agreement, while the review approach retains the stronger zero-false-denial
result on this dataset. Its 97.30% agreement among automatically decided cases
uses a smaller denominator than the prediction policy's 97.34%; those percentages
must not be presented as an equal-coverage safety comparison.

These external rows may be planned rather than submitted. Including them in the
prediction/all-paid policies assumes they were submitted on their listed dates;
the old ignore/range policies do not make that assumption. The observed forecast
changes here are D02 changes. Predicting payment does not establish submission.
Pending rows are processed before same-day targets by default; processing them
after same-day targets gives the **same 1,061 matches and two false denials**.
That agreement does not establish within-day order generally.

### B. Hide outcomes in labelled earlier history

To evaluate actual unknown-result handling, hide outcomes of earlier historical
claims known to have been submitted, then score the unchanged later test cohort.
Mask **25%** and **50%** using five fixed seeds (**11, 23, 37, 51, 71**) and mask
**100%** once. Repeating 100% with another seed would reproduce the same mask.
These runs exclude the external pending file to isolate historical-outcome effects.

| Earlier outcomes hidden | Claims hidden per run | Ignore payment usage | Assume paid | Predict with existing checker |
|---|---:|---:|---:|---:|
| 25% | 685 | 96.11% | 96.26% | **96.39%** (1,049–1,051 matches) |
| 50% | 1,370 | 95.74% | 96.11% | **96.35%** (1,049–1,051 matches) |
| 100% | 2,740 | 95.60% | 95.78% | **96.24%** (1,049 matches) |

Percentages at 25%/50% are averages over five masks of the **same** test cohort,
not independent validation cohorts or confidence intervals. With every earlier
outcome hidden, the prediction policy is only **2 matches below** the
observed-history control, and produces **one** false denial. It predicts the
hidden earlier claims themselves with **2,672/2,740 (97.52%)** correct statuses
and **2,657/2,740 (96.97%)** status/code matches. That is direct evidence about
withheld historical outcomes, unlike the unlabelled-file comparison.

A date-based backlog sensitivity hides all **834 September submissions** from
**1 September 2026** onward, keeping older outcomes visible. This selection uses
dates, not outcome labels. Predicting the backlog reproduces **all downstream
status/code forecasts** of the observed-history control: **1,051/1,090 (96.42%)**
with **zero false denials**. Ignoring the backlog yields **1,044 (95.78%)**
matches; assuming it all paid yields **1,047 (96.06%)**, with four false denials.
The range gate sends eight claims to review, including three recorded paid claims.
The backlog's own forecasts match **805/834** recorded status/code outcomes.

### Interpretation and next step

The results support replacing the *default prototype recommendation* in section 7
with predicted outcomes for genuinely submitted, unresolved claims. The simpler
method preserves most downstream agreement even when many prior outcomes are
hidden, and the external-pending comparison improves agreement while avoiding
14 broad review referrals. Preserve confirmed versus predicted usage separately;
reconcile the provisional entries when actual payer outcomes arrive. Keep existing
manual review for missing/invalid evidence, and explain dependencies on provisional
balances rather than claiming payment certainty.

This remains exploratory evidence on an already-inspected synthetic dataset.
Random masks and a 30-day backlog are simulations: actual unresolved claims may
be concentrated among difficult contracts, delayed adjudications or appeals.
Submission and adjudication timestamps are unavailable, and snapshot EVV is
assumed available during replay. The two false denials in comparison A prevent a
claim that this is universally safer than review. Validate on a fresh cohort with
real pending-outcome timing before treating the recommendation as production policy.

**Reproduce and inspect:**

```bash
# Reuse the existing validated, cleaned artifacts:
python3 experiment/unknown_outcomes.py

# Rebuild original and new results together in a separate directory:
python3 experiment/run_experiment.py --output-dir /tmp/claims-unknown-outcomes

python3 -m unittest discover -s experiment -p 'test_*.py' -v
```

[unknown_outcomes_metrics.json](experiment/unknown_outcomes_metrics.json) records
all policies, every mask/seed, the temporal backlog, input SHA-256 hashes and the
protocol. [unknown_outcomes_predictions.csv](experiment/unknown_outcomes_predictions.csv)
contains per-target predictions and decisions for comparison A, including the two
false-denial counterexamples. Original `claim_metrics.json` and `test_result.csv`
remain unchanged. **31 experiment tests pass**, including nine new tests for
label isolation, temporal ordering, predicted usage, denial handling, duplicate
exclusion, honest review scoring, same-day sensitivity and reproducibility.
