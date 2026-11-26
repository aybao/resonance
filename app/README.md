# Claim pre-check

Home-care billing demo: check claims deterministically and use Claude to propose
payer rule code. The rules chat returns code and synthetic example rows in one
call per turn. A helper executes accepted and candidate rules on identical rows;
users can raise issues with changed examples or accept the whole update with
**Accept new rule**.

Python standard library only (3.10+; tested on 3.14/Linux). No install step.

## Run

From `app/`:

```bash
python3 server.py                        # http://127.0.0.1:8000
python3 server.py --demo                 # isolated base-manual state as of 2026-04-30
python3 server.py --demo --as-of 2026-07-31
python3 -m unittest discover -s tests -t .
```

`--port` and `--state-dir` select a different port or saved full-app state.
Fresh full-app state includes all three manuals, both dated bulletins and the
full sample history. An untouched legacy baseline is upgraded to that starting
state; custom saved rules and acceptance tests are retained. Versions apply by
payer and **service date**, including the 45-day Northwind limit from 1 August
2026 and Harbor's weekend U3 modifier from 1 September.

Set `ANTHROPIC_API_KEY` to enable the rules chat. The key is read from the
environment only; never echoed, logged or saved. `ANTHROPIC_MODEL` overrides the
existing default, `claude-sonnet-5-5`. Claims checking, replay fixtures and manual
rule drafts through the HTTP API work without a key. Live Claude behaviour has
not been verified; automated/browser tests use an injected test transport.

## Use

- **Check claims:** upload a CSV or use sample claims. Review normalization,
  every denial code and its evidence, uncertain balances and proposed business
  corrections. Edit fields or classifications, re-check, and export the original
  columns with results and an audit trail. Clicking a claim also shows its matching
  EVV visits (including verification state) and referenced/related authorization
  records, coverage dates, and the balance before that claim.
- **Payer rules:** type a prompt, paste Markdown, attach a Markdown document, or
  combine a prompt and attachment, then click **Send**. The composer is available
  immediately; attachments can be removed before sending. Read the proposed
  update and changed examples with actual before/after results. Click a rule or use **Raise issue** on an
  example to attach its context to a message, or request an edit directly. Every revision is compared with the
  accepted rules. Each example has a **Raise issue** button; individual example
  approval is not required. Unchanged controls run in the background. Conflicting
  saved expectations need an explicit reason/source before acceptance. New versions and example baselines persist in `state/rules.json`.
- **Historical demo:** starts on April 30, 2026 with base manuals. Use
  **Fast forward: update system data** to reach June 30, then upload the June CSV
  from `../demo-data/claim-checking-csvs/` into Check claims. Fast forward updates
  reference data and clears the previous batch; uploading runs the checker.
  The selected month is pending: June 30 has June visits/authorizations but only
  pre-June claim outcomes. July 31 adds June outcomes and holds out July claims.
  Continue with each dated monthly CSV after advancing to its end date.
  Compare July with the original rules, then upload/review Northwind's July bulletin;
  do the same for Harbor after August. Documents are in `../demo-data/payer-updates/`.
  The comparison card is labelled **DEMO - COMPARING WITH TRUE HISTORICAL RESULTS**.
  Outcomes from the uploaded period are excluded from prediction. Returning to
  the full app restores its saved rules, data and claim batch.

## AI skill and runtime checks

The app loads [payer-rule-updater/SKILL.md](skills/payer-rule-updater/SKILL.md)
as the AI's standard instructions, alongside accepted code/versions, base manuals,
label-free sample data, the baseline checker and the current proposal. The skill
includes the code contract, data formats and a worked Northwind update.

Novel rules do not require adding another parameter to the engine. Python
`apply_rule(claim, context, failures)` transforms the baseline failure list. The
host handles CSV input, ordering, historical state and output codes/reasons.
The AI returns code and example rows, without proposal-level `payer_id` or
`effective_from` fields. The function checks `claim["payer"]` and
`claim["date_of_service"]` itself, so one update can cover multiple payers and
dates. Accepted updates run in order after baseline checks; a new proposal adds
one transform and preserves earlier accepted updates. Legacy saved payer/date
versions and parameter-based manual drafts remain supported.

