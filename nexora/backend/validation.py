"""CSV parsing and validation for NEXORA.

Standard library only. All money is converted to integer paise (1 INR = 100 paise)
immediately after parsing, so later arithmetic never suffers float rounding errors.

CSV schema (header names are case-insensitive, extra columns are ignored with a notice):

  Required : transaction_id, transaction_date, description, transaction_type, amount
  Optional : category, due_date, expected_payment_date, actual_payment_date,
             loan_repayment_due_date, opening_balance, linked_invoice_id

  transaction_type:
    income          cash actually received on transaction_date
    expense         cash actually paid on transaction_date
    invoice         money a customer owes you (receivable). Paid when actual_payment_date is set.
    bill            money you owe a supplier (payable).     Paid when actual_payment_date is set.
    loan_repayment  loan EMI / repayment.                   Paid when actual_payment_date is set.

  amount is always a positive number; the type decides the direction (in / out).
  Dates are ISO format YYYY-MM-DD.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

MAX_BYTES = 1_000_000
MAX_ROWS = 5_000
MAX_AMOUNT_PAISE = 10**11 * 100  # 100 billion rupees: anything above is certainly a typo

REQUIRED_COLUMNS = ("transaction_id", "transaction_date", "description", "transaction_type", "amount")
OPTIONAL_COLUMNS = (
    "category",
    "due_date",
    "expected_payment_date",
    "actual_payment_date",
    "loan_repayment_due_date",
    "opening_balance",
    "linked_invoice_id",
)
KNOWN_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS
TRANSACTION_TYPES = ("income", "expense", "invoice", "bill", "loan_repayment")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_WORDS = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b"
)


def normalize_description(desc: str) -> str:
    """Lower-case description without digits, punctuation or month names.

    "Staff salaries - Oct 2026" and "Staff salaries Sep" both become "staff salaries",
    which lets us spot recurring payments and duplicate-looking records.
    """
    s = _MONTH_WORDS.sub(" ", desc.lower())
    s = re.sub(r"[^a-z\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or desc.lower().strip()


@dataclass
class Txn:
    id: str
    line: int
    date: date
    desc: str
    type: str
    amount: int  # paise, always > 0
    category: str = ""
    due: date | None = None
    expected: date | None = None
    actual: date | None = None
    loan_due: date | None = None
    linked: str = ""
    norm: str = ""

    @property
    def inflow(self) -> bool:
        return self.type in ("income", "invoice")


class Validation:
    def __init__(self) -> None:
        self.fatal: list[str] = []
        self.accepted: list[Txn] = []
        self.rejected: list[dict] = []
        self.warnings: list[dict] = []
        self.unknown_columns: list[str] = []
        self.file_opening_balance: int | None = None  # paise
        self.rows_read = 0
        self.duplicate_groups: list[list[Txn]] = []

    def to_public(self) -> dict:
        def rs(p: int) -> float:
            return round(p / 100, 2)

        preview = [
            {
                "line": t.line,
                "transaction_id": t.id,
                "transaction_date": t.date.isoformat(),
                "description": t.desc,
                "transaction_type": t.type,
                "amount": rs(t.amount),
                "category": t.category,
                "due_date": t.due.isoformat() if t.due else "",
                "expected_payment_date": t.expected.isoformat() if t.expected else "",
                "actual_payment_date": t.actual.isoformat() if t.actual else "",
            }
            for t in self.accepted[:10]
        ]
        return {
            "ok": not self.fatal and bool(self.accepted),
            "fatal_errors": self.fatal,
            "rows_read": self.rows_read,
            "accepted_count": len(self.accepted),
            "rejected_count": len(self.rejected),
            "rejected": self.rejected[:100],
            "warnings": self.warnings,
            "unknown_columns": self.unknown_columns,
            "opening_balance_in_file": rs(self.file_opening_balance) if self.file_opening_balance is not None else None,
            "preview": preview,
        }


def parse_date(text: str) -> date:
    if not _DATE_RE.match(text):
        raise ValueError(f"'{text}' is not a valid date (use YYYY-MM-DD)")
    try:
        d = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"'{text}' is not a real calendar date") from None
    if not (2000 <= d.year <= 2100):
        raise ValueError(f"'{text}' is outside the supported range 2000-2100")
    return d


def parse_amount(text: str, *, allow_zero: bool = False) -> int:
    """Return paise. Accepts '1200', '1,200.50', '₹1,200'. Rejects negative/NaN/zero/3+ decimals."""
    s = text.strip()
    for junk in ("₹", "INR", "Rs.", "Rs", ",", " "):
        s = s.replace(junk, "")
    if s == "":
        raise ValueError("amount is empty")
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"amount '{text}' is not a number") from None
    if not d.is_finite():
        raise ValueError(f"amount '{text}' is not a finite number")
    if d < 0:
        raise ValueError(f"amount '{text}' is negative; enter a positive amount, the transaction_type sets the direction")
    if d == 0 and not allow_zero:
        raise ValueError("amount is zero")
    if d.adjusted() > 15:
        raise ValueError(f"amount '{text}' is implausibly large")
    if d != d.quantize(Decimal("0.01")):
        raise ValueError(f"amount '{text}' has more than 2 decimal places")
    paise = int((d * 100).to_integral_value())
    if paise > MAX_AMOUNT_PAISE:
        raise ValueError(f"amount '{text}' is implausibly large")
    return paise


def validate_csv(text) -> Validation:
    v = Validation()
    if not isinstance(text, str):
        v.fatal.append("CSV content must be text.")
        return v
    if len(text.encode("utf-8", "ignore")) > MAX_BYTES:
        v.fatal.append(f"File is too large (limit {MAX_BYTES // 1000} KB).")
        return v
    text = text.lstrip("﻿")
    if not text.strip():
        v.fatal.append("The file is empty.")
        return v
    if "\x00" in text:
        v.fatal.append("The file contains binary data and is not a valid CSV.")
        return v

    reader = csv.DictReader(io.StringIO(text))
    try:
        raw_header = reader.fieldnames
    except csv.Error as exc:
        v.fatal.append(f"Could not read the CSV header: {exc}")
        return v
    if not raw_header:
        v.fatal.append("The file has no header row.")
        return v
    header = [h.strip().lower() for h in raw_header]
    if len(set(header)) != len(header):
        v.fatal.append("The header row contains duplicate column names.")
        return v
    reader.fieldnames = header
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        v.fatal.append("Missing required column(s): " + ", ".join(missing) + ".")
        return v
    v.unknown_columns = [h for h in header if h not in KNOWN_COLUMNS]

    seen_ids: dict[str, int] = {}
    opening_values: set[int] = set()
    try:
        for row in reader:
            line = reader.line_num
            if all((val or "").strip() == "" for key, val in row.items() if key is not None):
                continue  # blank row
            v.rows_read += 1
            if v.rows_read > MAX_ROWS:
                v.fatal.append(f"Too many rows (limit {MAX_ROWS}).")
                v.accepted.clear()
                return v

            def get(key: str) -> str:
                return (row.get(key) or "").strip()

            errors: list[str] = []
            if None in row:
                errors.append("row has more values than the header has columns")
            tid = get("transaction_id")
            if not tid:
                errors.append("transaction_id is missing")
            elif len(tid) > 64:
                errors.append("transaction_id is longer than 64 characters")
            elif tid in seen_ids:
                errors.append(f"transaction_id '{tid}' already used on line {seen_ids[tid]}")

            desc = get("description")
            if not desc:
                errors.append("description is missing")
            elif len(desc) > 200:
                errors.append("description is longer than 200 characters")

            ttype = get("transaction_type").lower()
            if ttype not in TRANSACTION_TYPES:
                errors.append(f"transaction_type '{get('transaction_type')}' is not one of: " + ", ".join(TRANSACTION_TYPES))

            tdate = None
            try:
                tdate = parse_date(get("transaction_date"))
            except ValueError as exc:
                errors.append(f"transaction_date: {exc}" if get("transaction_date") else "transaction_date is missing")

            amount = 0
            try:
                amount = parse_amount(get("amount"))
            except ValueError as exc:
                errors.append(str(exc))

            opt_dates: dict[str, date | None] = {}
            for col in ("due_date", "expected_payment_date", "actual_payment_date", "loan_repayment_due_date"):
                opt_dates[col] = None
                if col in header and get(col):
                    try:
                        opt_dates[col] = parse_date(get(col))
                    except ValueError as exc:
                        errors.append(f"{col}: {exc}")

            if "opening_balance" in header and get("opening_balance"):
                try:
                    opening_values.add(parse_amount(get("opening_balance"), allow_zero=True))
                except ValueError as exc:
                    errors.append(f"opening_balance: {exc}")

            if errors:
                v.rejected.append({"line": line, "transaction_id": tid, "errors": errors})
                continue

            seen_ids[tid] = line
            v.accepted.append(
                Txn(
                    id=tid,
                    line=line,
                    date=tdate,
                    desc=desc,
                    type=ttype,
                    amount=amount,
                    category=get("category"),
                    due=opt_dates["due_date"],
                    expected=opt_dates["expected_payment_date"],
                    actual=opt_dates["actual_payment_date"],
                    loan_due=opt_dates["loan_repayment_due_date"],
                    linked=get("linked_invoice_id"),
                    norm=normalize_description(desc),
                )
            )
    except csv.Error as exc:
        v.fatal.append(f"The file is not a valid CSV: {exc}")
        v.accepted.clear()
        return v

    if v.rows_read == 0:
        v.fatal.append("The file has a header but no data rows.")
        return v

    if opening_values:
        v.file_opening_balance = sorted(opening_values)[0] if len(opening_values) == 1 else None
        if len(opening_values) > 1:
            v.warnings.append(
                {"code": "opening_balance_conflict", "message": "opening_balance has different values on different rows; ignoring it. Set the opening balance in the assumptions panel."}
            )
    if v.unknown_columns:
        v.warnings.append({"code": "unknown_columns", "message": "Ignored unknown column(s): " + ", ".join(v.unknown_columns) + "."})

    by_id = {t.id: t for t in v.accepted}
    for t in v.accepted:
        if t.linked and (t.linked not in by_id or by_id[t.linked].type != "invoice"):
            v.warnings.append(
                {"code": "linked_invoice_not_found", "message": f"Line {t.line}: linked_invoice_id '{t.linked}' does not match any invoice record.", "lines": [t.line]}
            )

    groups: dict[tuple, list[Txn]] = {}
    for t in v.accepted:
        groups.setdefault((t.date, t.amount, t.type, t.norm), []).append(t)
    for group in groups.values():
        if len(group) > 1:
            v.duplicate_groups.append(group)
            v.warnings.append(
                {
                    "code": "possible_duplicate",
                    "message": "Possible duplicate (same date, amount, type and description): "
                    + ", ".join(t.id for t in group)
                    + ". Kept in the calculations; please verify.",
                    "lines": [t.line for t in group],
                }
            )
    return v
