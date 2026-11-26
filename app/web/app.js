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

const CLAIM_COLUMNS = [
  ["claim_id", "Claim ID"], ["agency_id", "Agency ID"], ["payer", "Payer"],
  ["patient_id", "Patient ID"], ["service_code", "Service code"],
  ["date_of_service", "Service date"], ["submitted_date", "Submission date"],
  ["units", "Units"], ["auth_id", "Authorization ID"], ["modifier", "Modifier"],
  ["frequency_code", "Frequency code"], ["original_claim_id", "Original claim ID"],
  ["billed_amount", "Billed amount"]
];
function fullClaimRow(claim) {
  return h("div", { class: "table-wrap full-claim-row", tabindex: 0, "aria-label": "Complete claim row; scroll horizontally to see all fields" },
    h("table", {}, h("thead", {}, h("tr", {}, ...CLAIM_COLUMNS.map(([key, label]) => h("th", { scope: "col" }, label)))),
      h("tbody", {}, h("tr", {}, ...CLAIM_COLUMNS.map(([key]) => h("td", {}, claim?.[key] === "" ? "(blank)" : claim?.[key] ?? "Not available"))))));
}

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
  $("ai-status").textContent = s.ai_available ? `AI rule assistant ready (${s.model})` : "Rule assistant unavailable: API key not configured";
  const mode = $("mode-tools");
  if (s.mode === "demo") {
    const current = new Date(s.demo.as_of + "T00:00:00Z");
    const nextMonthEnd = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth() + 2, 0)).toISOString().slice(0, 10);
    const nextDate = s.demo.as_of < "2026-06-30" ? "2026-06-30" : nextMonthEnd;
    const date = h("input", { type: "date", value: nextDate > s.demo.end ? s.demo.end : nextDate, min: s.demo.as_of, "aria-label": "Advance demo through date" });
    mode.replaceChildren(badge("review", `Historical demo · Today’s date (simulated): ${s.demo.as_of}`), h("label", {}, "Fast forward to", date),
      h("button", { class: "secondary", onclick: () => guard(async () => {
        await api("POST", "/api/demo/advance", { date: date.value });
        await refreshResults(); await loadStatus(); await loadRules(); $("demo-report").hidden = true;
      }) }, "Fast forward: update system data"),
      h("button", { class: "secondary", onclick: () => guard(async () => { await api("POST", "/api/demo/end"); clearRuleConversation(); await loadStatus(); await loadRules(); await refreshResults(); $("demo-report").hidden = true; }) }, "Return to full app"));
    if (s.demo.last_report) renderDemoReport(s.demo.last_report);
    else $("demo-report").hidden = true;
  } else mode.replaceChildren(h("button", { class: "secondary", onclick: () => guard(async () => {
    await api("POST", "/api/demo/start", { date: "2026-04-30" }); clearRuleConversation(); await loadStatus(); await loadRules(); await refreshResults();
  }) }, "Start historical demo"));
  $("use-sample-claims").hidden = !!s.demo;
  $("demo-upload-help").hidden = !s.demo;
  const referenceDate = s.demo?.reference_data_as_of ?? s.demo?.as_of ?? "Date unavailable";
  const d = s.data;
  const card = $("data-card");
  card.replaceChildren(
    h("h2", {}, "Reference data"),
    s.demo ? h("div", { class: "demo-data-dates" },
      h("p", {}, h("strong", {}, `Reference data available as of: ${referenceDate}`)),
      Object.hasOwn(s.demo, "authorizations_from") ? h("p", { class: "small muted" }, `Authorizations: start dates from ${s.demo.authorizations_from || "no records"} through ${referenceDate}. EVV visits: ${s.demo.evv_from || "no records"} through ${referenceDate}. Claim outcome history: ${s.demo.history_from || "no records"} through ${s.demo.history_through || "no records"} (submission dates).`) : null,
      h("p", { class: "small muted" }, s.demo.prediction_history_before
        ? `Prediction history: submissions before ${s.demo.prediction_history_before}. Recorded batch outcomes are used only for comparison.`
        : `Pending batch: ${s.demo.pending_month || s.demo.as_of.slice(0, 7)}. Upload this month’s CSV. Its outcomes enter reference history next month.`)) : null,
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
async function uploadClaimsFile(file) {
  if (!file) return;
  const text = await file.text();
  await guard(async () => { results = await api("POST", "/api/claims", text); selectedLine = null; filter = "all"; render(); await loadStatus(); });
}
$("claims-file").addEventListener("change", async (e) => { await uploadClaimsFile(e.target.files[0]); e.target.value = ""; });
$("claims-upload").addEventListener("dragover", (e) => { e.preventDefault(); });
$("claims-upload").addEventListener("drop", (e) => {
  e.preventDefault(); uploadClaimsFile(e.dataTransfer.files[0]);
});
$("use-sample-claims").addEventListener("click", () => guard(async () => { results = await api("POST", "/api/claims/sample"); selectedLine = null; filter = "all"; render(); }));
async function refreshResults() {
  const r = await api("GET", "/api/results");
  renderRegressionChecks(r.regression_checks);
  if (r.rows.length) { results = r; render(); }
  else { results = null; selectedLine = null; $("results").hidden = true; $("export").hidden = true; }
}
function clearRuleConversation() {
  threads.clear(); currentView = null; selectExample(null);
  $("rules-conversation").querySelectorAll(".conversation-thread").forEach((node) => node.remove());
  $("rules-empty").hidden = false; $("rule-composer").hidden = false; $("rules-status").textContent = "";
  $("rule-message").value = ""; setRuleAttachment(null);
}
function renderDemoReport(report) {
  const root = $("demo-report"); root.hidden = false;
  root.replaceChildren(
    h("p", { class: "demo-accuracy" }, `ACCURACY: ${(report.accuracy * 100).toFixed(2)}%.`),
    h("h2", {}, "DEMO - COMPARING WITH TRUE HISTORICAL RESULTS"),
    h("p", {}, `${report.from_date} to ${report.through_date} · ${report.status_and_code_set_correct} of ${report.claims} claims match · ${report.mismatches.length} mismatches`),
    h("p", {}, `${report.false_alarms} false alarms (predicted denied, recorded paid) · ${report.missed_denials} missed denials (predicted paid, recorded denied) · ${report.manual_review} need review`),
    h("p", { class: "small muted" }, report.basis || ""),
    h("p", { class: "small muted" }, "A match requires the recorded status and, for denials, the recorded denial code. Documented mismatches still reduce accuracy."),
    report.unexplained_d99 ? h("p", {}, `${report.unexplained_d99} recorded D99 denials have no supplied denial rule or payer correspondence.`) : null,
    h("details", {}, h("summary", {}, `Mismatches (${report.mismatches.length})`), h("div", { class: "table-wrap" }, h("table", {},
      h("tr", {}, ...["Claim", "Predicted", "Recorded", "Basis"].map((text) => h("th", {}, text))),
      ...report.mismatches.map((r) => h("tr", {}, h("td", {}, r.claim_id, fullClaimRow(r.claim)), h("td", {}, `${r.status} ${r.codes.join(", ")}`), h("td", {}, `${r.actual_status} ${r.actual_code}`), h("td", { class: "reason" }, r.exception_reason || (r.actual_code === "D99" ? "D99: payer reason not supplied. Recorded remark: " : "Recorded remark: ") + r.remark)))))),
    h("p", { class: report.passed ? "ok demo-investigation-status" : "bad demo-investigation-status" },
      h("strong", {}, report.passed ? "Matches documented results" : "Unexpected"),
      report.passed ? " — All mismatches were documented in previous investigations." : ` — ${report.unexpected_mismatches} mismatches were not documented in previous investigations.`));
}

function render() {
  renderRegressionChecks(results.regression_checks);
  if (Object.hasOwn(results, "demo_report")) {
    if (results.demo_report) renderDemoReport(results.demo_report);
    else $("demo-report").hidden = true;
  }
  $("results").hidden = false; $("export").hidden = false;
  const by = results.summary.by_category;
  const tiles = [["all", "All claims", results.summary.total], ["denied", "Likely denied", by.denied || 0], ["review", "Needs review", by.review || 0],
    ["must_correct", "Must correct", by.must_correct || 0], ["formatted", "Auto-formatted", results.summary.formatted_rows], ["autofixed", "Auto-fixed", results.summary.autofixed_rows || 0], ["payable", "Likely paid", by.payable || 0]];
  $("tiles").replaceChildren(...tiles.map(([k, label, n]) =>
    h("button", { class: "tile" + (filter === k ? " on" : ""), onclick: () => { filter = k; render(); } }, h("b", {}, n), label)));
  const rows = results.rows.filter((r) => filter === "all" || (filter === "formatted" ? r.changes.length : filter === "autofixed" ? r.automatic_fixes?.length : r.category === filter));
  $("filters").replaceChildren(h("span", { class: "muted" }, `${rows.length} claims shown. Yellow cells: formatted, auto-fixed or edited; hover for the uploaded value. Click a row for details.`),
    results.summary.by_code && Object.keys(results.summary.by_code).length ? h("span", { class: "muted" }, " Codes: " + Object.entries(results.summary.by_code).map(([c, n]) => `${c}×${n}`).join("  ")) : null);
  const cols = ["line", ...CLAIM_COLUMNS.map(([key]) => key)];
  const table = $("claims-table");
  table.replaceChildren(h("tr", {}, ...["Line", ...CLAIM_COLUMNS.map(([, label]) => label), "Result", "Codes", "Why"].map((t) => h("th", {}, t))),
    ...rows.slice(0, 1500).map((r) => {
      const changed = Object.fromEntries(r.changes.map((c) => [c.field, c.before]));
      const edited = r.edits || {};
      const auto = Object.fromEntries((r.automatic_fixes || []).map((c) => [c.field, c]));
      return h("tr", { class: "click" + (r.line === selectedLine ? " sel" : ""), onclick: () => { selectedLine = r.line; render(); showDetail(r.line); } },
        ...cols.map((c) => c === "line" ? h("td", {}, r.line) : h("td", { class: c in changed || c in edited || c in auto ? "fixed" : "", title: c in edited ? `edited by you (uploaded: ${r.raw[c]})` : c in auto ? `Automatically fixed: ${auto[c].reason} (uploaded: ${r.raw[c]})` : c in changed ? `was: ${changed[c]}` : null }, r.clean[c])),
        h("td", {}, badge(r.override ? r.status : r.status === "check_previous_claims" || r.status === "manual_review" ? "review" : r.status, (r.override ? "You: " : "") + (r.category === "must_correct" ? "Must correct" : STATUS_LABEL[r.status] || r.status))),
        h("td", {}, codeChips(r.denial_codes)), h("td", { class: "reason" }, r.summary));
    }));
  if (rows.length > 1500) table.append(h("tr", {}, h("td", { colspan: cols.length + 3 }, "Showing first 1500 rows; use filters or the CSV download.")));
  if (selectedLine != null) showDetail(selectedLine, true);
}

function showDetail(line, keepScroll) {
  const r = results.rows.find((x) => x.line === line); if (!r) return;
  const d = $("detail"); d.hidden = false;
  const kids = [h("h2", {}, `Claim ${r.claim_id || "(no ID)"} — line ${r.line}`),
    h("p", {}, badge(r.status === "paid" ? "payable" : r.status, STATUS_LABEL[r.status] || r.status), " ", codeChips(r.denial_codes),
      r.override ? h("span", { class: "muted" }, ` (your classification; rules predicted ${STATUS_LABEL[r.predicted_status] || r.predicted_status} ${r.predicted_codes.join(", ")})`) : null)];
  kids.push(h("h3", {}, "Complete claim row"), fullClaimRow(r.clean));
  for (const i of r.issues) kids.push(h("div", { class: i.severity === "error" ? "failure" : "warning" }, h("b", {}, i.field + ": "), i.message,
    i.severity === "error" ? h("div", { class: "muted small" }, "Edit this value below, then the batch is re-checked.") : null));
  for (const f of r.failures) kids.push(h("div", { class: "failure" }, h("b", {}, f.code + " — " + f.meaning), h("div", {}, f.reason),
    h("div", { class: "muted" }, "Suggested action: " + f.suggestion), h("div", { class: "muted small" }, "Rule: " + f.rule)));
  if (r.status === "check_previous_claims") kids.push(h("div", { class: "warning" }, h("b", {}, "Authorization balance uncertain"), h("div", {}, r.previous_claims_action),
    h("div", {}, `Earlier unresolved claims: ${r.unresolved_prior_claim_ids.join(", ")}. Balance could be ${r.balance_range[0]}–${r.balance_range[1]} units; this claim bills ${r.clean.units}.`)));
  for (const w of r.warnings) kids.push(h("div", { class: "warning" }, w));
  if (r.changes.length) kids.push(h("p", { class: "small" }, h("b", {}, "Normalised automatically: "), r.changes.map((c) => `${c.field}: "${c.before}" → "${c.after}"`).join("; ")));
  if (r.automatic_fixes?.length) kids.push(h("section", { class: "automatic-fixes" }, h("h3", {}, "Automatically fixed"),
    ...r.automatic_fixes.map((c) => h("p", {}, h("strong", {}, c.field), `: ${c.before || "(blank)"} → ${c.after}. ${c.reason}`,
      c.source_ids?.length ? ` · Source: ${c.source_ids.join(", ")}` : ""))));
  kids.push(claimReferencePanel(r));
  const pc = Object.entries(r.proposed_changes || {});
  if (pc.length) kids.push(h("div", {}, h("h3", {}, "Proposed fixes (need your confirmation)"), ...pc.map(([field, p]) =>
    h("div", { class: "row" }, h("span", {}, h("b", {}, field), " → ", h("span", { class: "fix" }, p.value), " — " + p.reason),
      h("button", { onclick: () => applyEdit(r.line, { [field]: p.value }) }, "Apply fix")))));
  // manual correction of fields
  const editable = ["payer", "patient_id", "service_code", "date_of_service", "submitted_date", "units", "auth_id", "modifier", "frequency_code", "original_claim_id"];
  const inputs = {};
  kids.push(h("h3", {}, "Correct claim fields"), h("div", { class: "row" }, ...editable.map((f) => {
    inputs[f] = h("input", { size: f === "date_of_service" || f === "submitted_date" ? 11 : 9, value: r.edits && f in r.edits ? r.edits[f] : r.clean[f] });
    return h("label", {}, f, inputs[f]);
  }), h("button", { onclick: () => {
    const fields = {}; editable.forEach((f) => { const cur = r.edits && f in r.edits ? r.edits[f] : r.clean[f]; if (inputs[f].value !== cur) fields[f] = inputs[f].value; });
    applyEdit(r.line, fields);
  } }, "Save and re-check"), Object.keys(r.edits || {}).length ? h("button", { class: "secondary", onclick: () => guard(async () => { results = await api("POST", `/api/claims/line/${r.line}/revert`, {}); render(); }) }, "Undo my edits") : null));
  if (r.claim_id && r.category !== "duplicate_row") kids.push(overrideForm(r));
  kids.push(h("details", {}, h("summary", {}, "Evidence"), h("pre", {}, JSON.stringify(r.evidence, null, 2))));
  d.replaceChildren(...kids);
  if (!keepScroll) d.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
function claimReferencePanel(r) {
  const refs = r.reference_data || { evv_visits: [], authorizations: [] };
  const visits = refs.evv_visits;
  const verifiedMinutes = visits.filter((v) => v.verification_status === "verified").reduce((total, v) => total + v.duration_minutes, 0);
  const fact = (name, value) => h("div", {}, h("dt", {}, name), h("dd", {}, value == null || value === "" ? "—" : value));
  const evv = h("section", { class: "reference-panel" }, h("h3", {}, "EVV visits"),
    h("p", { class: "reference-summary" }, h("strong", {}, `${verifiedMinutes} verified minutes`), ` · ${r.clean.units || "—"} units billed`),
    h("p", { class: "small muted" }, `Patient ${r.clean.patient_id || "—"} · ${r.clean.service_code || "—"} · Service ${r.clean.date_of_service || "—"}. Visits are matched by their clock-in date.`),
    visits.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("tr", {}, ...["Visit", "Caregiver", "Clock in", "Clock out", "Minutes", "Verification"].map((name) => h("th", {}, name))),
      ...visits.map((v) => h("tr", {}, h("td", {}, v.visit_id), h("td", {}, v.caregiver_id), h("td", {}, v.clock_in), h("td", {}, v.clock_out), h("td", {}, v.duration_minutes),
        h("td", {}, badge(v.verification_status === "verified" ? "payable" : "review", v.verification_status.replaceAll("_", " "))))))) : h("p", { class: "muted" }, "No EVV visits found for this patient, service and date."),
    visits.some((v) => v.verification_status !== "verified") ? h("p", { class: "small muted" }, "Only verified visits count toward billable time.") : null);
  const auth = h("section", { class: "reference-panel" }, h("h3", {}, "Authorization"),
    h("p", { class: "reference-summary" }, "Claim reference: ", h("strong", {}, r.clean.auth_id || "Missing")),
    r.clean.auth_id && !refs.authorizations.some((a) => a.referenced) ? h("p", { class: "warning" }, "The authorization referenced by this claim was not found.") : null,
    !refs.authorizations.length ? h("p", { class: "muted" }, "No related authorization found for this payer, patient and service.") : null,
    ...refs.authorizations.map((a) => h("article", { class: "authorization-record" },
      h("div", { class: "example-title" }, h("strong", {}, a.auth_id), badge(a.issues.length ? "review" : "payable", a.referenced ? "Referenced by claim" : "Related coverage")),
      h("dl", { class: "reference-facts" }, fact("Payer", a.payer_id), fact("Patient", a.patient_id), fact("Service", a.service_code), fact("Coverage", `${a.start_date} to ${a.end_date}`), fact("Authorized units", a.units_authorized),
        a.referenced && !a.issues.length ? fact("Remaining before this claim", r.evidence.authorization_remaining) : null,
        a.referenced && !a.issues.length && r.evidence.authorization_remaining != null ? fact("Used in current ledger", Number(a.units_authorized) - r.evidence.authorization_remaining) : null),
      a.issues.length ? h("p", { class: "warning" }, a.issues.join(" · ")) : null)),
    r.balance_uncertain ? h("p", { class: "warning" }, `Earlier unresolved claims could leave ${r.balance_range[0]}–${r.balance_range[1]} units available.`) : null);
  return h("div", { class: "claim-references" }, evv, auth);
}
function applyEdit(line, fields) { if (!Object.keys(fields).length) return; guard(async () => { results = await api("POST", `/api/claims/line/${line}/edit`, { fields }); render(); }); }