After every response the app validates code and examples, executes before/after
comparisons, runs saved tests, and reports historical impact. Acceptance rechecks
the exact candidate and rejects stale review approval. Changed synthetic examples
and changed historical predictions each need group approval. Historical changes
and saved regressions require a recorded reason/source. All changed historical
rows are available for review with payer and service dates. Invalid code/output, execution errors and conflicting
confirmed expectations block acceptance. Single rule acceptance saves changed
outputs as regression baselines with `accepted_with_rule` provenance, distinct
from explicitly confirmed expectations. Unresolved payer questions remain
explicit; AI-proposed expected results are not independent validation. Every
claims recheck also runs active saved regression examples and reports the results
on the claims dashboard.

Rule code runs in a separate worker without API keys, imports, I/O or
introspection, over JSON copies of the inputs. The supported Python subset has
memory, instruction and wall-time limits. Approved code that fails on a later
claim sends it to review instead of treating it as payable. This is a local,
single-user demo, not a production hostile-code execution service.

## On-demand checks and demos

From `app/`:

```bash
# All local tests, including real HTTP workflows; no API key.
python3 -m unittest discover -s tests -t .

# Complete historical bulletin replay using repeatable AI fixtures.
python3 replay_history.py --report /tmp/replay-report.json

# Same replay, paused between stages for presentation.
python3 replay_history.py --interactive

# Explicit live-AI integration/demo; prompts for human review before acceptance.
python3 replay_history.py --interactive --live-ai

# Execute/validate a JSON draft and its example rows; no AI call.
python3 check_examples.py /path/to/draft.json --rules state/rules.json

# Check CSV claims with approved rules, including code transforms.
python3 check_claims.py sample_data/claims_to_check.csv --rules state/rules.json --output /tmp/checked.csv

# Browser workflow check (Firefox + geckodriver required, no Python packages).
# First run geckodriver --port 4445 in another terminal.
python3 tests/browser_rules_chat.py --screenshots /tmp/rules-browser
```

The historical script starts with June 30 history, checks through July 31,
accepts the first bulletin, checks through August 31, accepts the second, then
checks through January 31. A whole batch is predicted before any of its labels
are revealed; only earlier periods seed the observed ledger. Submission date
proxies availability because adjudication dates are absent.

Replay checks pass only when discrepancies exactly match the inspected,
claim-specific [exception fixture](sample_data/replay_exceptions.md). Every
exception remains in the reported accuracy and mismatch list. The replay cannot
silently whitelist new failures. Different cutoffs can expose unexpected
mismatches. This is an inspected regression dataset, not a new holdout.

See [the testing schedule](../design.md#testing) for which checks run after a
response, at acceptance, during development, before demos or on demand. The full
developer suite and historical demo do not run on every message or CSV upload.

## Layout and limits

`claimcheck/normalize.py` input validation · `rules.py` dated versions · `engine.py`
claim checks/state · `code_rules.py` bounded code worker · `pipeline.py` batches and
CSV export · `examples.py` scenario builder · `drafts.py` review/acceptance ·
`ai.py` skill and Claude client · `replay_demo.py` dated demo state · `session.py`
app state · `server.py` HTTP · `web/` interface.

The data format, known payer aliases and example services remain fixed. Novel
rules can use the available fields/context, but missing contracts or unexplained
D99 denials still require evidence. Only full-app rules and acceptance tests
persist; conversations and claim batches are session-local. The original
experiment used a different split/state setup; its metrics are documented
separately in [experiment_results.md](../experiment_results.md).

## Automatic claim fixes

The checker automatically fills a blank payer when valid authorization records
identify one payer for that patient, service and date; a supplied authorization
reference must agree. It also applies uniquely matching authorization references
with sufficient balance and modifiers dictated by accepted rules. Explicit user
edits take precedence. Ambiguous/conflicting reference records, insufficient or
uncertain balances, units, amounts and service/submission dates remain for review.

Fixes run in submission order before the ledger records each claim. The dashboard
highlights them, exposes sources/reasons and provides an **Auto-fixed** filter.
CSV source columns stay unchanged; `automatic_fixes` holds the audit and
`checked_claim` holds the complete corrected row. In demo mode corrected rows are
shown, while accuracy compares the original submitted claims with payer outcomes.
