"""Generate the synthetic demo dataset (deterministic).  python scripts/generate_sample.py [--as-of YYYY-MM-DD]

All data is invented. The story: a small wholesale/retail shop whose biggest customer pays late
while a large supplier bill and a loan EMI fall due, creating a projected cash shortfall.
"""
import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "sample_transactions.csv"
COLS = ["transaction_id", "transaction_date", "description", "transaction_type", "amount", "category", "due_date",
        "expected_payment_date", "actual_payment_date", "loan_repayment_due_date", "opening_balance", "linked_invoice_id"]
TARGET_BALANCE = 170_000  # cash on the reporting date


def build(as_of: date):
    rng = random.Random(42)
    start = as_of - timedelta(days=52)
    rows, n = [], [0]

    def add(d, desc, typ, amt, cat="", due="", exp="", act="", loan="", linked=""):
        n[0] += 1
        rows.append({"transaction_id": f"T{n[0]:04d}", "transaction_date": d.isoformat(), "description": desc, "transaction_type": typ,
                     "amount": f"{amt:.2f}".rstrip("0").rstrip("."), "category": cat,
                     "due_date": due.isoformat() if due else "", "expected_payment_date": exp.isoformat() if exp else "",
                     "actual_payment_date": act.isoformat() if act else "", "loan_repayment_due_date": loan.isoformat() if loan else "",
                     "opening_balance": "", "linked_invoice_id": linked})

    day = lambda k: as_of - timedelta(days=k)
    # daily cash sales (closed Sundays)
    for i in range(53):
        d = start + timedelta(days=i)
        if d.weekday() != 6:
            add(d, f"Counter sales {d.isoformat()}", "income", round(rng.uniform(7000, 12500), -1), "Sales")
    # monthly fixed costs (1st = rent + salaries, 3rd = electricity, 5th = loan EMI, 8th = broadband)
    d = date(start.year, start.month, 1)
    for _ in range(3):
        if start <= d <= as_of:
            add(d, f"Shop rent {d.strftime('%b')}", "expense", 40000, "Rent")
            add(d, f"Staff salaries {d.strftime('%b')}", "expense", 90000, "Payroll")
        for dd, desc, amt, cat, typ in ((d.replace(day=3), "Electricity bill", 13200 + rng.randint(0, 1500), "Utilities", "bill"),
                                        (d.replace(day=5), "Business loan EMI", 35000, "Loan", "loan_repayment"),
                                        (d.replace(day=8), "Broadband and phone", 1499, "Utilities", "bill")):
            if start <= dd <= as_of:
                if typ == "bill":
                    add(dd, desc, "bill", amt, cat, due=dd, act=dd)
                else:
                    add(dd, desc, "loan_repayment", amt, cat, loan=dd, act=dd)
        d = date(d.year + (d.month // 12), d.month % 12 + 1, 1)
    # weekly freight (Saturdays)
    for i in range(53):
        d = start + timedelta(days=i)
        if d.weekday() == 5:
            add(d, "Freight charges", "expense", round(rng.uniform(3000, 4200), -1), "Transport")
    # small repairs and marketing, then a few anomalies
    for k, amt in ((46, 1800), (38, 2200), (30, 1500), (22, 2500), (17, 1900)):
        add(day(k), "Shop repairs and maintenance", "expense", amt, "Repairs")
    add(day(9), "Emergency generator repair", "expense", 48000, "Repairs")
    for k, amt in ((40, 2500), (33, 3000), (27, 2800)):
        add(day(k), "Local advertising", "expense", amt, "Marketing")
    add(day(11), "Festive season campaign", "expense", 14000, "Marketing")
    add(day(5), "Social media ads", "expense", 6000, "Marketing")
    add(day(14), "Packaging materials", "expense", 4850, "Supplies")      # duplicate pair
    add(day(14), "Packaging materials", "expense", 4850, "Supplies")
    add(day(20), "Tea, snacks and cleaning", "expense", 1250, "")        # missing category
    # supplier purchases (paid)
    add(day(48), "Apex Distributors stock purchase", "bill", 180000, "Inventory", due=day(44), act=day(44))
    add(day(34), "Gupta Wholesale stock purchase", "bill", 75000, "Inventory", due=day(34), act=day(34))
    add(day(26), "Apex Distributors restock", "bill", 95000, "Inventory", due=day(20), act=day(20))
    add(day(12), "Bharat Packaging order", "bill", 22000, "Supplies", due=day(8), act=day(8))
    # customer invoices already paid
    add(day(49), "Invoice Kumar Hardware", "invoice", 140000, "Sales", due=day(34), exp=day(34), act=day(30))
    add(day(40), "Invoice Lotus Mart", "invoice", 85000, "Sales", due=day(25), exp=day(25), act=day(24))
    add(day(30), "Invoice Sunrise Pharma earlier order", "invoice", 56000, "Sales", due=day(17), exp=day(17), act=day(16))
    # pending receivables
    add(day(45), "Invoice Meena Retail", "invoice", 180000, "Sales", due=day(18), exp=day(3))        # overdue, promise missed
    add(day(35), "Invoice Sunrise Pharma", "invoice", 38000, "Sales", due=day(8), exp=as_of + timedelta(days=2))
    add(day(12), "Invoice Kumar Hardware", "invoice", 95000, "Sales", due=as_of + timedelta(days=7), exp=as_of + timedelta(days=7))
    add(day(6), "Invoice Lotus Mart", "invoice", 62000, "Sales", due=as_of + timedelta(days=12), exp=as_of + timedelta(days=20))
    add(day(4), "Invoice Raj Electricals", "invoice", 22000, "Sales")                                # no dates at all
    # upcoming payables
    add(day(7), "Apex Distributors stock purchase", "bill", 220000, "Inventory", due=as_of + timedelta(days=5), exp=as_of + timedelta(days=5))
    nxt = date(as_of.year + (as_of.month // 12), as_of.month % 12 + 1, 1)
    add(day(2), f"Shop rent {nxt.strftime('%b')}", "bill", 40000, "Rent", due=nxt, exp=nxt)
    add(day(2), f"Staff salaries {nxt.strftime('%b')}", "bill", 90000, "Payroll", due=nxt, exp=nxt)
    add(day(1), "Electricity bill", "bill", 14400, "Utilities", due=nxt.replace(day=3), exp=nxt.replace(day=3))
    add(day(1), "Business loan EMI", "loan_repayment", 35000, "Loan", loan=nxt.replace(day=5))
    rows = [r for r in rows if r["transaction_date"] >= start.isoformat()]
    rows.sort(key=lambda r: (r["transaction_date"], r["transaction_id"]))
    # opening balance chosen so that the cash balance on the reporting date equals TARGET_BALANCE
    net = 0
    for r in rows:
        t, a = r["transaction_type"], float(r["amount"])
        if t in ("income", "expense"):
            when = r["transaction_date"]
        else:
            when = r["actual_payment_date"]
        if when and when <= as_of.isoformat():
            net += a if t in ("income", "invoice") else -a
    rows[0]["opening_balance"] = str(int(TARGET_BALANCE - net))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=date.today().isoformat())
    args = ap.parse_args()
    rows = build(date.fromisoformat(args.as_of))
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    main()
