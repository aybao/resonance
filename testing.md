# Testing and runtime validation

The app has three different kinds of checks:

1. **Automatic runtime validation** of actual uploads and proposed rules.
2. **Historical demo/replay comparisons** against inspected synthetic outcomes.
3. **Developer tests** run explicitly from `app/tests/`, including a separate
   browser script. These test the code used by runtime and demo flows; the test
   runner itself is not invoked by ordinary user actions.

The current inventory is **81 `unittest` methods in seven files**, plus the
standalone browser smoke script. All 81 passed during this documentation update
on **24 November 2026**. Browser and live-provider checks are separate from that
81-test result. No live AI call was needed for the developer suite.

## When checks run

| Check | Automatic trigger or on-demand command | Part of |
|---|---|---|
| CSV structure and field validation | Every claims upload and re-check after a field edit; reference loading also reports reference problems. | Runtime, both modes. |
| Deterministic claim checks and pending-balance assessment | Each valid, unique claim in a batch; re-run after relevant reference/rule changes, field edits and classifications. | Runtime, both modes. |
| Markdown attachment validation | On file selection (extension) and Send (nonempty document); input remains available on failure. | Browser runtime. |
| Proposal/schema, code and scenario checks | On AI proposal processing; candidate evaluation on draft creation, GET, edits, chat, added examples and explicit decisions. | Rules-updater runtime. |
| Actual accepted/candidate example execution | During each draft evaluation, including after each AI response. | Rules-updater runtime. |
| Mechanical control cases | Refreshed with drafts and executed in evaluation; global-code controls cover all known payers at up to three example dates. | Rules-updater runtime. |
| Saved-example regressions | Each draft evaluation against active, non-superseded saved examples. | Rules-updater runtime. |
| Historical impact diagnostic | Each valid draft evaluation when recorded reference history exists. Uses observed earlier outcomes. | Rules-updater runtime; not the monthly demo score. |
| Acceptance validation | Again when **Accept new rule** is clicked, before saving. Saved-check regressions require a reason/source. | Rules-updater runtime. |
| Worker execution/output limits | Every generated-code execution, including later ordinary claims. Failures become errors or review outcomes. | Runtime, both modes. |
| Monthly upload comparison | After a demo batch is checked/re-checked, if all claim IDs are known to the demo source. | Web demo runtime. |
| Fast-forward date validation | On demo advance. Updates references and clears the batch; it does not run claims/tests. | Web demo runtime. |
| Scripted historical regression | `python3 app/replay_history.py --report /tmp/replay.json` | On-demand developer/demo script. |
| Unit, pipeline, session and HTTP suite | `python3 -m unittest discover -s app/tests -t app` | On-demand development; not ordinary runtime. |
| Browser smoke test | `python3 app/tests/browser_rules_chat.py` with a local driver | On-demand UI verification. |
| JSON draft helper | `python3 app/check_examples.py ...` | On-demand evaluation using the runtime evaluator. |
| Live Claude replay | `python3 app/replay_history.py --interactive --live-ai` | Explicit on-demand integration/demo; requires key and human acceptance. |

The full unit suite, browser script and complete historical replay do **not** run
on each chat message or CSV upload. Runtime validation does not establish that
an AI-generated rule implements every possible payer condition correctly.

## How to run checks

All commands in this document start at the repository root.

```bash
# Complete standard suite: no API key, network model or browser required.
python3 -m unittest discover -s app/tests -t app

# One module or one test.
PYTHONPATH=app python3 -m unittest tests.test_global_updates
PYTHONPATH=app python3 -m unittest tests.test_rules_engine.EVV.test_northwind_eight_minute_boundary

# Scripted regression replay, with every mismatch in a JSON report.
python3 app/replay_history.py --report /tmp/replay.json

# The same replay, with presentation pauses.
python3 app/replay_history.py --interactive

# Explicit live-provider check, with one acceptance prompt per update.
python3 app/replay_history.py --interactive --live-ai

# Validate/execute a local code draft and its example rows.
python3 app/check_examples.py /path/to/draft.json --rules app/state/rules.json
```

