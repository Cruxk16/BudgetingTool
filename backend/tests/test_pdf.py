import io
from datetime import date

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.services.importer import ImportFailed
from app.services.pdf_import import parse_pdf


def make_pdf(items):
    """items: (x, y, text, right_aligned) tuples, y measured from the top."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 9)
    for x, y, text, right in items:
        (c.drawRightString if right else c.drawString)(x, 760 - y, text)
    c.save()
    return buf.getvalue()


def lines(*texts, start=40, step=14):
    return [(40, start + i * step, t, False) for i, t in enumerate(texts)]


def test_chase_style():
    pdf = make_pdf(lines(
        "Opening/Closing Date 12/05/25 - 01/04/26", "ACCOUNT ACTIVITY", "PAYMENTS AND OTHER CREDITS",
        "12/20 Payment Thank You-Web -500.00", "PURCHASE", "12/06 WHOLE FOODS MARKET #123 AUSTIN TX 84.10",
        "01/02 NETFLIX.COM LOS GATOS CA 15.49", "Total fees charged in 2026 $0.00"))
    got = {r["name"]: r for r in parse_pdf(pdf, "credit")}
    assert got["Payment Thank You-Web"]["amount"] == -500
    assert got["WHOLE FOODS MARKET #123 AUSTIN TX"]["amount"] == 84.10
    assert got["WHOLE FOODS MARKET #123 AUSTIN TX"]["date"] == date(2025, 12, 6)  # year rolls back across new year
    assert got["NETFLIX.COM LOS GATOS CA"]["date"] == date(2026, 1, 2)
    assert len(got) == 3


def test_capital_one_style():
    pdf = make_pdf(lines(
        "Dec 14, 2025 - Jan 13, 2026 | 31 days in Billing Cycle", "Payments, Credits and Adjustments",
        "Dec 20 Dec 21 CAPITAL ONE MOBILE PYMT - $300.00", "Transactions",
        "Dec 15 Dec 16 SHELL OIL 5744 $41.20", "Jan 2 Jan 3 AMAZON.COM $25.00"))
    got = {r["name"]: r for r in parse_pdf(pdf, "credit")}
    assert got["CAPITAL ONE MOBILE PYMT"]["amount"] == -300
    assert got["SHELL OIL 5744"]["amount"] == 41.20 and got["SHELL OIL 5744"]["date"] == date(2025, 12, 15)
    assert got["AMAZON.COM"]["amount"] == 25 and got["AMAZON.COM"]["date"] == date(2026, 1, 2)


def test_wells_fargo_columns():
    items = lines("Statement period: January 1, 2026 - January 31, 2026", "Transaction history")
    y = 90
    items += [(40, y, "Date", False), (90, y, "Description", False), (330, y, "Deposits/Credits", False),
              (440, y, "Withdrawals/Debits", False), (540, y, "Ending daily balance", False)]
    for i, (d, desc, dep, wd, bal) in enumerate([
            ("1/3", "ACME CORP PAYROLL", "2,400.00", "", "3,000.00"),
            ("1/5", "EXAMPLE PROPERTY RENT", "", "1,500.00", ""),
            ("1/9", "VERIZON WIRELESS", "", "85.00", "1,415.00")]):
        yy = y + 16 * (i + 1)
        items += [(40, yy, d, False), (90, yy, desc, False)]
        if dep: items.append((390, yy, dep, True))
        if wd: items.append((500, yy, wd, True))
        if bal: items.append((590, yy, bal, True))
    items.append((40, 170, "1/31 Ending balance on 1/31 1,415.00", False))
    got = {r["name"]: r["amount"] for r in parse_pdf(make_pdf(items), "checking")}
    assert got == {"ACME CORP PAYROLL": -2400, "EXAMPLE PROPERTY RENT": 1500, "VERIZON WIRELESS": 85}


def test_unreadable_pdf():
    with pytest.raises(ImportFailed):
        parse_pdf(make_pdf([]), "checking")
    with pytest.raises(ImportFailed):
        parse_pdf(b"not a pdf", "checking")
