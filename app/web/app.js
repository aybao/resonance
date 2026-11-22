"use strict";
// All dynamic text goes through textContent (the h() helper) — never innerHTML.
const $ = (id) => document.getElementById(id);
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}
// replaceChildren stringifies null: drop empty children (conditional pieces) first.
const _replaceChildren = Element.prototype.replaceChildren;
Element.prototype.replaceChildren = function (...kids) { _replaceChildren.apply(this, kids.flat(Infinity).filter((k) => k != null && k !== false)); };
const CODES = ["D01","D02","D03","D04","D05","D06","D07","D08","D09","D10","D99"];
const LABEL = { payable: "Likely paid", denied: "Likely denied", review: "Needs review", must_correct: "Must correct", duplicate_row: "Repeated row" };
const STATUS_LABEL = { paid: "Likely paid", denied: "Likely denied", manual_review: "Needs review", check_previous_claims: "Check earlier claims", duplicate_row: "Repeated row" };

async function api(method, path, body, raw) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    if (typeof body === "string") { opts.body = body; opts.headers["Content-Type"] = "text/csv"; }
    else { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  }
  const res = await fetch(path, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
async function guard(fn) {
  const box = $("error");
  try { box.hidden = true; return await fn(); }
  catch (e) { box.textContent = e.message; box.hidden = false; window.scrollTo(0, 0); }
}
const codeChips = (codes) => (codes || []).map((c) => h("span", { class: "code" }, c));
const badge = (cls, text) => h("span", { class: "badge b-" + cls }, text);

// ---------- tabs / status ----------
document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("nav button").forEach((x) => x.classList.toggle("active", x === b));
  for (const t of ["claims", "rules"]) $("tab-" + t).hidden = t !== b.dataset.tab;
  if (b.dataset.tab === "rules") loadRules();
}));

async function loadStatus() {
  const s = await api("GET", "/api/status");
  $("ai-status").textContent = s.ai_available ? `AI rule assistant ready (${s.model})` : "AI rule assistant off: no API key (manual rule editing still works)";
  const d = s.data;
  const card = $("data-card");
  card.replaceChildren(
    h("h2", {}, "Reference data"),
    h("p", { class: d.authorizations && d.evv_visits ? "" : "bad" },
      `${d.authorizations} authorizations · ${d.evv_visits} EVV visits · ${d.history} past claims (history used for balances and duplicates)`),
    d.problems.length ? h("pre", {}, d.problems.join("\n")) : null,
    h("div", { class: "row" },
      h("button", { class: "secondary", onclick: () => guard(async () => { await api("POST", "/api/data/sample"); await loadStatus(); await refreshResults(); }) }, "Reload sample data"),
      ...[["authorizations", "authorizations"], ["evv", "EVV visits"], ["history", "claim history"]].map(([kind, label]) =>
        h("label", {}, "Replace " + label, h("input", { type: "file", accept: ".csv", onchange: (e) => uploadRef(kind, e.target) })))));
}
function readFile(input) { return new Promise((res) => { const f = input.files[0]; if (!f) return res(null); const r = new FileReader(); r.onload = () => res(r.result); r.readAsText(f); }); }
async function uploadRef(kind, input) {
  const text = await readFile(input); if (text == null) return;
  await guard(async () => { await api("POST", "/api/data/" + kind, text); await loadStatus(); await refreshResults(); });
  input.value = "";
}

// ---------- claims ----------
let results = null, filter = "all", selectedLine = null;
$("claims-file").addEventListener("change", async (e) => {
  const text = await readFile(e.target); if (text == null) return;
  await guard(async () => { results = await api("POST", "/api/claims", text); selectedLine = null; filter = "all"; render(); });
});
$("use-sample-claims").addEventListener("click", () => guard(async () => { results = await api("POST", "/api/claims/sample"); selectedLine = null; filter = "all"; render(); }));
async function refreshResults() { const r = await api("GET", "/api/results"); if (r.rows.length) { results = r; render(); } }

