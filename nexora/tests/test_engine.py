import unittest
from datetime import date

from backend import engine

HDR = "transaction_id,transaction_date,description,transaction_type,amount,category,due_date,expected_payment_date,actual_payment_date,loan_repayment_due_date,opening_balance,linked_invoice_id\n"


def csv(*rows):
    return HDR + "\n".join(rows) + "\n"


def run(text, **settings):
    settings.setdefault("min_reserve", 0)
    r = engine.analyze(text, settings)
    assert r["ok"], r
    return r


def metric(r, key):
    return next(m for m in r["metrics"] if m["key"] == key)["value"]


def kinds(r):
    return [w["kind"] for w in r["warnings"]]


# Rows: id,date,desc,type,amount,category,due,expected,actual,loan_due,opening,linked
HEALTHY = csv(
    "I1,2026-10-10,Sales,income,50000,Sales,,,,,100000,",
    "E1,2026-10-13,Rent,expense,20000,Rent,,,,,,",
    "INV1,2026-10-15,Invoice A,invoice,30000,Sales,2026-10-25,2026-10-23,,,,",
    "B1,2026-10-15,Supplier,bill,10000,Inventory,2026-10-25,,,,,",
)


class HealthyCase(unittest.TestCase):
    def setUp(self):
        self.r = run(HEALTHY, as_of="2026-10-20")

    def test_balance_and_totals_match_hand_calculation(self):
        self.assertEqual(metric(self.r, "current_balance"), 130000)  # 100000 + 50000 - 20000
        self.assertEqual(metric(self.r, "total_income"), 50000)
        self.assertEqual(metric(self.r, "total_expenses"), 20000)
        self.assertEqual(metric(self.r, "outstanding_receivables"), 30000)
        self.assertEqual(metric(self.r, "overdue_receivables"), 0)

    def test_forecast_rows(self):
        rows = {x["date"]: x for x in self.r["forecast"]["rows"]}
        self.assertEqual(rows["2026-10-22"]["closing"], 130000)
        self.assertEqual(rows["2026-10-23"]["closing"], 160000)  # +30000 receipt
        self.assertEqual(rows["2026-10-25"]["closing"], 150000)  # -10000 bill
        self.assertEqual(len(self.r["forecast"]["rows"]), 30)
        h = self.r["forecast"]["horizons"]
        self.assertEqual(h["30"]["min_balance"], 130000)
        self.assertEqual(h["7"]["ending_balance"], 150000)  # 130000 + 30000 (Oct 23) - 10000 (Oct 25)

    def test_no_shortage_warnings(self):
        self.assertNotIn("cash_shortage", kinds(self.r))
        self.assertTrue(any(x["title"] == "Keep monitoring weekly" for x in self.r["recommendations"]))

    def test_no_generative_ai_claim(self):
        self.assertFalse(self.r["ai"]["generative_ai_used"])
        self.assertTrue(all(x["basis"] == "rule-based" for x in self.r["recommendations"]))


class OverdraftCase(unittest.TestCase):
    TEXT = csv(
        "I1,2026-10-01,Sales,income,1000,Sales,,,,,9000,",
        "B1,2026-10-15,Big supplier,bill,50000,Inventory,2026-10-22,,,,,",
    )

    def test_shortage_exact_gap(self):
        r = run(self.TEXT, as_of="2026-10-20")
        h = r["forecast"]["horizons"]["30"]
        self.assertEqual(h["min_balance"], -40000)
        self.assertEqual(h["funding_gap"], 40000)
        self.assertEqual(h["first_shortage_date"], "2026-10-22")
        self.assertEqual(h["days_negative"], 29)  # Oct 22 .. Nov 19
        w = next(w for w in r["warnings"] if w["kind"] == "cash_shortage")
        for field in ("severity", "title", "affected", "observed", "rule", "why", "next_step"):
            self.assertTrue(w[field], field)
        self.assertEqual(w["severity"], "high")
        self.assertEqual(h["contributors"][0]["amount"], 50000)
        self.assertEqual(r["health"]["dimensions"][0]["status"], "high_risk")

    def test_reserve_breach_only(self):
        r = run(csv("I1,2026-10-01,Sales,income,1000,Sales,,,,,9000,", "B1,2026-10-15,Supplier,bill,6000,Inventory,2026-10-22,,,,,"), as_of="2026-10-20", min_reserve=5000)
        self.assertEqual(r["forecast"]["horizons"]["30"]["funding_gap"], 0)
        self.assertEqual(r["forecast"]["horizons"]["30"]["reserve_gap"], 1000)  # 10000 - 6000 = 4000 vs reserve 5000
        self.assertIn("below_reserve", kinds(r))
        self.assertNotIn("cash_shortage", kinds(r))


