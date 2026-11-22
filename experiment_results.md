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

**Recommendation:** Keep this as an explicit review warning with claim IDs and
the balance range; verify submission/payment status or the payer's current
balance before allowing the claim to proceed. The observed trade-off—ten
intercepted denials for four extra paid-claim reviews—is promising, but should
be checked on a fresh holdout before adoption. Do not automatically deny these
claims or report review referrals as prediction successes.
