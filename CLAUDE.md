# Project context

This is a synthetic take-home assessment for Exchange4Health: build an assistant
that helps home-care billing teams identify likely claim denials and corrections
before submission. The proposed product is a small web app where AI helps users
create and update payer rules, while claims are checked deterministically.

An exploratory experiment in `experiment/` implemented the supplied rules and
evaluated historical claims. It achieved 96.42% status-and-code-set accuracy;
remaining gaps involved missing contracts, unspecified denials and uncertain
authorization balances. A review step intercepted ten missed denials at the
cost of four extra reviews. The experiment provides supporting evidence and
reusable prototype code; rerunning it is not a prerequisite for building the app.

# Read first

1. `design.md` — intended architecture, user flows and testing approach.
2. `experiment_results.md` — findings, trade-offs and known limitations.
3. Assignment PDFs and the supplied data pack's README, billing basics and payer
   documents — original requirements and domain rules.
4. `experiment/README.md` — existing code and outputs, when relevant.

# Working instructions

Follow the user's current request and use `design.md` as the design reference;
distinguish implemented behaviour from proposals. Preserve supplied data and
keep scope appropriate for a short take-home demo. Return all applicable denial
codes with evidence, keep business corrections subject to user review, and
version rules by payer and effective service date. Validate rule updates with
reviewed examples; AI-generated expectations and review referrals are not proof
of accuracy. Never commit API keys.
