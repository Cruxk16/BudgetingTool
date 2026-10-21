"""Parse text-based bank statement PDFs into transactions.

Statements don't share a format, so this is heuristic: it finds lines that start with a date and end with an
amount, works out the year from the statement period, and decides money-in vs money-out from (in order)
  1. the Deposits / Withdrawals column the amount sits under (checking statements),
  2. an explicit minus / parentheses / CR marker,
  3. the section heading it falls under ("Payments and Credits" vs "Purchases"),
  4. keywords like "payroll" (checking only).
Always shown to the user as a preview before anything is saved.
Amounts are returned in the app's convention: positive = money left the account.
"""
import io
import re
from dataclasses import dataclass
from datetime import date, timedelta

from .importer import ImportFailed, parse_date

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
NUM_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?$")
AMOUNT = re.compile(r"^[-(]?\$?[\d,]*\d\.\d{2}\)?-?$")
SKIP_DESC = re.compile(r"^(beginning|ending|opening|closing|previous|new|starting)\b.*balance|^total\b|^balance\b",
                       re.I)
CREDIT_HEAD = re.compile(r"payments?|credits?|deposits?|refunds?", re.I)
DEBIT_HEAD = re.compile(r"purchases?|withdrawals?|debits?|charges|transactions?|fees|checks?", re.I)
CREDIT_WORDS = re.compile(r"payroll|direct dep|deposit|transfer from|interest paid|refund|reversal|credit", re.I)
PERIOD_NUM = re.compile(r"(\d{1,2}/\d{1,2}/\d{2,4})\s*(?:-|–|to|through|thru)\s*(\d{1,2}/\d{1,2}/\d{2,4})", re.I)
PERIOD_WORDS = re.compile(r"([A-Za-z]{3,9}\.? \d{1,2}, ?\d{4})\s*(?:-|–|to|through|thru)\s*([A-Za-z]{3,9}\.? \d{1,2}, ?\d{4})", re.I)


@dataclass
class W:
    text: str
    x0: float
    x1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2


def extract_lines(data: bytes) -> list[list[W]]:
    import pdfplumber
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as e:
        raise ImportFailed(f"Couldn't open the PDF ({'password protected?' if 'assword' in str(e) else e}).")
    lines: list[list[W]] = []
    with pdf:
        for page in pdf.pages:
            words = sorted(page.extract_words(keep_blank_chars=False), key=lambda w: (round(w["top"]), w["x0"]))
            cur, top = [], None
            for w in words:
                if top is not None and abs(w["top"] - top) > 3:
                    lines.append(sorted(cur, key=lambda x: x.x0))
                    cur = []
                if not cur:
                    top = w["top"]
                cur.append(W(w["text"], w["x0"], w["x1"]))
            if cur:
                lines.append(sorted(cur, key=lambda x: x.x0))
    return lines


def find_period(text: str) -> tuple[date, date] | None:
    for rx in (PERIOD_NUM, PERIOD_WORDS):
        for m in rx.finditer(text):
            a, b = parse_date(m.group(1).replace(".", "")), parse_date(m.group(2).replace(".", ""))
            if a is None and b is None:
                a, b = parse_date(re.sub(r"^(\w{3})\w*", r"\1", m.group(1).replace(".", ""))), \
                       parse_date(re.sub(r"^(\w{3})\w*", r"\1", m.group(2).replace(".", "")))
            if a and b and a <= b:
                return a, b
    return None


def _take_date(ws: list[W], i: int):
    """(month, day, year|None, next_index) if a date starts at ws[i]."""
    if i >= len(ws):
        return None
    m = NUM_DATE.match(ws[i].text)
    if m:
        y = int(m.group(3)) if m.group(3) else None
        if y is not None and y < 100:
            y += 2000
        mo, d = int(m.group(1)), int(m.group(2))
        return (mo, d, y, i + 1) if 1 <= mo <= 12 and 1 <= d <= 31 else None
    mon = MONTHS.get(ws[i].text.lower().rstrip(".")[:3]) if re.fullmatch(r"[A-Za-z]{3,4}\.?", ws[i].text) else None
    if mon and i + 1 < len(ws) and re.fullmatch(r"\d{1,2}", ws[i + 1].text) and 1 <= int(ws[i + 1].text) <= 31:
        return mon, int(ws[i + 1].text), None, i + 2
    return None


