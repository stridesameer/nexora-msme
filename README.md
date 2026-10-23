NEXORA: See what's next.

Cash-flow forecasting and financial-health checks for small businesses (MSMEs). Upload a transaction CSV, get your cash balance, a 7-day and 30-day forecast, explainable warnings, a health profile and recommended actions.

Honest summary of what is inside: every number comes from transparent, deterministic calculations. The forecast is a rule-based baseline, not a trained ML model. Warnings and recommendations are rule-based text, not generative AI. All demo data is synthetic. Python standard library only (no packages to install).

Run it (2 minutes)

Needs Python 3.10+ (tested on 3.13).

cd nexora
python scripts/generate_sample.py      # optional: regenerate demo data for today's date
python backend/server.py               # then open http://127.0.0.1:8000
Click Load demo data. Run the tests:

python -m unittest discover -s tests -t .
CSV format

Column	Required	Meaning
transaction_id	yes	Unique per row
transaction_date	yes	YYYY-MM-DD
description	yes	Free text (max 200)
transaction_type	yes	income, expense, invoice, bill, loan_repayment
amount	yes	Positive number (₹, commas allowed). The type sets the direction
category	no	e.g. Rent, Payroll
due_date	no	When an invoice/bill is due
expected_payment_date	no	When you expect it to be paid (used by the forecast)
actual_payment_date	no	Fill in once paid; this is when the cash actually moved
loan_repayment_due_date	no	Due date of an EMI
opening_balance	no	Cash before the first transaction (one value in the file)
linked_invoice_id	no	On an income row: the invoice it settles, so it is not counted twice
Rules: income/expense are cash that already moved on transaction_date. invoice (customer owes you), bill (you owe a supplier) and loan_repayment become cash only when actual_payment_date is filled; until then they are pending and feed the forecast. Invalid rows are rejected with a line-numbered reason; duplicate-looking rows are kept but flagged. Max 1 MB / 5,000 rows.

What the dashboard does

Data: load demo, upload/drag a CSV, preview, accepted/rejected counts, download the sample.
Overview: balance, income, expenses, receivables (and overdue), loan repayments due, expected expenses, average daily outflow, lowest projected balance. "Show formulas" reveals each formula. Missing data shows "Insufficient data", never an invented value.
Forecast: 7/30-day toggle, line chart, day-by-day table, funding gap, days below reserve, what drives the low point. Assumptions you can change live: opening balance, minimum reserve, delay all receipts, expense %, extra daily spend, overdue-collection assumption, reporting date; plus per-invoice/bill date editing.
Health: five dimensions (liquidity, stability, receivables, expense pressure, repayment risk) with the rule used for each, plus a 0-100 heuristic index (not a credit score).
Warnings: duplicates, unusually large expenses, category spikes, missing info, overdue invoices, income drops, repayments at risk, cash shortage / below-reserve. Each shows severity, observed value, rule, why it matters, next step ("Why was this triggered?").
Actions: rule-based recommendations, each with the evidence that triggered it.
Reset clears everything on the page. Nothing is stored server-side.
Project layout

backend/validation.py   CSV parsing + validation (integer paise, strict dates)
backend/engine.py       metrics, forecast, anomaly rules, health, recommendations
backend/app.py          request router shared by local server and Lambda
backend/server.py       local server (127.0.0.1 only)
backend/lambda_handler.py   AWS Lambda Function URL entry point
frontend/               index.html, style.css, app.js (native SVG chart, no libraries)
data/sample_transactions.csv   synthetic demo data (53 days history + upcoming)
scripts/                generate_sample.py, package_lambda.py
tests/                  54 unit/API tests
docs/                   ARCHITECTURE.md, DEPLOYMENT.md, DEMO_SCRIPT.md
More docs

Architecture and formulas · AWS deployment · 2-minute demo script

Known limitations

Baseline forecast only: no seasonality, no unscheduled sales, no trained model. Variable spending is included only if you enter it.
Recurring costs are detected by repeated descriptions on a weekly, bi-weekly or monthly rhythm; irregular patterns are missed.
Duplicate detection is exact (same date, amount, type, description).
No login. The AWS Function URL in the deployment guide is public unless you choose IAM auth; use only synthetic data there.
Not tested: a real AWS deployment (not done from the build environment), very large files, browsers other than headless Chromium.
Amazon Bedrock is not integrated (cost/availability could not be verified as ₹0). Text is rule-based.
Not financial, tax or legal advice; no regulatory compliance is claimed.
