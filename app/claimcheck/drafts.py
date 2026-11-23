"""Rule-update workflow: propose -> execute examples -> user raises issues or accepts the rule.

Single rule acceptance saves changed outputs as regression baselines. Explicitly
confirmed expectations retain their separate provenance; generated outputs are not proof of accuracy.
"""

import copy
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .code_rules import validate_code, RuleCodeError
from .normalize import CLAIM_FIELDS

from .examples import deterministic_boundary_examples, run_examples, validate_scenario
from .history import impact
from .rules import DEFAULT_PARAMS, RuleStore, valid_date, validate_params


class DraftError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def candidate_store(store, draft):
    cand = RuleStore()
    cand.restore(store.snapshot())
    if draft.get("code") is not None:
        cand.add_update(draft["code"], source=draft["source"], summary=draft["summary"])
    else:
        cand.add_version(draft["payer_id"], draft["effective_from"], draft["params"], source=draft["source"],
                         summary=draft["summary"], payer_name=draft.get("payer_name"))
    return cand


def _context_for_ai(store, dataset, draft):
    from . import engine
    import inspect
    lines = ["Accepted rules (all payers; these are the comparison baseline):", json.dumps(dict(payers=store.payers, updates=store.updates)),
             "Baseline checker implementation (your code transforms its failure list):", inspect.getsource(engine.check_claim)]
    if dataset.history:
        lines += ["Example clean claim rows (labels excluded):",
                  json.dumps([{k: r.get(k, "") for k in CLAIM_FIELDS} for r in dataset.history[:2]])]
    lines += ["Sample authorizations:", json.dumps(dataset.authorizations[:2]),
              "Sample EVV visits:", json.dumps(dataset.visits[:2])]
    source_dir = Path(__file__).resolve().parent.parent / "sample_data" / "payers"
    lines += ["Base payer manuals:", "\n".join(p.read_text() for p in sorted(source_dir.glob('*.md')))]
    if draft:
        lines += ["Current unaccepted draft:", json.dumps({k: draft.get(k) for k in
                  ("code", "summary", "proposal_errors")})]
    return "\n".join(lines)


def new_draft(payer_id="", effective_from="", params=None, source="", summary="", payer_name=""):
    return dict(id=uuid.uuid4().hex[:8], payer_id=payer_id, payer_name=payer_name, effective_from=effective_from,
                params=params or {}, source=source, summary=summary, examples=[], messages=[],
                open_questions=[], decisions={}, code=None, proposal_errors=[], status="draft", created_at=_now())


def apply_ai_proposal(draft, proposal):
    """Merge a (validated) AI proposal into the draft. Returns notes for the user."""
    if not isinstance(proposal, dict):
        raise DraftError("AI response must be a structured proposal")
    notes = []
    draft["proposal_errors"] = []
    if any(key in proposal for key in ("payer_id", "payer_name", "effective_from", "params")):
        draft["proposal_errors"].append("Put payer and service-date conditions inside code; do not return separate scope fields")
    if "code" in proposal:
        draft["code"] = proposal["code"]
        draft["params"] = {}
        for key in ("payer_id", "payer_name", "effective_from"):
            draft.pop(key, None)
        draft["decisions"] = {}
        try:
            validate_code(draft["code"])
        except RuleCodeError as exc:
            draft["proposal_errors"].append(str(exc))
    draft["open_questions"] = [proposal["clarifying_question"]] if proposal.get("clarifying_question") else []
    if proposal.get("summary"):
        draft["summary"] = proposal["summary"]
    if proposal.get("source_quote"):
        draft["source"] = proposal["source_quote"]
    ai_examples = []
    for i, ex in enumerate(proposal.get("examples") or []):
        if not isinstance(ex, dict):
            notes.append("Dropped malformed AI example: expected an object")
            if "code" in proposal:
                draft["proposal_errors"].append("AI example must be an object")
            continue
        s = dict(id=f"ai-{len(draft['messages'])}-{i}", title=ex.get("title", "Example"),
                 origin="ai", claim=ex.get("claim", {}), context=ex.get("context", {}),
                 expected_codes=ex.get("expected_codes"))
        if validate_scenario(s):
            notes.append(f"Dropped AI example {s['title']!r}: " + "; ".join(validate_scenario(s)))
            if "code" in proposal:
                draft["proposal_errors"].extend(validate_scenario(s))
        else:
            ai_examples.append(s)
    if "code" in proposal and not ai_examples:
        draft["proposal_errors"].append("A code update needs valid example rows; ask the assistant to supply them")
    if ai_examples:
        draft["examples"] = [e for e in draft["examples"] if e.get("origin") == "user"] + ai_examples
        draft["decisions"] = {}
    return notes