function render() {
  $("results").hidden = false; $("export").hidden = false;
  const by = results.summary.by_category;
  const tiles = [["all", "All claims", results.summary.total], ["denied", "Likely denied", by.denied || 0], ["review", "Needs review", by.review || 0],
    ["must_correct", "Must correct", by.must_correct || 0], ["formatted", "Auto-formatted", results.summary.formatted_rows], ["payable", "Likely paid", by.payable || 0]];
  $("tiles").replaceChildren(...tiles.map(([k, label, n]) =>
    h("button", { class: "tile" + (filter === k ? " on" : ""), onclick: () => { filter = k; render(); } }, h("b", {}, n), label)));
  const rows = results.rows.filter((r) => filter === "all" || (filter === "formatted" ? r.changes.length : r.category === filter));
  $("filters").replaceChildren(h("span", { class: "muted" }, `${rows.length} shown. Yellow cells were normalised automatically (hover for the original value).`),
    results.summary.by_code && Object.keys(results.summary.by_code).length ? h("span", { class: "muted" }, " Codes: " + Object.entries(results.summary.by_code).map(([c, n]) => `${c}×${n}`).join("  ")) : null);
  const cols = ["line", "claim_id", "payer", "patient_id", "service_code", "date_of_service", "submitted_date", "units", "auth_id", "modifier"];
  const table = $("claims-table");
  table.replaceChildren(h("tr", {}, ...["Line", "Claim", "Payer", "Patient", "Service", "Service date", "Submitted", "Units", "Auth", "Mod", "Result", "Codes", "Why"].map((t) => h("th", {}, t))),
    ...rows.slice(0, 1500).map((r) => {
      const changed = Object.fromEntries(r.changes.map((c) => [c.field, c.before]));
      const edited = r.edits || {};
      return h("tr", { class: "click" + (r.line === selectedLine ? " sel" : ""), onclick: () => { selectedLine = r.line; render(); showDetail(r.line); } },
        ...cols.map((c) => c === "line" ? h("td", {}, r.line) : h("td", { class: c in changed || c in edited ? "fixed" : "", title: c in edited ? `edited by you (uploaded: ${r.raw[c]})` : c in changed ? `was: ${changed[c]}` : null }, r.clean[c])),
        h("td", {}, badge(r.override ? r.status : r.status === "check_previous_claims" || r.status === "manual_review" ? "review" : r.status, (r.override ? "You: " : "") + (r.category === "must_correct" ? "Must correct" : STATUS_LABEL[r.status] || r.status))),
        h("td", {}, codeChips(r.denial_codes)), h("td", { class: "reason" }, r.summary));
    }));
  if (rows.length > 1500) table.append(h("tr", {}, h("td", { colspan: 13 }, "Showing first 1500 rows; use filters or the CSV download.")));
  if (selectedLine != null) showDetail(selectedLine, true);
}

