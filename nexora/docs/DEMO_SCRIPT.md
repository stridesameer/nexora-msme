# 2-minute demo script

Setup: `python backend/server.py`, open http://127.0.0.1:8000 (or the Lambda URL). Reload the page for a clean start.

**0:00 Problem (15 s).** "A shop can be profitable and still run out of cash, because customers pay late while suppliers and EMIs don't wait. NEXORA tells you *when* before it happens."

**0:15 Load (10 s).** Click **Load demo data**. "94 synthetic records, all validated: 94 accepted, 0 rejected. One possible duplicate is already flagged."

**0:25 Overview (15 s).** "Balance ₹1.7 lakh, but ₹3.97 lakh is owed to us and ₹2.18 lakh of it is overdue." Tick **Show formulas**: "Every number shows how it is calculated."

**0:40 Forecast (25 s).** Scroll to the forecast. "Next 30 days: balance drops to −₹15,820 on 28 October when the ₹2.2 lakh Apex bill is due, two days before Meena Retail's ₹1.8 lakh arrives." Hover the chart. Switch **7 days** ↔ **30 days**. "This is a transparent baseline estimate, not a black-box model."

**1:05 What-if (25 s).** In the receivables table change Meena Retail's date to 27 Oct. "Gap gone, instantly." Then set **Delay all customer receipts** to 5 days: "Here is the risk if customers are late." Press **Reset assumptions**.

**1:30 Explainability (20 s).** Open **Warnings**, click **Why was this triggered?** on the shortage and the large-expense alert: rule, observed value, why it matters, next step. "No fraud claims: it says *possible* and shows the rule."

**1:50 Close (10 s).** Show **Actions** (each has its evidence) and **Health** (rules visible, "not a credit score"). "Rule-based, runs on one AWS Lambda, nothing stored. NEXORA: see what's next."

Numbers above are for the committed sample data (reporting date 23 Oct 2026). If you regenerate it, re-read the figures on screen.
