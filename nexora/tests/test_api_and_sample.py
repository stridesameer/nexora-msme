import json
import unittest
from pathlib import Path

from backend import engine
from backend.app import handle_request

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample_transactions.csv"


def post(path, payload):
    s, h, b = handle_request("POST", path, json.dumps(payload).encode())
    return s, json.loads(b)


class ApiTests(unittest.TestCase):
    def test_static_and_health(self):
        for p in ("/", "/style.css", "/app.js"):
            s, h, b = handle_request("GET", p)
            self.assertEqual(s, 200, p)
            self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        s, h, b = handle_request("GET", "/api/health")
        self.assertEqual(json.loads(b)["status"], "ok")

    def test_sample_download(self):
        s, h, b = handle_request("GET", "/sample_transactions.csv")
        self.assertEqual(s, 200)
        self.assertIn("attachment", h["Content-Disposition"])
        self.assertTrue(b.startswith(b"transaction_id"))

    def test_unknown_paths_and_traversal(self):
        for p in ("/nope", "/../backend/engine.py", "/..%2fbackend/app.py", "/backend/engine.py", "/data/sample_transactions.csv"):
            self.assertEqual(handle_request("GET", p)[0], 404, p)
        self.assertEqual(handle_request("GET", "/api/analyze")[0], 405)
        self.assertEqual(handle_request("DELETE", "/api/analyze")[0], 405)

    def test_bad_requests(self):
        self.assertEqual(handle_request("POST", "/api/analyze", b"{not json")[0], 400)
        self.assertEqual(handle_request("POST", "/api/analyze", b"[]")[0], 400)
        self.assertEqual(handle_request("POST", "/api/analyze", b'{"csv": 5}')[0], 400)
        self.assertEqual(handle_request("POST", "/api/analyze", b"x" * 3_000_000)[0], 413)
        s, body = post("/api/analyze", {"csv": "a,b\n1,2", "settings": {}})
        self.assertEqual(s, 422)
        self.assertFalse(body["ok"])
        s, body = post("/api/analyze", {"csv": SAMPLE.read_text(), "settings": {"receipt_delay_days": -4}})
        self.assertEqual(s, 400)

    def test_validate_endpoint_empty_upload(self):
        s, body = post("/api/validate", {"csv": ""})
        self.assertEqual(s, 200)
        self.assertFalse(body["ok"])
        self.assertTrue(body["fatal_errors"])


class SampleDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SAMPLE.read_text(encoding="utf-8")
        cls.r = engine.analyze(cls.text, {})

    def test_sample_is_clean_and_covers_30_plus_days(self):
        v = self.r["validation"]
        self.assertEqual((v["rejected_count"], v["fatal_errors"]), (0, []))
        self.assertGreaterEqual(v["accepted_count"], 90)
        self.assertGreaterEqual(self.r["meta"]["period_days"], 30)
        self.assertEqual(self.r["meta"]["opening_balance_source"], "file")

    def test_sample_balance_matches_design(self):
        self.assertEqual(next(m for m in self.r["metrics"] if m["key"] == "current_balance")["value"], 170000)

    def test_sample_demonstrates_all_warning_types(self):
        kinds = {w["kind"] for w in self.r["warnings"]}
        for k in ("cash_shortage", "possible_duplicate", "large_expense", "category_shift", "income_drop", "overdue_receivable", "missing_info"):
            self.assertIn(k, kinds)
        for w in self.r["warnings"]:
            for f in ("severity", "title", "affected", "observed", "rule", "why", "next_step"):
                self.assertTrue(w[f] not in (None, ""), (w["kind"], f))
            self.assertNotIn("fraud", (w["title"] + w["why"]).lower().replace("not evidence of fraud", ""))

    def test_moving_late_payment_resolves_shortage(self):
        meena = next(x for x in self.r["receivables"] if "Meena" in x["description"])
        self.assertGreater(self.r["forecast"]["horizons"]["30"]["funding_gap"], 0)
        fixed = engine.analyze(self.text, {"payment_overrides": {meena["id"]: "2026-10-27"}})
        self.assertEqual(fixed["forecast"]["horizons"]["30"]["funding_gap"], 0)
        self.assertNotIn("cash_shortage", [w["kind"] for w in fixed["warnings"]])

    def test_forecast_rows_are_internally_consistent(self):
        rows = self.r["forecast"]["rows"]
        self.assertEqual(len(rows), 30)
        for prev, cur in zip(rows, rows[1:]):
            self.assertAlmostEqual(prev["closing"], cur["opening"], places=2)
        for x in rows:
            self.assertAlmostEqual(x["opening"] + x["inflow"] - x["outflow"], x["closing"], places=2)


if __name__ == "__main__":
    unittest.main()
