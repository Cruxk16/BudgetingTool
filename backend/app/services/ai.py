"""Ask questions about your finances in plain English, answered by Gemini.

The model never sees your database. It gets a handful of read-only tools (budget, spending, transactions...) and
calls them as needed; only what a tool returns is sent to Google.
"""
import os
from datetime import date

import httpx
from sqlmodel import Session, select

from .. import config
from ..db import Account, Transaction, get_setting, set_setting
from . import analyzer, budget, goals as goals_svc
from .common import month_key

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
FALLBACK_MODELS = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
                   "gemini-4-argon"]
SKIP_WORDS = ("embedding", "tts", "image", "live", "audio", "robotics", "computer-use", "aqa", "vision", "transcribe", "banana")
LIST_URL = "https://generativelanguage.googleapis.com/v1beta/models"
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
KEY_FILE = config.DATA_DIR / "gemini.key"
MAX_ROUNDS = 6


class AIError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def api_key() -> str | None:
    key = os.getenv("GEMINI_API_KEY")
    if key:
        return key.strip()
    return KEY_FILE.read_text().strip() if KEY_FILE.exists() else None


def save_key(key: str | None) -> None:
    if not key:
        KEY_FILE.unlink(missing_ok=True)
        return
    KEY_FILE.write_text(key.strip())
    KEY_FILE.chmod(0o600)


def current_model(session: Session) -> str:
    return get_setting(session, "gemini_model") or DEFAULT_MODEL


def set_model(session: Session, model: str) -> None:
    set_setting(session, "gemini_model", model.strip() or None)


def available_models(session: Session) -> dict:
    """Models this API key can use for chat, falling back to a built-in list if Google can't be reached."""
    current, key = current_model(session), api_key()
    ids, live = [], False
    if key:
        try:
            token = None
            for _ in range(5):
                r = httpx.get(LIST_URL, headers={"x-goog-api-key": key}, timeout=20,
                              params={"pageSize": 100, **({"pageToken": token} if token else {})})
                r.raise_for_status()
                data = r.json()
                for m in data.get("models", []):
                    mid = m["name"].removeprefix("models/")
                    if ("generateContent" in m.get("supportedGenerationMethods", []) and mid.startswith("gemini")
                            and not any(w in mid for w in SKIP_WORDS)):
                        ids.append(mid)
                token = data.get("nextPageToken")
                if not token:
                    break
            live = bool(ids)
        except (httpx.HTTPError, KeyError, ValueError):
            ids = []
    if not ids:
        ids = list(FALLBACK_MODELS)
    if current not in ids:
        ids.insert(0, current)
    return {"models": sorted(set(ids)), "current": current, "from_google": live}


def system_prompt() -> str:
    return f"""You are the analyst inside a private, local budgeting app. Today is {date.today().isoformat()}.
Answer the user's questions about THEIR money using the tools; never guess numbers you could look up.

How the app works:
- The user has a planned monthly budget (baseline). Income is their take-home pay. Surplus = income - planned lines.
- Categories with no budget line (Shopping, Entertainment, Travel, Gifts, Other...) are "fun money": what is left
  for them is the surplus minus unpaid bills and overspending. get_month_progress returns it as `free`.
- Amounts: positive = money spent, negative = money in/refund. Transfers (card payments) are excluded from spending.
- The "Gym (payroll)" line is deducted from paychecks and never appears as a transaction.
Be concise and concrete: lead with the answer, show the key numbers, and say when data is thin (few months imported).
Give practical suggestions when asked, but you are not a licensed financial advisor."""


DECLS = [
    {"name": "get_budget_plan", "description": "The planned monthly budget: income, every planned line (fixed/variable/payroll) with what the user "
     "typically actually spends, and the planned surplus."},
    {"name": "get_month_progress", "description": "This month so far: planned vs actual per category, pace status, and the "
     "'free' object = how much fun money is left, per-day amount, and unplanned category spending vs usual."},
    {"name": "spending_by_category", "description": "Total spending per category for one month.",
     "parameters": {"type": "object", "properties": {"month": {"type": "string", "description": "YYYY-MM; default this month"}}}},
    {"name": "monthly_trend", "description": "Income, spending and net for each of the last 12 months."},
    {"name": "compare_months", "description": "Category-by-category change between a month and the month before it.",
     "parameters": {"type": "object", "properties": {"month": {"type": "string", "description": "YYYY-MM; default this month"}}}},
    {"name": "top_merchants", "description": "Top 10 merchants by total spend and by visit count over recent months.",
     "parameters": {"type": "object", "properties": {"months": {"type": "integer", "description": "default 3"}}}},
    {"name": "search_transactions", "description": "Find individual transactions. Returns up to `limit` newest matches plus the "
     "total count and sum of all matches.",
     "parameters": {"type": "object", "properties": {
         "text": {"type": "string", "description": "substring of the merchant/description"},
         "category": {"type": "string"}, "start_date": {"type": "string", "description": "YYYY-MM-DD"},
         "end_date": {"type": "string", "description": "YYYY-MM-DD"},
         "min_amount": {"type": "number", "description": "only transactions at least this large (absolute value)"},
         "include_transfers": {"type": "boolean"}, "limit": {"type": "integer", "description": "max 50, default 25"}}}},
    {"name": "get_subscriptions", "description": "Detected recurring subscriptions with monthly and yearly cost."},
    {"name": "get_unusual_charges", "description": "Recent charges far above that merchant's usual amount."},
    {"name": "get_goals", "description": "Savings goals with progress, projected finish dates, and what cutting spending would do."},
]