def _trailing_amounts(ws: list[W], start: int):
    """Peel amounts off the end of a line. Returns (description_words, [(value, negative, cx)])."""
    end, out = len(ws), []
    while end > start:
        t = ws[end - 1].text
        cr = t.upper() == "CR" and end - 1 > start and AMOUNT.match(ws[end - 2].text)
        a = ws[end - 2] if cr else ws[end - 1]
        if not AMOUNT.match(a.text):
            break
        neg = cr or a.text.startswith(("-", "(")) or a.text.endswith(("-", ")"))
        end -= 2 if cr else 1
        if end > start and ws[end - 1].text in ("-", "–"):
            neg, end = True, end - 1
        out.append((float(re.sub(r"[^\d.]", "", a.text)), neg, a.cx))
    return ws[start:end], out[::-1]


def parse_pdf(data: bytes, account_type: str = "checking") -> list[dict]:
    lines = extract_lines(data)
    text = "\n".join(" ".join(w.text for w in ln) for ln in lines)
    if len(text.strip()) < 50:
        raise ImportFailed("This PDF has no readable text (it may be a scan). Download the statement again from "
                           "the bank's website rather than scanning it.")

    period = find_period(text)
    if period:
        end = period[1]
    else:
        years = [int(y) for y in re.findall(r"\b(20\d\d)\b", text)]
        if not years:
            raise ImportFailed("Couldn't find the statement period/year in this PDF.")
        end = date(max(years), 12, 31)

    def year_for(mo, d, y):
        if y:
            return y
        yr = end.year
        try:
            if date(yr, mo, d) > end + timedelta(days=31):
                yr -= 1
        except ValueError:
            pass
        return yr

    cols: dict[str, float] = {}
    section_credit = False
    rows = []
    for ln in lines:
        low = " ".join(w.text for w in ln).lower()
        if re.search(r"deposit|credit", low) and re.search(r"withdraw|debit", low) and not _take_date(ln, 0):
            cols = {}
            for w in ln:
                t = w.text.lower()
                if re.search(r"deposit|credit", t):
                    cols["credit"] = w.cx
                elif re.search(r"withdraw|debit", t):
                    cols["debit"] = w.cx
                elif "balance" in t:
                    cols["balance"] = w.cx
            if "credit" not in cols or "debit" not in cols:
                cols = {}
            continue

        dt = _take_date(ln, 0)
        if not dt:
            if len(ln) <= 12 and not any(AMOUNT.match(w.text) for w in ln):
                if CREDIT_HEAD.search(low):
                    section_credit = True
                elif DEBIT_HEAD.search(low):
                    section_credit = False
            continue
        mo, d, y, i = dt
        second = _take_date(ln, i)  # posting date
        if second:
            i = second[3]
        desc_words, amts = _trailing_amounts(ln, i)
        desc = re.sub(r"\s+", " ", " ".join(w.text for w in desc_words)).strip()
        if not amts or not desc or SKIP_DESC.search(desc):
            continue

        if cols:
            picks = [(min(cols, key=lambda c: abs(cols[c] - cx)), v, neg) for v, neg, cx in amts]
            picks = [p for p in picks if p[0] != "balance"]
            if not picks:
                continue
            col, value, neg = picks[0]
            credit = col == "credit"
        else:
            value, neg, _ = amts[0]
            if account_type == "credit":
                credit = section_credit or neg
            else:
                credit = section_credit or neg or bool(CREDIT_WORDS.search(desc))
        try:
            when = date(year_for(mo, d, y), mo, d)
        except ValueError:
            continue
        rows.append({"date": when, "amount": -value if credit else value, "name": desc, "source_category": None})

    if not rows:
        raise ImportFailed("Couldn't find any transactions in this PDF. It may use a layout I don't recognize.")
    return rows
