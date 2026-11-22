"""Rule-update workflow: propose -> test and show impact -> user confirms examples -> activate.

AI-proposed expectations are *proposals*. Only expectations a user confirms become acceptance tests.
"""

import copy
import json
import re
import uuid
from datetime import datetime, timezone

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
    cand.add_version(draft["payer_id"], draft["effective_from"], draft["params"], source=draft["source"],
                     summary=draft["summary"], payer_name=draft.get("payer_name"))
    return cand


def _context_for_ai(store, dataset, draft):
    lines = ["Current rules (all payers, by version):", json.dumps(
        {pid: [dict(effective_from=v["effective_from"], params=v["params"]) for v in p["versions"]]
         for pid, p in store.payers.items()}, indent=1)]
    if dataset.history:
        lines += ["Example clean claim rows (shape only):", json.dumps(dataset.history[:2])]
    if draft:
        lines += ["Current draft:", json.dumps({k: draft[k] for k in ("payer_id", "effective_from", "params", "summary")})]
    return "\n".join(lines)


def new_draft(payer_id="", effective_from="", params=None, source="", summary="", payer_name=""):
    return dict(id=uuid.uuid4().hex[:8], payer_id=payer_id, payer_name=payer_name, effective_from=effective_from,
                params=params or {}, source=source, summary=summary, examples=[], messages=[],
                open_questions=[], decisions={}, status="draft", created_at=_now())


def apply_ai_proposal(draft, proposal):
    """Merge a (validated) AI proposal into the draft. Returns notes for the user."""
    notes = []
    if proposal.get("clarifying_question"):
        draft["open_questions"] = [proposal["clarifying_question"]]
    else:
        draft["open_questions"] = []
    pid = (proposal.get("payer_id") or draft["payer_id"]).strip()
    if pid and not re.fullmatch(r"PY\d{2}", pid):
        notes.append(f"Ignored unusable payer id {pid!r}")
        pid = draft["payer_id"]
    eff = proposal.get("effective_from") or draft["effective_from"]
    if eff and not valid_date(eff):
        notes.append(f"Ignored invalid effective date {eff!r}")
        eff = draft["effective_from"]
    if proposal.get("params"):
        errs = validate_params(proposal["params"])
        if errs:
            notes.append("AI proposed invalid rules, not applied: " + "; ".join(errs))
        else:
            draft["params"] = proposal["params"]
    draft.update(payer_id=pid, effective_from=eff)
    if proposal.get("payer_name"):
        draft["payer_name"] = proposal["payer_name"]
    if proposal.get("summary"):
        draft["summary"] = proposal["summary"]
    if proposal.get("source_quote"):
        draft["source"] = proposal["source_quote"]
    ai_examples = []
    for i, ex in enumerate(proposal.get("examples") or []):
        s = dict(id=f"ai-{len(draft['messages'])}-{i}", title=ex.get("title", "Example"), payer_id=pid,
                 origin="ai", claim=ex.get("claim", {}), context=ex.get("context", {}),
                 expected_codes=ex.get("expected_codes"))
        if validate_scenario(s):
            notes.append(f"Dropped AI example {s['title']!r}: " + "; ".join(validate_scenario(s)))
        else:
            ai_examples.append(s)
    if ai_examples:
        draft["examples"] = [e for e in draft["examples"] if e.get("origin") == "user"] + ai_examples
        draft["decisions"] = {}
    return notes


def refresh_boundary_examples(draft, store):
    if not (draft["payer_id"] and valid_date(draft["effective_from"])) or validate_params(draft["params"]):
        return
    keep = [e for e in draft["examples"] if e.get("origin") != "boundary"]
    full = store.full_params(draft["payer_id"], draft["effective_from"], draft["params"])
    draft["examples"] = keep + deterministic_boundary_examples(draft["payer_id"], draft["effective_from"], full)


def chat(draft, store, dataset, ai, user_message):
    """One chat turn: send the conversation to the AI and update the draft."""
    draft["messages"].append({"role": "user", "content": user_message})
    proposal = ai.propose([{"role": m["role"], "content": m["content"]} for m in draft["messages"]],
                          _context_for_ai(store, dataset, draft if draft["params"] else None))
    notes = apply_ai_proposal(draft, proposal)
    reply = proposal.get("assistant_message", "")
    if draft["open_questions"]:
        reply += "\n\nQuestion: " + draft["open_questions"][0]
    draft["messages"].append({"role": "assistant", "content": reply})
    refresh_boundary_examples(draft, store)
    return notes


def evaluate(draft, store, dataset):
    """Everything the user needs to review: rules diff, example results, regressions, history impact."""
    problems = []
    if not draft["payer_id"]:
        problems.append("Payer not set")
    if not valid_date(draft["effective_from"]):
        problems.append("Effective date (by date of service) not set")
    if not draft["params"] and not problems:
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
    ev["regressions"] = [t for t in run_examples([t for t in store.acceptance if not t.get("superseded_by")], cand) if t["passed"] is False]
    ev["impact"] = impact(dataset, store, cand) if dataset.history else None
    ev["can_activate"] = not (ev["undecided"] or mismatched)
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
        raise DraftError(f"{ev['undecided']} example(s) still need your decision")
    if ev["regressions"] and not regression_reason.strip():
        raise DraftError(f"{len(ev['regressions'])} previously confirmed test(s) would change; "
                         "give the reason/source for changing earlier expectations")
    version = store.add_version(draft["payer_id"], draft["effective_from"], draft["params"], source=draft["source"],
                                summary=draft["summary"], payer_name=draft.get("payer_name") or None, approved_at=_now())
    if ev["regressions"]:
        # earlier expectations are superseded only with an explicit, recorded reason
        for t in ev["regressions"]:
            for old in store.acceptance:
                if old["id"] == t["id"]:
                    old["superseded_by"] = version["version"]
                    old["superseded_reason"] = regression_reason.strip()
    for r in ev["examples"]:
        d = r["decision"]
        if not d:
            continue  # Unchanged examples run in the background and need no user decision.
        if d["decision"] == "unclear":
            version.setdefault("open_questions", []).append(f"{r['title']}: {d.get('note') or 'payer clarification needed'}")
            continue
        store.acceptance.append(dict(
            id=f"{draft['payer_id']}-v{version['version']}-{r['id']}-{uuid.uuid4().hex[:4]}",
            title=r["title"], payer_id=draft["payer_id"], claim=r["claim"], context=r["context"],
            expected_codes=r["final_expected"], version=version["version"], effective_from=draft["effective_from"],
            source=draft["source"], basis="user_confirmed", note=d.get("note", ""), confirmed_at=_now(),
            ai_proposed=r.get("origin") == "ai"))
    draft["status"] = "activated"
    store.save()
    return version
