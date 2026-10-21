import re

from sqlmodel import Session, select

from ..db import Account, CategoryRule, Transaction
from .common import merchant_key

CATEGORIES = [
    "Rent/Housing", "Car Payment", "Car Insurance", "Gas", "Groceries", "Dining",
    "Shopping", "Subscriptions", "Utilities", "Phone/Internet", "Entertainment",
    "Health", "Travel", "Gifts", "Student Loans", "Renters Insurance", "Other",
]
TRANSFER_CATEGORIES = ["Card Payment", "Transfer"]
DISCRETIONARY = ["Dining", "Shopping", "Entertainment", "Subscriptions", "Travel", "Gifts", "Other"]

# Checked in order against the transaction description; first hit wins.
KEYWORDS = [
    ("Subscriptions", ["netflix", "spotify", "hulu", "disney", "youtube", "hbo", "apple.com/bill", "amazon prime",
                       "audible", "icloud", "adobe", "dropbox", "patreon", "paramount", "peacock", "subscription"]),
    ("Rent/Housing", ["rent", "apartments", "apartment", "property mgmt", "property management", "mortgage", "hoa"]),
    ("Car Payment", ["auto loan", "auto finance", "toyota financial", "honda financial", "ford credit",
                     "gm financial", "nissan motor acceptance", "car payment", "hyundai motor finance"]),
    ("Student Loans", ["student loan", "dept of education", "dept of ed", "navient", "nelnet", "mohela", "great lakes",
                       "aidvantage", "school loan", "sallie mae"]),
    ("Renters Insurance", ["renters", "renter's", "lemonade", "assurant"]),
    ("Car Insurance", ["geico", "state farm", "progressive", "allstate", "farmers ins", "liberty mutual", "insurance"]),
    ("Phone/Internet", ["verizon", "at&t", "att", "t-mobile", "tmobile", "comcast", "xfinity", "spectrum",
                        "internet", "mint mobile", "cricket wireless", "cox comm"]),
    ("Utilities", ["electric", "power", "water", "utility", "utilities", "pg&e", "con ed", "duke energy", "sewer",
                   "natural gas", "gas & elec"]),
    ("Groceries", ["whole foods", "trader joe", "kroger", "safeway", "publix", "aldi", "wegmans", "sprouts",
                   "grocery", "grocer", "brandywine", "supermarket", "market basket", "food lion", "giant"]),
    ("Gas", ["shell", "chevron", "exxon", "mobil", "bp", "sunoco", "speedway", "valero", "fuel", "gas station",
             "wawa", "sheetz", "circle k"]),
    ("Dining", ["restaurant", "cafe", "coffee", "starbucks", "chipotle", "mcdonald", "doordash", "uber eats",
                "ubereats", "grubhub", "pizza", "grill", "kitchen", "taco", "burger", "dunkin", "olive garden",
                "panera", "subway", "bar & ", "brewery", "diner", "bakery"]),
    ("Health", ["pharmacy", "cvs", "walgreens", "medical", "dental", "dentist", "doctor", "clinic", "hospital",
                "health", "fitness", "gym", "optical"]),
    ("Travel", ["airline", "airlines", "delta air", "united air", "american air", "southwest", "airbnb", "hotel",
                "marriott", "hilton", "expedia", "hyatt", "jetblue", "spirit air", "lodging"]),
    ("Entertainment", ["amc ", "cinema", "theater", "theatre", "ticketmaster", "steam", "playstation", "xbox",
                       "nintendo", "movie", "concert", "bowling"]),
    ("Gifts", ["gift", "florist", "flowers", "1-800-flowers"]),
    ("Shopping", ["amazon", "target", "best buy", "walmart", "ebay", "etsy", "home depot", "lowes", "lowe's", "ikea",
                  "nike", "apple store", "costco", "macys", "nordstrom", "tj maxx", "marshalls", "kohls"]),
]
KEYWORD_RES = [(cat, re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(k) for k in kws) + r")s?(?![a-z])", re.I))
               for cat, kws in KEYWORDS]

