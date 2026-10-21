import re
import statistics
from collections import defaultdict
from datetime import date, timedelta

from sqlmodel import Session, select

from ..db import Account, Transaction, get_setting
from .common import merchant_key

INCOME_RE = re.compile(r"payroll|direct dep|dir dep|salary|paycheck", re.I)
FREQ_FACTOR = {"weekly": 52 / 12, "biweekly": 26 / 12, "semimonthly": 2, "monthly": 1}


def classify_frequency(dates: list[date]) -> str:
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    if not gaps:
        return "monthly"
    med = statistics.median(gaps)
    sd = statistics.pstdev(gaps) if len(gaps) > 1 else 0
    if 5 <= med <= 9:
        return "weekly"
    if 13 <= med <= 15 and sd <= 0.8:
        return "biweekly"
    if 13 <= med <= 17:
        return "semimonthly"
    if 25 <= med <= 35:
        return "monthly"
    return "irregular"


def detect_income(session: Session, today: date | None = None) -> dict:
    today = today or date.today()
    since = today - timedelta(days=365)
    q = select(Transaction, Account).where(
        Transaction.account_id == Account.id,
        Account.role == "income_bills",
        Transaction.amount < 0,
        Transaction.date >= since,
    )
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for t, _ in session.exec(q):
        if t.is_transfer:
            continue
        groups[merchant_key(t.merchant_name, t.name)].append(t)

    sources = []
    for key, ts in groups.items():
        ts.sort(key=lambda t: t.date)
        is_income = any(INCOME_RE.search(t.name) for t in ts)
        if len(ts) < 3 and not is_income:
            continue
        if len(ts) < 2:
            continue
        freq = classify_frequency([t.date for t in ts])
        recent = [-t.amount for t in ts[-3:]]
        amount = statistics.median(recent)
        if freq == "irregular":
            last90 = sum(-t.amount for t in ts if (today - t.date).days <= 90)
            monthly = last90 / 3
        else:
            monthly = amount * FREQ_FACTOR[freq]
        sources.append({
            "name": ts[-1].merchant_name or ts[-1].name, "amount": round(amount, 2),
            "frequency": freq, "monthly": round(monthly, 2),
            "last_date": ts[-1].date.isoformat(), "count": len(ts),
        })
    sources.sort(key=lambda s: -s["monthly"])
    detected = round(sum(s["monthly"] for s in sources), 2)

    override = get_setting(session, "income_override")
    return {
        "sources": sources,
        "detected_monthly": detected,
        "monthly": float(override) if override else detected,
        "overridden": override is not None,
    }
