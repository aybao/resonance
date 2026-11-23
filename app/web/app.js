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
  $("ai-status").textContent = s.ai_available ? `AI rule assistant ready (${s.model})` : "AI rule assistant off: set ANTHROPIC_API_KEY to create or update rules";
  const mode = $("mode-tools");
  if (s.mode === "demo") {
    const date = h("input", { type: "date", value: s.demo.as_of < "2026-07-31" ? "2026-07-31" : s.demo.as_of < "2026-08-31" ? "2026-08-31" : s.demo.end, min: s.demo.as_of, "aria-label": "Advance demo through date" });
    mode.replaceChildren(badge("review", `Historical demo · ${s.demo.as_of}`), date,
      h("button", { class: "secondary", onclick: () => guard(async () => {
        const res = await api("POST", "/api/demo/advance", { date: date.value });
        results = res.results; selectedLine = null; filter = "all"; render(); await loadStatus(); await loadRules(); renderDemoReport(res.report);
      }) }, "Advance and check claims"),
      h("button", { class: "secondary", onclick: () => guard(async () => { await api("POST", "/api/demo/end"); clearRuleConversation(); await loadStatus(); await loadRules(); await refreshResults(); $("demo-report").hidden = true; }) }, "Return to full app"));
    if (s.demo.last_report) renderDemoReport(s.demo.last_report);
  } else mode.replaceChildren(h("button", { class: "secondary", onclick: () => guard(async () => {
    await api("POST", "/api/demo/start", { date: "2026-06-30" }); clearRuleConversation(); await loadStatus(); await loadRules(); await refreshResults();
  }) }, "Start historical demo"));
  const d = s.data;
  const card = $("data-card");
  card.replaceChildren(
    h("h2", {}, "Reference data"),
    h("p", { class: d.authorizations && d.evv_visits ? "" : "bad" },
      `${d.authorizations} authorizations · ${d.evv_visits} EVV visits · ${d.history} past claims (history used for balances and duplicates)`),
    d.problems.length ? h("pre", {}, d.problems.join("\n")) : null,
    h("div", { class: "row" },
      h("button", { class: "secondary", disabled: !!s.demo, onclick: () => guard(async () => { await api("POST", "/api/data/sample"); await loadStatus(); await refreshResults(); }) }, "Reload sample data"),
      ...[["authorizations", "authorizations"], ["evv", "EVV visits"], ["history", "claim history"]].map(([kind, label]) =>
        h("label", {}, "Replace " + label, h("input", { type: "file", accept: ".csv", disabled: !!s.demo, onchange: (e) => uploadRef(kind, e.target) })))));
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
async function refreshResults() {
  const r = await api("GET", "/api/results");
  if (r.rows.length) { results = r; render(); }
  else { results = null; selectedLine = null; $("results").hidden = true; $("export").hidden = true; }
}
function clearRuleConversation() {
  threads.clear(); currentView = null; selectExample(null);
  $("rules-conversation").querySelectorAll(".conversation-thread").forEach((node) => node.remove());
  $("rules-empty").hidden = false; $("rule-composer").hidden = true; $("rules-status").textContent = "";
}
function renderDemoReport(report) {
  const root = $("demo-report"); root.hidden = false;
  root.replaceChildren(h("h2", {}, `Historical replay · ${report.from_date} to ${report.through_date}`),
    h("p", {}, `${(report.accuracy * 100).toFixed(2)}% agreement across ${report.claims} claims · ${report.known_exceptions} known exceptions · ${report.unexpected_mismatches} unexpected mismatches · ${report.manual_review} need review · ${report.false_alarms} false alarms`),
    h("p", { class: report.passed ? "ok" : "bad" }, report.passed ? "Replay passed: all other rows match." : "Replay found unexpected mismatches. All mismatches are listed below."),
    h("p", { class: "small muted" }, "Batch labels were revealed after prediction. History advances by submission date because adjudication dates are unavailable. Use Payer rules to upload the next bulletin."),
    h("details", {}, h("summary", {}, `All mismatches (${report.mismatches.length})`), h("div", { class: "table-wrap" }, h("table", {},
      h("tr", {}, ...["Claim", "Predicted", "Recorded", "Basis"].map((text) => h("th", {}, text))),
      ...report.mismatches.map((r) => h("tr", {}, h("td", {}, r.claim_id), h("td", {}, `${r.status} ${r.codes.join(", ")}`), h("td", {}, `${r.actual_status} ${r.actual_code}`), h("td", { class: "reason" }, r.exception_reason || "Unexpected: " + r.remark)))))));
}

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