The suite uses temporary state and fake AI transports. HTTP tests start real
localhost servers on automatically selected ports, so they need permission to
open local sockets. Rule tests launch the bounded Python worker and rely on Unix
resource facilities. A restricted execution environment can prevent these tests
from running even when the application logic is unchanged. Runtime varies with
CPU/process overhead; the documentation verification run took about 56 seconds.

Standard assertions/uncaught exceptions make `unittest` exit nonzero on failure.
`replay_history.py` exits nonzero for unexpected historical differences.
`check_examples.py` exits nonzero for reported problems; a healthy unaccepted
candidate is not itself a helper failure. Provider errors are not replaced by
fixture output on a live run.

## Runtime rule checks: what passes actually mean

The evaluator builds a temporary candidate store without changing accepted rules,
then executes both stores on the same scenario inputs. Status/code changes mark
an example as changed; the UI hides unchanged cases. Generated expected codes
are suggestions. A mismatch with an unconfirmed AI expectation does not prove the
engine is wrong, and does not by itself prevent whole-rule acceptance.

Acceptance needs no per-example approval. Changed actual outputs are saved as
`accepted_with_rule` regression snapshots. Explicit API confirmations/corrections
have `user_confirmed` provenance. Neither generated snapshots nor a user clicking
accept are a statistical accuracy estimate. Clarification questions, invalid
code/examples, execution failures and conflicts with explicit expectations block
acceptance. Earlier saved-check changes require a recorded reason/source.

Mechanical controls exercise baseline filing boundaries, earlier dates, modifiers,
daily limits and EVV rounding across known payers. Saved examples compare full
expected code sets. Controls do not exhaust arbitrary generated code or infer
its intended effective dates from source. The AI skill and source evidence must
supply appropriate guards and examples.

