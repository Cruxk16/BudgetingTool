import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from sqlmodel import Session, select

from ..db import Account, Transaction

SPEND_ROLES = ("income_bills", "spending")


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def month_start(key: str) -> date:
    y, m = key.split("-")
    return date(int(y), int(m), 1)


def month_end(key: str) -> date:
    s = month_start(key)
    return s.replace(day=calendar.monthrange(s.year, s.month)[1])


def add_months(d: date, n: int) -> date:
    idx = d.year * 12 + (d.month - 1) + n
    y, m = divmod(idx, 12)
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def prev_months(today: date, n: int) -> list[str]:
    """The n complete months before today's month, oldest first."""
    first = today.replace(day=1)
    return [month_key(add_months(first, -i)) for i in range(n, 0, -1)]


def merchant_key(merchant_name: Optional[str], name: str) -> str:
    s = (merchant_name or name or "").lower()
    s = re.sub(r"[#*]\s*\w*\d\w*", " ", s)
    s = re.sub(r"\d+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class Expense:
    id: int
    date: date
    month: str
    amount: float  # positive = spend, negative = refund
    category: str
    merchant: str
    key: str
    account_id: int
    account_name: str
    role: str


def load_expenses(
    session: Session,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> list[Expense]:
    """Real spending only: no transfers, no deposits, no savings/ignored accounts."""
    q = select(Transaction, Account).where(Transaction.account_id == Account.id)
    if start:
        q = q.where(Transaction.date >= start)
    if end:
        q = q.where(Transaction.date <= end)
    out = []
    for t, a in session.exec(q):
        if a.role not in SPEND_ROLES or t.is_transfer or t.amount == 0:
            continue
        if a.role == "income_bills" and t.amount < 0:
            continue  # a deposit, not a refund
        label = f"{a.name} ••{a.mask}" if a.mask else a.name
        out.append(Expense(
            t.id, t.date, month_key(t.date), t.amount, t.category,
            t.merchant_name or t.name, merchant_key(t.merchant_name, t.name),
            a.id, label, a.role,
        ))
    return out


def trailing_start(today: date, months: int) -> date:
    return add_months(today.replace(day=1), -months)


def day_before_month(today: date) -> date:
    return today.replace(day=1) - timedelta(days=1)
