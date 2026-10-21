import math
from datetime import date

from sqlmodel import Session, select

from ..db import Goal, GoalContribution
from .categorize import DISCRETIONARY
from .common import add_months


def saved_amount(session: Session, goal: Goal) -> float:
    contribs = session.exec(select(GoalContribution).where(GoalContribution.goal_id == goal.id)).all()
    return goal.saved_so_far + sum(c.amount for c in contribs)


def months_until(target: date, today: date) -> int:
    return max(1, math.ceil((target - today).days / 30.4375))


def months_to_goal(remaining: float, monthly: float) -> int | None:
    if remaining <= 0:
        return 0
    return math.ceil(remaining / monthly) if monthly > 0 else None


def _need(goal: Goal, remaining: float, today: date) -> float:
    """Monthly amount this goal asks for; infinity = 'whatever is left'."""
    if goal.monthly_contribution:
        return goal.monthly_contribution
    if goal.target_date:
        return remaining / months_until(goal.target_date, today)
    return math.inf


def plan_all(session: Session, surplus: float, budget_lines: list[dict], today: date | None = None) -> list[dict]:
    today = today or date.today()
    goals = sorted(session.exec(select(Goal).where(Goal.status == "active")), key=lambda g: (g.priority, g.id))
    available = max(surplus, 0.0)
    results = []
    for g in goals:
        saved = saved_amount(session, g)
        remaining = max(g.price - saved, 0.0)
        need = _need(g, remaining, today)
        granted = min(need, available) if remaining > 0 else 0.0
        available -= granted
        months = months_to_goal(remaining, granted)
        shortfall = 0.0 if math.isinf(need) else round(max(need - granted, 0), 2)

        req_by_date = remaining / months_until(g.target_date, today) if g.target_date else None
        fits = remaining == 0 or (not math.isinf(need) and granted >= need - 0.005)
        results.append({
            "id": g.id, "name": g.name, "price": g.price, "saved": round(saved, 2),
            "remaining": round(remaining, 2), "progress": round(min(saved / g.price, 1), 3) if g.price else 1,
            "priority": g.priority, "target_date": g.target_date.isoformat() if g.target_date else None,
            "monthly_contribution": g.monthly_contribution,
            "required_monthly": round(req_by_date, 2) if req_by_date is not None else None,
            "allocated_monthly": round(granted, 2),
            "months_to_goal": months,
            "completion_date": add_months(today, months).isoformat() if months is not None else None,
            "feasible": fits, "shortfall": shortfall,
            "faster": faster(remaining, granted, budget_lines, today),
        })
    return results


def faster(remaining: float, monthly: float, lines: list[dict], today: date) -> list[dict]:
    """What cutting the biggest discretionary categories would do to the finish date."""
    if remaining <= 0:
        return []
    base = months_to_goal(remaining, monthly)
    cands = sorted((l for l in lines if l["kind"] == "variable" and l["category"] in DISCRETIONARY
                    and l["amount"] >= 40), key=lambda l: -l["amount"])[:3]
    out = []
    for l in cands:
        cut = max(10, min(100, round(l["amount"] * 0.25 / 10) * 10))
        new = months_to_goal(remaining, monthly + cut)
        out.append({"category": l["category"], "cut": cut, "new_months": new,
                    "months_saved": (base - new) if base is not None else None,
                    "new_completion": add_months(today, new).isoformat()})
    return out
