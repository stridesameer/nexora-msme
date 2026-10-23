import unittest
from datetime import date

from backend.validation import MAX_BYTES, normalize_description, parse_amount, parse_date, validate_csv

HDR = "transaction_id,transaction_date,description,transaction_type,amount\n"


class ParseTests(unittest.TestCase):
    def test_amounts(self):
        self.assertEqual(parse_amount("1,200.50"), 120050)
        self.assertEqual(parse_amount("₹ 5000"), 500000)
        for bad in ("-5", "0", "abc", "NaN", "Infinity", "1.234", "", "1e999999"):
            with self.assertRaises(ValueError, msg=bad):
                parse_amount(bad)

    def test_dates(self):
        self.assertEqual(parse_date("2026-02-28"), date(2026, 2, 28))
        for bad in ("2026-02-30", "28/02/2026", "2026-2-8", "1999-01-01", "", "tomorrow"):
            with self.assertRaises(ValueError, msg=bad):
                parse_date(bad)

    def test_normalize(self):
        self.assertEqual(normalize_description("Staff salaries - Oct 2026"), "staff salaries")
        self.assertEqual(normalize_description("Marketing spend"), "marketing spend")  # 'mar' inside a word is kept


class ValidateTests(unittest.TestCase):
    def test_valid_minimal_columns_only(self):
        v = validate_csv(HDR + "A1,2026-10-01,Sale,income,100\nA2,2026-10-02,Rent,expense,50.5\n")
        self.assertEqual((len(v.accepted), len(v.rejected), v.fatal), (2, 0, []))
        self.assertEqual(v.accepted[1].amount, 5050)

    def test_missing_required_column(self):
        v = validate_csv("transaction_id,description,amount\nA1,x,5\n")
        self.assertTrue(v.fatal and "transaction_date" in v.fatal[0])

    def test_empty_and_header_only_and_whitespace(self):
        for text in ("", "   \n  ", HDR):
            self.assertTrue(validate_csv(text).fatal, repr(text))

    def test_non_text_and_binary(self):
        self.assertTrue(validate_csv(None).fatal)
        self.assertTrue(validate_csv("a,b\x00c\n1,2").fatal)

    def test_too_large(self):
        self.assertTrue(validate_csv(HDR + "x" * (MAX_BYTES + 1)).fatal)

    def test_bad_rows_rejected_with_messages(self):
        text = HDR + "\n".join([
            "G1,2026-10-01,ok,income,10",
            "B1,2026-13-01,bad date,income,10",
            "B2,2026-10-01,neg,expense,-10",
            "B3,2026-10-01,nan,expense,NaN",
            "B4,2026-10-01,type,refund,10",
            "G1,2026-10-02,dup id,income,10",
            ",2026-10-02,no id,income,10",
            "B5,2026-10-02,,income,10",
            "B6,2026-10-02,extra,income,10,surplus",
        ]) + "\n"
        v = validate_csv(text)
        self.assertEqual(len(v.accepted), 1)
        self.assertEqual(len(v.rejected), 8)
        joined = " ".join(" ".join(r["errors"]) for r in v.rejected)
        for needle in ("transaction_date", "negative", "not a finite", "transaction_type", "already used", "transaction_id is missing", "description is missing", "more values"):
            self.assertIn(needle, joined)

    def test_invalid_optional_date_rejects_row(self):
        v = validate_csv(HDR.strip() + ",due_date\nA1,2026-10-01,x,invoice,5,notadate\n")
        self.assertEqual(len(v.rejected), 1)
        self.assertIn("due_date", v.rejected[0]["errors"][0])

    def test_duplicate_looking_flagged_not_dropped(self):
        v = validate_csv(HDR + "A1,2026-10-01,Packaging 1,expense,100\nA2,2026-10-01,Packaging 2,expense,100\nA3,2026-10-02,Packaging,expense,100\n")
        self.assertEqual(len(v.accepted), 3)
        self.assertEqual(len(v.duplicate_groups), 1)
        self.assertTrue(any(w["code"] == "possible_duplicate" for w in v.warnings))

    def test_unknown_columns_and_opening_balance(self):
        v = validate_csv(HDR.strip() + ",opening_balance,colour\nA1,2026-10-01,x,income,5,1000,red\n")
        self.assertEqual(v.file_opening_balance, 100000)
        self.assertEqual(v.unknown_columns, ["colour"])

    def test_bom_and_case_insensitive_header(self):
        v = validate_csv("﻿Transaction_ID,TRANSACTION_DATE,Description,Transaction_Type,Amount\nA1,2026-10-01,x,income,5\n")
        self.assertEqual(len(v.accepted), 1)


if __name__ == "__main__":
    unittest.main()
