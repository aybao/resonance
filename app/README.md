# Claim pre-check (demo app)

Web app for home-care billing teams: upload claims, see predicted denial codes, reasons and suggested fixes
before submission. **Claims are checked deterministically; AI only helps create and update payer rules**,
and nothing it proposes takes effect until a user reviews examples and activates it.

Standard library only (Python 3.10+; developed on 3.14). No install step.

## Run

```bash
cd app
python3 server.py            # http://127.0.0.1:8000  (--port N to change)
python3 -m unittest discover -s tests -t .   # 52 tests, ~8s
```

Optional AI rule assistant: `export ANTHROPIC_API_KEY=...` before starting (read from the environment
only; never echoed, logged or saved). Model defaults to `claude-sonnet-5-5`
(`ANTHROPIC_MODEL` overrides). The rules chat requires a key. Claims checks and manual rule drafts through the HTTP API work without one.

## Use

1. **Check claims**: reference data (authorizations, EVV, claim history) loads from `sample_data/`.
   Upload a claims CSV or click *Use sample claims*. Yellow cells were auto-normalised (hover for the original);
   filter to *Must correct* (blocked rows), *Likely denied*, *Needs review*. Click a row for every code with
   evidence, suggested actions, proposed fixes (applied only on click), field edits, and your own classification
   override (with a reason). Download the results CSV (original columns untouched + result columns).
2. **Payer rules**: rules are versioned per payer and apply by *date of service* (the baseline is the three base
   manuals only; no later updates are preloaded). Upload a Markdown payer document to start a single chat. Each assistant reply shows the proposed
   rule changes and only examples whose outcomes change, with before/after results. Click an example
   (including a changed historical claim) to attach its context to a message, or ask for a rule edit directly.
   Review affected examples (confirm / correct / "needs payer clarification"), then activate. Unchanged
   scenarios run in the background and require no decision; regressions against confirmed tests remain visible. Confirmed examples are saved as versioned acceptance tests (`state/rules.json`).

## Layout

`claimcheck/normalize.py` cleaning/validation · `rules.py` versioned rule vocabulary + store · `engine.py` checker,
ledger, pending-claim review · `pipeline.py` batch run/export · `examples.py` scenarios · `drafts.py` update workflow ·
`history.py` replay/impact · `ai.py` Claude client · `session.py` app state · `server.py` HTTP · `web/` UI.

## Known limits

- AI path is tested only with a fake transport; it has not been run against the live API.
- The rules chat was checked in headless Firefox with a fake AI: upload, changed examples, comments,
  revisions, review, activation, and desktop/mobile layouts. Automated tests cover the HTTP API end to end.
- Rule vocabulary is closed (EVV rounding, authorization, filing limit, modifier-by-weekday, daily maximum,
  duplicates, notes). Contract terms (Summit D10) and unspecified D99 denials can't be modelled; these remain misses.
- One draft = one payer version; multi-document uploads are done one document at a time.
- Single-user, in-memory session; only rules/acceptance tests persist. Payer aliases are a fixed list.
- Replaying history with the active rules gives 96.9% status+code-set agreement (observed-outcome ledger); this
  is not a holdout estimate. The 98.7%/97.3% figures in design.md came from the experiment's sequential setup.
- In-batch review (`check_previous_claims`) fires only for earlier claims that are themselves undecided; decided
  earlier claims use the ledger, so the experiment's review counts are not reproduced.
