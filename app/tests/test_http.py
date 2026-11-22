"""End-to-end through the real HTTP server (no browser): upload -> results -> corrections -> export,
and a rule update that changes outcomes by service date only."""
import csv
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from claimcheck.ai import AIClient
from claimcheck.session import Session
from server import make_server
from tests.test_drafts import UPDATE, base_store, fake_ai
from tests.test_pipeline import AUTHS, EVV, claim_row, csv_text

HEAD = ["claim_id", "agency_id", "payer", "patient_id", "service_code", "date_of_service", "submitted_date",
        "units", "auth_id", "modifier", "frequency_code", "original_claim_id", "billed_amount"]


class Api:
    def __init__(self, base):
        self.base = base

    def call(self, method, path, data=None, raw=False):
        body = data if isinstance(data, (bytes, type(None))) else (data.encode() if isinstance(data, str) else json.dumps(data).encode())
        req = urllib.request.Request(self.base + path, data=body, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                payload = r.read().decode()
                return r.status, payload if raw else json.loads(payload)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())


class HttpCase(unittest.TestCase):
    ai = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.session = Session(Path(self.tmp.name), autoload=False, ai=self.ai)
        self.server = make_server(self.session, 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.api = Api(f"http://127.0.0.1:{self.server.server_address[1]}")
        for kind, text in (("authorizations", AUTHS), ("evv", EVV)):
            self.assertEqual(self.api.call("POST", f"/api/data/{kind}", text)[0], 200)
        self.assertEqual(self.api.call("POST", "/api/data/history", csv_text([], HEAD + ["status", "paid_amount", "denial_code", "remark"]))[0], 200)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()


FIXTURE = [
    claim_row(cid="C000001"),                                                       # payable (8 units, 180 min)
    claim_row(cid="C000002", payer="Summit Care", dos="07/02/2026", sub="07/05/2026", units="1"),   # formatting only
    claim_row(cid="C000003", dos="2026-07-03", auth="", units="1"),                 # D07
    claim_row(cid="C000004", dos="2026-07-04", sub="2026-10-04", units="1"),        # D03 (92 days)
    claim_row(cid="C000005", payer="Acme", dos="2026-07-05"),                       # malformed payer
    claim_row(cid="C000006", dos="2026-07-01", units="1", sub="2026-07-09"),        # D05 duplicate of C000001
]


class UploadToResults(HttpCase):
    def test_full_flow(self):
        code, res = self.api.call("POST", "/api/claims", csv_text(FIXTURE))
        self.assertEqual(code, 200)
        by = {r["claim_id"]: r for r in res["rows"]}
        self.assertEqual(by["C000001"]["category"], "payable")
        self.assertEqual(by["C000002"]["category"], "payable")
        self.assertEqual({c["field"] for c in by["C000002"]["changes"]}, {"payer", "date_of_service", "submitted_date"})
        self.assertEqual(by["C000003"]["denial_codes"], ["D07"])
        self.assertEqual(by["C000004"]["denial_codes"], ["D03"])
        self.assertEqual(by["C000005"]["category"], "must_correct")
        self.assertEqual(by["C000006"]["denial_codes"], ["D05"])
        self.assertEqual(res["summary"]["by_category"], {"payable": 2, "denied": 3, "must_correct": 1})

        # apply a proposed fix (user-initiated): C000003 gets the suggested authorization
        proposed = by["C000003"]["proposed_changes"]["auth_id"]["value"]
        code, res = self.api.call("POST", "/api/claims/line/4/edit", {"fields": {"auth_id": proposed}})
        by = {r["claim_id"]: r for r in res["rows"]}
        self.assertEqual(by["C000003"]["denial_codes"], [])
        self.assertEqual(by["C000003"]["edits"], {"auth_id": proposed})
        # fix the malformed payer
        _, res = self.api.call("POST", "/api/claims/line/6/edit", {"fields": {"payer": "Northwind"}})
        self.assertEqual({r["claim_id"]: r for r in res["rows"]}["C000005"]["category"], "denied")  # no auth for PY01

        # override a classification; it is recorded, prediction retained
        code, res = self.api.call("POST", "/api/claims/C000004/override", {"status": "paid", "note": "payer extension"})
        row = {r["claim_id"]: r for r in res["rows"]}["C000004"]
        self.assertEqual((row["status"], row["predicted_status"], row["category"]), ("paid", "denied", "denied"))
        self.assertEqual(self.api.call("POST", "/api/claims/C000004/override", {"status": "denied"})[0], 400)  # needs a code

        text = self.api.call("GET", "/api/export.csv", raw=True)[1]
        out = {r["claim_id"]: r for r in csv.DictReader(io.StringIO(text))}
        self.assertEqual(out["C000002"]["payer"], "Summit Care")       # source preserved
        self.assertEqual(out["C000003"]["auth_id"], "")
        self.assertIn("auth_id=", out["C000003"]["user_edits"])
        self.assertEqual(out["C000004"]["user_status"], "paid")
        self.assertEqual(out["C000004"]["predicted_denial_codes"], "D02;D03")  # fixing C000003 consumed the last unit
        self.assertEqual(len(out), 6)

    def test_bad_inputs_are_rejected_cleanly(self):
        self.assertEqual(self.api.call("POST", "/api/claims", "claim_id,payer\nC1,PY01\n")[0], 400)
        self.assertEqual(self.api.call("POST", "/api/claims/line/99/edit", {"fields": {}})[0], 404)
        self.assertEqual(self.api.call("POST", "/api/claims/line/2/edit", {"fields": {"status": "x"}})[0], 404)
        self.assertEqual(self.api.call("GET", "/../server.py")[0], 404)
        code, status = self.api.call("GET", "/api/status")
        self.assertNotIn("key", json.dumps(status).lower().replace("ai_available", ""))


class RuleUpdateByDate(HttpCase):
    ai = fake_ai(UPDATE)

    def test_update_changes_only_affected_service_dates(self):
        self.session.rules = base_store()
        self.session.rules.path = Path(self.tmp.name) / "rules.json"
        self.session.rules.add_version("PY01", "2026-07-01", {"authorization": {"required": False}})  # keep fixture simple
        # NB: 'authorization' change keeps the claims free of other failures
        claims = csv_text([
            claim_row(cid="C000011", payer="PY01", svc="T1005", dos="2026-07-31", sub="2026-09-09", units="1", auth=""),
            claim_row(cid="C000012", payer="PY01", svc="T1005", dos="2026-08-01", sub="2026-09-10", units="1", auth=""),
            claim_row(cid="C000013", payer="PY03", svc="T1005", dos="2026-08-01", sub="2026-09-10", units="1"),
        ])
        evv = EVV + "V000020,CG0001,PT0001,T1005,2026-07-31 09:00,2026-07-31 10:00,verified\n" \
                    "V000021,CG0001,PT0001,T1005,2026-08-01 09:00,2026-08-01 10:00,verified\n"
        self.api.call("POST", "/api/data/evv", evv)
        _, res = self.api.call("POST", "/api/claims", claims)
        before = {r["claim_id"]: r["denial_codes"] for r in res["rows"]}
        self.assertEqual(before, {"C000011": ["D03"], "C000012": ["D03"], "C000013": []})

        code, draft = self.api.call("POST", "/api/drafts", {"message": "Northwind: 50 days from 1 Aug"})
        self.assertEqual(code, 200)
        did = draft["draft"]["id"]
        ev = draft["evaluation"]
        self.assertFalse(ev["can_activate"])
        self.assertEqual(self.api.call("POST", f"/api/drafts/{did}/activate", {})[0], 400)
        for r in ev["examples"]:
            code, draft = self.api.call("POST", f"/api/drafts/{did}/decide",
                                        {"example_id": r["id"], "decision": "set", "codes": r["actual"]["codes"]})
            self.assertEqual(code, 200)
        self.assertTrue(draft["evaluation"]["can_activate"])
        code, out = self.api.call("POST", f"/api/drafts/{did}/activate", {})
        self.assertEqual(code, 200)
        self.assertEqual(out["version"]["params"]["timely_filing_days"], 50)
        # saved to disk as a versioned rule set
        saved = json.loads((Path(self.tmp.name) / "rules.json").read_text())
        self.assertEqual(len(saved["payers"]["PY01"]["versions"]), 3)

        _, res = self.api.call("GET", "/api/results")
        after = {r["claim_id"]: r["denial_codes"] for r in res["rows"]}
        self.assertEqual(after, {"C000011": ["D03"], "C000012": [], "C000013": []})

    def test_manual_edit_path_works_without_ai(self):
        code, d = self.api.call("POST", "/api/drafts", {"payer_id": "PY03", "effective_from": "2026-10-01",
                                                        "params": {"timely_filing_days": 90}, "source": "memo"})
        self.assertEqual(code, 200)
        self.assertTrue(d["evaluation"]["examples"])
        self.assertEqual(self.api.call("PUT", f"/api/drafts/{d['draft']['id']}", {"params": {"timely_filing_days": "x"}})[0], 400)


class NoKey(HttpCase):
    def test_chat_without_key_is_a_clear_error(self):
        import os
        old = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            self.session.ai = AIClient()
            code, body = self.api.call("POST", "/api/drafts", {"message": "hi"})
            self.assertEqual(code, 503)
            self.assertIn("No API key", body["error"])
        finally:
            if old:
                os.environ["ANTHROPIC_API_KEY"] = old


if __name__ == "__main__":
    unittest.main()
