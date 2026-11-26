# Claim pre-check

A prototype web app for home-care billing teams to check claims before submission.
It combines deterministic claim checks with an AI assistant for creating and
updating payer rules. This is a demonstration using synthetic data; a “Likely
paid” result is not a payment guarantee.

The app can:

- Normalize claims CSVs and flag missing or invalid values.
- Return all detected denial codes, reasons and suggested corrections.
- Show relevant EVV visits, authorizations and uncertain authorization balances.
- Re-check user edits and classification overrides, then export the results.
- Propose rule code in chat, show actual before/after examples, and activate it
  only after the user accepts the update.
- Simulate historical data availability and compare predictions with recorded
  outcomes in a separate demo mode.

## Start the app

Use Python 3.10 or newer and a modern browser. The app uses the Python standard
library; there is no package installation or frontend build step. Generated rule
execution uses Unix process/resource facilities and is developed/tested on Linux.

From the repository root:

```bash
python3 app/server.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Add `--port 8001` if needed.
Sample authorizations, visits and claim history load automatically. Fresh full-app
state includes the supplied payer manuals and both dated bulletins.

To use the AI assistant, set `ANTHROPIC_API_KEY` in the environment **before**
starting the server. `ANTHROPIC_MODEL` can override the configured model. Checking
claims and running the fixture-based tests/replay do not require an API key.
Never put a real key in source files or documentation.

## Check claims and understand the results

1. In **Check claims**, upload/drop a claims CSV, or choose **Use sample claims**
   in full-app mode. Use [the sample CSV](app/sample_data/claims_to_check.csv) as
   the format reference; the detailed [input contract](documentation.md#input-data)
   lists every column. Reference data can be replaced in full-app mode.
2. Use the dashboard tiles to filter results. Yellow cells identify formatting
   changes or user edits; hover to see the uploaded value. Formatting changes are
   automatic. Uniquely supported payer, authorization and modifier values are
   filled automatically; ambiguous matches and uncertain balances need review.
   Use **Auto-fixed** to filter corrected rows and inspect each change’s source.
3. Click a row to see every failure, suggested actions, matching EVV visits and
   authorization records. Check the visit verification state and the authorization
   balance **before that claim**. Apply a proposed fix or edit a field, then save
   and re-check. A classification override affects that claim and subsequent
   balance calculations; it does not change payer rules.
4. Choose **Download results CSV**. Original uploaded values remain in their
   columns; additional columns record predictions, reasons, changes and overrides.
   `automatic_fixes` records each correction and `checked_claim` contains the
   complete corrected values used by the checker.

| Displayed result | What it means |
|---|---|
| **Likely paid** | No implemented rule violation was found with the available data. |
| **Likely denied** | At least one implemented rule failed; read all returned codes. |
| **Needs review** | The app cannot confidently classify the claim, for example because rules are unavailable or code could not execute. |
| **Check earlier claims** | Earlier unresolved submissions could leave insufficient authorization units. Reconcile them before submission. |
| **Must correct** | Input errors prevent checking, such as invalid dates or conflicting claim IDs. |
| **Repeated row** | Another row represents the same normalized claim; it is not processed twice. |

Denial codes describe failed checks: for example **D03** means late filing,
**D04** means a missing/invalid modifier, and **D10** means a payer contract-specific
denial. See the [complete code list](documentation.md#denial-codes). A claim can
have several codes. **Auto-formatted** is a filter, not a payment outcome.

## Create or update rules

1. Open **Payer rules**. The chatbox is available immediately. Type a prompt,
   paste Markdown, attach a `.md`/`.markdown` document, or combine a prompt and
   attachment. File selection only stages the attachment; click **Send** to submit.
   Enter sends and Shift+Enter adds a line.
2. Review the explanation and examples whose outcomes change. Before/after results
   come from executing the rules. A proposal can cover multiple payers and dates;
   its code contains the applicability conditions.
3. Use **Raise issue** on an example, click the rule heading, or type a follow-up
   request. You can attach more Markdown to a follow-up. Earlier replies remain
   in the conversation.
4. Check **Execution checks passed**, then approve the changed synthetic examples
   as a group. Expand any saved regressions and historical prediction changes.
   Historical changes require a separate group approval; changing history or saved
   expectations also requires a reason/source. Check payer and service dates carefully.
5. Click **Accept new rule** (or **Approve changes and accept rule**) once. No
   individual example approval is required. Current claims are re-checked and
   saved regression checks appear on the claims dashboard after every batch check.
   Revising a proposal or changing its comparison results requires fresh approval.


Accepted rules and example baselines persist in `app/state/rules.json` by default.
Claims, edits, reference-data replacements and conversations are session-local.
A browser refresh loses the displayed chat; stopping the server loses in-memory
session data. AI-proposed examples and acceptance are not independent evidence
of accuracy.

## Try the historical demo

```bash
python3 app/server.py --demo
```

Alternatively, click **Start historical demo** in the app.

1. The simulated date starts at **30 April 2026**, with base manuals only.
2. Use **Fast forward: update system data** to reach **30 June 2026**. This updates
   references and clears the previous claims batch; it does **not** run the checker.
3. Upload `demo-data/claim-checking-csvs/claims_2026-06-01_to_2026-06-30.csv`.
   The selected month's outcomes are held out of reference history.
4. Continue through later month ends and their matching CSVs. Upload the Northwind
   July bulletin and Harbor August bulletin from `demo-data/payer-updates/` through
   the rules chat. Acceptance automatically re-checks the loaded batch. Northwind's
   extension applies from **1 August service dates**; Harbor's weekend modifier
   change applies from **1 September service dates**.
5. The dashboard shows corrected claims. The demo score assesses the original
   submissions before automatic fixes, because those are what the payer processed.
   Read **DEMO - COMPARING WITH TRUE HISTORICAL RESULTS** for agreement, missed
   denials, false alarms and all mismatches. Documented exceptions still count as
   prediction errors. **Return to full app** restores its earlier server-side state.

CSV periods are **submission-date** ranges. The first Northwind-affected rows in
these monthly files are in September, so accepting that bulletin need not change
July or August's loaded predictions. See [the demo walkthrough](demo-data/README.md).

## Documentation and tests

- [documentation.md](documentation.md): architecture, data formats, rule execution,
  persistence, API, commands and limitations.
- [testing.md](testing.md): every implemented app test, automatic runtime checks,
  browser checks, historical replay and when each runs.
- [design.md](design.md): design rationale and original experiment results.
- [experiment_results.md](experiment_results.md): the separate exploratory experiment.

Run the app's developer tests from the repository root:

```bash
python3 -m unittest discover -s app/tests -t app
```