# Fallback: the category column some banks (Chase, Capital One) include in their CSVs.
SOURCE_HINTS = [
    ("grocer", "Groceries"), ("gas", "Gas"), ("automotive", "Gas"), ("food", "Dining"), ("dining", "Dining"),
    ("restaurant", "Dining"), ("shop", "Shopping"), ("merchandise", "Shopping"), ("travel", "Travel"),
    ("airfare", "Travel"), ("lodging", "Travel"), ("entertain", "Entertainment"), ("health", "Health"),
    ("medical", "Health"), ("phone", "Phone/Internet"), ("cable", "Phone/Internet"), ("utilit", "Utilities"),
    ("gift", "Gifts"), ("home", "Shopping"),
]

# Wording banks use for paying a card from checking, or moving money between own accounts.
TRANSFER_RE = re.compile(
    r"payment thank you|pymt|autopay|auto pay|online pmt|online payment|payment received|epayment|mobile payment|"
    r"card payment|credit card pmt|crcardpmt|credit crd|online transfer|transfer to|transfer from|funds transfer|"
    r"internal transfer", re.I)


CARD_PAYMENT_RE = re.compile(
    r"payment thank you|pymt|autopay|auto pay|online pmt|payment received|epayment|mobile payment|card payment|"
    r"credit card pmt|crcardpmt|credit crd", re.I)


def keyword_category(t: Transaction) -> str:
    text = f"{t.merchant_name or ''} {t.name}"
    for cat, rx in KEYWORD_RES:
        if rx.search(text):
            return cat
    src = (t.source_category or "").lower()
    for hint, cat in SOURCE_HINTS:
        if hint in src:
            return cat
    return "Other"


def looks_like_transfer(t: Transaction) -> bool:
    if TRANSFER_RE.search(t.name):
        return True
    # Capital One / Chase label card payments in their own Category column
    return (t.source_category or "").strip().lower() in ("payment/credit", "payment", "payments")


def _rule_matches(rule: CategoryRule, t: Transaction) -> bool:
    pat = rule.pattern.lower()
    if rule.match_type == "merchant":
        return pat in ((t.merchant_name or "").lower(), merchant_key(t.merchant_name, t.name), t.name.lower())
    return pat in t.name.lower() or pat in (t.merchant_name or "").lower()


def apply_all(session: Session) -> int:
    """Recompute category / is_transfer for every transaction the user hasn't hand-edited."""
    rules = session.exec(select(CategoryRule)).all()
    accounts = {a.id: a for a in session.exec(select(Account))}
    roles = {i: a.role for i, a in accounts.items()}
    txns = [t for t in session.exec(select(Transaction)) if not t.manual]

    for t in txns:
        rule = next((r for r in rules if _rule_matches(r, t)), None)
        if rule:
            t.category, t.is_transfer = rule.category, rule.is_transfer
        else:
            t.is_transfer = looks_like_transfer(t)
            t.category = keyword_category(t)
            if roles.get(t.account_id) == "income_bills" and t.amount < 0:
                t.category = "Income"

    # A checking outflow matched by a card inflow of the same amount within 5 days
    # is a card payment even when the description didn't say so.
    free = [t for t in txns if not any(_rule_matches(r, t) for r in rules)]
    outs = [t for t in free if roles.get(t.account_id) == "income_bills" and t.amount > 0]
    ins = [t for t in free if roles.get(t.account_id) == "spending" and t.amount < 0]
    used: set[int] = set()
    paired_outs: set[int] = set()
    for o in outs:
        for i in ins:
            if i.id in used:
                continue
            if abs(o.amount + i.amount) < 0.005 and abs((o.date - i.date).days) <= 5:
                o.is_transfer = i.is_transfer = True
                used.add(i.id)
                paired_outs.add(o.id)
                break

    for t in txns:
        if t.is_transfer:
            # Anything landing on a card is a payment toward it; so is a checking outflow matched to one.
            is_card_payment = (accounts[t.account_id].type == "credit" or t.id in paired_outs
                               or bool(CARD_PAYMENT_RE.search(t.name)))
            t.category = "Card Payment" if is_card_payment else "Transfer"
        session.add(t)
    session.commit()
    return len(txns)