class DelayedPaymentCase(unittest.TestCase):
    TEXT = csv(
        "I1,2026-10-01,Sales,income,1000,Sales,,,,,9000,",
        "INV1,2026-10-05,Invoice C,invoice,30000,Sales,2026-10-22,2026-10-22,,,,",
        "B1,2026-10-15,Supplier,bill,25000,Inventory,2026-10-23,,,,,",
    )

    def test_baseline_ok_then_delay_creates_gap(self):
        base = run(self.TEXT, as_of="2026-10-20")
        self.assertEqual(base["forecast"]["horizons"]["30"]["funding_gap"], 0)
        delayed = run(self.TEXT, as_of="2026-10-20", receipt_delay_days=3)
        h = delayed["forecast"]["horizons"]["30"]
        self.assertEqual(h["min_balance"], -15000)  # 10000 - 25000 on Oct 23, receipt only on Oct 25
        self.assertEqual(h["first_shortage_date"], "2026-10-23")
        self.assertEqual(delayed["forecast"]["scenario"]["baseline_funding_gap"], 0)
        self.assertEqual(delayed["forecast"]["scenario"]["scenario_funding_gap"], 15000)
        self.assertTrue(any(x["title"] == "Review delayed receipts" for x in delayed["recommendations"]))

    def test_changing_expected_date_recalculates(self):
        fixed = run(self.TEXT, as_of="2026-10-20", receipt_delay_days=3, payment_overrides={"INV1": "2026-10-22"})
        self.assertEqual(fixed["forecast"]["horizons"]["30"]["funding_gap"], 0)
        rec = next(x for x in fixed["receivables"] if x["id"] == "INV1")
        self.assertTrue(rec["overridden"])
        self.assertEqual(rec["forecast_date"], "2026-10-22")
        moved = run(self.TEXT, as_of="2026-10-20", payment_overrides={"INV1": "2026-10-28"})
        self.assertEqual(moved["forecast"]["horizons"]["30"]["min_balance"], -15000)

    def test_expense_adjustment(self):
        r = run(self.TEXT, as_of="2026-10-20", expense_adjust_pct=20)
        row = next(x for x in r["forecast"]["rows"] if x["date"] == "2026-10-23")
        self.assertEqual(row["outflow"], 30000)  # 25000 * 1.2
        self.assertEqual(metric(r, "upcoming_expenses"), 30000)


class OverdueCase(unittest.TestCase):
    TEXT = csv(
        "I1,2026-10-01,Sales,income,1000,Sales,,,,,9000,",
        "INV1,2026-09-01,Late customer,invoice,20000,Sales,2026-10-01,,,,,",
    )

    def test_overdue_metrics_and_warning(self):
        r = run(self.TEXT, as_of="2026-10-20")
        self.assertEqual(metric(r, "overdue_receivables"), 20000)
        w = next(w for w in r["warnings"] if w["kind"] == "overdue_receivable")
        self.assertIn("19 day", w["observed"])
        self.assertEqual(w["severity"], "medium")  # 8-30 days
        row = next(x for x in r["forecast"]["rows"] if x["date"] == "2026-10-27")
        self.assertEqual(row["inflow"], 20000)  # as_of + 7-day collection lag

    def test_overdue_excluded_when_assumption_off(self):
        r = run(self.TEXT, as_of="2026-10-20", collect_overdue=False)
        self.assertEqual(sum(x["inflow"] for x in r["forecast"]["rows"]), 0)
        self.assertEqual(len(r["forecast"]["excluded"]), 1)