def refresh_boundary_examples(draft, store):
    keep = [e for e in draft["examples"] if e.get("origin") != "boundary"]
    if draft.get("code") is not None:
        # Controls span all known payers at sample dates. Applicability comes from
        # executing the code, never from extracting guessed scope metadata.
        dates = sorted({e["claim"]["date_of_service"] for e in keep})[:3]
        controls = []
        for payer in store.payers:
            for day in dates:
                version = store.resolve(payer, day)
                if not version:
                    continue
                for ex in deterministic_boundary_examples(payer, day, version["params"]):
                    ex["id"] = f"control-{payer}-{day}-{ex['id']}"
                    ex["title"] = f"{payer} · {day} · {ex['title']}"
                    ex["claim"]["payer"] = payer
                    controls.append(ex)
        draft["examples"] = keep + controls
        return
    if not (draft.get("payer_id") and valid_date(draft.get("effective_from"))) or validate_params(draft["params"]):
        return
    full = store.full_params(draft["payer_id"], draft["effective_from"], draft["params"])
    draft["examples"] = keep + deterministic_boundary_examples(draft["payer_id"], draft["effective_from"], full)


def chat(draft, store, dataset, ai, user_message):
    """One chat turn: send the conversation to the AI and update the draft."""
    messages = draft["messages"] + [{"role": "user", "content": user_message}]
    proposal = ai.propose(messages, _context_for_ai(store, dataset, draft))
    draft["messages"] = messages
    notes = apply_ai_proposal(draft, proposal)
    reply = proposal.get("assistant_message", "")
    if draft["open_questions"]:
        reply += "\n\nQuestion: " + draft["open_questions"][0]
    draft["messages"].append({"role": "assistant", "content": reply})
    refresh_boundary_examples(draft, store)
    return notes


def evaluate(draft, store, dataset):
    """Everything the user needs to review: rules diff, example results, regressions, history impact."""
    problems = list(draft.get("proposal_errors", [])) + list(draft.get("open_questions", []))
    if draft.get("code"):
        try:
            validate_code(draft["code"])
        except RuleCodeError as exc:
            problems.append(str(exc))
    if draft.get("code") is None and draft.get("params"):
        if not draft.get("payer_id"):
            problems.append("Payer not set for legacy parameter draft")
        if not valid_date(draft.get("effective_from")):
            problems.append("Effective date not set for legacy parameter draft")
    if not draft["params"] and not draft.get("code") and not problems:
        problems.append("No rule changes in this draft")
    ev = dict(problems=problems, examples=[], regressions=[], impact=None, can_activate=False, undecided=0)
    if problems:
        return ev
    try:
        cand = candidate_store(store, draft)
    except ValueError as exc:
        ev["problems"].append(str(exc))
        return ev
    before = {r["id"]: r for r in run_examples(draft["examples"], store)}
    after = run_examples(draft["examples"], cand)
    for r in after:
        dec = draft["decisions"].get(r["id"])
        r["before"] = before[r["id"]]["actual"]["codes"]
        r["before_status"] = before[r["id"]]["actual"]["status"]
        r["changed"] = (r["before_status"], r["before"]) != (r["actual"]["status"], r["actual"]["codes"])
        r["decision"] = dec
        final = None
        if dec and dec["decision"] in ("confirm", "set"):
            final = dec["codes"] if dec["decision"] == "set" else r["expected_codes"]
        r["final_expected"] = final
        r["matches_decision"] = None if final is None else sorted(final) == sorted(r["actual"]["codes"])
    ev["examples"] = after
    ev["undecided"] = sum(1 for r in after if r["changed"] and not r["decision"])
    mismatched = [r for r in after if r["matches_decision"] is False]
    saved = run_examples([t for t in store.acceptance if not t.get("superseded_by")], cand)
    ev["regressions"] = [t for t in saved if t["passed"] is False]
    ev["checks"] = dict(examples_run=len(after), unchanged_controls=sum(not r["changed"] for r in after), saved_tests=len(saved))
    ev["impact"] = impact(dataset, store, cand) if dataset.history else None
    errors = [r["actual"]["error"] for r in after if r["actual"].get("error")]
    errors += [r["actual"]["error"] for r in saved if r["actual"].get("error")]
    if ev["impact"]:
        errors += ev["impact"].get("errors", [])
    if errors:
        ev["problems"].extend(sorted(set(errors)))
    ev["can_activate"] = not (mismatched or errors)
    if mismatched:
        ev["problems"].append(f"{len(mismatched)} confirmed expectation(s) differ from the rule result; fix the rule or the expectation")
    return ev