// ---------- rules: one conversation ----------
let activeRules = null, currentView = null, selectedExample = null, ruleBusy = false;
const threads = new Map();
const DENIAL_MEANING = { D01: "No verified EVV visit", D02: "Authorization units exceeded", D03: "Filing deadline exceeded", D04: "Missing or invalid modifier", D05: "Duplicate claim", D06: "Daily maximum exceeded", D07: "Authorization missing", D08: "Invalid authorization", D09: "Verified time exceeded", D10: "Contract requirement", D99: "Manual review" };
async function loadRules() { return guard(async () => { activeRules = await api("GET", "/api/rules"); }); }

// A small, safe Markdown renderer: document/model text is always added as text nodes.
function inline(text) {
  return String(text).split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part) =>
    part.startsWith("**") ? h("strong", {}, part.slice(2, -2)) : part.startsWith("`") ? h("code", {}, part.slice(1, -1)) : part);
}
function markdown(text) {
  const box = h("div", { class: "chat-prose" });
  let list = null;
  for (const line of String(text || "").split("\n")) {
    const item = line.match(/^\s*(?:[-*]|\d+\.)\s+(.+)/);
    if (item) {
      if (!list) { list = h("ul"); box.append(list); }
      list.append(h("li", {}, inline(item[1])));
    } else {
      list = null;
      if (line.trim()) box.append(h(/^#{1,6}\s/.test(line) ? "h3" : "p", {}, inline(line.replace(/^#{1,6}\s+/, ""))));
    }
  }
  return box;
}
function message(role, ...content) {
  return h("article", { class: "rule-message " + role }, h("div", { class: "message-author" }, role === "user" ? "You" : "Rule assistant"), h("div", { class: "message-body" }, ...content));
}
async function ruleTask(task, label) {
  if (ruleBusy) return;
  ruleBusy = true;
  $("rules-status").textContent = label;
  $("rules-conversation").setAttribute("aria-busy", "true");
  setRuleControls();
  try { return await task(); }
  catch (e) { $("rules-status").textContent = e.message; }
  finally {
    ruleBusy = false;
    $("rules-conversation").removeAttribute("aria-busy");
    setRuleControls();
  }
}
function setRuleControls() {
  $("rules-file").disabled = ruleBusy;
  $("send-rule-message").disabled = ruleBusy;
  $("rule-message").disabled = ruleBusy;
  document.querySelectorAll("#mode-tools button, #mode-tools input").forEach((node) => { node.disabled = ruleBusy; });
  document.querySelectorAll(".rule-actions button").forEach((b) => { b.disabled = ruleBusy || b.dataset.blocked === "true"; });
}
$("rules-file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (!file) return;
  ruleTask(async () => {
    if (!/\.(md|markdown)$/i.test(file.name)) throw new Error("Choose a .md or .markdown file.");
    const text = await file.text();
    if (!text.trim()) throw new Error("This Markdown file is empty.");
    const view = await api("POST", "/api/drafts", { message: text });
    threads.set(view.draft.id, { root: h("div", { class: "conversation-thread" }), turns: new Map(), filename: file.name });
    $("rules-empty").hidden = true;
    $("rules-conversation").append(threads.get(view.draft.id).root);
    selectExample(null);
    $("rule-message").value = "";
    showDraft(view);
    $("rules-status").textContent = "Document reviewed. The proposed rules are ready below.";
    event.target.value = "";
  }, "Reading document and checking affected examples…");
});
$("rule-composer").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = $("rule-message").value.trim();
  if (!text || !currentView || ruleBusy) return;
  const context = selectedExample ? "Regarding this example:\n" + JSON.stringify(selectedExample, null, 2) + "\n\n" : "";
  ruleTask(async () => {
    const view = await api("POST", `/api/drafts/${currentView.draft.id}/chat`, { message: context + text });
    // Store the short display text separately; the AI receives the full scenario.
    const thread = threads.get(view.draft.id);
    thread.nextUser = { text, title: selectedExample && selectedExample.title };
    selectExample(null);
    $("rule-message").value = "";
    showDraft(view);
    $("rules-status").textContent = "Rule update revised. Review the latest examples below.";
  }, "Updating the rule and re-checking examples…");
});
$("rule-message").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("rule-composer").requestSubmit(); }
});
function selectExample(example) {
  selectedExample = example;
  const box = $("example-context"); box.hidden = !example;
  box.replaceChildren(example ? [h("span", {}, "Commenting on: ", h("strong", {}, example.title)), h("button", { type: "button", class: "secondary", "aria-label": "Remove example from message", onclick: () => selectExample(null) }, "×")] : []);
  document.querySelectorAll(".example-select").forEach((b) => b.setAttribute("aria-pressed", String(!!example && b.dataset.exampleKey === example.key)));
  if (example) $("rule-message").focus();
}
function outcome(status, codes) {
  return h("div", { class: "outcome" }, badge(status === "paid" ? "payable" : status, STATUS_LABEL[status] || status),
    codes.length ? h("span", { class: "outcome-codes" }, codes.map((c) => h("span", { title: DENIAL_MEANING[c] || c }, codeChips([c]), " ", DENIAL_MEANING[c] || c))) : null);
}
const FIELD_LABELS = { timely_filing_days: "Filing limit (days)", daily_max_units: "Daily maximum (units)", modifier_rules: "Required modifiers", evv: "Visit verification", authorization: "Authorization", duplicates: "Duplicate checks", notes: "Notes" };
function ruleValue(value) {
  if (value == null) return "Not set";
  if (typeof value === "boolean") return value ? "Required" : "Not required";
  if (Array.isArray(value)) return value.length ? value.map((v) => typeof v === "object" ? `${v.service_code}: ${v.modifier}${v.days && v.days.length ? " (" + v.days.join(", ") + ")" : ""}` : v).join("; ") : "None";
  if (typeof value === "object") return Object.entries(value).map(([k, v]) => `${k.replaceAll("_", " ")}: ${ruleValue(v)}`).join("; ");
  return String(value).replaceAll("_", " ");
}
function ruleSummary(d) {
  const versions = activeRules?.payers[d.payer_id]?.versions || [];
  const prior = versions.filter((v) => v.effective_from <= d.effective_from).at(-1);
  const changes = Object.entries(d.params).filter(([key, value]) => JSON.stringify(prior?.params[key]) !== JSON.stringify(value));
  return h("section", { class: "rule-summary" }, h("div", { class: "rule-summary-heading" }, h("button", { type: "button", class: "rule-title-button", onclick: () => selectExample({ key: `${d.id}-rule`, title: d.summary || "Proposed rule update", payer_id: d.payer_id, effective_from: d.effective_from, params: d.params, code: d.code, source: d.source }) }, d.summary || "Proposed rule update", h("span", { class: "small muted" }, " · Click to comment")), badge("review", "Draft")),
    h("p", { class: "muted" }, `${d.payer_name || activeRules?.payers[d.payer_id]?.name || d.payer_id || "Payer to confirm"} · ${d.payer_id || ""} · Service dates from ${d.effective_from || "to be confirmed"}`),
    changes.length ? h("dl", { class: "rule-diff" }, ...changes.map(([key, value]) => h("div", {}, h("dt", {}, FIELD_LABELS[key] || key.replaceAll("_", " ")), h("dd", {}, h("span", { class: "muted" }, ruleValue(prior?.params[key])), h("span", { class: "change-arrow", "aria-label": "changes to" }, " → "), h("strong", {}, ruleValue(value)))))) : null,
    d.code ? h("details", {}, h("summary", {}, "Proposed rule code"), h("pre", {}, d.code)) : null,
    d.source ? h("details", { class: "source-detail" }, h("summary", {}, "Source passage"), h("blockquote", {}, d.source)) : null);
}
function showDraft(view) {
  currentView = view;
  const d = view.draft, thread = threads.get(d.id), last = d.messages.length - 1;
  // Retain each assistant response and its evaluated examples as a separate chat turn.
  for (let i = 0; i < d.messages.length; i++) {
    if (thread.turns.has(i)) continue;
    const m = d.messages[i];
    let node;
    if (m.role === "user") {
      if (i === 0 && thread.filename) node = message("user", h("strong", { class: "file-attachment" }, "↥ " + thread.filename), h("p", {}, "Create or update the payer rules from this document."), h("details", {}, h("summary", {}, "View uploaded document"), markdown(m.content)));
      else node = message("user", thread.nextUser?.title ? h("div", { class: "quoted-example" }, "On: " + thread.nextUser.title) : null, markdown(thread.nextUser?.text || m.content));
      thread.nextUser = null;
    } else node = message("assistant", markdown(m.content));
    thread.turns.set(i, node); thread.root.append(node);
  }
  const isNewReply = thread.lastReply !== last;
  thread.lastReply = last;
  const latest = thread.turns.get(last);
  if (latest && d.messages[last].role === "assistant") {
    latest.querySelector(".update-output")?.remove();
    latest.querySelector(".message-body").append(updateOutput(view));
  }
  // Previous turns remain readable, while decisions always apply to the latest revision.
  for (const t of threads.values()) for (const [index, node] of t.turns) {
    const old = t !== thread || index !== last;
    node.querySelectorAll(".rule-actions").forEach((el) => { el.hidden = old; });
  }
  $("rule-composer").hidden = d.status === "activated";
  setRuleControls();
  if (isNewReply && latest) latest.scrollIntoView({ behavior: "smooth", block: "start" });
}
function updateOutput(view) {
  const d = view.draft, ev = view.evaluation;
  const examples = ev.examples.filter((e) => e.changed);
  const output = h("div", { class: "update-output" }, ruleSummary(d));
  if (ev.checks) output.append(h("p", { class: "small muted" }, `${ev.checks.examples_run} scenarios executed · ${ev.checks.unchanged_controls} unchanged controls · ${ev.checks.saved_tests} saved tests checked`));
  for (const note of [...(view.notes || []), ...ev.problems]) output.append(h("p", { class: "warning" }, note));
  if (((Object.keys(d.params).length || d.code) && !ev.problems.length) || examples.length) {
    output.append(h("h3", { class: "examples-heading" }, `${examples.length} affected example${examples.length === 1 ? "" : "s"}`),
      h("p", { class: "small muted" }, "Only changed outcomes are shown. Click an example to discuss it, or review its result below."));
    output.append(...examples.map((e) => exampleCard(d, e)));
    if (!examples.length) output.append(h("p", { class: "muted" }, "No generated example outcomes change with this update."));
  }
  if (ev.impact) {
    const i = ev.impact;
    output.append(h("p", { class: "history-impact" }, h("strong", {}, `${i.changed_count} recorded claim outcome${i.changed_count === 1 ? "" : "s"} would change.`), ` ${i.now_correct} would match their recorded outcome; ${i.now_wrong} would stop matching.`));
    if (i.examples.length) output.append(h("details", {}, h("summary", {}, `Changed historical claims (${i.examples.length} shown)`), ...i.examples.map((c) => {
      const data = { key: `${d.id}-history-${c.claim_id}`, title: `Historical claim ${c.claim_id}`, ...c };
      return h("div", { class: "example" }, h("button", { type: "button", class: "example-select", "data-example-key": data.key, "aria-pressed": "false", onclick: () => selectExample(data) }, h("strong", {}, c.claim_id), h("span", { class: "small muted" }, `${c.payer} · Service ${c.date_of_service}`), comparison(c.before.status, c.before.codes, c.after.status, c.after.codes), h("span", { class: "small muted" }, `Recorded outcome: ${c.actual.status}${c.actual.code ? " · " + c.actual.code : ""} · Click to comment →`)));
    })));
  }
  const reason = h("input", { placeholder: "Reason / source for changing earlier confirmed expectations", "aria-label": "Reason for changing earlier confirmed expectations" });
  if (ev.regressions.length) output.append(h("div", { class: "warning" }, h("strong", {}, `${ev.regressions.length} previously confirmed tests would change`), ...ev.regressions.map((r) => h("p", {}, `${r.title}: ${r.expected_codes.join(", ") || "payable"} → ${r.actual.codes.join(", ") || "payable"}`)), reason));
  const activate = h("button", { type: "button", disabled: !ev.can_activate, "data-blocked": String(!ev.can_activate), onclick: () => ruleTask(async () => {
    await api("POST", `/api/drafts/${d.id}/activate`, { regression_reason: reason.value });
    d.status = "activated";
    output.querySelectorAll(".rule-actions").forEach((el) => { el.hidden = true; });
    output.append(h("p", { class: "activation-success", role: "status" }, "✓ New rule accepted. Current claims have been re-checked."));
    $("rule-composer").hidden = true;
    selectExample(null);
    await loadRules(); await refreshResults();
    $("rules-status").textContent = "Rules saved. Upload another document to start a new update.";
  }, "Activating reviewed rules…") }, "Accept new rule");
  output.append(h("div", { class: "rule-actions activation-row" }, activate, h("span", { class: "small muted" }, ev.undecided ? `${ev.undecided} affected example${ev.undecided === 1 ? "" : "s"} to review` : ev.can_activate ? "Ready to activate after your review." : "Reply below to complete this draft.")));
  return output;
}
function comparison(beforeStatus, beforeCodes, afterStatus, afterCodes) {
  return h("div", { class: "outcome-comparison" }, h("div", {}, h("span", { class: "outcome-label" }, "Before"), outcome(beforeStatus, beforeCodes)), h("span", { class: "change-arrow", "aria-hidden": "true" }, "→"), h("div", {}, h("span", { class: "outcome-label" }, "After update"), outcome(afterStatus, afterCodes)));
}
function exampleCard(d, e) {
  const c = e.claim, x = e.context, dec = e.decision;
  const data = { key: `${d.id}-${e.id}`, title: e.title, claim: c, context: x, before: { status: e.before_status, codes: e.before }, after: e.actual };
  const decide = (decision, codes) => ruleTask(async () => { showDraft(await api("POST", `/api/drafts/${d.id}/decide`, { example_id: e.id, decision, codes, note: "" })); $("rules-status").textContent = "Example review saved."; }, "Saving example review…");
  const codeSelect = [...new Set([...CODES, ...e.actual.codes, ...(e.expected_codes || []), ...(dec?.codes || [])])].sort().map((code) => h("label", {}, h("input", { type: "checkbox", value: code, checked: !!dec?.codes?.includes(code) }), code + " · " + (DENIAL_MEANING[code] || "Additional payer rule")));
  const status = dec ? dec.decision === "unclear" ? "Needs payer clarification" : e.matches_decision ? "✓ Reviewed" : "Expectation differs — revise the rule" : "Needs your review";
  return h("div", { class: "example" + (e.matches_decision === false ? " fail" : dec ? " done" : "") },
    h("button", { type: "button", class: "example-select", "data-example-key": data.key, "aria-pressed": String(selectedExample?.key === data.key), onclick: () => selectExample(data) },
      h("div", { class: "example-title" }, h("strong", {}, e.title), h("span", { class: "small " + (dec ? "ok" : "muted") }, status)),
      h("p", { class: "claim-facts" }, `${c.service_code} · ${c.units} units · Service ${c.date_of_service} · Submitted ${c.submitted_date}`),
      comparison(e.before_status, e.before, e.actual.status, e.actual.codes),
      h("span", { class: "comment-hint" }, "Click to comment on this example →")),
    h("details", { class: "example-evidence" }, h("summary", {}, "Why this changes / claim context"),
      ...e.actual.reasons.map((r) => h("p", {}, r)),
      !e.actual.codes.length && e.before.length ? h("p", {}, "This update removes: " + e.before.map((code) => `${code} (${DENIAL_MEANING[code]})`).join(", ")) : null,
      h("dl", {}, ...Object.entries({ ...c, ...x }).map(([key, value]) => h("div", {}, h("dt", {}, key.replaceAll("_", " ")), h("dd", {}, Array.isArray(value) ? value.join(" + ") || "None" : String(value)))))),
    h("div", { class: "rule-actions example-review" },
      h("button", { type: "button", class: "secondary", onclick: () => decide("set", e.actual.codes) }, "Result is correct"),
      h("button", { type: "button", class: "text-button", onclick: () => decide("unclear") }, "Needs payer clarification"),
      h("details", { class: "correct-expectation" }, h("summary", {}, "Set a different expectation"), h("p", { class: "small muted" }, "Choose all expected denial codes, or leave empty for payable."), h("div", { class: "code-options" }, codeSelect), h("button", { type: "button", class: "secondary", onclick: () => decide("set", codeSelect.map((label) => label.firstChild).filter((i) => i.checked).map((i) => i.value)) }, "Save expectation"))));
}

guard(async () => { await loadStatus(); await refreshResults(); });
