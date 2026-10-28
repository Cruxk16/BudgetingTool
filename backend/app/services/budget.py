import calendar
import math
import statistics
from collections import defaultdict
from datetime import date

from sqlmodel import Session, select

from ..db import Budget
from .categorize import DISCRETIONARY
from .common import (EXTRA_INCOME, REIMBURSEMENT, Expense, day_before_month, load_expenses, month_end,
                     month_inflows, month_key, month_start, prev_months, trailing_start)
from .income import detect_income

BASELINE = "baseline"  # pseudo-month holding the user's planned budget
VARIABLE_ONLY = {"Groceries", "Dining", "Gas", "Shopping"}


def find_sinking(rows: list[Expense]) -> list[dict]:
    """Charges that repeat every ~6 or ~12 months at a similar amount."""
    by = defaultdict(list)
    for r in rows:
        by[r.key].append(r)
    out = []
    for key, rs in by.items():
        rs.sort(key=lambda r: r.date)
        if len(rs) < 2:
            continue
        gaps = [(b.date - a.date).days for a, b in zip(rs, rs[1:])]
        if not all(150 <= g <= 200 or 330 <= g <= 400 for g in gaps):
            continue
        amts = [r.amount for r in rs]
        if min(amts) <= 0 or max(amts) / min(amts) > 1.2:
            continue
        annual = rs[-1].amount * 365 / statistics.median(gaps)
        out.append({"key": key, "name": rs[-1].merchant, "category": rs[-1].category,
                    "each": round(rs[-1].amount, 2), "annual": round(annual, 2),
                    "monthly": round(annual / 12, 2)})
    return out


def find_fixed(rows: list[Expense], months: list[str]) -> list[dict]:
    """Merchants charged about once a month at a near-identical amount."""
    by = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r.month in months:
            by[r.key][r.month].append(r)
    need = max(2, math.ceil(0.7 * len(months)))
    out = []
    for key, mm in by.items():
        latest_rows = mm[max(mm)]
        if latest_rows[0].category in VARIABLE_ONLY:
            continue
        if len(mm) < need or any(len(v) > 1 for v in mm.values()):
            continue
        amts = [v[0].amount for v in mm.values()]
        med = statistics.median(amts)
        if med <= 0 or (max(amts) - min(amts)) / med > 0.15:
            continue
        out.append({"key": key, "name": latest_rows[0].merchant, "category": latest_rows[0].category,
                    "amount": round(latest_rows[0].amount, 2)})
    return out


def generate(session: Session, n_months: int = 6, today: date | None = None) -> dict:
    today = today or date.today()
    window = trailing_start(today, 12)
    rows = load_expenses(session, window, day_before_month(today))
    candidates = prev_months(today, n_months)
    first_data = min((r.month for r in rows), default=None)
    months = [m for m in candidates if first_data and m >= first_data]

    sinking = find_sinking(rows)
    sink_keys = {s["key"] for s in sinking}
    rows = [r for r in rows if r.key not in sink_keys]
    fixed = find_fixed(rows, months)
    fixed_keys = {f["key"] for f in fixed}

    lines = []
    by_cat = defaultdict(list)
    for f in fixed:
        by_cat[f["category"]].append(f)
    for cat, items in by_cat.items():
        lines.append({"category": cat, "kind": "fixed", "amount": round(sum(i["amount"] for i in items), 2),
                      "detail": ", ".join(f"{i['name']} ${i['amount']:.2f}" for i in items), "flag": None})

    monthly = defaultdict(lambda: defaultdict(float))
    for r in rows:
        if r.month in months and r.key not in fixed_keys:
            monthly[r.category][r.month] += r.amount
    for cat, per in monthly.items():
        vals = [max(per.get(m, 0), 0) for m in months]
        if not any(vals):
            continue
        med, mean = statistics.median(vals), statistics.mean(vals)
        vol = statistics.pstdev(vals) / mean if mean else 0
        flag = detail = None
        if med == 0:
            flag, detail = "occasional", f"Only some months; ${mean:.0f}/mo on average"
        elif vol > 0.5:
            flag, detail = "unpredictable", f"Varies a lot month to month; consider a ~10% buffer (${med * 1.1:.0f})"
        else:
            detail = f"Median of last {len(months)} months"
        lines.append({"category": cat, "kind": "variable", "amount": round(med, 2), "detail": detail, "flag": flag})

    for s in sinking:
        lines.append({"category": f"Sinking: {s['name']}", "kind": "sinking", "amount": s["monthly"],
                      "detail": f"${s['annual']:.2f}/yr (${s['each']:.2f} each time)", "flag": None})

    order = {"fixed": 0, "sinking": 1, "variable": 2, "payroll": 3}
    lines.sort(key=lambda l: (order[l["kind"]], -l["amount"]))

    income = detect_income(session, today)
    return _summarize(lines, income, months)


