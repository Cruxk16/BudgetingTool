from datetime import date, datetime, timezone
from typing import Optional

from sqlmodel import Field, Session, SQLModel, create_engine

from .config import DB_URL

engine = create_engine(DB_URL, connect_args={"check_same_thread": False})


class Account(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    institution: str = ""
    name: str = ""
    mask: Optional[str] = None
    type: str = "checking"  # checking | savings | credit
    role: str = "ignore"  # income_bills | spending | savings | ignore
    plaid_account_id: Optional[str] = Field(default=None, index=True)  # set when linked through Plaid
    plaid_item_id: Optional[int] = Field(default=None, foreign_key="plaiditem.id")


class PlaidItem(SQLModel, table=True):
    """One bank login linked through Plaid."""
    id: Optional[int] = Field(default=None, primary_key=True)
    item_id: str = Field(index=True)
    access_token: str
    institution: str = ""
    cursor: Optional[str] = None  # /transactions/sync position
    error: Optional[str] = None  # e.g. ITEM_LOGIN_REQUIRED until the user reconnects
    last_synced: Optional[datetime] = None


class Transaction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="account.id", index=True)
    dedupe_key: str = Field(index=True)  # lets overlapping statements be re-imported safely
    date: date
    amount: float  # positive = money left the account, negative = money came in
    merchant_name: Optional[str] = None
    name: str = ""
    source_category: Optional[str] = None  # category column from the bank's CSV, if any
    category: str = "Other"
    is_transfer: bool = False
    manual: bool = False  # user edited category/transfer flag; auto-categorizer leaves it alone
    notes: Optional[str] = None


class CategoryRule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    match_type: str = "merchant"  # merchant | contains
    pattern: str
    category: str = "Other"
    is_transfer: bool = False


class Budget(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    month: str = Field(index=True)  # YYYY-MM
    category: str
    amount: float
    kind: str = "variable"  # fixed | variable | sinking
    detail: Optional[str] = None


class Goal(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    price: float
    saved_so_far: float = 0
    monthly_contribution: Optional[float] = None
    target_date: Optional[date] = None
    priority: int = 1
    status: str = "active"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class GoalContribution(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    goal_id: int = Field(foreign_key="goal.id", index=True)
    date: date
    amount: float


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str = ""


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """create_all doesn't alter existing tables, so add columns introduced after a database was created."""
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            have = {r[1] for r in conn.exec_driver_sql(f'PRAGMA table_info("{table.name}")')}
            for col in table.columns:
                if col.name not in have:
                    conn.exec_driver_sql(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" '
                                         f'{col.type.compile(engine.dialect)}')


def get_session():
    with Session(engine) as session:
        yield session


def get_setting(session: Session, key: str) -> Optional[str]:
    row = session.get(Setting, key)
    return row.value if row else None


def set_setting(session: Session, key: str, value: Optional[str]) -> None:
    row = session.get(Setting, key)
    if value is None:
        if row:
            session.delete(row)
    elif row:
        row.value = value
        session.add(row)
    else:
        session.add(Setting(key=key, value=value))
    session.commit()
