"""NEXORA calculation engine: metrics, forecast, anomaly rules, health profile, recommendations.

Standard library only. Everything here is deterministic and rule-based: there is NO machine
learning and NO generative AI in this module. Money is integer paise internally and rupees
(floats rounded to 2 dp) in the returned JSON.
"""
from __future__ import annotations

import calendar
import math
import re
import statistics
from datetime import date, datetime, timedelta

from .validation import Txn, parse_date, validate_csv

VERSION = "0.1.0"
HORIZON = 30

# ---- thresholds (all documented in docs/ARCHITECTURE.md) -------------------------------------
LARGE_EXPENSE_MIN_SAMPLES = 4      # other expenses needed in the same category
LARGE_EXPENSE_MEDIAN_MULT = 2.5
CATEGORY_SHIFT_RATIO = 1.5
CATEGORY_SHIFT_MIN_RISE = 500_000  # paise (Rs 5,000)
INCOME_DROP_RATIO = 0.6
INCOME_DROP_MIN_PRIOR = 500_000    # paise (Rs 5,000)
RUNWAY_HEALTHY_DAYS = 30
RUNWAY_RISK_DAYS = 14

DEFAULT_SETTINGS = {
    "business_name": "My Business",
    "opening_balance": None,      # rupees; None = use file value, else assume 0
    "min_reserve": 25000,         # rupees
    "as_of": None,                # ISO date; None = latest realised transaction date
    "period_days": None,          # None = all history
    "receipt_delay_days": 0,
    "collect_overdue": True,
    "overdue_lag_days": 7,
    "expense_adjust_pct": 0,
    "daily_variable_spend": 0,    # rupees per day
    "payment_overrides": {},      # transaction_id -> ISO date
}


# ---- helpers ---------------------------------------------------------------------------------
def rs(p) -> float:
    return round(p / 100, 2)


def inr(p) -> str:
    r = int(round(p / 100))
    s = str(abs(r))
    if len(s) > 3:
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", s[:-3])
        s = head + "," + s[-3:]
    return ("-" if r < 0 else "") + "₹" + s


def d_iso(d):
    return d.isoformat() if d else None


def add_months(d: date, n: int) -> date:
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def clean_settings(raw) -> dict:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("settings must be an object")
    s = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_SETTINGS.items()}

    def num(key, lo, hi, integer=False, nullable=False):
        v = raw.get(key)
        if v is None or v == "":
            if nullable:
                s[key] = None
            return
        if isinstance(v, bool):
            raise ValueError(f"{key} must be a number")
        try:
            x = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number") from None
        if not math.isfinite(x) or not (lo <= x <= hi):
            raise ValueError(f"{key} must be between {lo} and {hi}")
        s[key] = int(round(x)) if integer else x

    num("opening_balance", -1e11, 1e11, nullable=True)
    num("min_reserve", 0, 1e11)
    num("receipt_delay_days", 0, 180, integer=True)
    num("overdue_lag_days", 1, 180, integer=True)
    num("expense_adjust_pct", -50, 200)
    num("daily_variable_spend", 0, 1e9)
    num("period_days", 1, 3650, integer=True, nullable=True)
    if "collect_overdue" in raw:
        s["collect_overdue"] = bool(raw["collect_overdue"])
    if raw.get("business_name"):
        s["business_name"] = str(raw["business_name"])[:80]
    if raw.get("as_of"):
        s["as_of"] = parse_date(str(raw["as_of"]))
    ov = raw.get("payment_overrides") or {}
    if not isinstance(ov, dict) or len(ov) > 500:
        raise ValueError("payment_overrides must be an object with at most 500 entries")
    s["payment_overrides"] = {}
    for k, val in ov.items():
        if val:
            s["payment_overrides"][str(k)] = parse_date(str(val))
    return s


def _sched_date(t: Txn):
    """Default date a pending item is expected to settle, or None if the data has no date."""
    if t.type == "invoice" or t.type == "bill":
        return t.expected or t.due
    return t.expected or t.loan_due or t.due or t.date  # loan_repayment


# ---- recurring detection ---------------------------------------------------------------------
def detect_recurring(realized_out):
    groups: dict[str, list] = {}
    for d, p, t in realized_out:
        groups.setdefault(t.norm, []).append((d, p, t))
    found = []
    for norm, items in groups.items():
        dates = sorted({d for d, _, _ in items})
        if len(dates) < 2:
            continue
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        med = statistics.median(gaps)
        if 6 <= med <= 8:
            period = "weekly"
        elif 13 <= med <= 16:
            period = "biweekly"
        elif 26 <= med <= 35:
            period = "monthly"
        else:
            continue
        if period != "monthly" and len(dates) < 4:
            continue  # weekly/bi-weekly patterns need at least 4 occurrences
        if sum(1 for g in gaps if abs(g - med) <= max(2, 0.25 * med)) < 0.8 * len(gaps):
            continue
        items.sort(key=lambda x: x[0])
        amts = [p for _, p, _ in items]
        mean = sum(amts) / len(amts)
        if any(abs(a - mean) > 0.25 * mean for a in amts):
            continue
        last3 = [p for _, p, _ in items[-3:]]
        found.append(
            {
                "norm": norm,
                "description": items[-1][2].desc,
                "category": items[-1][2].category,
                "period": period,
                "amount": int(round(sum(last3) / len(last3))),
                "last_date": dates[-1],
                "occurrences": len(dates),
            }
        )
    return found


def _recurring_dates(rec, as_of, end):
    out = []
    last = rec["last_date"]
    k = 1
    while True:
        if rec["period"] == "weekly":
            d = last + timedelta(days=7 * k)
        elif rec["period"] == "biweekly":
            d = last + timedelta(days=14 * k)
        else:
            d = add_months(last, k)
        k += 1
        if d > end:
            break
        if d > as_of:
            out.append(d)
    return out