def _summarize(lines: list[dict], income: dict, months: list[str]) -> dict:
    tot = {k: round(sum(l["amount"] for l in lines if l["kind"] == k), 2) for k in ("fixed", "sinking", "variable", "payroll")}
    expenses = round(sum(tot.values()), 2)
    surplus = round(income["monthly"] - expenses, 2)
    return {"months_used": months, "n_months": len(months), "income": income, "lines": lines,
            "totals": {**tot, "expenses": expenses, "surplus": surplus},
            "suggestions": cut_suggestions(lines, surplus)}


def cut_suggestions(lines: list[dict], surplus: float) -> list[dict]:
    if surplus >= 0:
        return []
    deficit, out = -surplus, []
    cands = sorted((l for l in lines if l["kind"] == "variable" and l["category"] in DISCRETIONARY),
                   key=lambda l: -l["amount"])
    for l in cands:
        if deficit <= 0:
            break
        cut = round(min(l["amount"] * 0.3, deficit), 2)
        if cut > 0:
            out.append({"category": l["category"], "cut": cut, "from": l["amount"]})
            deficit -= cut
    return out


def save(session: Session, month: str, lines: list[dict]) -> None:
    for b in session.exec(select(Budget).where(Budget.month == month)):
        session.delete(b)
    for l in lines:
        session.add(Budget(month=month, category=l["category"], amount=l["amount"],
                           kind=l["kind"], detail=l.get("detail")))
    session.commit()


def saved_lines(session: Session, month: str) -> list[dict]:
    rows = session.exec(select(Budget).where(Budget.month == month)).all()
    return [{"id": b.id, "category": b.category, "kind": b.kind, "amount": b.amount, "detail": b.detail,
             "flag": None} for b in rows]


def ensure_month(session: Session, month: str) -> None:
    """A new month starts as a copy of the baseline plan."""
    if session.exec(select(Budget).where(Budget.month == month)).first():
        return
    for b in session.exec(select(Budget).where(Budget.month == BASELINE)):
        session.add(Budget(month=month, category=b.category, amount=b.amount, kind=b.kind, detail=b.detail))
    session.commit()


def reset_to_baseline(session: Session, month: str) -> None:
    for b in session.exec(select(Budget).where(Budget.month == month)):
        session.delete(b)
    session.commit()
    ensure_month(session, month)


def current_budget(session: Session, today: date | None = None) -> dict:
    """This month's plan (seeded from the baseline), with what history says you typically spend alongside.
    With no plan at all, falls back to a preview generated from history."""
    today = today or date.today()
    month = month_key(today)
    ensure_month(session, month)
    lines = saved_lines(session, month)
    history = generate(session, today=today)
    if not lines:
        return {**history, "saved": False, "month": month}
    typical: dict[str, float] = defaultdict(float)
    for l in history["lines"]:
        if l["kind"] != "sinking":
            typical[l["category"]] += l["amount"]
    for l in lines:
        l["typical"] = round(typical[l["category"]], 2) if history["n_months"] and l["category"] in typical else None
    return {**_summarize(lines, history["income"], history["months_used"]), "saved": True, "month": month,
            "has_baseline": session.exec(select(Budget).where(Budget.month == BASELINE)).first() is not None}