class LoanCase(unittest.TestCase):
    def test_upcoming_loan_and_repayment_risk(self):
        text = csv("I1,2026-10-01,Sales,income,1000,Sales,,,,,19000,", "L1,2026-10-01,Bank EMI,loan_repayment,35000,Loan,,,,2026-10-30,,")
        r = run(text, as_of="2026-10-20")
        self.assertEqual(metric(r, "upcoming_loan_repayments"), 35000)
        self.assertIn("repayment_at_risk", kinds(r))
        self.assertEqual(r["health"]["dimensions"][4]["status"], "high_risk")

    def test_loan_covered(self):
        text = csv("I1,2026-10-01,Sales,income,1000,Sales,,,,,199000,", "L1,2026-10-01,Bank EMI,loan_repayment,35000,Loan,,,,2026-10-30,,")
        r = run(text, as_of="2026-10-20")
        self.assertNotIn("repayment_at_risk", kinds(r))
        self.assertEqual(r["health"]["dimensions"][4]["status"], "healthy")  # 200000/35000 = 5.7x

    def test_loan_not_changed_by_expense_adjust(self):
        text = csv("I1,2026-10-01,Sales,income,1000,Sales,,,,,199000,", "L1,2026-10-01,Bank EMI,loan_repayment,35000,Loan,,,,2026-10-30,,")
        r = run(text, as_of="2026-10-20", expense_adjust_pct=50)
        self.assertEqual(metric(r, "upcoming_loan_repayments"), 35000)


class NoDoubleCounting(unittest.TestCase):
    def test_invoice_settled_by_linked_income_counted_once(self):
        text = csv(
            "I0,2026-10-01,Sales,income,1000,Sales,,,,,0,",
            "INV1,2026-10-02,Customer invoice,invoice,20000,Sales,2026-10-10,,,,,",
            "R1,2026-10-09,Bank receipt customer,income,20000,Sales,,,,,,INV1",
        )
        r = run(text, as_of="2026-10-12")
        self.assertEqual(metric(r, "total_income"), 21000)
        self.assertEqual(metric(r, "outstanding_receivables"), 0)
        self.assertEqual(metric(r, "current_balance"), 21000)

    def test_paid_invoice_counted_once_via_actual_date(self):
        text = csv("I0,2026-10-01,Sales,income,1000,Sales,,,,,0,", "INV1,2026-10-02,Customer,invoice,20000,Sales,2026-10-10,,2026-10-09,,,")
        r = run(text, as_of="2026-10-12")
        self.assertEqual(metric(r, "total_income"), 21000)
        self.assertEqual(metric(r, "outstanding_receivables"), 0)


class AnomalyCases(unittest.TestCase):
    def test_unusually_large_expense(self):
        rows = [f"R{i},2026-09-{i + 1:02d},Repairs,expense,1000,Repairs,,,,,{'0' if i == 0 else ''}," for i in range(5)]
        rows.append("R9,2026-10-10,Generator repair,expense,20000,Repairs,,,,,,")
        r = run(csv(*rows), as_of="2026-10-12")
        w = [w for w in r["warnings"] if w["kind"] == "large_expense"]
        self.assertEqual(len(w), 1)
        self.assertIn("R9", w[0]["affected"])
        self.assertEqual(w[0]["severity"], "high")  # > 5x median

    def test_no_large_expense_without_history(self):
        r = run(csv("A,2026-10-01,Repairs,expense,1000,Repairs,,,,,0,", "B,2026-10-02,Repairs,expense,99000,Repairs,,,,,,"), as_of="2026-10-05")
        self.assertNotIn("large_expense", kinds(r))
        self.assertTrue(r["checks_skipped"])

    def test_duplicates_surface_as_warning(self):
        r = run(csv("A,2026-10-01,Packaging,expense,4850,Supplies,,,,,0,", "B,2026-10-01,Packaging,expense,4850,Supplies,,,,,,"), as_of="2026-10-05")
        self.assertIn("possible_duplicate", kinds(r))
        self.assertEqual(metric(r, "total_expenses"), 9700)  # kept, not silently removed

    def test_income_drop(self):
        rows = ["A0,2026-09-01,Sales,income,1000,Sales,,,,,0,"]
        rows += ["P1,2026-09-30,Big sale,income,100000,Sales,,,,,,", "N1,2026-10-20,Small sale,income,10000,Sales,,,,,,"]
        r = run(csv(*rows), as_of="2026-10-26")
        self.assertIn("income_drop", kinds(r))

    def test_category_shift(self):
        rows = ["A0,2026-09-01,Sales,income,1000,Sales,,,,,0,", "M1,2026-10-02,Ads,expense,1000,Marketing,,,,,,", "M2,2026-10-20,Ads,expense,12000,Marketing,,,,,,"]
        r = run(csv(*rows), as_of="2026-10-26")
        self.assertIn("category_shift", kinds(r))

    def test_missing_info_warnings(self):
        r = run(csv("A,2026-10-01,Sales,income,1000,Sales,,,,,0,", "INV,2026-10-02,No dates,invoice,500,Sales,,,,,,", "E,2026-10-03,Mystery,expense,100,,,,,,,"), as_of="2026-10-05")
        titles = [w["title"] for w in r["warnings"]]
        self.assertIn("Invoices without a due date", titles)
        self.assertIn("Pending items left out of the forecast", titles)
        self.assertIn("Expenses without a category", titles)

    def test_nothing_invented_for_tiny_dataset(self):
        r = run(csv("A,2026-10-01,Sales,income,1000,Sales,,,,,5000,"), as_of="2026-10-02")
        self.assertEqual(r["warnings"], [])
        self.assertIsNone(metric(r, "avg_daily_outflow"))  # < 7 days of history
        self.assertIsNone(metric(r, "runway_days"))
        stab = next(d for d in r["health"]["dimensions"] if d["key"] == "stability")
        self.assertEqual(stab["status"], "unknown")