def decide(draft, example_id, decision, codes=None, note=""):
    if decision not in ("confirm", "set", "unclear", "clear"):
        raise DraftError("decision must be confirm, set, unclear or clear")
    ex = next((e for e in draft["examples"] if e["id"] == example_id), None)
    if ex is None:
        raise DraftError("Unknown example")
    if decision == "clear":
        draft["decisions"].pop(example_id, None)
        return
    if decision == "confirm" and ex.get("expected_codes") is None:
        raise DraftError("This example has no proposed expectation; choose the codes yourself")
    if decision == "set":
        if not isinstance(codes, list) or not all(re.fullmatch(r"D\d{2}", c) for c in codes):
            raise DraftError("codes must be a list of denial codes ([] for payable)")
    draft["decisions"][example_id] = dict(decision=decision, codes=sorted(codes) if codes is not None else None, note=note)


def activate(draft, store, dataset, regression_reason=""):
    ev = evaluate(draft, store, dataset)
    if ev["problems"] and not ev["can_activate"]:
        raise DraftError("; ".join(ev["problems"]) or "Draft not ready")
    if not ev["can_activate"]:
        raise DraftError("Draft is not ready for acceptance")
    if ev["regressions"] and not regression_reason.strip():
        raise DraftError(f"{len(ev['regressions'])} saved example test(s) would change; "
                         "give the reason/source for changing earlier expectations")
    global_update = draft.get("code") is not None
    if global_update:
        version = store.add_update(draft["code"], source=draft["source"], summary=draft["summary"], approved_at=_now())
    else:
        version = store.add_version(draft["payer_id"], draft["effective_from"], draft["params"], source=draft["source"],
                                    summary=draft["summary"], payer_name=draft.get("payer_name") or None, approved_at=_now())
    if ev["regressions"]:
        # earlier expectations are superseded only with an explicit, recorded reason
        for t in ev["regressions"]:
            for old in store.acceptance:
                if old["id"] == t["id"]:
                    old["superseded_by"] = f"update-{version['version']}" if global_update else version["version"]
                    old["superseded_reason"] = regression_reason.strip()
    for r in ev["examples"]:
        d = r["decision"]
        basis = "user_confirmed"
        if not d:
            if not r["changed"]:
                continue
            # The single acceptance action approves the version as a whole. Keep
            # these snapshots distinct from independently confirmed expectations.
            d = dict(decision="set", codes=r["actual"]["codes"], note="Accepted with rule version")
            r["final_expected"] = list(r["actual"]["codes"])
            basis = "accepted_with_rule"
        if d["decision"] == "unclear":
            version.setdefault("open_questions", []).append(f"{r['title']}: {d.get('note') or 'payer clarification needed'}")
            continue
        store.acceptance.append(dict(
            id=f"{'update' if global_update else draft['payer_id']}-v{version['version']}-{r['id']}-{uuid.uuid4().hex[:4]}",
            title=r["title"], payer_id=r["payer_id"], claim=r["claim"], context=r["context"],
            expected_codes=r["final_expected"], version=version["version"], update_version=version["version"] if global_update else None,
            effective_from=draft.get("effective_from"),
            source=draft["source"], basis=basis, note=d.get("note", ""), confirmed_at=_now(),
            ai_proposed=r.get("origin") == "ai"))
    draft["status"] = "activated"
    store.save()
    return version
