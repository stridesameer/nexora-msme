# NEXORA architecture

```
Browser (HTML/CSS/JS, native SVG chart)
   │  POST /api/validate  { csv }
   │  POST /api/analyze   { csv, settings }     (stateless: CSV is re-sent on every recalculation)
   ▼
backend/app.py  ── handle_request(method, path, body) ──┬─ local: backend/server.py (127.0.0.1)
                                                        └─ AWS: Lambda Function URL → lambda_handler.py
   ▼
backend/validation.py → backend/engine.py   (pure Python, standard library)
```

One router serves both environments, so what you test locally is what runs on Lambda. The Lambda also serves the static frontend, so no S3 or API Gateway is needed. Nothing is written to disk or a database.

## Calculation rules

All money is integer paise internally.

- **Cash balance** = opening balance + cash received − cash paid, up to the reporting date. Cash received/paid = `income`/`expense` rows on `transaction_date`, plus invoices/bills/loans on `actual_payment_date`. Records dated after the reporting date are ignored (and a notice is shown).
- **Reporting date** = your setting, else the latest realised transaction date.
- **Opening balance** = your setting, else the value in the file, else ₹0 (stated on screen).
- **Total income / expenses** = cash in / out inside the selected period. Loan repayments count as expenses (cash out).
- **Outstanding receivables** = unpaid invoices dated on/before the reporting date. **Overdue** = unpaid with `due_date` before the reporting date.
- **Average daily outflow** = expenses ÷ days in period (≥ 7 days of history, else "Insufficient data"). **Runway** = balance ÷ average daily outflow.
- **No double counting**: a paid invoice counts once (via `actual_payment_date`). An `income` row with `linked_invoice_id` settles that invoice; the invoice itself is then ignored.

## Forecast (deterministic baseline, 30 days, 7-day view is the first 7 rows)

Start from the current balance. For each day add scheduled receipts, subtract scheduled payments:

- Receivable arrives on override date, else `expected_payment_date`, else `due_date`, plus your receipt delay (not applied to dates you set yourself).
- Already-late receivable: assumed collected `overdue_lag_days` after the reporting date (or excluded if you turn that off).
- Bills/loans: paid on override, else expected, else due date (loans: `loan_repayment_due_date` first). Already-late payables are assumed paid next day.
- Recurring expenses: a description (months and digits stripped) seen ≥ 4× weekly/bi-weekly or ≥ 2× monthly with consistent gaps (median gap and ≥ 80% of gaps near it) and amounts within ±25% of their mean is projected forward at the average of its last 3 amounts. A projected occurrence is skipped if an explicit pending bill with the same description is within 7 days.
- Expense adjustment % applies to bills, recurring and variable spend, not loan repayments. Variable spend is the per-day amount you enter (a suggestion computed from non-recurring history is offered).
- Not assumed: future sales, new invoices, anything unscheduled.

Per horizon: lowest balance and date, days negative, days below reserve, funding gap = max(0, −lowest), reserve gap = max(0, reserve − lowest), top payments up to the low point, receipts arriving after it. If you change delay/dates/expense %, the unchanged-data result is also computed for comparison.

## Warning rules (thresholds in `engine.py`)

| Check | Fires when |
|---|---|
| Possible duplicate | same date, amount, type, normalised description |
| Large expense | amount > max(2.5 × category median, mean + 2 std) using ≥ 4 other expenses in the category; "high" if > 5× median |
| Category spike | last-14-day spend ≥ 1.5 × previous 14 days and rise ≥ ₹5,000 (needs 28 days) |
| Income drop | last-14-day receipts < 60% of previous 14 days, previous ≥ ₹5,000 (needs 28 days) |
| Overdue invoice | unpaid, due date passed (> 30 d high, > 7 d medium) |
| Missing info | invoice without due date; pending item without any date (left out of forecast); expense without category |
| Unpaid bill/loan past date | pending with date ≤ reporting date |
| Repayment at risk | projected balance on an EMI date < 0 (high) or < reserve (medium) |
| Cash shortage / below reserve | projected balance < 0 (high) / < reserve (medium) within 30 days |

Checks that lack data (e.g. under 28 days of history) are listed as skipped instead of guessing. Wording says "possible", never "fraud".

## Health index

Each dimension is healthy (100), needs attention (55), high risk (15) or unknown. Index = average of known dimensions (≥ 3 required). Thresholds: liquidity (shortfall or runway < 14 d high; below reserve or runway < 30 d attention), stability (share of negative weeks > 50% high, > 25% attention), receivables (overdue/outstanding > 30% high, ≥ 10% attention), expense pressure (expenses/income > 100% high, ≥ 80% attention), repayment (cash/repayments due < 1.5× or shortfall on a due date high, < 3× attention). It is a heuristic summary, not a credit rating or underwriting decision.

## Security notes

- Input: 1 MB / 5,000 row limit, strict parsing, NUL/binary rejected, rows validated individually, errors never expose stack traces.
- Frontend escapes all user text; CSP allows only same-origin scripts; no external requests, fonts or CDNs.
- No secrets exist in the code; no AWS SDK or credentials are used. No persistence.
- No authentication in this MVP. Do not upload real financial data to a public deployment.
