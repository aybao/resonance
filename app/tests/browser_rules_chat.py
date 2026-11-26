#!/usr/bin/env python3
"""On-demand browser smoke test. Start geckodriver --port 4445 first. No API key needed.

Run from app/: python3 tests/browser_rules_chat.py [--driver http://127.0.0.1:4445]
Uses an isolated local server/session and a fake novel-rule proposal, then checks
upload, comments, revisions, acceptance, desktop/mobile layout and demo controls.
"""
import argparse
import base64
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import make_server
from claimcheck.session import Session
from tests.test_code_rules import proposal
from tests.test_drafts import fake_ai


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--driver', default='http://127.0.0.1:4445')
    parser.add_argument('--screenshots', type=Path, help='Optional destination for desktop/mobile PNGs')
    args = parser.parse_args()

    def wd(method, path, body=None):
        request = urllib.request.Request(args.driver + path, method=method,
            data=json.dumps(body).encode() if body is not None else None, headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)['value']
        except urllib.error.HTTPError as exc:
            raise RuntimeError(exc.read().decode()) from None

    with tempfile.TemporaryDirectory(prefix='rules-browser-') as state:
        ai = fake_ai(proposal())
        app = Session(state_dir=state, ai=ai)
        server = make_server(app, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        sid = None
        try:
            sid = wd('POST', '/session', {'capabilities': {'alwaysMatch': {
                'browserName': 'firefox', 'moz:firefoxOptions': {'args': ['-headless']}}}})['sessionId']
            def js(script):
                return wd('POST', f'/session/{sid}/execute/sync',
                    {'script': 'return window.eval(' + json.dumps('(function(){' + script + '})()') + ')', 'args': []})
            def wait(script):
                for attempt in range(150):
                    if js(script):
                        return
                    time.sleep(.2)
                raise AssertionError('Timeout: ' + script)
            wd('POST', f'/session/{sid}/window/rect', {'width': 1280, 'height': 1100})
            wd('POST', f'/session/{sid}/url', {'url': f'http://127.0.0.1:{server.server_port}'})
            wait('return $("ai-status").textContent.includes("ready")')
            js('document.querySelector("[data-tab=rules]").click()')
            wait('return activeRules !== null')
            assert js('return !$("rule-composer").hidden && !$("rule-message").disabled && $("send-rule-message").disabled')
            if args.screenshots:
                args.screenshots.mkdir(parents=True, exist_ok=True)
                (args.screenshots / 'initial-desktop.png').write_bytes(base64.b64decode(wd('GET', f'/session/{sid}/screenshot')))
            wd('POST', f'/session/{sid}/window/rect', {'width': 390, 'height': 844})
            assert js('const box = $("rule-message").getBoundingClientRect(); return box.top >= 0 && box.bottom <= window.innerHeight && $("send-rule-message").getBoundingClientRect().bottom <= window.innerHeight')
            if args.screenshots:
                (args.screenshots / 'initial-mobile.png').write_bytes(base64.b64decode(wd('GET', f'/session/{sid}/screenshot')))
            wd('POST', f'/session/{sid}/window/rect', {'width': 1280, 'height': 1100})
            # Empty attachments do not consume a model call or discard the prompt.
            js('const dt = new DataTransfer(); dt.items.add(new File([""], "empty.md")); $("rules-file").files=dt.files; $("rules-file").dispatchEvent(new Event("change")); $("rule-message").value="Please add whole-hour billing for Summit."; $("rule-message").dispatchEvent(new Event("input")); $("rule-composer").requestSubmit()')
            wait('return !ruleBusy && $("rules-status").textContent.includes("empty")')
            assert not ai.calls
            assert js('return $("rule-message").value.includes("whole-hour") && $("rule-attachment").textContent.includes("empty.md")')
            js('$("rule-attachment").querySelector("button").click()')
            # Prompt alone starts a draft immediately through the same composer.
            js('$("rule-composer").requestSubmit()')
            wait('return currentView !== null && !ruleBusy')
            assert len(ai.calls) == 1
            assert ai.calls[0]['messages'][0]['content'] == 'Please add whole-hour billing for Summit.'
            # Reset only the browser state to exercise a document-only first turn.
            wd('POST', f'/session/{sid}/url', {'url': f'http://127.0.0.1:{server.server_port}'})
            wait('return $("ai-status").textContent.includes("ready")')
            js('document.querySelector("[data-tab=rules]").click()')
            js('const dt=new DataTransfer(); dt.items.add(new File(["# Document-only test contract"], "document-only.md")); $("rules-file").files=dt.files; $("rules-file").dispatchEvent(new Event("change"))')
            assert len(ai.calls) == 1  # Choosing a file stages it without submitting.
            assert js('return currentView === null && !$("rule-attachment").hidden && !$("send-rule-message").disabled')
            js('$("rule-composer").requestSubmit()')
            wait('return currentView !== null && !ruleBusy')
            assert len(ai.calls) == 2
            assert '# Document-only test contract' in ai.calls[-1]['messages'][0]['content']
            assert js('return document.querySelector(".rule-message.user").textContent.includes("document-only.md")')
            # Start the full workflow with a prompt and attachment sent together.
            wd('POST', f'/session/{sid}/url', {'url': f'http://127.0.0.1:{server.server_port}'})
            wait('return $("ai-status").textContent.includes("ready")')
            js('document.querySelector("[data-tab=rules]").click()')
            wait('return activeRules !== null')
            initial_calls = len(ai.calls)
            js('const f = new File(["# Test contract\\nWhole-hour billing from 2026-09-01 for PY03"], "test-contract.md", {type:"text/markdown"}); const dt=new DataTransfer(); dt.items.add(f); $("rules-file").files=dt.files; $("rules-file").dispatchEvent(new Event("change"));')
            assert len(ai.calls) == initial_calls
            js('$("rule-message").value="Apply this contract to Summit only."; $("rule-message").dispatchEvent(new Event("input")); $("rule-composer").requestSubmit()')
            wait('return currentView !== null && !ruleBusy')
            assert len(ai.calls) == initial_calls + 1
            assert "Apply this contract to Summit only." in ai.calls[-1]["messages"][0]["content"]
            assert "Whole-hour billing from" in ai.calls[-1]["messages"][0]["content"]
            assert js('return document.querySelector(".rule-message.user").textContent.includes("Apply this contract") && document.querySelector(".rule-message.user").textContent.includes("test-contract.md")')
            assert js('return currentView.draft.code.includes("apply_rule")')
            assert js('return !("payer_id" in currentView.draft) && !("effective_from" in currentView.draft)')
            assert js('return !document.body.textContent.includes("Payer to confirm")')
            assert js('return document.querySelectorAll(".example-evidence").length === currentView.evaluation.examples.filter(e=>e.changed).length')
            assert js('return !document.body.textContent.includes("Unaffected baseline")')
            js('document.querySelector(".example-select").click(); const dt=new DataTransfer(); dt.items.add(new File(["# Follow-up context"], "follow-up.md")); $("rules-file").files=dt.files; $("rules-file").dispatchEvent(new Event("change")); $("rule-message").value="Please check this case again."; $("rule-message").dispatchEvent(new Event("input")); $("rule-composer").requestSubmit()')
            wait('return currentView.draft.messages.length === 4 && !ruleBusy')
            assert len(ai.calls) == initial_calls + 2
            assert 'Regarding this example:' in ai.calls[-1]['messages'][-1]['content']
            assert 'date_of_service' in ai.calls[-1]['messages'][-1]['content']
            assert '# Follow-up context' in ai.calls[-1]['messages'][-1]['content']
            assert js('return document.querySelectorAll(".rule-message.user")[1].textContent.includes("follow-up.md") && !document.querySelectorAll(".rule-message.user")[0].textContent.includes("follow-up.md")')
            assert js('return document.querySelectorAll(".rule-message.assistant").length === 2')
            assert js('return $("example-context").hidden')
            js('document.querySelectorAll(".rule-title-button")[1].click()')
            assert js('return selectedExample.code === currentView.draft.code')
            js('selectExample(null)')
            assert js('return [...document.querySelectorAll(".example-review")].every(row => row.querySelectorAll("button").length === 1 && row.querySelector("button").textContent === "Raise issue")')
            assert js('return [...document.querySelectorAll(".example-review")].every(row => !row.hidden)')
            assert js('return Object.keys(currentView.draft.decisions).length === 0')
            assert js('return currentView.evaluation.can_activate')
            assert js('return currentView.evaluation.execution.status === "passed" && document.querySelectorAll(".execution-check").length === 2')
            assert js('const ev=currentView.evaluation; const last=document.querySelectorAll(".update-output")[1]; return !ev.impact?.changed_count || last.querySelectorAll(".historical-changes .example").length === ev.impact.changed_count')

            assert js('return document.documentElement.scrollWidth <= window.innerWidth')
            if args.screenshots:
                args.screenshots.mkdir(parents=True, exist_ok=True)
                js('document.querySelectorAll(".rule-message.assistant")[1].scrollIntoView()')
                (args.screenshots / 'rules-desktop.png').write_bytes(base64.b64decode(wd('GET', f'/session/{sid}/screenshot')))
            wd('POST', f'/session/{sid}/window/rect', {'width': 390, 'height': 844})
            assert js('return document.documentElement.scrollWidth <= window.innerWidth')
            if args.screenshots:
                (args.screenshots / 'rules-mobile.png').write_bytes(base64.b64decode(wd('GET', f'/session/{sid}/screenshot')))
            assert js('return [...document.querySelectorAll(".activation-row")].filter(e=>!e.hidden).at(-1).querySelector("button").disabled')
            js('const box=[...document.querySelectorAll(".semantic-approval")].find(e=>!e.hidden); box.querySelectorAll("input[type=checkbox]").forEach(c=>{c.checked=true;c.dispatchEvent(new Event("change"))}); const reason=box.querySelector("textarea"); if(reason){reason.value="Reviewed test contract and service dates";reason.dispatchEvent(new Event("input"))}')
            js('[...document.querySelectorAll(".activation-row")].filter(e=>!e.hidden).at(-1).querySelector("button").click()')
            wait('return !ruleBusy && document.querySelector(".activation-success") !== null')
            assert js('return !$("rule-composer").hidden')
            js('document.querySelector("[data-tab=claims]").click(); $("use-sample-claims").click()')
            wait('return results !== null && results.rows.length > 0 && !$("results").hidden')
            js('const claim = results.rows.find(r => r.reference_data.evv_visits.length && r.reference_data.authorizations.some(a => a.referenced)); [...document.querySelectorAll("#claims-table tr.click")].find(row => row.children[0].textContent === String(claim.line)).click()')
            assert js('return !$("detail").hidden && $("detail").textContent.includes("EVV visits") && $("detail").textContent.includes("Authorization")')
            assert js('return $("detail").textContent.includes("verified minutes")')
            assert js('const claim = results.rows.find(r=>r.line===selectedLine); return claim.reference_data.evv_visits.every(v=>$("detail").textContent.includes(v.visit_id)) && claim.reference_data.authorizations.every(a=>$("detail").textContent.includes(a.auth_id))')
            if args.screenshots:
                js('document.querySelector(".claim-references").scrollIntoView({block:"start"})')
                (args.screenshots / 'claim-mobile.png').write_bytes(base64.b64decode(wd('GET', f'/session/{sid}/screenshot')))
                wd('POST', f'/session/{sid}/window/rect', {'width': 1280, 'height': 1100})
                js('document.querySelector(".claim-references").scrollIntoView({block:"start"})')
                (args.screenshots / 'claim-desktop.png').write_bytes(base64.b64decode(wd('GET', f'/session/{sid}/screenshot')))
                wd('POST', f'/session/{sid}/window/rect', {'width': 390, 'height': 844})
            assert js('return document.documentElement.scrollWidth <= window.innerWidth')
            js('[...$("mode-tools").querySelectorAll("button")].find(b=>b.textContent.includes("Start historical")).click()')
            wait('return $("mode-tools").textContent.includes("Historical demo")')
            assert js('return $("rules-empty").hidden === false && !$("rule-composer").hidden')
            js('[...$("mode-tools").querySelectorAll("button")].find(b=>b.textContent.includes("Fast forward")).click()')
            wait('return $("mode-tools").textContent.includes("2026-06-30")')
            assert js('return $("demo-report").hidden && $("results").hidden')
            assert js('return document.documentElement.scrollWidth <= window.innerWidth')
            js('[...$("mode-tools").querySelectorAll("button")].find(b=>b.textContent.includes("Return to full")).click()')
            wait('return $("mode-tools").textContent.includes("Start historical")')
            assert js('return $("demo-report").hidden')
            assert not js('return !$("error").hidden')
            print('PASS: initial composer, prompt-only/document-only/combined requests, attachment validation, revisions, acceptance, desktop/mobile, claim details and demo reset.')
        finally:
            if sid:
                wd('DELETE', f'/session/{sid}')
            app.end_demo()
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    main()