# ---- forecast --------------------------------------------------------------------------------
def build_forecast(pending, recurring, as_of, balance, cfg):
    """Return (rows, sched, excluded, recurring_out). Money in paise."""
    end = as_of + timedelta(days=HORIZON)
    days = [as_of + timedelta(days=i) for i in range(1, HORIZON + 1)]
    ev = {d: [] for d in days}
    sched = {}
    excluded = []
    adj = 1 + cfg["expense_adjust_pct"] / 100.0
    overrides = cfg["payment_overrides"]

    for t in pending:
        over = overrides.get(t.id)
        base = over or _sched_date(t)
        if base is None:
            sched[t.id] = {"date": None, "status": "unscheduled", "note": "No due or expected date in the data"}
            excluded.append({"id": t.id, "description": t.desc, "amount": rs(t.amount), "reason": "no due/expected date: left out of the forecast"})
            continue
        note = ""
        status = "scheduled"
        if t.type == "invoice":
            eff = base if over else base + timedelta(days=cfg["receipt_delay_days"])
            if over:
                note = "date set by you"
            elif cfg["receipt_delay_days"]:
                note = f"delayed {cfg['receipt_delay_days']}d by assumption"
            if eff <= as_of:
                status = "overdue"
                if over:
                    eff = as_of + timedelta(days=1)
                elif cfg["collect_overdue"]:
                    eff = as_of + timedelta(days=cfg["overdue_lag_days"])
                    note = f"already late; assumed collected {cfg['overdue_lag_days']}d after reporting date"
                else:
                    sched[t.id] = {"date": None, "status": "overdue", "note": "overdue and excluded by assumption"}
                    excluded.append({"id": t.id, "description": t.desc, "amount": rs(t.amount), "reason": "overdue receivable excluded (assumption: collect_overdue off)"})
                    continue
            amount, kind, direction = t.amount, "receivable", "in"
        else:
            eff = base
            if over:
                note = "date set by you"
            if eff <= as_of:
                status = "overdue"
                eff = as_of + timedelta(days=1)
                note = "already due/unpaid; assumed paid next day"
            if t.type == "bill":
                amount, kind = int(round(t.amount * adj)), "bill"
            else:
                amount, kind = t.amount, "loan"
            direction = "out"
        if eff > end:
            sched[t.id] = {"date": eff, "status": "beyond_horizon", "note": f"after the {HORIZON}-day window"}
            continue
        if eff <= as_of + timedelta(days=7) and status == "scheduled":
            status = "due_soon"
        sched[t.id] = {"date": eff, "status": status, "note": note}
        ev[eff].append({"id": t.id, "desc": t.desc, "amount": amount, "kind": kind, "dir": direction, "late": status == "overdue", "norm": t.norm})

    recurring_out = []
    for rec in recurring:
        dates = _recurring_dates(rec, as_of, end)
        used = []
        for d in dates:
            dup = any(
                e["kind"] in ("bill",) and e["norm"] == rec["norm"] and abs((d - x).days) <= 7
                for x, evs in ev.items()
                for e in evs
            )
            if dup:
                continue
            amt = int(round(rec["amount"] * adj))
            ev[d].append({"id": None, "desc": rec["description"] + " (recurring, estimated)", "amount": amt, "kind": "recurring", "dir": "out", "late": False, "norm": rec["norm"]})
            used.append(d_iso(d))
        recurring_out.append(
            {"description": rec["description"], "period": rec["period"], "amount": rs(rec["amount"]), "last_seen": d_iso(rec["last_date"]), "occurrences": rec["occurrences"], "projected_dates": used}
        )

    var = int(round(cfg["daily_variable_spend"] * 100 * adj))
    rows = []
    bal = balance
    for d in days:
        evs = list(ev[d])
        if var > 0:
            evs.append({"id": None, "desc": "Variable daily spend (your assumption)", "amount": var, "kind": "variable", "dir": "out", "late": False, "norm": ""})
        inflow = sum(e["amount"] for e in evs if e["dir"] == "in")
        outflow = sum(e["amount"] for e in evs if e["dir"] == "out")
        opening = bal
        bal = bal + inflow - outflow
        rows.append({"date": d, "opening": opening, "inflow": inflow, "outflow": outflow, "closing": bal, "events": evs})
    return rows, sched, excluded, recurring_out


def summarize_horizon(rows, h, reserve):
    sub = rows[:h]
    mn = min(sub, key=lambda r: r["closing"])
    neg = [r for r in sub if r["closing"] < 0]
    below = [r for r in sub if r["closing"] < reserve]
    contributors = []
    for r in sub:
        if r["date"] <= mn["date"]:
            for e in r["events"]:
                if e["dir"] == "out" and e["kind"] != "variable":
                    contributors.append({"date": d_iso(r["date"]), "description": e["desc"], "amount": rs(e["amount"]), "kind": e["kind"]})
    contributors.sort(key=lambda c: -c["amount"])
    late_in = []
    for r in rows:
        if r["date"] > mn["date"]:
            for e in r["events"]:
                if e["dir"] == "in":
                    late_in.append({"date": d_iso(r["date"]), "description": e["desc"], "amount": rs(e["amount"]), "id": e["id"]})
    late_in.sort(key=lambda c: -c["amount"])
    return {
        "days": h,
        "start_balance": rs(sub[0]["opening"]),
        "ending_balance": rs(sub[-1]["closing"]),
        "min_balance": rs(mn["closing"]),
        "min_date": d_iso(mn["date"]),
        "total_inflow": rs(sum(r["inflow"] for r in sub)),
        "total_outflow": rs(sum(r["outflow"] for r in sub)),
        "days_negative": len(neg),
        "days_below_reserve": len(below),
        "first_shortage_date": d_iso(neg[0]["date"]) if neg else None,
        "funding_gap": rs(max(0, -mn["closing"])),
        "reserve_gap": rs(max(0, reserve - mn["closing"])),
        "contributors": contributors[:5],
        "late_receipts": late_in[:5],
    }