class RecurringCases(unittest.TestCase):
    def test_weekly_recurring_projected(self):
        rows = ["S0,2026-09-01,Sales,income,1000,Sales,,,,,0,"] + [f"F{i},2026-09-{d:02d},Freight charges,expense,1000,Transport,,,,,," for i, d in enumerate((5, 12, 19, 26))]
        r = run(csv(*rows), as_of="2026-10-02")
        rec = r["forecast"]["recurring"][0]
        self.assertEqual(rec["period"], "weekly")
        self.assertEqual(rec["projected_dates"][:2], ["2026-10-03", "2026-10-10"])
        row = next(x for x in r["forecast"]["rows"] if x["date"] == "2026-10-03")
        self.assertEqual(row["outflow"], 1000)

    def test_recurring_not_double_counted_with_explicit_bill(self):
        rows = ["S0,2026-08-01,Sales,income,1000,Sales,,,,,0,", "R1,2026-08-01,Shop rent Aug,expense,40000,Rent,,,,,,", "R2,2026-09-01,Shop rent Sep,expense,40000,Rent,,,,,,",
                "R3,2026-09-20,Shop rent Oct,bill,40000,Rent,2026-10-01,2026-10-01,,,,"]
        r = run(csv(*rows), as_of="2026-09-25")
        total_out = sum(x["outflow"] for x in r["forecast"]["rows"])
        self.assertEqual(total_out, 40000)

    def test_variable_spend(self):
        r = run(csv("A,2026-10-01,Sales,income,1000,Sales,,,,,5000,"), as_of="2026-10-02", daily_variable_spend=100)
        self.assertEqual(sum(x["outflow"] for x in r["forecast"]["rows"]), 3000)


class SettingsAndHelpers(unittest.TestCase):
    def test_bad_settings_rejected(self):
        for bad in ({"receipt_delay_days": -1}, {"expense_adjust_pct": 9999}, {"min_reserve": "abc"}, {"as_of": "31/12/2026"}, {"payment_overrides": {"x": "nope"}}, {"opening_balance": float("nan")}):
            with self.assertRaises(ValueError, msg=str(bad)):
                engine.clean_settings(bad)

    def test_opening_balance_user_overrides_file(self):
        r = run(HEALTHY, as_of="2026-10-20", opening_balance=0)
        self.assertEqual(metric(r, "current_balance"), 30000)
        self.assertEqual(r["meta"]["opening_balance_source"], "user")

    def test_inr_and_add_months(self):
        self.assertEqual(engine.inr(12345600), "₹1,23,456")
        self.assertEqual(engine.inr(-50000), "-₹500")
        self.assertEqual(engine.add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(engine.add_months(date(2026, 11, 5), 2), date(2027, 1, 5))

    def test_invalid_csv_returns_error_not_exception(self):
        r = engine.analyze("", {})
        self.assertFalse(r["ok"])
        r = engine.analyze("a,b\n1,2\n", {})
        self.assertFalse(r["ok"])

    def test_period_filter(self):
        rows = ["O,2026-09-01,Old sale,income,1000,Sales,,,,,0,", "N,2026-10-10,New sale,income,500,Sales,,,,,,"]
        r = run(csv(*rows), as_of="2026-10-12", period_days=7)
        self.assertEqual(metric(r, "total_income"), 500)
        self.assertEqual(metric(r, "current_balance"), 1500)  # balance always uses full history


if __name__ == "__main__":
    unittest.main()