function overrideForm(r) {
  const ov = r.override;
  const sel = h("select", {}, ...[["", "No override (use rules)"], ["paid", "Treat as paid"], ["denied", "Treat as denied"], ["manual_review", "Needs review"]].map(([v, t]) => h("option", { value: v, selected: (ov ? ov.status : "") === v }, t)));
  const boxes = CODES.map((c) => h("label", { style: "display:inline-block;margin-right:8px" }, h("input", { type: "checkbox", value: c, style: "display:inline", checked: ov && ov.denial_codes.includes(c) }), " " + c));
  const note = h("input", { size: 40, placeholder: "Reason / source (kept in the export)", value: ov ? ov.note : "" });
  return h("div", {}, h("h3", {}, "Your classification"), h("p", { class: "muted small" }, "Your classification sets this claim’s outcome and affects later authorization balances."),
    h("div", { class: "row" }, sel, ...boxes), h("div", { class: "row" }, note, h("button", { onclick: () => guard(async () => {
      const codes = boxes.map((b) => b.firstChild).filter((i) => i.checked).map((i) => i.value);
      results = sel.value ? await api("POST", `/api/claims/${r.claim_id}/override`, { status: sel.value, denial_codes: codes, note: note.value })
        : await api("DELETE", `/api/claims/${r.claim_id}/override`);
      render();
    }) }, "Save classification")));
}

