import statistics
from collections import defaultdict
from datetime import date, timedelta

from sqlmodel import Session, select

from ..db import Account, Transaction
from .budget import find_fixed, find_sinking
from .common import (load_expenses, month_end, month_key, month_start, prev_months,
                     trailing_start)


def overview(session: Session, months: int = 12, today: date | None = None) -> list[dict]:
    today = today or date.today()
    keys = prev_months(today, months - 1) + [month_key(today)]
    start = month_start(keys[0])
    spend, income = defaultdict(float), defaultdict(float)
    for r in load_expenses(session, start, today):
        spend[r.month] += r.amount
    q = select(Transaction, Account).where(Transaction.account_id == Account.id, Transaction.date >= start,
                                           Account.role == "income_bills", Transaction.amount < 0)
    for t, _ in session.exec(q):
        if not t.is_transfer:
            income[month_key(t.date)] += -t.amount
    return [{"month": k, "income": round(income[k], 2), "spending": round(spend[k], 2),
             "net": round(income[k] - spend[k], 2)} for k in keys]


def by_category(session: Session, month: str) -> list[dict]:
    tot = defaultdict(float)
    for r in load_expenses(session, month_start(month), month_end(month)):
        tot[r.category] += r.amount
    return sorted(({"category": c, "amount": round(a, 2)} for c, a in tot.items() if a > 0),
                  key=lambda x: -x["amount"])


def merchants(session: Session, months: int = 3, today: date | None = None) -> dict:
    today = today or date.today()
    tot, cnt, name = defaultdict(float), defaultdict(int), {}
    for r in load_expenses(session, trailing_start(today, months), today):
        tot[r.key] += r.amount
        cnt[r.key] += 1
        name[r.key] = r.merchant
    rows = [{"merchant": name[k], "total": round(tot[k], 2), "visits": cnt[k]} for k in tot]
    return {"by_spend": sorted(rows, key=lambda x: -x["total"])[:10],
            "by_visits": sorted(rows, key=lambda x: -x["visits"])[:10]}


def by_card(session: Session, month: str) -> list[dict]:
    cards = defaultdict(lambda: {"total": 0.0, "categories": defaultdict(float)})
    for r in load_expenses(session, month_start(month), month_end(month)):
        if r.role != "spending":
            continue
        cards[r.account_name]["total"] += r.amount
        cards[r.account_name]["categories"][r.category] += r.amount
    return [{"card": c, "total": round(v["total"], 2),
             "categories": {k: round(a, 2) for k, a in sorted(v["categories"].items(), key=lambda x: -x[1])}}
            for c, v in sorted(cards.items(), key=lambda x: -x[1]["total"])]


def month_over_month(session: Session, month: str) -> list[dict]:
    prev = month_key((month_start(month) - timedelta(days=1)))
    cur, old = defaultdict(float), defaultdict(float)
    for r in load_expenses(session, month_start(prev), month_end(month)):
        (cur if r.month == month else old)[r.category] += r.amount
    cats = set(cur) | set(old)
    rows = [{"category": c, "current": round(cur[c], 2), "previous": round(old[c], 2),
             "change": round(cur[c] - old[c], 2)} for c in cats]
    return sorted(rows, key=lambda x: -abs(x["change"]))


def subscriptions(session: Session, today: date | None = None) -> dict:
    today = today or date.today()
    rows = load_expenses(session, trailing_start(today, 12), today)
    months = prev_months(today, 6)
    subs = [{"name": f["name"], "monthly": f["amount"], "yearly": round(f["amount"] * 12, 2)}
            for f in find_fixed(rows, months) if f["category"] in ("Subscriptions", "Entertainment")]
    subs += [{"name": s["name"], "monthly": s["monthly"], "yearly": s["annual"]}
             for s in find_sinking(rows) if s["category"] in ("Subscriptions", "Entertainment")]
    subs.sort(key=lambda s: -s["monthly"])
    return {"items": subs, "monthly_total": round(sum(s["monthly"] for s in subs), 2),
            "yearly_total": round(sum(s["yearly"] for s in subs), 2)}


def unusual(session: Session, today: date | None = None) -> list[dict]:
    """Recent charges well above the merchant's usual amount."""
    today = today or date.today()
    rows = load_expenses(session, trailing_start(today, 12), today)
    by = defaultdict(list)
    for r in rows:
        by[r.key].append(r)
    out = []
    for rs in by.values():
        if len(rs) < 4:
            continue
        for r in rs:
            if (today - r.date).days > 60:
                continue
            others = [x.amount for x in rs if x.id != r.id and x.amount > 0]
            if len(others) < 3:
                continue
            med = statistics.median(others)
            if r.amount > 2.5 * med and r.amount - med > 50:
                out.append({"date": r.date.isoformat(), "merchant": r.merchant, "amount": round(r.amount, 2),
                            "usual": round(med, 2), "category": r.category})
    return sorted(out, key=lambda x: x["date"], reverse=True)
