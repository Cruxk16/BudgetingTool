"""Fake data so the app is usable (and testable) without importing any statements."""
import random
from datetime import date, timedelta

from sqlmodel import Session, select

from .db import Account, Transaction
from .services import categorize
from .services.common import add_months

DEMO_NAME = "Demo Bank"


def _txn(n, acct, d, amount, name, source_category=None):
    return Transaction(account_id=acct.id, dedupe_key=f"demo-{n}", date=d, amount=amount, name=name,
                       source_category=source_category)


def seed(session: Session, today: date | None = None) -> int:
    today = today or date.today()
    for a in session.exec(select(Account).where(Account.institution == DEMO_NAME)):
        for t in session.exec(select(Transaction).where(Transaction.account_id == a.id)):
            session.delete(t)
        session.delete(a)
    session.commit()

    accts = {}
    for key, name, mask, type_, role in [
        ("wf", "Wells Fargo Checking", "1111", "checking", "income_bills"),
        ("chase", "Chase Freedom", "2222", "credit", "spending"),
        ("cap", "Capital One Quicksilver", "3333", "credit", "spending"),
    ]:
        a = Account(institution=DEMO_NAME, name=name, mask=mask, type=type_, role=role)
        session.add(a)
        accts[key] = a
    session.commit()
    for a in accts.values():
        session.refresh(a)

    rnd = random.Random(42)
    out, n = [], 0

    def add(*args, **kw):
        nonlocal n
        n += 1
        out.append(_txn(n, *args, **kw))

    start = add_months(today.replace(day=1), -12)
    # biweekly paychecks
    d = start + timedelta(days=3)
    while d <= today:
        add(accts["wf"], d, -2400.00, "ACME CORP PAYROLL")
        d += timedelta(days=14)

    m = start
    while m <= today:
        mo = lambda day: m.replace(day=min(day, 28))
        if mo(1) <= today:
            add(accts["wf"], mo(1), 1500.00, "EXAMPLE PROPERTY RENT")
            add(accts["wf"], mo(5), 350.00, "TOYOTA FINANCIAL")
            add(accts["wf"], mo(8), 85.00, "VERIZON WIRELESS")
            add(accts["wf"], mo(12), round(rnd.uniform(90, 130), 2), "CITY POWER & LIGHT")
            add(accts["chase"], mo(10), 15.49, "NETFLIX.COM")
            add(accts["cap"], mo(14), 11.99, "SPOTIFY USA")
            for w in range(4):
                add(accts["chase"], mo(2 + w * 7), round(rnd.uniform(85, 140), 2), "WHOLE FOODS MARKET")
                add(accts["cap"], mo(4 + w * 7), round(rnd.uniform(35, 55), 2), "SHELL OIL 5744")
            for _ in range(rnd.randint(6, 10)):
                add(accts[rnd.choice(["chase", "cap"])], mo(rnd.randint(1, 28)), round(rnd.uniform(12, 70), 2),
                    rnd.choice(["CHIPOTLE", "STARBUCKS", "DOORDASH", "OLIVE GARDEN"]))
            for _ in range(rnd.randint(2, 4)):
                add(accts[rnd.choice(["chase", "cap"])], mo(rnd.randint(1, 28)), round(rnd.uniform(20, 120), 2),
                    rnd.choice(["AMAZON.COM", "TARGET", "BEST BUY"]))
            # card payments: Chase matched by wording, Capital One only by amount+date pairing
            spent_chase = round(rnd.uniform(500, 700), 2)
            add(accts["wf"], mo(25), spent_chase, "CHASE CREDIT CRD AUTOPAY")
            add(accts["chase"], mo(26), -spent_chase, "PAYMENT THANK YOU")
            spent_cap = round(rnd.uniform(250, 400), 2)
            add(accts["wf"], mo(27), spent_cap, "CAPITAL ONE MOBILE PYMT")
            add(accts["cap"], mo(28), -spent_cap, "CREDIT RECEIVED")
        m = add_months(m, 1)
    # semi-annual insurance premium
    for k in (-2, -8):
        dd = add_months(today.replace(day=15), k)
        add(accts["wf"], dd, 620.00, "GEICO")

    out = [t for t in out if t.date <= today]
    for t in out:
        session.add(t)
    session.commit()
    categorize.apply_all(session)
    return len(out)
