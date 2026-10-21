"""Parse bank CSV downloads into transactions.

Handles the formats seen from the three banks (and most others):
  - Chase card:    Transaction Date, Post Date, Description, Category, Type, Amount, Memo
  - Capital One:   Transaction Date, Posted Date, Card No., Description, Category, Debit, Credit
  - Wells Fargo:   no header row: Date, Amount, *, (blank), Description
  - Generic:       a Date column, a Description column, and Amount or Debit/Credit columns

Amounts are stored in the app's convention: positive = money left the account.
"""
import csv
import io
import re
from collections import Counter
from datetime import date, datetime

from sqlmodel import Session, select

from ..db import Account, Transaction
from . import categorize
from .income import INCOME_RE

DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%m-%d-%Y", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y")
ALIASES = {
    "date": ("transaction date", "trans date", "trans. date", "date", "posting date", "posted date", "post date"),
    "description": ("description", "name", "payee", "merchant", "details", "memo", "original description"),
    "amount": ("amount", "transaction amount"),
    "debit": ("debit", "withdrawal", "withdrawals", "money out"),
    "credit": ("credit", "deposit", "deposits", "money in"),
    "category": ("category",),
}


class ImportFailed(ValueError):
    pass


def parse_date(s: str) -> date | None:
    s = s.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def parse_amount(s: str) -> float | None:
    s = (s or "").strip()
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-")
    s = re.sub(r"[^\d.]", "", s)
    if not s:
        return None
    v = float(s)
    return -v if neg else v


def _find(header: list[str], key: str) -> int | None:
    low = [h.strip().lower() for h in header]
    for alias in ALIASES[key]:
        if alias in low:
            return low.index(alias)
    return None


def parse_csv(text: str, sign: str = "auto", account_type: str = "checking") -> list[dict]:
    """Returns [{date, amount, name, source_category}] with amounts in app convention.

    sign: 'auto' | 'negative_is_spend' | 'positive_is_spend' (only matters for single-Amount-column files).
    """
    rows = [r for r in csv.reader(io.StringIO(text.lstrip("﻿"))) if any(c.strip() for c in r)]
    if not rows:
        raise ImportFailed("The file is empty.")

    first = rows[0]
    headerless = parse_date(first[0]) is not None  # e.g. Wells Fargo
    if headerless:
        if len(first) < 5:
            raise ImportFailed("No header row, and it doesn't look like a Wells Fargo export.")
        cols = {"date": 0, "amount": 1, "description": 4, "category": None, "debit": None, "credit": None}
        body = rows
    else:
        header, body = first, rows[1:]
        cols = {k: _find(header, k) for k in ALIASES}
        # prefer "Description" over a looser match like "Memo"
        if cols["date"] is None or cols["description"] is None:
            raise ImportFailed(f"Couldn't find Date and Description columns in header: {', '.join(header)}")
        if cols["amount"] is None and cols["debit"] is None and cols["credit"] is None:
            raise ImportFailed(f"Couldn't find an Amount (or Debit/Credit) column in header: {', '.join(header)}")

    def cell(r, k):
        i = cols[k]
        return r[i] if i is not None and i < len(r) else ""

    parsed = []
    for r in body:
        d = parse_date(cell(r, "date"))
        if d is None:
            continue  # footer / junk line
        desc = re.sub(r"\s+", " ", cell(r, "description")).strip()
        if cols["amount"] is not None:
            a = parse_amount(cell(r, "amount"))
            mode = "single"
        else:
            deb, cre = parse_amount(cell(r, "debit")), parse_amount(cell(r, "credit"))
            a = abs(deb) if deb else -abs(cre) if cre else None
            mode = "split"
        if a is None:
            continue
        parsed.append({"date": d, "amount": a, "name": desc, "source_category": cell(r, "category").strip() or None,
                       "_mode": mode})

    if not parsed:
        raise ImportFailed("No transactions found in the file.")

    if parsed[0]["_mode"] == "single":
        if sign == "auto":
            flip = _guess_flip(parsed, account_type)
        else:
            flip = sign == "negative_is_spend"
        if flip:
            for p in parsed:
                p["amount"] = -p["amount"]
    for p in parsed:
        p.pop("_mode")
    return parsed


def _guess_flip(parsed: list[dict], account_type: str) -> bool:
    """True when the file shows purchases as negative numbers (so we must flip to our convention)."""
    # Card payments are always money in on a card; paychecks always money in on checking.
    rx = categorize.TRANSFER_RE if account_type == "credit" else INCOME_RE
    votes = [p["amount"] > 0 for p in parsed if rx.search(p["name"])]
    if votes:
        return sum(votes) > len(votes) / 2  # positive = inflow  =>  purchases are negative
    return sum(p["amount"] < 0 for p in parsed) > len(parsed) / 2  # purchases outnumber everything else


def import_rows(session: Session, account: Account, parsed: list[dict]) -> dict:
    """Insert new transactions; ones already imported (overlapping statements) are skipped."""
    existing = set(session.exec(select(Transaction.dedupe_key).where(Transaction.account_id == account.id)))
    seen: Counter = Counter()
    added = skipped = 0
    for p in parsed:
        base = f"{p['date']}|{p['amount']:.2f}|{p['name'].lower()}"
        seen[base] += 1  # two identical same-day charges stay distinct
        key = f"{base}#{seen[base]}"
        if key in existing:
            skipped += 1
            continue
        session.add(Transaction(account_id=account.id, dedupe_key=key, date=p["date"], amount=p["amount"],
                                name=p["name"], source_category=p["source_category"]))
        added += 1
    session.commit()
    categorize.apply_all(session)
    return {"added": added, "skipped_duplicates": skipped,
            "from": min(p["date"] for p in parsed).isoformat(), "to": max(p["date"] for p in parsed).isoformat()}
