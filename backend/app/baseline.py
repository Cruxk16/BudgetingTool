"""Load the user's own planned budget (data/baseline.json) as the baseline."""
import json
from pathlib import Path

from sqlmodel import Session, select

from . import config
from .db import Budget, CategoryRule, set_setting
from .services import budget, categorize


def load(session: Session, path: Path | None = None) -> int:
    path = path or config.BASELINE_FILE
    spec = json.loads(Path(path).read_text())
    lines = []
    for l in spec["lines"]:
        items = l["items"]
        lines.append({
            "category": l["category"], "kind": l["kind"],
            "amount": round(sum(i["amount"] for i in items), 2),
            "detail": ", ".join(f"{i['name']} ${i['amount']:.2f}" for i in items),
        })
    budget.save(session, budget.BASELINE, lines)
    if spec.get("income"):
        set_setting(session, "income_override", str(spec["income"]))
    for r in spec.get("rules", []):
        pattern = r["pattern"].lower()
        exists = session.exec(select(CategoryRule).where(CategoryRule.pattern == pattern)).first()
        if not exists:
            session.add(CategoryRule(match_type=r.get("match_type", "contains"), pattern=pattern,
                                     category=r["category"], is_transfer=r.get("is_transfer", False)))
    session.commit()
    categorize.apply_all(session)
    return len(lines)


def autoload(session: Session) -> None:
    """First run only: if a baseline file exists and no baseline has been saved yet."""
    if config.BASELINE_FILE.exists() and not session.exec(
            select(Budget).where(Budget.month == budget.BASELINE)).first():
        load(session)