function showDetail(line, keepScroll) {
  const r = results.rows.find((x) => x.line === line); if (!r) return;
  const d = $("detail"); d.hidden = false;
  const kids = [h("h2", {}, `Claim ${r.claim_id || "(no ID)"} — line ${r.line}`),
    h("p", {}, badge(r.status === "paid" ? "payable" : r.status, STATUS_LABEL[r.status] || r.status), " ", codeChips(r.denial_codes),
      r.override ? h("span", { class: "muted" }, ` (your classification; model predicted ${STATUS_LABEL[r.predicted_status] || r.predicted_status} ${r.predicted_codes.join(", ")})`) : null)];
  for (const i of r.issues) kids.push(h("div", { class: i.severity === "error" ? "failure" : "warning" }, h("b", {}, i.field + ": "), i.message,
    i.severity === "error" ? h("div", { class: "muted small" }, "Edit this value below, then the batch is re-checked.") : null));
  for (const f of r.failures) kids.push(h("div", { class: "failure" }, h("b", {}, f.code + " — " + f.meaning), h("div", {}, f.reason),
    h("div", { class: "muted" }, "Suggested action: " + f.suggestion), h("div", { class: "muted small" }, "Rule: " + f.rule)));
  if (r.status === "check_previous_claims") kids.push(h("div", { class: "warning" }, h("b", {}, "Authorization balance uncertain"), h("div", {}, r.previous_claims_action),
    h("div", {}, `Earlier unresolved claims: ${r.unresolved_prior_claim_ids.join(", ")}. Balance could be ${r.balance_range[0]}–${r.balance_range[1]} units; this claim bills ${r.clean.units}.`)));
  for (const w of r.warnings) kids.push(h("div", { class: "warning" }, w));
  if (r.changes.length) kids.push(h("p", { class: "small" }, h("b", {}, "Normalised automatically: "), r.changes.map((c) => `${c.field}: "${c.before}" → "${c.after}"`).join("; ")));
  const pc = Object.entries(r.proposed_changes || {});
  if (pc.length) kids.push(h("div", {}, h("h3", {}, "Proposed fixes (need your confirmation)"), ...pc.map(([field, p]) =>
    h("div", { class: "row" }, h("span", {}, h("b", {}, field), " → ", h("span", { class: "fix" }, p.value), " — " + p.reason),
      h("button", { onclick: () => applyEdit(r.line, { [field]: p.value }) }, "Apply fix")))));
  // manual correction of fields
  const editable = ["payer", "patient_id", "service_code", "date_of_service", "submitted_date", "units", "auth_id", "modifier", "frequency_code", "original_claim_id"];
  const inputs = {};
  kids.push(h("h3", {}, "Correct claim fields"), h("div", { class: "row" }, ...editable.map((f) => {
    inputs[f] = h("input", { size: f === "date_of_service" || f === "submitted_date" ? 11 : 9, value: r.edits && f in r.edits ? r.edits[f] : r.raw[f] });
    return h("label", {}, f, inputs[f]);
  }), h("button", { onclick: () => {
    const fields = {}; editable.forEach((f) => { const cur = r.edits && f in r.edits ? r.edits[f] : r.raw[f]; if (inputs[f].value !== cur) fields[f] = inputs[f].value; });
    applyEdit(r.line, fields);
  } }, "Save and re-check"), Object.keys(r.edits || {}).length ? h("button", { class: "secondary", onclick: () => guard(async () => { results = await api("POST", `/api/claims/line/${r.line}/revert`, {}); render(); }) }, "Undo my edits") : null));
  if (r.claim_id && r.category !== "duplicate_row") kids.push(overrideForm(r));
  kids.push(h("details", {}, h("summary", {}, "Evidence"), h("pre", {}, JSON.stringify(r.evidence, null, 2))));
  d.replaceChildren(...kids);
  if (!keepScroll) d.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
function applyEdit(line, fields) { if (!Object.keys(fields).length) return; guard(async () => { results = await api("POST", `/api/claims/line/${line}/edit`, { fields }); render(); }); }

function overrideForm(r) {
  const ov = r.override;
  const sel = h("select", {}, ...[["", "No override (use model)"], ["paid", "Treat as paid"], ["denied", "Treat as denied"], ["manual_review", "Needs review"]].map(([v, t]) => h("option", { value: v, selected: (ov ? ov.status : "") === v }, t)));
  const boxes = CODES.map((c) => h("label", { style: "display:inline-block;margin-right:8px" }, h("input", { type: "checkbox", value: c, style: "display:inline", checked: ov && ov.denial_codes.includes(c) }), " " + c));
  const note = h("input", { size: 40, placeholder: "Reason / source (kept in the export)", value: ov ? ov.note : "" });
  return h("div", {}, h("h3", {}, "Your classification"), h("p", { class: "muted small" }, "Overrides this claim only; it does not change payer rules. Later claims use your decision for balances."),
    h("div", { class: "row" }, sel, ...boxes), h("div", { class: "row" }, note, h("button", { onclick: () => guard(async () => {
      const codes = boxes.map((b) => b.firstChild).filter((i) => i.checked).map((i) => i.value);
      results = sel.value ? await api("POST", `/api/claims/${r.claim_id}/override`, { status: sel.value, denial_codes: codes, note: note.value })
        : await api("DELETE", `/api/claims/${r.claim_id}/override`);
      render();
    }) }, "Save classification")));
}

// ---------- rules ----------
async function loadRules() {
  const rv = await api("GET", "/api/rules");
  $("rule-versions").replaceChildren(...Object.entries(rv.payers).map(([pid, p]) =>
    h("div", {}, h("h3", {}, `${pid} — ${p.name}`), ...p.versions.map((v) => h("details", {},
      h("summary", {}, `v${v.version} · applies to service dates from ${v.effective_from} · ${v.summary || ""}`),
      h("div", { class: "muted small" }, "Source: " + v.source), v.open_questions ? h("div", { class: "warning" }, "Open: " + v.open_questions.join("; ")) : null,
      h("pre", {}, JSON.stringify(v.params, null, 2)))))));
  $("acceptance").replaceChildren(rv.acceptance.length ? h("table", {}, h("tr", {}, ...["Payer", "From", "Example", "Expected codes", "Basis"].map((t) => h("th", {}, t))),
    ...rv.acceptance.map((t) => h("tr", {}, h("td", {}, t.payer_id), h("td", {}, t.effective_from), h("td", {}, t.title + (t.superseded_by ? ` (superseded by v${t.superseded_by}: ${t.superseded_reason})` : "")),
      h("td", {}, t.expected_codes.length ? codeChips(t.expected_codes) : "payable"), h("td", {}, t.basis + (t.ai_proposed ? " (AI proposed)" : ""))))) : h("p", { class: "muted" }, "None yet. Confirm examples when activating a rule update."));
}
$("reset-rules").addEventListener("click", () => guard(async () => { if (confirm("Replace all rules and confirmed tests with the supplied-manual rules?")) { await api("POST", "/api/rules/reset"); await loadRules(); await refreshResults(); } }));
$("save-key").addEventListener("click", () => guard(async () => { await api("POST", "/api/ai/key", { key: $("api-key").value }); $("api-key").value = ""; await loadStatus(); }));
$("start-draft").addEventListener("click", () => guard(async () => {
  const message = $("new-request").value.trim(); if (!message) throw new Error("Enter the rule text or request first");
  showDraft(await api("POST", "/api/drafts", { message })); $("new-request").value = "";
}));
$("start-manual").addEventListener("click", () => guard(async () => showDraft(await api("POST", "/api/drafts", { payer_id: "PY01", effective_from: "", params: {}, source: "" }))));
$("run-accuracy").addEventListener("click", () => guard(async () => {
  const a = await api("GET", "/api/accuracy");
  $("accuracy").replaceChildren(h("p", {}, `${(a.accuracy * 100).toFixed(2)}% of ${a.claims} recorded claims matched (status and code set). Denials with their recorded code detected: ${a.actual_denied_code_detected}/${a.actual_denied}. Missed denials: ${a.missed_denials}. False alarms on paid claims: ${a.false_alarms}.`));
}));

function showDraft(view) {
  const d = view.draft, ev = view.evaluation;
  const root = $("draft");
  const f = (key, label, size) => { const i = h("input", { value: d[key], size }); i.dataset.key = key; return h("label", {}, label, i); };
  const params = h("textarea", { rows: 10, class: "mono" }); params.value = JSON.stringify(d.params, null, 2);
  const fields = h("div", { class: "row" }, f("payer_id", "Payer ID", 7), f("payer_name", "Payer name", 22), f("effective_from", "Applies to service dates from (YYYY-MM-DD)", 12), f("source", "Source / evidence", 40));
  const save = () => guard(async () => {
    const body = { params: JSON.parse(params.value) };
    fields.querySelectorAll("input").forEach((i) => (body[i.dataset.key] = i.value));
    showDraft(await api("PUT", `/api/drafts/${d.id}`, body));
  });
  const chatBox = h("div", { class: "chat" }, d.messages.length ? d.messages.map((m) => h("div", { class: "msg " + m.role }, (m.role === "user" ? "You: " : "AI: ") + m.content)) : h("span", { class: "muted" }, "No AI conversation yet."));
  const msg = h("input", { size: 70, placeholder: "Ask for a change, e.g. “apply this only to T1019” or “make it 50 days”" });
  const chatRow = h("div", { class: "row" }, msg, h("button", { onclick: () => guard(async () => { if (!msg.value.trim()) return; showDraft(await api("POST", `/api/drafts/${d.id}/chat`, { message: msg.value })); }) }, "Send to AI"));
  const reason = h("input", { size: 50, placeholder: "Reason / source for changing earlier confirmed expectations" });
  const kids = [h("div", { class: "card" }, h("h2", {}, `Draft rule version${d.summary ? " — " + d.summary : ""}`),
    view.notes.length ? h("div", { class: "warning" }, view.notes.join("\n")) : null, chatBox, chatRow,
    h("h3", {}, "Rule version (edit by hand if needed)"), fields,
    h("p", { class: "muted small" }, "params are merged over the version in force at that date. Only listed keys change."), params,
    h("button", { class: "secondary", onclick: save }, "Apply edits and re-run examples")),
  ];
  if (ev.problems.length) kids.push(h("div", { class: "warning" }, ev.problems.join("\n")));
  if (ev.examples.length) kids.push(h("div", { class: "card" }, h("h2", {}, "Examples — confirm the correct outcome for each"),
    h("p", { class: "muted small" }, "AI-proposed expectations are suggestions, not proof. Confirm, correct or mark as needing payer clarification; confirmed examples become saved tests."),
    ...ev.examples.map((e) => exampleCard(d, e))));
  if (ev.impact) {
    const i = ev.impact;
    kids.push(h("div", { class: "card" }, h("h2", {}, "Impact on recorded history"),
      h("p", {}, `${i.changed_count} historical claim outcomes would change; ${i.now_correct} would match their recorded outcome and ${i.now_wrong} would no longer match. Accuracy ${(i.accuracy_before.accuracy * 100).toFixed(2)}% → ${(i.accuracy_after.accuracy * 100).toFixed(2)}%.`),
      i.examples.length ? h("table", {}, h("tr", {}, ...["Claim", "Payer", "Service date", "Before", "After", "Recorded"].map((t) => h("th", {}, t))),
        ...i.examples.map((c) => h("tr", {}, h("td", {}, c.claim_id), h("td", {}, c.payer), h("td", {}, c.date_of_service), h("td", {}, c.before.status, " ", codeChips(c.before.codes)),
          h("td", {}, c.after.status, " ", codeChips(c.after.codes)), h("td", {}, c.actual.status, " ", c.actual.code)))) : null));
  }
  if (ev.regressions.length) kids.push(h("div", { class: "failure" }, h("b", {}, `${ev.regressions.length} previously confirmed test(s) would change:`),
    ...ev.regressions.map((t) => h("div", {}, `${t.title}: expected ${t.expected_codes.join(", ") || "payable"}, now ${t.actual.codes.join(", ") || "payable"}`)), reason));
  kids.push(h("div", { class: "card" }, h("button", { disabled: !ev.can_activate, onclick: () => guard(async () => {
    await api("POST", `/api/drafts/${d.id}/activate`, { regression_reason: reason.value });
    root.replaceChildren(h("div", { class: "card ok" }, "Rule version activated and saved. Current claim results were re-checked."));
    await loadRules(); await refreshResults(); }) }, "Activate this rule version"),
    h("span", { class: "muted" }, ev.undecided ? `  ${ev.undecided} example(s) still need a decision` : "")));
  root.replaceChildren(...kids);
}

function exampleCard(d, e) {
  const c = e.claim, x = e.context, act = e.actual.codes;
  const decide = (decision, codes) => guard(async () => showDraft(await api("POST", `/api/drafts/${d.id}/decide`, { example_id: e.id, decision, codes, note: "" })));
  const dec = e.decision;
  const sel = CODES.map((k) => h("label", { style: "display:inline-block;margin-right:6px" }, h("input", { type: "checkbox", value: k, style: "display:inline", checked: dec && dec.codes && dec.codes.includes(k) }), " " + k));
  const cls = e.matches_decision === false ? " fail" : dec ? " done" : "";
  return h("div", { class: "example" + cls }, h("b", {}, e.title), h("span", { class: "muted small" }, ` [${e.origin || "example"}]`),
    h("div", { class: "small muted" }, `Claim: ${c.service_code} on ${c.date_of_service}, submitted ${c.submitted_date}, ${c.units} units, modifier ${c.modifier || "none"}${c.frequency_code === "7" ? ", corrected claim" : ""}. `
      + `Context: EVV ${(x.evv_minutes || []).join("+") || "none"} min${x.unverified_evv_minutes ? ` (+${x.unverified_evv_minutes.join("+")} unverified)` : ""}, auth ${x.auth || "valid"}${x.auth_units_remaining != null ? ` (${x.auth_units_remaining} units left)` : ""}`
      + `${x.daily_paid_units ? `, ${x.daily_paid_units} units already paid that day` : ""}${x.prior_submission ? ", earlier submission exists" : ""}.`),
    h("div", {}, "AI proposed: ", e.expected_codes == null ? h("span", { class: "muted" }, "nothing (your call)") : e.expected_codes.length ? codeChips(e.expected_codes) : "payable",
      "   Before update: ", (e.before.length ? codeChips(e.before) : "payable"), "   Rule now gives: ", act.length ? codeChips(act) : "payable", e.changed ? h("b", {}, "  (changed)") : null),
    e.actual.reasons.length ? h("div", { class: "muted small" }, e.actual.reasons.join(" | ")) : null,
    h("div", { class: "row" },
      e.expected_codes != null ? h("button", { class: "secondary", onclick: () => decide("confirm") }, "Confirm AI's expectation") : null,
      h("button", { class: "secondary", onclick: () => decide("set", act) }, "Rule result is correct"),
      h("span", {}, "or set: ", sel, h("button", { class: "secondary", onclick: () => decide("set", sel.map((l) => l.firstChild).filter((i) => i.checked).map((i) => i.value)) }, "Set (none ticked = payable)")),
      h("button", { class: "secondary", onclick: () => decide("unclear") }, "Needs payer clarification")),
    dec ? h("div", { class: dec.decision === "unclear" ? "warning" : e.matches_decision ? "ok" : "bad" },
      dec.decision === "unclear" ? "Marked as needing payer clarification (not saved as a test)" : `Your expectation: ${(e.final_expected || []).join(", ") || "payable"} — ${e.matches_decision ? "rule agrees" : "rule DISAGREES: edit the rule or the expectation"}`) : null);
}

guard(async () => { await loadStatus(); await refreshResults(); });