Approved code is also bounded on every later claim: syntax/language restrictions,
JSON input/output validation, memory limit, trace budget and worker response
wait. These are execution checks, not proof of rule semantics or a comprehensive
hostile-code security test. See [worker limits](documentation.md#rule-execution-worker).

## Historical comparisons and the demo

### Web demo

The web demo starts at **2026-04-30**. Fast-forwarding changes the reference-data
cutoff and clears the previous batch. A separate monthly CSV upload runs the
checker. At June 30, June is pending: June references are available, while June's
recorded outcomes are excluded from prediction. At July 31, June outcomes can
enter history and July remains pending. History is further restricted to dates
before the uploaded batch's earliest submission.

After prediction, a known-ID batch gets a historical comparison. Field edits,
classification changes and rule acceptance re-check the batch and refresh that
comparison. Unknown claim IDs suppress the report. This is not an automatic unit
test execution; it is a live diagnostic using the current rules and inputs.

The session tests pin these inspected results:

| Fixture | Asserted result |
|---|---|
| June CSV after advancing to June 30 | 361 claims, 354 matches, seven unexplained/documented D99 errors; zero unexpected mismatches. |
| September with base manuals | 26 false alarms. |
| September after Northwind alone | Nine false alarms; C005332 changes from late-filing denial to paid. |
| September with both supplied bulletins | 805/834 matches, 29 documented errors, zero unexpected mismatches, 21 missed denials, one false alarm. |

Northwind's supplied change is **45** days from 1 August 2026 service dates; it
first changes the supplied September **submission** file. Some developer fixtures
use 50 or 60 days deliberately to test configurable updates. Those fixture values
are not amendments to the real supplied bulletin.

### Three-stage command-line replay

The scripted path explicitly starts at **2026-06-30** and predicts periods ending
July 31, August 31 and January 31, introducing Northwind and Harbor updates between
stages. It uses `HistoricalReplay.replay()`, not the web fast-forward/upload flow.
Fixture proposals are checked against independently source-transcribed parameter
rules before acceptance. `--interactive` pauses the same process; `--live-ai`
replaces fixture proposals with real requests and asks for user acceptance.

`demo-data/replay_exceptions.json` holds 121 reviewed diagnostic entries. An
exception requires the exact claim, recorded status/code, prediction status/full
code list and allowed period end. Exceptions are not discovered or added during
a run. The full replay test removes an entry and checks that the run then fails.

A historical `passed` flag means **all remaining differences are explicitly
documented**. It does not mean all predictions are correct. Documented D10/D99
and balance discrepancies remain in the score denominator and mismatch list.
Changing a rule or a cutoff can expose a new mismatch that needs investigation.
See [the exception rationale](demo-data/replay_exceptions.md).

Historical matching requires the correct status and, for denials, membership of
the recorded single code in the predicted set. It does not prove exact code-set
equality despite the legacy metric field name. Uncertainty referrals are not
counted as correct denials. These inspected synthetic cohorts are not a holdout
or a production accuracy assessment.

## Developer test inventory

Every method below is discovered by the standard command and is **on demand**,
not directly invoked by an app user. The labels identify which production flow
it exercises, rather than implying that the whole test method runs at runtime.

| Source file | Methods | Main coverage |
|---|---:|---|
| [test_rules_engine.py](app/tests/test_rules_engine.py) | 19 | Manual-rule units, boundaries, duplicates and legacy versions. |
| [test_pipeline.py](app/tests/test_pipeline.py) | 20 | Input audit, sequential ledger, export and claim reference details. |
| [test_drafts.py](app/tests/test_drafts.py) | 12 | Proposal/review/acceptance lifecycle, provenance, persistence and AI client contract. |
| [test_code_rules.py](app/tests/test_code_rules.py) | 10 | Novel code execution, isolation, errors, resource checks and label exclusion. |
| [test_global_updates.py](app/tests/test_global_updates.py) | 5 | Code-owned payer/date scope, combined updates, compatibility and revisions. |
| [test_http.py](app/tests/test_http.py) | 7 | Real HTTP upload, correction, export, acceptance, demo and unavailable-AI paths. |
| [test_replay.py](app/tests/test_replay.py) | 8 | Demo state, withheld outcomes, live metrics, historical fixtures and exception strictness. |
| **Total** | **81** | Browser script is additional. |

### test_rules_engine.py

Source: [test_rules_engine.py](app/tests/test_rules_engine.py). Exercises deterministic checks used during ordinary claims processing and demo uploads.


**EVV**

| Test method | What it verifies |
|---|---|
| `test_clean_claim_passes` | A valid scenario returns no denial codes for each of the three base-manual payers. |
| `test_no_verified_visit_is_d01` | Unverified-only time does not satisfy the EVV requirement and produces D01. |
| `test_visits_are_aggregated_before_rounding` | Three ten-minute visits become 30 minutes/two full units; billing three produces D09. |
| `test_northwind_eight_minute_boundary` | Northwind supports one unit at 22 minutes and two at 23 minutes. |
| `test_other_payers_need_complete_units` | Harbor/Summit require 30 rather than 29 minutes for two units. |


**Authorization**

| Test method | What it verifies |
|---|---|
| `test_missing_wrong_and_expired` | Missing authorization gives D07; wrong service/member or out-of-date coverage gives D08. |
| `test_balance_boundary` | An exact remaining-unit fit passes; one unit over gives D02. |


**FilingAndModifiers**

| Test method | What it verifies |
|---|---|
| `test_northwind_baseline_30_days` | Base-manual day 30 passes/day 31 fails; August remains subject to 30 days until an update is loaded. |
| `test_summit_60_days_and_harbor_none` | Summit day 60 passes/day 61 fails; Harbor has no supplied baseline filing limit. |
| `test_harbor_baseline_modifier_u2` | Base-manual T1019 requires U2, including weekends before applying the later bulletin. |
| `test_modifier_only_applies_to_harbor_t1019` | The Harbor requirement does not spill into Northwind or Harbor T1005. |


**DailyAndDuplicates**

| Test method | What it verifies |
|---|---|
| `test_harbor_daily_maximum` | Harbor permits a total of 32 units and rejects 33; Northwind has no corresponding cap. |
| `test_duplicate_and_corrected_claim_exemption` | An earlier submission causes D05 for an original claim; a valid correction is exempt. |
| `test_all_failures_are_reported` | A multi-failure claim returns the entire expected code set, not one primary code. |


**VersionsAndValidation**

| Test method | What it verifies |
|---|---|
| `test_resolution_by_service_date` | Legacy versions switch at the service-date boundary; a missing payer/version resolves to none. |
| `test_unknown_payer_needs_review_not_paid` | The engine returns manual review rather than payment when approved baseline rules are absent. |
| `test_params_validation_rejects_unknown_and_bad_values` | Legacy parameters reject unknown keys, negative limits, unsupported rounding and malformed modifier rules. |
| `test_add_version_merges_and_replaces_same_date` | Legacy versions inherit omitted parameters, preserve earlier dates, replace a same-date entry and reject an invalid limit. |
| `test_expectations_are_compared_as_code_sets` | Scenario execution compares the full actual code set with its expected set. |

### test_pipeline.py

Source: [test_pipeline.py](app/tests/test_pipeline.py). Exercises CSV/batch processing used at runtime, including reference-detail data for the dashboard.


**Normalisation**

| Test method | What it verifies |
|---|---|
| `test_canonical_forms` | Aliases, date formats, modifier casing and leading-zero units normalize to canonical values. |
| `test_unknown_values_are_flagged_not_guessed` | Unknown payers/services, invalid dates and invalid units are rejected by field normalization. |
| `test_idempotent` | Re-normalizing already cleaned values makes no further changes. |
| `test_changes_recorded_and_raw_preserved` | Normalization audits changed fields while retaining original uploaded values. |
| `test_missing_column_rejected` | A missing standard claim column prevents ingestion. |
| `test_blank_required_and_correction_without_original` | Missing required values and corrected claims without an original ID become Must correct. |
| `test_identical_rows_collapse_conflicting_rows_flagged` | Identical normalized exports are repeated rows; reused IDs with differing values flag both conflicting claims. |
| `test_missing_auth_is_a_denial_not_a_format_error` | A blank authorization stays checkable as D07 and can get a reviewed matching-authorization suggestion. |


**Ledger**

| Test method | What it verifies |
|---|---|
| `test_paid_history_consumes_authorization_denied_does_not` | Only earlier paid units consume the reference authorization balance. |
| `test_denied_history_still_makes_duplicates` | A denied submission establishes duplicate history but can be referenced by a corrected claim. |
| `test_batch_claims_consume_in_submission_order` | The earlier submitted claim uses balance first even when uploaded rows are reversed. |
| `test_decided_earlier_claims_use_the_ledger_without_double_counting` | Decided claims consume balance exactly once; later over-balance claims retain definite D02. |
| `test_undecided_earlier_claims_trigger_review_only_if_they_could_change_the_decision` | Earlier unresolved corrections produce a conditional balance only when relevant; fitting claims still pass and invalid/later-date cases are not treated as earlier exposure. |
| `test_user_override_changes_ledger` | Overriding an earlier paid prediction to denied frees its units for later claims while retaining the original prediction. |
| `test_outcomes_in_history_never_influence_a_batch_claim_directly` | An unrelated recorded denial is not copied into the uploaded claim prediction; history serves ledger/duplicate context. |


**Export**

| Test method | What it verifies |
|---|---|
| `test_original_columns_preserved_with_results_appended` | Export retains original aliases/dates and extra columns, then appends prediction and normalization audit fields. |


**ClaimReferenceDetails**

| Test method | What it verifies |
|---|---|
| `test_related_records_are_scoped_to_claim_and_balance_is_from_its_check` | EVV and coverage match the claim; each row retains the authorization balance at its own check, not the final batch balance. |
| `test_wrong_referenced_authorization_is_shown_with_mismatches_and_alternative` | A wrongly referenced authorization stays visible with payer/member/service mismatch labels and related correct coverage. |
| `test_unverified_and_overnight_visits_remain_visible_on_clock_in_date` | An overnight unverified visit is visible only on its clock-in date and does not inflate verified minutes. |
| `test_missing_references_have_empty_visits_and_useful_related_coverage` | No matching EVV gives an empty visit list; an unknown reference still leaves related authorization candidates visible. |

### test_drafts.py

Source: [test_drafts.py](app/tests/test_drafts.py). Exercises the rules-updater lifecycle and client contract. Tests use fake transport and explicitly defined expectations.


**Workflow**

| Test method | What it verifies |
|---|---|
| `test_chat_builds_validated_draft_and_drops_bad_examples` | A proposal installs code without payer/date routing metadata, drops a malformed example with a note, generates controls and records the conversation. |
| `test_deprecated_ai_scope_fields_are_not_applied` | AI output using old parameter/routing fields is flagged instead of silently changing the draft parameters. |
| `test_clarifying_question_leaves_draft_empty` | A clarifying reply remains a conversation without an activatable rule update. |
| `test_acceptance_needs_no_per_example_decisions_but_respects_explicit_expectations` | A healthy candidate is immediately eligible; explicitly setting a conflicting expected code blocks it until the expectation matches. |
| `test_activate_saves_version_and_acceptance_tests_and_changes_outcomes_by_date` | Acceptance saves the global code and explicit examples, changes affected execution, and preserves the earlier legacy baseline. |
| `test_single_acceptance_saves_changed_actual_outputs_with_distinct_provenance` | Whole-rule acceptance needs no individual decisions and saves only changed actual outputs as accepted_with_rule baselines. |
| `test_impact_on_history_shows_changed_outcomes_for_affected_dates_only` | Historical impact lists only Northwind claims on/after the intended boundary whose filing codes change. |
| `test_regression_against_saved_tests_needs_a_reason` | A later update conflicting with saved examples cannot be accepted without a reason; supersession retains that reason. |
| `test_unclear_examples_are_recorded_not_saved_as_tests` | The explicit decision API records payer-clarification cases on the version without promoting them to confirmed expected-output tests. |
| `test_persistence_round_trip` | Saving/reloading the rule snapshot preserves its contents. |


**Client**

| Test method | What it verifies |
|---|---|
| `test_no_key_means_unavailable_and_error_is_explicit` | The client reports unavailable and raises a clear error when no key/test transport is supplied. |
| `test_request_is_forced_tool_call_with_context_and_no_key_in_payload` | Inspects model context, schema and credential exclusion. Despite the legacy name, the asserted tool_choice is auto, not a forced tool selection. |

### test_code_rules.py

Source: [test_code_rules.py](app/tests/test_code_rules.py). Exercises generated-code validation/execution used by draft evaluation and later claims. The whole-hour contract is a test fixture, not a supplied payer policy.


**ExecutableRules**

| Test method | What it verifies |
|---|---|
| `test_novel_rule_actual_output_overrides_incorrect_ai_prediction` | A synthetic whole-hour rule actually returns D10 even when AI expected none; an explicit wrong confirmation blocks acceptance, and saved code respects date/payer guards after reload. |
| `test_revision_compares_with_accepted_rules_and_makes_one_call` | A follow-up uses one model request, clears earlier decisions and compares its candidate against accepted rules, not the preceding draft. |
| `test_invalid_code_and_runtime_failure_block_activation` | Imports, malformed outputs, missing data access and infinite loops prevent acceptance. |
| `test_io_and_introspection_are_rejected` | Unavailable I/O/import/introspection operations cannot successfully execute as rule code. |
| `test_worker_does_not_mutate_host_inputs_and_preserves_other_failures` | The worker returns a new result without changing caller data and retains an unrelated D07 failure. |
| `test_full_app_includes_dated_bulletins` | Full startup rules include the supplied 45-day Northwind and weekend-U3 Harbor changes at their dates. |
| `test_code_context_excludes_recorded_labels` | Historical status and denial-code sentinel labels are absent from the AI context. |
| `test_malformed_code_examples_block_acceptance` | A bad scenario alongside valid examples still produces blocking problems for a code proposal. |
| `test_memory_failure_does_not_damage_app_or_next_call` | An oversized allocation fails inside the worker; a subsequent valid call still succeeds. |
| `test_execution_error_on_history_blocks_acceptance` | Code that passes synthetic examples but fails on real loaded history is rejected through historical-impact error checking. |

### test_global_updates.py

Source: [test_global_updates.py](app/tests/test_global_updates.py). Exercises the code-owned scope model used by all new AI proposals.


**GlobalCodeUpdates**

| Test method | What it verifies |
|---|---|
| `test_contract_has_no_scope_fields_and_examples_carry_their_own_payer` | AI schema/draft omit top-level scope fields; examples supply payer/date data and missing payer is rejected. |
| `test_one_acceptance_applies_two_payers_and_dates_and_preserves_controls` | One saved/reloaded update applies both bulletins, preserves earlier dates/other payers, and saves affected cases with whole-rule provenance. |
| `test_later_update_preserves_earlier_accepted_code_outside_its_own_date_condition` | A later October filing change overrides the earlier August change only within its own guard. |
| `test_legacy_saved_code_is_retained_alongside_new_global_updates` | Old snapshots without updates retain legacy scoped code alongside newly accepted global code. |
| `test_followup_replaces_only_the_pending_update` | Repeated drafting leaves accepted state untouched and adds only the final candidate when accepted. |

### test_http.py

Source: [test_http.py](app/tests/test_http.py). Exercises endpoints through real temporary localhost servers; this is API integration coverage rather than a browser test.


**UploadToResults**

| Test method | What it verifies |
|---|---|
| `test_full_flow` | Real HTTP upload returns expected categories, normalizations and duplicates; applying a fix/override changes later ledger results and CSV audit fields remain correct. |
| `test_bad_inputs_are_rejected_cleanly` | Missing columns/unknown lines/path traversal fail cleanly and status output does not disclose the key. |


**RuleUpdateByDate**

| Test method | What it verifies |
|---|---|
| `test_update_changes_only_affected_service_dates` | An AI draft accepted through HTTP changes only the intended dated Northwind fixture and is persisted as code. |
| `test_manual_edit_path_works_without_ai` | The legacy manual parameter-draft API works without AI and rejects an invalid parameter edit. |


**CodeAndDemo**

| Test method | What it verifies |
|---|---|
| `test_novel_rule_acceptance_rechecks_real_claims_and_exports_reasons` | One acceptance with no individual decisions re-checks a real uploaded row as D10, exports the reason and rejects repeat acceptance. |
| `test_demo_api_keeps_full_state_and_prunes_history` | Demo start/advance/end use isolated state; advance returns no checked batch/report, reference reload is blocked and full rules restore afterward. |


**NoKey**

| Test method | What it verifies |
|---|---|
| `test_chat_without_key_is_a_clear_error` | A real HTTP draft request returns 503 and an explicit no-key message when AI is unavailable. |

### test_replay.py

Source: [test_replay.py](app/tests/test_replay.py). Exercises both the monthly web-demo session logic and the separate three-stage regression replay.


**HistoricalDemo**

| Test method | What it verifies |
|---|---|
| `test_demo_prunes_history_and_restores_full_state_without_writing_it` | Demo state excludes future references, accepts a temporary rule, leaves the full-app file untouched and restores the original in-memory rules/history. |
| `test_advance_updates_data_and_upload_withholds_batch_outcomes` | The April start and June advance do not invoke the checker; a June upload withholds its labels, pins 354/361 agreement/seven D99 errors, and July advance admits June history while clearing results. |
| `test_rule_acceptance_recomputes_live_report_and_false_alarms` | Accepting Northwind updates the loaded September report from 26 to nine false alarms; mutating a comparison label changes metrics but not predictions. |
| `test_september_both_bulletins_match_verified_investigation` | The exact September cohort with both bulletin rules yields 805/834 matches and 29 documented differences; removing bulletins exposes unexpected failures. |
| `test_rejects_backwards_dates_and_reference_replacement` | Invalid/non-forward dates and manual reference replacement/reload are refused in demo mode. |
| `test_empty_and_invalid_advances_preserve_current_state` | Rejects an invalid calendar date without replacing the dataset. The legacy name does not imply that valid web advances need claims in the period. |
| `test_complete_replay_passes_only_with_exact_documented_exceptions` | Runs the complete scripted replay with source fixtures, preserves imperfect accuracy, and proves that deleting a known-exception entry causes failure. |
| `test_pristine_old_baseline_migrates_to_full_but_custom_rules_are_preserved` | Startup upgrades untouched base-only state to both bulletins but keeps a custom saved payer version. |


## Browser smoke coverage

[app/tests/browser_rules_chat.py](app/tests/browser_rules_chat.py) is a standalone
script, deliberately not named `test_*.py`. It is **not** one of the 81 discovered
methods. It uses Firefox WebDriver through the standard library, a temporary app
server/state directory and a fake novel-rule response.

In one terminal:

```bash
geckodriver --port 4445
```

In another, from the repository root:

```bash
python3 app/tests/browser_rules_chat.py --screenshots /tmp/rules-browser
```

`--driver http://127.0.0.1:4446` selects a driver on another port. Screenshots are
optional PNG artifacts; assertions, not image-diff baselines, determine success.
The script checks:

1. Initial chatbox and Send state, including mobile viewport visibility.
2. Empty attachment validation, preservation of the typed prompt and attachment
   removal without a model call.
3. Prompt-only, document-only and combined initial requests. Attaching a file does
   not send it; combined text/document content reaches the same model request.
4. A code-only proposal without top-level payer/effective-date routing fields.
5. Only changed synthetic examples appear in the main scenario output.
6. Raise issue attaches scenario context, and a follow-up can carry another
   Markdown document with its own correct chat-history preview.
7. Earlier replies remain visible; rule headings can also supply message context.
8. Every example action area has only Raise issue, and acceptance is available
   without individually confirmed decisions.
9. Desktop/mobile layouts do not overflow the page horizontally.
10. Acceptance keeps the composer available and re-checks results.
11. Sample-claims row clicks show EVV/authorization panels and their actual IDs.
12. Demo start clears the chat while keeping the composer visible; fast-forward
    changes the simulated date and leaves claims/report empty; returning restores
    full mode without an error banner.

The server and browser session are closed after the run; screenshots use the
requested output directory. The geckodriver process started separately remains
under the operator's control.

## Data fixtures and what they establish

| Fixture | Purpose |
|---|---|
| Inline claim/reference CSVs in `test_pipeline.py` | Small independent ledgers, explicit inputs and complete expected outputs. |
| `test_drafts.UPDATE` | Synthetic 50-day Northwind update for lifecycle tests; it is intentionally different from the supplied 45-day bulletin. |
| `test_code_rules.WHOLE_HOURS` | A synthetic, guarded novel rule demonstrating code beyond the old parameter vocabulary. |
| `demo_fixtures.py` | Repeatable Northwind/Harbor code and scenarios transcribed from supplied bulletins; never a silent fallback for real AI. |
| `app/sample_data/` | Full-app references used by integration and history-impact tests. |
| `demo-data/reference-data/` | Known historical outcomes and reference inputs for dated demo/replay checks. |
| `demo-data/claim-checking-csvs/` | Label-free monthly input files organized by inclusive submission-date range. |
| `demo-data/replay_exceptions.json` | Exact, inspected discrepancy signatures and documented reasons. |

Tests with fake transport prove application orchestration and handling of the
returned fixture. They do not prove Claude will produce the same rule, honor the
skill, use the tool, or remain compatible with the configured model name. A live
provider check is explicit, may cost money, and needs human review.

The legacy decision API remains tested for manually supplied expectations, even
though the current UI exposes only Raise issue and whole-rule acceptance.

## Existing coverage boundaries

- There is no measured fresh holdout or production evaluation.
- The worker tests cover important restrictions, malformed results, loops,
  memory failure and recovery; they are not a comprehensive adversarial sandbox
  audit, all timeout/crash permutations, or a Windows portability suite.
- HTTP tests cover real upload/edit/acceptance/export and path handling. They do
  not exhaust every endpoint, request-size limit, concurrency scenario or
  cross-origin/security configuration.
- The browser script covers its listed smoke path. It does not separately exercise
  every CSV drag/drop interaction, all override combinations, every accessibility
  interaction, or the entire monthly historical upload walkthrough in Firefox.
  Python session tests cover the important monthly comparison/label-withholding
  logic instead.
- Command-line helpers reuse tested engine/evaluation paths, but not every CLI
  argument/error path has a dedicated `unittest` method.
- Reference-field error reporting is not proof of complete referential integrity.
- Runtime examples/history are finite. A valid candidate can still fail on future
  data or encode the wrong business interpretation.

## Relationship to the original experiment

`experiment/` has a separate 22-test prototype suite, runnable on demand:

```bash
python3 -m unittest discover -s experiment -p 'test_*.py'
```

It covers the earlier cleaning/checking/pending-claim experiment and is **not**
part of the app's 81-method command, web demo or runtime validation. Its full runner
rebuilds experiment artifacts; it is not required to start the app. Consult
[experiment/README.md](experiment/README.md) and
[experiment_results.md](experiment_results.md) for that pipeline's setup and
metrics. Do not replace those results with the app's monthly/replay numbers.

For behavioural details behind these checks, see
[documentation.md](documentation.md); for the short user workflow, see
[README.md](README.md).