def progress(session: Session, today: date | None = None) -> dict:
    today = today or date.today()
    month = month_key(today)
    budget = current_budget(session, today)
    spent = defaultdict(float)
    for r in load_expenses(session, month_start(month), month_end(month)):
        spent[r.category] += r.amount
    dim = calendar.monthrange(today.year, today.month)[1]
    day_frac = today.day / dim

    items, seen = [], set()
    for l in budget["lines"]:
        if l["kind"] == "sinking":
            continue
        cat, amt = l["category"], l["amount"]
        seen.add(cat)
        if l["kind"] == "payroll":  # comes out of the paycheck, never appears on a statement
            items.append({"category": cat, "kind": "payroll", "budget": amt, "spent": amt, "pct": 1.0,
                          "status": "payroll"})
            continue
        s = round(spent.get(cat, 0), 2)
        if l["kind"] == "fixed":
            status = "paid" if s >= amt * 0.95 else "upcoming"
        elif s > amt:
            status = "over"
        elif amt and s / amt > day_frac + 0.10:
            status = "over_pace"
        else:
            status = "on_pace"
        items.append({"category": cat, "kind": l["kind"], "budget": amt, "spent": s,
                      "pct": round(s / amt, 3) if amt else None, "status": status})
    unplanned = {c: round(v, 2) for c, v in spent.items() if c not in seen and v > 0}
    for cat, s in unplanned.items():
        items.append({"category": cat, "kind": "unbudgeted", "budget": 0, "spent": s, "pct": None,
                      "status": "unplanned"})
    return {"month": month, "day_fraction": round(day_frac, 3), "items": items,
            "total_budget": round(sum(i["budget"] for i in items), 2),
            "total_spent": round(sum(i["spent"] for i in items), 2),
            "surplus": budget["totals"]["surplus"], "income": budget["income"]["monthly"],
            "free": free_to_spend(session, budget, items, unplanned, today, dim)}


def free_to_spend(session: Session, budget: dict, items: list[dict], unplanned: dict, today: date, dim: int) -> dict:
    """What's genuinely left this month for categories with no budget line (shopping, fun, ...).

    Planned lines reserve max(budget, spent): an unpaid bill is held back, an overspent line eats into the pool."""
    planned = [i for i in items if i["kind"] != "unbudgeted"]
    committed = sum(max(i["budget"], i["spent"]) for i in planned) + budget["totals"]["sinking"]
    unplanned_spent = round(sum(unplanned.values()), 2)
    inflows = month_inflows(session, month_start(month_key(today)), month_end(month_key(today)))
    reimbursed = min(inflows[REIMBURSEMENT], unplanned_spent)  # paybacks for planned lines change nothing: those are held back anyway
    extra = inflows[EXTRA_INCOME]
    left = round(budget["income"]["monthly"] + extra - committed - (unplanned_spent - reimbursed), 2)
    days_left = dim - today.day + 1

    # What history says you usually spend outside the plan, per month.
    rows = load_expenses(session, trailing_start(today, 6), day_before_month(today))
    months = [m for m in prev_months(today, 6) if rows and m >= min(r.month for r in rows)]
    planned_cats = {i["category"] for i in planned}
    typical = defaultdict(float)
    for r in rows:
        if r.month in months and r.category not in planned_cats:
            typical[r.category] += r.amount
    typical = {c: round(v / len(months), 2) for c, v in typical.items() if months and v > 0}
    typical_total = round(sum(typical.values()), 2)
    cats = sorted(set(typical) | set(unplanned), key=lambda c: -(unplanned.get(c, 0) + typical.get(c, 0)))
    return {
        "left": left, "per_day": round(max(left, 0) / days_left, 2), "days_left": days_left,
        "committed": round(committed, 2), "unplanned_spent": unplanned_spent,
        "reimbursed": round(reimbursed, 2), "extra_income": extra,
        "typical_total": typical_total, "typical_months": len(months),
        "planned_surplus": budget["totals"]["surplus"],
        "categories": [{"category": c, "this_month": unplanned.get(c, 0), "typical": typical.get(c)} for c in cats],
    }