class Tools:
    def __init__(self, session: Session):
        self.s = session

    def get_budget_plan(self):
        b = budget.current_budget(self.s)
        return {"income_monthly": b["income"]["monthly"], "totals": b["totals"],
                "lines": [{k: l.get(k) for k in ("category", "kind", "amount", "typical", "detail")} for l in b["lines"]]}

    def get_month_progress(self):
        return budget.progress(self.s)

    def spending_by_category(self, month: str | None = None):
        return analyzer.by_category(self.s, month or month_key(date.today()))

    def monthly_trend(self):
        return analyzer.overview(self.s)

    def compare_months(self, month: str | None = None):
        return analyzer.month_over_month(self.s, month or month_key(date.today()))

    def top_merchants(self, months: int = 3):
        return analyzer.merchants(self.s, months=max(1, min(int(months), 12)))

    def search_transactions(self, text: str = "", category: str = "", start_date: str = "", end_date: str = "",
                            min_amount: float = 0, include_transfers: bool = False, limit: int = 25):
        q = select(Transaction, Account).where(Transaction.account_id == Account.id)
        if text:
            q = q.where(Transaction.name.ilike(f"%{text}%"))
        if category:
            q = q.where(Transaction.category == category)
        if start_date:
            q = q.where(Transaction.date >= date.fromisoformat(start_date))
        if end_date:
            q = q.where(Transaction.date <= date.fromisoformat(end_date))
        if not include_transfers:
            q = q.where(Transaction.is_transfer == False)  # noqa: E712
        rows = [(t, a) for t, a in self.s.exec(q.order_by(Transaction.date.desc()))
                if abs(t.amount) >= min_amount]
        limit = max(1, min(int(limit), 50))
        return {"matches": len(rows), "sum_of_all_matches": round(sum(t.amount for t, _ in rows), 2),
                "transactions": [{"date": t.date.isoformat(), "description": t.name, "amount": t.amount,
                                  "category": t.category, "account": a.name} for t, a in rows[:limit]]}

    def get_subscriptions(self):
        return analyzer.subscriptions(self.s)

    def get_unusual_charges(self):
        return analyzer.unusual(self.s)

    def get_goals(self):
        b = budget.current_budget(self.s)
        return {"monthly_surplus": b["totals"]["surplus"],
                "goals": goals_svc.plan_all(self.s, b["totals"]["surplus"], b["lines"])}

    def run(self, name: str, args: dict):
        if name not in {d["name"] for d in DECLS}:
            return {"error": f"unknown tool {name}"}
        try:
            return getattr(self, name)(**(args or {}))
        except Exception as e:  # bad arguments from the model: let it retry
            return {"error": str(e)}


def call_gemini(payload: dict, model: str) -> dict:
    key = api_key()
    if not key:
        raise AIError("No Gemini API key set. Add one on the Ask AI page.", 400)
    try:
        r = httpx.post(URL.format(model=model), headers={"x-goog-api-key": key}, json=payload, timeout=90)
    except httpx.HTTPError as e:
        raise AIError(f"Couldn't reach Gemini: {e}")
    if r.status_code != 200:
        try:
            msg = r.json()["error"]["message"]
        except Exception:
            msg = r.text[:200]
        raise AIError(f"Gemini error ({r.status_code}, model {model}): {msg}", 400 if r.status_code in (400, 403) else 502)
    return r.json()


def ask(session: Session, question: str, history: list[dict]) -> dict:
    tools = Tools(session)
    model = current_model(session)
    contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["text"]}]} for m in history]
    contents.append({"role": "user", "parts": [{"text": question}]})
    used: list[str] = []

    for _ in range(MAX_ROUNDS):
        resp = call_gemini({"systemInstruction": {"parts": [{"text": system_prompt()}]}, "contents": contents,
                            "tools": [{"functionDeclarations": DECLS}]}, model)
        cands = resp.get("candidates") or []
        if not cands or "content" not in cands[0]:
            reason = (resp.get("promptFeedback") or {}).get("blockReason") or (cands[0].get("finishReason") if cands else "")
            raise AIError(f"Gemini returned no answer{f' ({reason})' if reason else ''}. Try rephrasing.")
        content = cands[0]["content"]
        parts = content.get("parts", [])
        calls = [p["functionCall"] for p in parts if "functionCall" in p]
        if not calls:
            text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
            return {"answer": text or "(no answer)", "tools_used": used, "model": model}
        contents.append(content)  # verbatim, so Gemini keeps its reasoning context between tool calls
        results = []
        for c in calls:
            used.append(c["name"])
            results.append({"functionResponse": {"name": c["name"],
                                                 "response": {"result": tools.run(c["name"], c.get("args") or {})}}})
        contents.append({"role": "user", "parts": results})
    raise AIError("That took too many steps. Try a more specific question.")