def _serialize_rows(rows, reserve):
    out = []
    for r in rows:
        status = "shortfall" if r["closing"] < 0 else ("below_reserve" if r["closing"] < reserve else "ok")
        out.append(
            {
                "date": d_iso(r["date"]),
                "opening": rs(r["opening"]),
                "inflow": rs(r["inflow"]),
                "outflow": rs(r["outflow"]),
                "closing": rs(r["closing"]),
                "status": status,
                "events": [{"description": e["desc"], "amount": rs(e["amount"]), "direction": e["dir"], "kind": e["kind"], "id": e["id"]} for e in r["events"]],
            }
        )
    return out


# ---- main entry ------------------------------------------------------------------------------
def analyze(csv_text, settings=None) -> dict:
    cfg = clean_settings(settings)
    v = validate_csv(csv_text)
    pub = v.to_public()
    if v.fatal:
        return {"ok": False, "error": v.fatal[0], "validation": pub}
    if not v.accepted:
        return {"ok": False, "error": "No valid records were found in the file.", "validation": pub}
    txns = v.accepted
    by_id = {t.id: t for t in txns}
    reserve = int(round(cfg["min_reserve"] * 100))

    # Invoices settled by a linked income row are counted once (via the income row).
    settled = {}
    for t in txns:
        if t.type == "income" and t.linked in by_id and by_id[t.linked].type == "invoice":
            settled[t.linked] = t
    realized = []  # (date, signed paise, txn)
    pending = []
    for t in txns:
        if t.type in ("income", "expense"):
            realized.append((t.date, t.amount if t.inflow else -t.amount, t))
        elif t.id in settled:
            continue
        elif t.actual:
            realized.append((t.actual, t.amount if t.inflow else -t.amount, t))
        else:
            pending.append(t)

    as_of = cfg["as_of"] or (max(d for d, _, _ in realized) if realized else max(t.date for t in txns))
    used = [e for e in realized if e[0] <= as_of]
    after = len(realized) - len(used)

    if cfg["opening_balance"] is not None:
        opening, ob_src = int(round(cfg["opening_balance"] * 100)), "user"
    elif v.file_opening_balance is not None:
        opening, ob_src = v.file_opening_balance, "file"
    else:
        opening, ob_src = 0, "assumed_zero"

    balance = opening + sum(p for _, p, _ in used)
    first_date = min((d for d, _, _ in used), default=as_of)
    period_start = first_date
    if cfg["period_days"]:
        period_start = max(first_date, as_of - timedelta(days=cfg["period_days"] - 1))
    span = (as_of - period_start).days + 1
    in_period = [e for e in used if e[0] >= period_start]
    income = sum(p for _, p, _ in in_period if p > 0)
    expenses = sum(-p for _, p, _ in in_period if p < 0)
    loans_paid_p = sum(-p for _, p, t in in_period if t.type == "loan_repayment")

    has_invoices = any(t.type == "invoice" for t in txns)
    has_loans = any(t.type == "loan_repayment" for t in txns)
    pend_inv = [t for t in pending if t.type == "invoice"]
    outstanding = sum(t.amount for t in pend_inv if t.date <= as_of)
    overdue_list = [t for t in pend_inv if t.due and t.due < as_of]
    overdue_amt = sum(t.amount for t in overdue_list)

    # recurring + forecast
    realized_out = [(d, -p, t) for d, p, t in used if p < 0 and t.type in ("expense", "bill")]
    recurring = detect_recurring(realized_out)
    rec_norms = {r["norm"] for r in recurring}
    rows, sched, excluded, recurring_out = build_forecast(pending, recurring, as_of, balance, cfg)
    h7 = summarize_horizon(rows, 7, reserve)
    h30 = summarize_horizon(rows, 30, reserve)

    scenario = None
    if cfg["receipt_delay_days"] or cfg["payment_overrides"] or cfg["expense_adjust_pct"]:
        base_cfg = dict(cfg, receipt_delay_days=0, payment_overrides={}, expense_adjust_pct=0)
        brows, _, _, _ = build_forecast(pending, recurring, as_of, balance, base_cfg)
        b30 = summarize_horizon(brows, 30, reserve)
        scenario = {"baseline_min_balance": b30["min_balance"], "baseline_min_date": b30["min_date"], "baseline_funding_gap": b30["funding_gap"], "scenario_min_balance": h30["min_balance"], "scenario_funding_gap": h30["funding_gap"]}

    horizon_end = as_of + timedelta(days=HORIZON)
    loans_due = [t for t in pending if t.type == "loan_repayment" and sched.get(t.id, {}).get("date") is not None]
    loans_due_amt = sum(t.amount for t in loans_due)
    bills_due = [t for t in pending if t.type == "bill" and sched.get(t.id, {}).get("date") is not None]
    rec_events = [(r["date"], e) for r in rows for e in r["events"] if e["kind"] == "recurring"]
    exp_upcoming = sum(int(round(t.amount * (1 + cfg["expense_adjust_pct"] / 100))) for t in bills_due) + sum(e["amount"] for _, e in rec_events)

    avg_out = None
    if span >= 7 and used:
        avg_out = expenses / span
    runway = (balance / avg_out) if avg_out and avg_out > 0 and balance > 0 else (0 if avg_out and balance <= 0 else None)

    # variable spend suggestion = non-recurring, non-loan realised outflow per day
    var_out = sum(-p for _, p, t in in_period if p < 0 and t.type != "loan_repayment" and t.norm not in rec_norms)
    suggested_var = rs(var_out / span) if span >= 7 else None

    # daily / monthly summaries
    daily = {}
    for d, p, _ in used:
        r = daily.setdefault(d, [0, 0])
        r[0 if p > 0 else 1] += abs(p)
    run = opening
    daily_rows = []
    for d in sorted(daily):
        run += daily[d][0] - daily[d][1]
        daily_rows.append({"date": d_iso(d), "inflow": rs(daily[d][0]), "outflow": rs(daily[d][1]), "net": rs(daily[d][0] - daily[d][1]), "balance": rs(run)})
    monthly = {}
    for d, p, _ in in_period:
        m = monthly.setdefault(d.strftime("%Y-%m"), [0, 0])
        m[0 if p > 0 else 1] += abs(p)
    monthly_rows = [{"month": k, "inflow": rs(a), "outflow": rs(b), "net": rs(a - b), "partial": k == as_of.strftime("%Y-%m") or k == period_start.strftime("%Y-%m")} for k, (a, b) in sorted(monthly.items())]

    def metric(key, label, value, unit, formula, note=""):
        return {"key": key, "label": label, "value": value, "unit": unit, "formula": formula, "note": note}

    ins = None  # None renders as "Insufficient data"
    metrics = [
        metric("current_balance", "Cash balance", rs(balance), "inr", "Opening balance + all cash received − all cash paid, up to the reporting date", {"user": "Opening balance set by you", "file": "Opening balance read from the file", "assumed_zero": "No opening balance supplied: assumed ₹0"}[ob_src]),
        metric("total_income", "Total income", rs(income), "inr", "Sum of cash received in the period (income rows + invoices with an actual payment date)"),
        metric("total_expenses", "Total expenses", rs(expenses), "inr", "Sum of cash paid in the period (expenses + paid bills + paid loan repayments)", f"includes {inr(loans_paid_p)} of loan repayments" if loans_paid_p else ""),
        metric("net_cash_flow", "Net cash flow", rs(income - expenses), "inr", "Total income − total expenses (period)"),
        metric("outstanding_receivables", "Outstanding receivables", rs(outstanding), "inr", "Sum of unpaid invoices dated on or before the reporting date", f"{len(pend_inv)} unpaid invoice(s)" if has_invoices else "No invoice records in the file"),
        metric("overdue_receivables", "Overdue invoices", rs(overdue_amt), "inr", "Unpaid invoices whose due_date is before the reporting date", f"{len(overdue_list)} overdue" if has_invoices else "No invoice records in the file"),
        metric("upcoming_loan_repayments", "Loan repayments (next 30d)", rs(loans_due_amt), "inr", "Unpaid loan_repayment rows scheduled within 30 days (late unpaid ones counted as due now)", f"{len(loans_due)} repayment(s)" if has_loans else "No loan records in the file"),
        metric("upcoming_expenses", "Expected expenses (next 30d)", rs(exp_upcoming), "inr", "Unpaid bills due within 30 days + projected recurring expenses (after your % adjustment)"),
        metric("avg_daily_outflow", "Avg daily cash outflow", rs(avg_out) if avg_out is not None else ins, "inr", "Total expenses in the period ÷ days in the period (needs at least 7 days of history)", "" if avg_out is not None else "Needs at least 7 days of history"),
        metric("min_projected_7", "Lowest balance, next 7d", h7["min_balance"], "inr", "Minimum projected closing balance over the next 7 days", h7["min_date"]),
        metric("min_projected_30", "Lowest balance, next 30d", h30["min_balance"], "inr", "Minimum projected closing balance over the next 30 days", h30["min_date"]),
        metric("runway_days", "Cash runway", round(runway, 1) if runway is not None else ins, "days", "Cash balance ÷ average daily outflow (ignores future receipts)", "" if runway is not None else "Needs at least 7 days of history"),
    ]

    # ---- tables for the UI ----
    receivables = []
    for t in pend_inv:
        s = sched.get(t.id, {})
        receivables.append({
            "id": t.id, "description": t.desc, "amount": rs(t.amount), "due_date": d_iso(t.due), "expected_date": d_iso(t.expected),
            "forecast_date": d_iso(s.get("date")), "status": s.get("status"), "note": s.get("note", ""),
            "days_overdue": (as_of - t.due).days if t.due and t.due < as_of else 0, "overridden": t.id in cfg["payment_overrides"],
        })
    receivables.sort(key=lambda r: (r["forecast_date"] or "9999", r["id"]))
    obligations = []
    for r in rows:
        status = "shortfall" if r["closing"] < 0 else ("below_reserve" if r["closing"] < reserve else "covered")
        for e in r["events"]:
            if e["dir"] == "out" and e["kind"] != "variable":
                obligations.append({"id": e["id"], "description": e["desc"], "kind": e["kind"], "amount": rs(e["amount"]), "date": d_iso(r["date"]), "balance_after_day": rs(r["closing"]), "status": status, "late": e["late"], "editable": e["id"] is not None, "overridden": e["id"] in cfg["payment_overrides"]})

    # ---- anomalies / warnings ----
    warnings, skipped = [], []

    def warn(kind, sev, title, affected, observed, rule, why, nxt):
        warnings.append({"id": f"w{len(warnings) + 1}", "kind": kind, "severity": sev, "title": title, "affected": affected, "observed": observed, "rule": rule, "why": why, "next_step": nxt})

    # duplicates
    for g in v.duplicate_groups:
        warn("possible_duplicate", "medium", "Possible duplicate transactions", [t.id for t in g], f"{len(g)} records on {g[0].date.isoformat()} of {inr(g[0].amount)} described as \"{g[0].desc}\"",
             "Same date, amount, type and normalised description on more than one record",
             "If one of them is entered twice, income or expenses are overstated and the balance is wrong. This is a data check, not evidence of fraud.",
             "Compare the records with your bank statement or invoices; delete the extra row from the CSV if it is a double entry.")
    # large expenses
    exp_rows = [(d, -p, t) for d, p, t in used if p < 0 and t.type in ("expense", "bill")]
    cats: dict[str, list] = {}
    for d, a, t in exp_rows:
        cats.setdefault(t.category.lower() or "uncategorised", []).append((d, a, t))
    large_found = 0
    for cat, items in cats.items():
        for d, a, t in items:
            others = [x[1] for x in items if x[2] is not t]
            if len(others) < LARGE_EXPENSE_MIN_SAMPLES:
                continue
            med, mean = statistics.median(others), statistics.mean(others)
            sd = statistics.pstdev(others)
            thresh = max(LARGE_EXPENSE_MEDIAN_MULT * med, mean + 2 * sd)
            if a > thresh:
                large_found += 1
                sev = "high" if a > 5 * med else "medium"
                warn("large_expense", sev, "Unusually large expense", [t.id, d_iso(d)], f"{inr(a)} on {d.isoformat()} ({t.desc}); typical in '{cat}' is {inr(med)}",
                     f"Amount > max({LARGE_EXPENSE_MEDIAN_MULT}× category median, mean + 2 std) based on {len(others)} other '{cat}' expenses (limit {inr(thresh)})",
                     "A one-off or mis-keyed large payment can drain cash and distort your averages. It is flagged because it is out of pattern, not because it is wrong.",
                     "Check the bill or receipt. If it is genuine, decide whether it will recur and plan cash for it.")
    if not any(len(i) > LARGE_EXPENSE_MIN_SAMPLES for i in cats.values()):
        skipped.append("Unusually-large-expense check skipped: no expense category has enough history (needs 5+ entries).")
    # category shift + income drop (need 28 days)
    if (as_of - first_date).days >= 27:
        r0, r1, p0, p1 = as_of - timedelta(days=13), as_of, as_of - timedelta(days=27), as_of - timedelta(days=14)
        rec_c: dict[str, int] = {}
        pri_c: dict[str, int] = {}
        for d, a, t in exp_rows:
            c = t.category or ""
            if not c:
                continue
            if r0 <= d <= r1:
                rec_c[c] = rec_c.get(c, 0) + a
            elif p0 <= d <= p1:
                pri_c[c] = pri_c.get(c, 0) + a
        for c, rv in sorted(rec_c.items(), key=lambda kv: -kv[1]):
            pv = pri_c.get(c, 0)
            if rv - pv >= CATEGORY_SHIFT_MIN_RISE and (pv == 0 or rv >= CATEGORY_SHIFT_RATIO * pv):
                warn("category_shift", "medium" if rv - pv >= 1_000_000 else "low", f"Spending jump in '{c}'", [c], f"{inr(rv)} in the last 14 days vs {inr(pv)} in the 14 days before",
                     f"Last-14-day spend ≥ {CATEGORY_SHIFT_RATIO}× previous 14 days and rise ≥ {inr(CATEGORY_SHIFT_MIN_RISE)}",
                     "Rapid growth in one category squeezes cash faster than revenue may grow.", "Review the entries in this category and confirm they are planned and worth the cost.")
        rec_in = sum(p for d, p, _ in used if p > 0 and r0 <= d <= r1)
        pri_in = sum(p for d, p, _ in used if p > 0 and p0 <= d <= p1)
        if pri_in >= INCOME_DROP_MIN_PRIOR and rec_in < INCOME_DROP_RATIO * pri_in:
            warn("income_drop", "high" if rec_in < 0.3 * pri_in else "medium", "Incoming payments have dropped", [f"{p0.isoformat()} to {r1.isoformat()}"], f"{inr(rec_in)} received in the last 14 days vs {inr(pri_in)} in the 14 days before ({round(100 * rec_in / pri_in)}%)",
                 f"Last-14-day receipts < {int(INCOME_DROP_RATIO * 100)}% of the previous 14 days (previous ≥ {inr(INCOME_DROP_MIN_PRIOR)})",
                 "Lower inflows with unchanged costs shrink the cash buffer. Lumpy invoice payments can also cause this.", "Check whether large customer payments are delayed and chase them; confirm sales are normal.")
    else:
        skipped.append("Category-shift and income-drop checks skipped: they need at least 28 days of history.")
    # overdue receivables
    big = sorted(overdue_list, key=lambda t: -t.amount)
    for t in big[:6]:
        days = (as_of - t.due).days
        warn("overdue_receivable", "high" if days > 30 else ("medium" if days > 7 else "low"), f"Overdue invoice: {t.desc}", [t.id, f"due {t.due.isoformat()}"], f"{inr(t.amount)} is {days} day(s) past its due date",
             "Invoice unpaid and due_date before the reporting date (>30d high, >7d medium)", "Money you have earned but not received cannot pay your bills; the longer it is overdue the less likely it is collected on time.", "Call or message the customer today, agree a payment date and record it as the expected payment date.")
    if len(big) > 6:
        rest = big[6:]
        warn("overdue_receivable", "low", f"{len(rest)} more overdue invoices", [t.id for t in rest[:5]], f"{inr(sum(t.amount for t in rest))} across {len(rest)} smaller invoices", "Unpaid invoices past due_date", "Small overdue amounts add up.", "Review the receivables table and follow up in order of size.")
    # missing info
    no_date_inv = [t for t in pend_inv if not t.due]
    if no_date_inv:
        warn("missing_info", "low", "Invoices without a due date", [t.id for t in no_date_inv[:5]], f"{len(no_date_inv)} unpaid invoice(s) have no due_date", "Unpaid invoice with empty due_date", "Without a due date we cannot tell whether they are overdue.", "Add due dates to these invoices in your CSV.")
    unsched = [t for t in pending if sched.get(t.id, {}).get("status") == "unscheduled"]
    if unsched:
        warn("missing_info", "medium", "Pending items left out of the forecast", [t.id for t in unsched[:5]], f"{len(unsched)} unpaid item(s) worth {inr(sum(t.amount for t in unsched))} have no due or expected date", "Unpaid invoice/bill with neither due_date nor expected_payment_date",
             "The forecast is incomplete: it ignores these amounts.", "Add an expected_payment_date or set one in the obligations table.")
    uncat = [t for t in txns if t.type in ("expense", "bill") and not t.category]
    if uncat:
        warn("missing_info", "low", "Expenses without a category", [t.id for t in uncat[:5]], f"{len(uncat)} expense record(s) have no category", "Expense/bill with empty category", "Category checks cannot include them.", "Add categories to these rows.")
    late_pay = [t for t in pending if t.type in ("bill", "loan_repayment") and sched.get(t.id, {}).get("status") == "overdue" and t.id not in cfg["payment_overrides"]]
    for t in late_pay[:4]:
        warn("late_payable", "high" if t.type == "loan_repayment" else "medium", f"Unpaid {t.type.replace('_', ' ')} is past its date: {t.desc}", [t.id], f"{inr(t.amount)} was due {_sched_date(t).isoformat()} and has no actual payment date", "Unpaid bill/loan with scheduled date on or before the reporting date", "Late loan payments can attract penalties and affect your credit record.", "Pay it or record the actual payment date; the forecast assumes it is paid tomorrow.")
    if after:
        warn("future_dated_actuals", "low", "Payments dated after the reporting date were ignored", [], f"{after} realised record(s) are dated after {as_of.isoformat()}", "Realised transaction date later than reporting date", "They have not happened yet as of the reporting date, so they are excluded from the balance.", "Check the dates, or move the reporting date.")
    # forecast based
    if h30["funding_gap"] > 0:
        in7 = h7["funding_gap"] > 0
        warn("cash_shortage", "high", "Projected cash shortfall" + (" within 7 days" if in7 else " within 30 days"), [h30["first_shortage_date"], h30["min_date"]],
             f"Lowest projected balance {inr(h30['min_balance'] * 100)} on {h30['min_date']}; negative on {h30['days_negative']} of 30 days; funding gap {inr(h30['funding_gap'] * 100)}",
             "Projected closing balance < ₹0 on any forecast day", "A negative balance means you cannot pay on time: suppliers, salaries or EMIs may be missed.",
             "Bring forward receipts (see the receivables table), delay or split big payments, or arrange short-term funding before the first shortage date.")
    elif h30["reserve_gap"] > 0:
        warn("below_reserve", "medium", "Cash falls below your minimum reserve", [h30["min_date"]], f"Lowest projected balance {inr(h30['min_balance'] * 100)} on {h30['min_date']} vs reserve {inr(reserve)}", "Projected closing balance < configured minimum reserve", "You stay positive but with little buffer for surprises.", "Pull receipts forward or reduce discretionary spend to keep the reserve.")
    cov = 0
    for o in obligations:
        if o["kind"] == "loan" and o["status"] != "covered" and cov < 4:
            cov += 1
            short = o["status"] == "shortfall"
            warn("repayment_at_risk", "high" if short else "medium", "Loan repayment may not be covered", [o["id"], o["date"]], f"{inr(o['amount'] * 100)} due {o['date']}; projected balance that day {inr(o['balance_after_day'] * 100)}",
                 "Projected closing balance on the repayment date is " + ("< ₹0" if short else "below the minimum reserve"), "Missing an EMI can add penalties and hurt future borrowing.", "Make sure inflows land before this date or talk to your lender early about timing.")

    order = {"high": 0, "medium": 1, "low": 2}
    warnings.sort(key=lambda w: order[w["severity"]])
    for i, w in enumerate(warnings):
        w["id"] = f"w{i + 1}"

    # ---- health profile ----
    dims = []

    def dim(key, label, status, value, rule, explanation):
        dims.append({"key": key, "label": label, "status": status, "value": value, "rule": rule, "explanation": explanation})

    if h30["funding_gap"] > 0 or (runway is not None and runway < RUNWAY_RISK_DAYS):
        ls = "high_risk"
    elif h30["reserve_gap"] > 0 or (runway is not None and runway < RUNWAY_HEALTHY_DAYS):
        ls = "attention"
    elif runway is None and avg_out is None:
        ls = "healthy" if h30["reserve_gap"] == 0 and h30["funding_gap"] == 0 else "unknown"
    else:
        ls = "healthy"
    dim("liquidity", "Liquidity position", ls, f"runway {round(runway, 1)}d; lowest 30d balance {inr(h30['min_balance'] * 100)}" if runway is not None else f"lowest 30d balance {inr(h30['min_balance'] * 100)}",
        f"High risk: projected shortfall or runway < {RUNWAY_RISK_DAYS}d. Attention: below reserve or runway < {RUNWAY_HEALTHY_DAYS}d.", "Can you cover the next month of outgoings with the cash you have and the receipts you expect?")
    weeks = []
    for i in range(0, 26):
        e = as_of - timedelta(days=7 * i)
        s0 = e - timedelta(days=6)
        if s0 < period_start:
            break
        weeks.append(sum(p for d, p, _ in used if s0 <= d <= e))
    if len(weeks) >= 3:
        negs = sum(1 for w in weeks if w < 0)
        share = negs / len(weeks)
        dim("stability", "Cash-flow stability", "healthy" if share <= 0.25 else ("attention" if share <= 0.5 else "high_risk"), f"{negs} of {len(weeks)} weeks had negative net cash flow",
            "Share of negative weeks: ≤25% healthy, ≤50% attention, otherwise high risk", "Frequent cash-negative weeks make the balance volatile.")
    else:
        dim("stability", "Cash-flow stability", "unknown", "Insufficient data", "Needs at least 3 complete weeks of history", "Not enough history to judge stability.")
    if not has_invoices:
        dim("receivables", "Receivables risk", "unknown", "No invoice records", "Needs invoice rows", "Without invoices we cannot assess customer payment risk.")
    elif outstanding == 0:
        dim("receivables", "Receivables risk", "healthy", "No unpaid invoices", "Overdue ÷ outstanding", "Nothing is owed to you right now.")
    else:
        pct = overdue_amt / outstanding
        dim("receivables", "Receivables risk", "healthy" if pct < 0.10 else ("attention" if pct <= 0.30 else "high_risk"), f"{round(pct * 100)}% of {inr(outstanding)} outstanding is overdue", "Overdue ÷ outstanding: <10% healthy, 10–30% attention, >30% high risk", "A large overdue share means customers are paying late.")
    if income > 0:
        ratio = expenses / income
        dim("expense_pressure", "Expense pressure", "healthy" if ratio < 0.8 else ("attention" if ratio <= 1.0 else "high_risk"), f"expenses are {round(ratio * 100)}% of income", "Expenses ÷ income: <80% healthy, 80–100% attention, >100% high risk", "Spending more than you receive drains cash.")
    else:
        dim("expense_pressure", "Expense pressure", "unknown" if expenses == 0 else "high_risk", "No income in the period" if expenses else "Insufficient data", "Expenses ÷ income", "Cannot compare expenses with income.")
    risky_loan = any(o["kind"] == "loan" and o["status"] == "shortfall" for o in obligations)
    if loans_due_amt == 0:
        dim("repayment", "Upcoming repayment risk", "healthy", "No loan repayments due in 30 days", "Cash ÷ repayments due in 30 days", "No EMIs fall due in the window.")
    else:
        cover = balance / loans_due_amt
        dim("repayment", "Upcoming repayment risk", "high_risk" if (risky_loan or cover < 1.5) else ("attention" if cover < 3 else "healthy"), f"cash covers repayments {round(cover, 1)}×; {inr(loans_due_amt)} due", "Cash ÷ repayments due: ≥3× healthy, 1.5–3× attention, <1.5× or projected shortfall on the due date high risk", "Shows whether current cash can service the next EMIs.")
    pts = {"healthy": 100, "attention": 55, "high_risk": 15}
    known = [d for d in dims if d["status"] in pts]
    if len(known) >= 3:
        score = round(sum(pts[d["status"]] for d in known) / len(known))
        label = "Healthy" if score >= 75 else ("Needs attention" if score >= 45 else "High risk")
    else:
        score, label = None, "Insufficient data"
    rank = {"high_risk": 0, "attention": 1, "healthy": 2, "unknown": 3}
    factors = [f"{d['label']}: {d['value']}" for d in sorted(dims, key=lambda d: rank[d["status"]]) if d["status"] in ("high_risk", "attention")][:4] or ["No dimension is flagged."]
    health = {"dimensions": dims, "score": score, "label": label, "factors": factors,
              "formula": "Average of the known dimensions, scoring healthy=100, needs attention=55, high risk=15 (equal weights; at least 3 known dimensions required).",
              "disclaimer": "A simple heuristic summary of your own data. It is not a credit rating, a bank underwriting decision or a validated creditworthiness score."}

    # ---- recommendations (rule-based) ----
    recs = []

    def rec(pri, title, action, evidence, related):
        recs.append({"id": f"r{len(recs) + 1}", "priority": pri, "title": title, "action": action, "evidence": evidence, "related_warnings": related, "basis": "rule-based"})

    wk = lambda kind: [w["id"] for w in warnings if w["kind"] == kind]
    if overdue_list:
        old = max(overdue_list, key=lambda t: (as_of - t.due).days)
        rec("high" if overdue_amt >= 0.3 * max(outstanding, 1) else "medium", "Follow up on overdue invoices", "Contact these customers first, in order of amount, and record the date they promise to pay.",
            [f"{len(overdue_list)} invoice(s) worth {inr(overdue_amt)} are past due", f"Oldest: {old.desc}, {(as_of - old.due).days} days late ({inr(old.amount)})"], wk("overdue_receivable"))
    if h30["funding_gap"] > 0:
        ev = [f"Projected balance falls to {inr(h30['min_balance'] * 100)} on {h30['min_date']} (gap {inr(h30['funding_gap'] * 100)})"]
        ev += [f"{c['description']}: {inr(c['amount'] * 100)} on {c['date']}" for c in h30["contributors"][:3]]
        rec("high", "Close the projected funding gap", "Before the first shortage date, bring receipts forward, ask to split or delay the largest payments, or line up short-term working capital.", ev, wk("cash_shortage"))
        if h30["late_receipts"]:
            lr = h30["late_receipts"][0]
            rec("high", "Collect earlier from the receivable that arrives after the low point", f"Ask for payment before {h30['min_date']}; use the date box in the receivables table to test the effect.",
                [f"{lr['description']} ({inr(lr['amount'] * 100)}) is expected on {lr['date']}, after the lowest balance on {h30['min_date']}"], wk("cash_shortage"))
        bills_before = [c for c in h30["contributors"] if c["kind"] == "bill"]
        if bills_before:
            rec("medium", "Negotiate supplier payment terms", "Ask suppliers for a few extra days or a split payment on bills due before the low point.", [f"{c['description']}: {inr(c['amount'] * 100)} due {c['date']}" for c in bills_before[:3]], wk("cash_shortage"))
    for o in obligations:
        if o["kind"] == "loan" and o["status"] != "covered":
            rec("high" if o["status"] == "shortfall" else "medium", "Prepare for the loan repayment", "Keep the amount aside or contact your lender about timing before the due date.", [f"{o['description']}: {inr(o['amount'] * 100)} due {o['date']}", f"Projected balance that day: {inr(o['balance_after_day'] * 100)}"], wk("repayment_at_risk"))
            break
    if scenario and scenario["scenario_funding_gap"] > scenario["baseline_funding_gap"]:
        rec("high", "Review delayed receipts", "Your delay/date/expense assumptions create a shortfall that the unchanged data does not. Confirm which customers might really pay late.", [f"Without your assumptions the lowest balance is {inr(scenario['baseline_min_balance'] * 100)}", f"With them it is {inr(scenario['scenario_min_balance'] * 100)}"], wk("cash_shortage"))
    if h30["funding_gap"] == 0 and h30["reserve_gap"] > 0:
        rec("medium", "Rebuild a minimum cash reserve", "Pull forward receipts or trim non-essential spend so the lowest balance stays above your reserve.", [f"Lowest projected balance {inr(h30['min_balance'] * 100)} vs reserve {inr(reserve)} (short by {inr(h30['reserve_gap'] * 100)})"], wk("below_reserve"))
    if avg_out and avg_out > 0:
        target = int(round(avg_out * 15))
        if reserve < target:
            rec("low", "Consider a larger reserve", "A common rule of thumb is to hold about two weeks of average outflow.", [f"Average daily outflow {inr(avg_out)}; 15 days ≈ {inr(target)} vs your reserve {inr(reserve)}"], [])
    for w in warnings:
        if w["kind"] in ("large_expense", "category_shift"):
            rec("medium", "Review unusually high spending", "Confirm the spending is planned and decide if it will repeat.", [w["observed"]], [w["id"]])
            break
    if v.duplicate_groups:
        rec("medium", "Check possible duplicate entries", "Remove double entries so income and expenses are not overstated.", [f"{len(v.duplicate_groups)} group(s) of same-day, same-amount, same-description records"], wk("possible_duplicate"))
    if unsched:
        rec("medium", "Add dates to undated invoices/bills", "Give each a due or expected payment date so the forecast includes them.", [f"{len(unsched)} item(s) worth {inr(sum(t.amount for t in unsched))} are not in the forecast"], wk("missing_info"))
    if not any(r["priority"] == "high" for r in recs) and h30["funding_gap"] == 0 and h30["reserve_gap"] == 0:
        rec("low", "Keep monitoring weekly", "Cash looks sufficient on current data. Re-run this check after new invoices or bills are recorded.", [f"Lowest projected balance {inr(h30['min_balance'] * 100)} on {h30['min_date']}, above your reserve of {inr(reserve)}"], [])
    recs.sort(key=lambda r: order[r["priority"]])

    # ---- assumptions text ----
    assumptions = [
        "Deterministic baseline built from your records. It is an estimate, not a trained prediction model.",
        f"Starting cash: {inr(balance)} on {as_of.isoformat()} (opening balance {inr(opening)}, source: {ob_src.replace('_', ' ')}).",
        "Receivables arrive on their expected_payment_date (else due_date); bills and loan repayments are paid on their expected/due date.",
        f"Receipt delay: {cfg['receipt_delay_days']} day(s) applied to receivables you have not given an explicit date.",
        ("Overdue receivables are assumed collected " + str(cfg["overdue_lag_days"]) + " day(s) after the reporting date.") if cfg["collect_overdue"] else "Overdue receivables are excluded from the forecast.",
        "Unpaid bills/loans already past their date are assumed paid the next day.",
        f"Recurring expenses are projected only when the same description repeated on a weekly, bi-weekly or monthly rhythm in the history ({len(recurring)} found).",
        f"Expense adjustment: {cfg['expense_adjust_pct']:+g}% on bills, recurring and variable spend (loan repayments unchanged).",
        f"Variable daily spend: {inr(cfg['daily_variable_spend'] * 100)} per day" + (" (not included, so only scheduled items are forecast)" if cfg["daily_variable_spend"] == 0 else "") + ".",
        "No future sales or other unscheduled income are assumed.",
    ]
    unknown_ids = [k for k in cfg["payment_overrides"] if k not in by_id]
    if unknown_ids:
        assumptions.append("Ignored payment-date overrides for unknown ids: " + ", ".join(unknown_ids[:5]))

    return {
        "ok": True,
        "meta": {"version": VERSION, "business_name": cfg["business_name"], "as_of": d_iso(as_of), "period_start": d_iso(period_start), "period_days": span, "opening_balance": rs(opening), "opening_balance_source": ob_src, "min_reserve": rs(reserve), "forecast_method": "deterministic baseline (not machine learning)", "record_count": len(txns)},
        "validation": pub,
        "metrics": metrics,
        "daily": daily_rows[-90:],
        "monthly": monthly_rows,
        "forecast": {"rows": _serialize_rows(rows, reserve), "horizons": {"7": h7, "30": h30}, "assumptions": assumptions, "excluded": excluded, "recurring": recurring_out, "scenario": scenario, "suggested_daily_variable_spend": suggested_var},
        "receivables": receivables,
        "obligations": obligations,
        "warnings": warnings,
        "checks_skipped": skipped,
        "health": health,
        "recommendations": recs,
        "ai": {"generative_ai_used": False, "note": "All warnings and recommendations are generated by fixed rules from your numbers. No generative AI or external service is used."},
    }
