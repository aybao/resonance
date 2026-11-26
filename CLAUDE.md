# Project context

This is a synthetic take-home assessment for Exchange4Health: build an assistant
that helps home-care billing teams identify likely claim denials and corrections
before submission. The proposed product is a small web app where AI helps users
create and update payer rules, while claims are checked deterministically.

The experiment in `experiment/` applies the supplied rules and forecasts earlier
unknown outcomes into a provisional ledger. It achieves 97.34% agreement with
recorded status/denial codes on the inspected synthetic cohort, with 23 missed
denials and two false denials. Predicted usage stays distinct from confirmed
payments. Invalid inputs and execution failures return Unable to check. Rerunning
the experiment is not a prerequisite for ordinary app work.

# Read first

1. `plans/design.md` — intended architecture, user flows and testing approach.
2. `plans/experiment_results.md` — findings, trade-offs and known limitations.
3. Assignment PDFs and the supplied data pack's README, billing basics and payer
   documents — original requirements and domain rules.
4. `experiment/README.md` — existing code and outputs, when relevant.

# Working instructions

Follow the user's current request and use `plans/design.md` as the design reference;
distinguish implemented behaviour from proposals. Preserve supplied data and
keep scope appropriate for a short take-home demo. Return all applicable denial
codes with evidence, keep unsupported business corrections subject to explicit user edits, and
version rules by payer and effective service date. Validate rule updates with
reviewed examples; AI-generated expectations and provisional predictions are not proof
of accuracy. Never commit API keys.