// ---------- rules: one conversation ----------
let activeRules = null, currentView = null, selectedExample = null, ruleBusy = false, ruleAttachment = null;
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
  $("send-rule-message").disabled = ruleBusy || (!$("rule-message").value.trim() && !ruleAttachment);
  document.querySelectorAll("#rule-attachment button, #example-context button").forEach((node) => { node.disabled = ruleBusy; });
  $("rule-message").disabled = ruleBusy;
  document.querySelectorAll("#mode-tools button, #mode-tools input").forEach((node) => { node.disabled = ruleBusy; });
  document.querySelectorAll(".rule-actions button").forEach((b) => { b.disabled = ruleBusy || b.dataset.blocked === "true"; });
}
function setRuleAttachment(file) {
  ruleAttachment = file;
  $("rules-file").value = "";
  const root = $("rule-attachment"); root.hidden = !file;
  root.replaceChildren(file ? [h("span", {}, "↥ ", h("strong", {}, file.name)), h("button", {
    type: "button", class: "secondary", "aria-label": "Remove Markdown attachment", onclick: () => { setRuleAttachment(null); $("rules-status").textContent = "Attachment removed."; }
  }, "×")] : []);
  setRuleControls();
}
$("rules-file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (!file || ruleBusy) return;
  if (!/\.(md|markdown)$/i.test(file.name)) {
    $("rules-status").textContent = "Choose a .md or .markdown file.";
    event.target.value = "";
    return;
  }
  setRuleAttachment(file);
  $("rules-status").textContent = "Document attached. Send to generate a rule proposal.";
  $("rule-message").focus();
});
$("rule-message").addEventListener("input", setRuleControls);
$("rule-composer").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = $("rule-message").value.trim(), file = ruleAttachment, example = selectedExample;
  if ((!text && !file) || ruleBusy) return;
  const draftId = currentView?.draft.status === "draft" ? currentView.draft.id : null;
  ruleTask(async () => {
    const attachment = file ? { name: file.name, text: await file.text() } : null;
    if (attachment && !attachment.text.trim()) throw new Error("This Markdown file is empty. Remove it or choose another document.");
    const parts = [];
    if (example) parts.push("Regarding this example:\n" + JSON.stringify(example, null, 2));
    if (text) parts.push(text);
    else if (attachment) parts.push("Create or update the payer rules from the attached document.");
    if (attachment) parts.push(`Attached Markdown document: ${attachment.name}\n\n${attachment.text}`);
    const view = await api("POST", draftId ? `/api/drafts/${draftId}/chat` : "/api/drafts", { message: parts.join("\n\n") });
    if (!draftId) {
      const root = h("div", { class: "conversation-thread" });
      threads.set(view.draft.id, { root, turns: new Map() });
      $("rules-empty").hidden = true;
      $("rules-conversation").append(root);
    }
    // Keep the visible prompt/attachment separate from the context sent to the AI.
    threads.get(view.draft.id).nextUser = { text, attachment, title: example?.title };
    selectExample(null);
    $("rule-message").value = "";
    setRuleAttachment(null);
    showDraft(view);
    $("rules-status").textContent = draftId ? "Proposal revised. Review changed examples." : "Proposal ready for review.";
  }, "Generating rules and checking examples…");
});
$("rule-message").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("rule-composer").requestSubmit(); }
});
function selectExample(example) {
  selectedExample = example;
  const box = $("example-context"); box.hidden = !example;
  box.replaceChildren(example ? [h("span", {}, "Commenting on: ", h("strong", {}, example.title)), h("button", { type: "button", class: "secondary", "aria-label": "Remove example from message", onclick: () => selectExample(null) }, "×")] : []);
  document.querySelectorAll(".example-select").forEach((b) => b.setAttribute("aria-pressed", String(!!example && b.dataset.exampleKey === example.key)));
  if (example) { document.querySelector("[data-tab=rules]").click(); $("rule-message").focus(); }
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
  if (d.code != null || !d.payer_id) return h("section", { class: "rule-summary" },
    h("div", { class: "rule-summary-heading" }, h("button", { type: "button", class: "rule-title-button", onclick: () => selectExample({ key: `${d.id}-rule`, title: d.summary || "Proposed rule update", code: d.code, source: d.source }) }, d.summary || "Proposed rule update", h("span", { class: "small muted" }, " · Click to comment")), badge("review", "Draft")),
    h("p", { class: "muted" }, d.code ? "Review the proposed code, source passage and changed examples before accepting." : "Reply below to clarify the proposed update."),
    d.code ? h("details", {}, h("summary", {}, "Proposed rule code"), h("pre", {}, d.code)) : null,
    d.source ? h("details", { class: "source-detail" }, h("summary", {}, "Source passage"), h("blockquote", {}, d.source)) : null);
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
      const input = thread.nextUser;
      node = message("user", input?.title ? h("div", { class: "quoted-example" }, "On: " + input.title) : null,
        input?.attachment ? h("strong", { class: "file-attachment" }, "↥ " + input.attachment.name) : null,
        input ? (input.text ? markdown(input.text) : h("p", {}, "Create or update rules from this document.")) : markdown(m.content),
        input?.attachment ? h("details", {}, h("summary", {}, "View attached document"), markdown(input.attachment.text)) : null);
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
    node.querySelectorAll(".activation-row").forEach((el) => { el.hidden = old || d.status === "activated"; });
    node.querySelectorAll(".example-review").forEach((el) => { el.hidden = d.status === "activated"; });
  }
  $("rule-composer").hidden = false;
  setRuleControls();
  if (isNewReply && latest) latest.scrollIntoView({ behavior: "smooth", block: "start" });
}
function updateOutput(view) {
  const d = view.draft, ev = view.evaluation;
  const examples = ev.examples.filter((e) => e.changed);
  const output = h("div", { class: "update-output" }, ruleSummary(d));
  if (ev.checks) output.append(h("p", { class: "small muted" }, `${ev.checks.examples_run} scenarios executed · ${ev.checks.unchanged_controls} unchanged controls · ${ev.checks.saved_tests} saved tests checked`));
  for (const note of [...(view.notes || []), ...ev.problems]) output.append(h("p", { class: "warning" }, note));
  const executionPassed = ev.execution?.status === "passed";
  output.prepend(h("p", { class: executionPassed ? "ok execution-check" : "warning execution-check", role: "status" },
    h("strong", {}, executionPassed ? "Execution checks passed" : "Execution checks blocked"),
    executionPassed ? " · Code ran and returned valid results for the tested cases." : " · Resolve the errors before approval."));
  if (!executionPassed) return output;
  if (ev.review) output.append(h("p", { class: "review-summary" }, h("strong", {},
    `Review required: ${ev.review.synthetic_count} synthetic changes · ${ev.review.regression_count} saved regressions · ${ev.review.historical_count} historical changes`)));

  if (((Object.keys(d.params).length || d.code) && !ev.problems.length) || examples.length) {
    output.append(h("h3", { class: "examples-heading" }, `${examples.length} affected example${examples.length === 1 ? "" : "s"}`),
      h("p", { class: "small muted" }, "Examples below show predictions changed by this proposal."));
    output.append(...examples.map((e) => exampleCard(d, e)));
    if (!examples.length) output.append(h("p", { class: "muted" }, "No example predictions changed."));
  }
  if (ev.regressions.length) output.append(h("details", { class: "warning regression-changes" },
    h("summary", {}, `${ev.regressions.length} previously accepted examples no longer match`),
    ...ev.regressions.map((r) => regressionCard(r, `${d.id}-saved-${r.id}`))));
  if (ev.impact) {
    const i = ev.impact;
    output.append(h("p", { class: "history-impact" }, h("strong", {}, `${i.changed_count} historical prediction${i.changed_count === 1 ? "" : "s"} would change.`), ` ${i.now_correct} would match their recorded outcome; ${i.now_wrong} would stop matching.`));
    if (i.changed_count) output.append(h("p", { class: "warning" }, "Historical predictions usually stay unchanged for a future-dated rule. Check each payer and service date against the source; explain any retroactive changes before approving."),
      h("details", { class: "historical-changes" }, h("summary", {}, `Review historical changes (${i.changed_count})`), ...i.examples.map((c) => {
        const data = { key: `${d.id}-history-${c.claim_id}`, title: `Historical claim ${c.claim_id}`, ...c };
        return h("div", { class: "example" }, h("div", { class: "example-content" }, h("strong", {}, c.claim_id),
          h("p", {}, `${c.payer} · Service date: ${c.date_of_service}`), fullClaimRow(c.claim),
          comparison(c.before.status, c.before.codes, c.after.status, c.after.codes), h("span", { class: "small muted" }, `Recorded outcome: ${c.actual.status}${c.actual.code ? " · " + c.actual.code : ""}`)),
          h("div", { class: "rule-actions example-review" }, raiseIssueButton(data)));
      })));
  }
  const review = ev.review;
  const synthetic = h("input", { type: "checkbox", class: "approve-synthetic" });
  const history = h("input", { type: "checkbox", class: "approve-history" });
  const needsSynthetic = !!review && !!(review.synthetic_count || review.regression_count);
  const needsHistory = !!review?.historical_count;
  const needsReason = needsHistory || ev.regressions.length > 0;
  const reason = h("textarea", { rows: "2", placeholder: "Reason and source for changing earlier expectations or historical predictions", "aria-label": "Reason and source for changed expectations" });
  const approvals = h("div", { class: "semantic-approval activation-row" }, h("h3", {}, "Approve the reviewed changes"),
    needsSynthetic ? h("label", { class: "approval-option" }, synthetic, `I approve ${review.synthetic_count} changed synthetic examples and ${review.regression_count} changed saved expectations.`) : null,
    needsHistory ? h("label", { class: "approval-option" }, history, `I approve ${review.historical_count} changed historical predictions after checking payer and service dates.`) : null,
    needsReason ? reason : null);
  output.append(approvals);
  const activate = h("button", { type: "button", disabled: !ev.can_activate, "data-blocked": String(!ev.can_activate), onclick: () => ruleTask(async () => {
    try {
      await api("POST", `/api/drafts/${d.id}/activate`, { regression_reason: reason.value, review_token: review.token, approve_synthetic: synthetic.checked, approve_history: history.checked });
    } catch (error) {
      // Refresh comparisons and reset consent if data or accepted rules changed.
      showDraft(await api("GET", `/api/drafts/${d.id}`));
      throw error;
    }
    d.status = "activated";
    output.querySelectorAll(".activation-row").forEach((el) => { el.hidden = true; });
    output.querySelectorAll(".rule-actions").forEach((el) => { el.hidden = true; });
    document.querySelectorAll(".example-review").forEach((el) => { el.hidden = true; });
    output.append(h("p", { class: "activation-success", role: "status" }, "Rules accepted; loaded claims rechecked."));
    $("rule-composer").hidden = false;
    selectExample(null);
    await loadRules(); await refreshResults();
    $("rules-status").textContent = "Rules saved; loaded claims rechecked.";
  }, "Activating reviewed rules…") }, needsHistory || ev.regressions.length ? "Approve changes and accept rule" : "Accept new rule");
  const updateApproval = () => { activate.disabled = !ev.can_activate || (needsSynthetic && !synthetic.checked) || (needsHistory && !history.checked) || (needsReason && !reason.value.trim()); activate.dataset.blocked = String(activate.disabled); };
  synthetic.addEventListener("change", updateApproval); history.addEventListener("change", updateApproval); reason.addEventListener("input", updateApproval); updateApproval();
  output.append(h("div", { class: "rule-actions activation-row" }, activate, h("span", { class: "small muted" }, ev.can_activate ? "Accepting saves the rules and rechecks loaded claims." : "Resolve the issues above before accepting.")));
  return output;
}
function regressionCard(r, key) {
  return h("div", { class: "example" }, h("div", { class: "example-content" }, h("strong", {}, r.title),
    fullClaimRow(r.full_claim || r.claim),
    h("p", {}, `Saved expectation: ${r.expected_status || (r.expected_codes.length ? "denied" : "paid")} · ${r.expected_codes.join(", ") || "no denial codes"}`),
    h("p", {}, `New result: ${r.actual.status} · ${r.actual.codes.join(", ") || "no denial codes"}`),
    r.actual.error ? h("p", { class: "warning" }, r.actual.error) : null,
    h("details", {}, h("summary", {}, "Supporting context"), h("pre", {}, JSON.stringify(r.context, null, 2)))),
    h("div", { class: "rule-actions example-review" }, raiseIssueButton({ key, title: r.title, ...r })));
}
function renderRegressionChecks(report) {
  const root = $("regression-report"); root.hidden = !report;
  if (!report) return;
  root.replaceChildren(h("strong", { class: report.failed ? "bad" : "ok" },
    report.total ? `${report.passed}/${report.total} runtime regression checks passed` : "No saved regression examples yet"),
    report.failed ? h("details", {}, h("summary", {}, `${report.failed} saved expectations no longer match`),
      ...report.failures.map((r) => regressionCard(r, `runtime-${r.id}`))) : null);
}
function comparison(beforeStatus, beforeCodes, afterStatus, afterCodes) {
  return h("div", { class: "outcome-comparison" }, h("div", {}, h("span", { class: "outcome-label" }, "Before"), outcome(beforeStatus, beforeCodes)), h("span", { class: "change-arrow", "aria-hidden": "true" }, "→"), h("div", {}, h("span", { class: "outcome-label" }, "After update"), outcome(afterStatus, afterCodes)));
}
function raiseIssueButton(data) {
  return h("button", { type: "button", class: "secondary example-select", "data-example-key": data.key,
    "aria-pressed": String(selectedExample?.key === data.key), onclick: () => selectExample(data) }, "Raise issue");
}
function exampleCard(d, e) {
  const c = e.full_claim || e.claim, x = e.context;
  const data = { key: `${d.id}-${e.id}`, title: e.title, claim: c, context: x, before: { status: e.before_status, codes: e.before }, after: e.actual };
  return h("div", { class: "example" + (e.matches_decision === false ? " fail" : "") },
    h("div", { class: "example-content" },
      h("div", { class: "example-title" }, h("strong", {}, e.title)),
      fullClaimRow(c),
      comparison(e.before_status, e.before, e.actual.status, e.actual.codes)),
    h("details", { class: "example-evidence" }, h("summary", {}, "Rule evidence and example inputs"),
      ...e.actual.reasons.map((r) => h("p", {}, r)),
      !e.actual.codes.length && e.before.length ? h("p", {}, "This update removes: " + e.before.map((code) => `${code} (${DENIAL_MEANING[code]})`).join(", ")) : null,
      h("dl", {}, ...Object.entries({ ...c, ...x }).map(([key, value]) => h("div", {}, h("dt", {}, key.replaceAll("_", " ")), h("dd", {}, Array.isArray(value) ? value.join(" + ") || "None" : String(value)))))),
    h("div", { class: "rule-actions example-review" }, raiseIssueButton(data)));
}

setRuleControls();
guard(async () => { await loadStatus(); await refreshResults(); });
