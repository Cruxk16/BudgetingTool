"""Pull transactions straight from the bank through Plaid instead of uploading statements.

Credentials come from PLAID_CLIENT_ID / PLAID_SECRET / PLAID_ENV, or from data/plaid.json (saved from Settings).
Each linked bank login is a PlaidItem holding its access token and /transactions/sync cursor; both live only in the
local database. Plaid's sign convention matches ours (positive = money left the account), so amounts go in as-is.
"""
import json
import os
from datetime import date, datetime, timezone

import httpx
from sqlmodel import Session, select

from .. import config
from ..db import Account, PlaidItem, Transaction
from . import categorize

CREDS_FILE = config.DATA_DIR / "plaid.json"
HOSTS = {"sandbox": "https://sandbox.plaid.com", "production": "https://production.plaid.com"}
DAYS_REQUESTED = 730  # as much history as Plaid will give on first link
ACCOUNT_TYPES = {("depository", "checking"): "checking", ("depository", "savings"): "savings",
                 ("depository", "money market"): "savings", ("depository", "cd"): "savings", ("credit", None): "credit"}
DEFAULT_ROLE = {"checking": "income_bills", "savings": "savings", "credit": "spending"}


class PlaidError(Exception):
    def __init__(self, message: str, status: int = 502, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


def credentials() -> dict | None:
    cid, secret = os.getenv("PLAID_CLIENT_ID"), os.getenv("PLAID_SECRET")
    if cid and secret:
        return {"client_id": cid.strip(), "secret": secret.strip(), "env": os.getenv("PLAID_ENV", "sandbox").strip()}
    if CREDS_FILE.exists():
        c = json.loads(CREDS_FILE.read_text())
        if c.get("client_id") and c.get("secret"):
            return {"env": "sandbox", **c}
    return None


def save_credentials(client_id: str | None, secret: str | None, env: str = "sandbox") -> None:
    if not client_id or not secret:
        CREDS_FILE.unlink(missing_ok=True)
        return
    if env not in HOSTS:
        raise PlaidError("env must be sandbox or production", 400)
    CREDS_FILE.write_text(json.dumps({"client_id": client_id.strip(), "secret": secret.strip(), "env": env}))
    CREDS_FILE.chmod(0o600)


def call(path: str, body: dict) -> dict:
    c = credentials()
    if not c:
        raise PlaidError("Plaid isn't set up. Add your client ID and secret in Settings.", 400)
    try:
        r = httpx.post(HOSTS.get(c["env"], HOSTS["sandbox"]) + path, timeout=60,
                       json={"client_id": c["client_id"], "secret": c["secret"], **body})
    except httpx.HTTPError as e:
        raise PlaidError(f"Couldn't reach Plaid: {e}")
    data = r.json() if r.content else {}
    if r.status_code != 200:
        code = data.get("error_code")
        msg = data.get("display_message") or data.get("error_message") or r.text[:200]
        raise PlaidError(f"Plaid error ({code or r.status_code}): {msg}", 400 if r.status_code < 500 else 502, code)
    return data


def link_token(session: Session, item_id: int | None = None) -> str:
    """Token for Plaid Link. With item_id, opens Link in update mode to fix a broken login."""
    body = {"client_name": "MoneyTool", "user": {"client_user_id": "moneytool-local"},
            "country_codes": ["US"], "language": "en"}
    if item_id:
        item = session.get(PlaidItem, item_id)
        if not item:
            raise PlaidError("No such bank connection", 404)
        body["access_token"] = item.access_token
    else:
        body["products"] = ["transactions"]
        body["transactions"] = {"days_requested": DAYS_REQUESTED}
    return call("/link/token/create", body)["link_token"]


def _account_type(a: dict) -> str | None:
    return ACCOUNT_TYPES.get((a.get("type"), a.get("subtype"))) or ACCOUNT_TYPES.get((a.get("type"), None))


def link(session: Session, public_token: str, institution: str = "") -> dict:
    """Swap Link's public token for an access token, create the accounts, and pull history."""
    ex = call("/item/public_token/exchange", {"public_token": public_token})
    item = session.exec(select(PlaidItem).where(PlaidItem.item_id == ex["item_id"])).first()
    if item:
        item.access_token = ex["access_token"]
    else:
        item = PlaidItem(item_id=ex["item_id"], access_token=ex["access_token"], institution=institution or "Bank")
    session.add(item)
    session.commit()
    session.refresh(item)

    created = 0
    for a in call("/accounts/get", {"access_token": item.access_token})["accounts"]:
        type_ = _account_type(a)
        if not type_:
            continue  # loans, investments: nothing to budget
        if session.exec(select(Account).where(Account.plaid_account_id == a["account_id"])).first():
            continue
        session.add(Account(institution=item.institution, name=a.get("official_name") or a.get("name") or type_,
                            mask=a.get("mask"), type=type_, role=DEFAULT_ROLE[type_],
                            plaid_account_id=a["account_id"], plaid_item_id=item.id))
        created += 1
    session.commit()
    return {"item_id": item.id, "accounts_created": created, **sync_item(session, item)}


def _fields(t: dict) -> dict:
    pfc = t.get("personal_finance_category") or {}
    src = (pfc.get("detailed") or pfc.get("primary") or "").replace("_", " ").lower() or None
    return {"date": date.fromisoformat(t["date"]), "amount": float(t["amount"]), "name": t.get("name") or "",
            "merchant_name": t.get("merchant_name"), "source_category": src}


def sync_item(session: Session, item: PlaidItem) -> dict:
    """Apply everything new since the item's cursor. Pending transactions are skipped until they post."""
    accounts = {a.plaid_account_id: a.id for a in session.exec(select(Account).where(Account.plaid_item_id == item.id))}
    for _ in range(3):  # Plaid asks us to restart if data changes mid-pagination
        cursor, added, modified, removed = item.cursor, [], [], []
        try:
            while True:
                page = call("/transactions/sync", {"access_token": item.access_token, "count": 500,
                                                   **({"cursor": cursor} if cursor else {})})
                added += page["added"]
                modified += page["modified"]
                removed += page["removed"]
                cursor = page["next_cursor"]
                if not page["has_more"]:
                    break
            break
        except PlaidError as e:
            if e.code == "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION":
                continue
            item.error = e.code or str(e)
            session.add(item)
            session.commit()
            raise
    else:
        raise PlaidError("Plaid kept changing data during sync; try again in a minute.")

    existing = {t.dedupe_key: t for t in session.exec(
        select(Transaction).where(Transaction.account_id.in_(list(accounts.values()))))}
    counts = {"added": 0, "updated": 0, "removed": 0}
    for t in added + modified:
        acct, key = accounts.get(t["account_id"]), f"plaid:{t['transaction_id']}"
        if acct is None or t.get("pending"):
            continue
        row = existing.get(key)
        if row:
            for k, v in _fields(t).items():
                setattr(row, k, v)
            counts["updated"] += 1
        else:
            row = Transaction(account_id=acct, dedupe_key=key, **_fields(t))
            existing[key] = row
            counts["added"] += 1
        session.add(row)
    for r in removed:
        row = existing.pop(f"plaid:{r['transaction_id']}", None)
        if row:
            session.delete(row)
            counts["removed"] += 1

    item.cursor, item.error = cursor, None
    item.last_synced = datetime.now(timezone.utc)
    session.add(item)
    session.commit()
    categorize.apply_all(session)
    return counts


def sync_all(session: Session) -> dict:
    """Sync every linked bank; one broken login doesn't stop the others."""
    totals, errors = {"added": 0, "updated": 0, "removed": 0}, []
    for item in session.exec(select(PlaidItem)).all():
        try:
            for k, v in sync_item(session, item).items():
                totals[k] += v
        except PlaidError as e:
            errors.append({"institution": item.institution, "error": str(e), "code": e.code})
    return {**totals, "errors": errors}


def unlink(session: Session, item_id: int) -> None:
    """Revoke the access token at Plaid. Accounts and their transactions are kept as ordinary accounts."""
    item = session.get(PlaidItem, item_id)
    if not item:
        raise PlaidError("No such bank connection", 404)
    try:
        call("/item/remove", {"access_token": item.access_token})
    except PlaidError as e:
        if e.status != 400:
            raise  # already-invalid tokens are fine to forget; network trouble isn't
    for a in session.exec(select(Account).where(Account.plaid_item_id == item.id)):
        a.plaid_item_id = a.plaid_account_id = None
        session.add(a)
    session.delete(item)
    session.commit()


def status(session: Session) -> dict:
    c = credentials()
    items = session.exec(select(PlaidItem)).all()
    return {"configured": c is not None, "env": c["env"] if c else None,
            "items": [{"id": i.id, "institution": i.institution, "error": i.error,
                       "last_synced": i.last_synced.isoformat() if i.last_synced else None} for i in items]}
