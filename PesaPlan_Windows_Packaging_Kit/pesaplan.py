#!/usr/bin/env python3
"""
PesaPlan - a beautiful PyQt5 budget tracker with M-Pesa support.

Features
  * Every transaction stores its exact date AND time
  * Paste M-Pesa SMS messages -> auto-parsed (amount, fee, time, balance, code),
    auto-categorised, de-duplicated by transaction code
  * Dashboard: stat cards, spending donut, daily cash-flow bars,
    "when you spend" (hour-of-day) chart, budget status, recent activity
  * Monthly budgets per category with progress meters
  * Filterable transaction table + CSV export

Install / run (PowerShell):
    pip install PyQt5
    python pesaplan.py
"""
import sys
import os
import re
import csv
import math
import random
import sqlite3
from calendar import monthrange
from contextlib import contextmanager
from datetime import datetime, date, timedelta

from PyQt5.QtCore import Qt, QRectF, QPointF, QDateTime, QDate, QTime, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen, QBrush, QFont, QLinearGradient, QPainterPath, QFontMetrics
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QFrame, QStackedWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QLineEdit, QComboBox, QDialog, QFormLayout, QDoubleSpinBox,
    QDateTimeEdit, QDateEdit, QMessageBox, QPlainTextEdit, QScrollArea, QFileDialog,
    QToolTip, QButtonGroup, QAbstractSpinBox,
)

APP_NAME = "PesaPlan"

# Keep the development database beside the source on Linux.
# Store installed Windows application data in the user's Local AppData.
if sys.platform == "win32":
    APP_DATA_DIR = os.path.join(
        os.environ.get(
            "LOCALAPPDATA",
            os.path.join(os.path.expanduser("~"), "AppData", "Local")
        ),
        "PesaPlan"
    )
else:
    APP_DATA_DIR = os.path.dirname(os.path.abspath(__file__))

os.makedirs(APP_DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(APP_DATA_DIR, "pesaplan.db")
FMT = "%Y-%m-%d %H:%M:%S"
# --------------------------------------------------------------------------- theme
BG = "#0E1117"
SIDEBAR = "#0A0D12"
CARD = "#161B22"
CARD2 = "#1D2430"
BORDER = "#252E3C"
TEXT = "#E6EDF3"
MUTED = "#8B98A9"
ACCENT = "#2ECC71"
INCOME = "#34D399"
EXPENSE = "#F87171"
WARN = "#FBBF24"
BLUE = "#60A5FA"
PURPLE = "#A78BFA"

PALETTE = ["#34D399", "#60A5FA", "#F472B6", "#FBBF24", "#A78BFA", "#F87171",
           "#2DD4BF", "#FB923C", "#818CF8", "#A3E635", "#22D3EE", "#E879F9",
           "#F9A8D4", "#94A3B8", "#FCD34D", "#7DD3FC", "#C4B5FD", "#86EFAC", "#FDBA74", "#5EEAD4",
           "#FDA4AF", "#BEF264"]

EXPENSE_CATS = ["Food & Groceries", "Transport", "Airtime & Data", "Bills & Utilities", "Rent",
                "Shopping", "Health", "Education", "Entertainment", "Savings & Investments",
                "Loans & Fuliza", "Cash Withdrawal", "Transaction Fees", "Other"]
INCOME_CATS = ["Salary", "Side Hustle", "Deals", "Gifts", "Received Money", "Refund",
               "Interest & Dividends", "Other Income"]
ALL_CATS = EXPENSE_CATS + INCOME_CATS
ACCOUNT_KINDS = ["Bank", "M-Pesa", "Cash", "Other"]
KIND_ICON = {"Bank": "🏦", "M-Pesa": "📲", "Cash": "💵", "Other": "👛"}
DEFAULT_ACCOUNTS = [("M-Pesa", "M-Pesa"), ("Cash", "Cash"), ("Bank Account 1", "Bank"),
                    ("Bank Account 2", "Bank"), ("Bank Account 3", "Bank"), ("Bank Account 4", "Bank")]


def cat_color(name):
    if name in ALL_CATS:
        return PALETTE[ALL_CATS.index(name) % len(PALETTE)]
    return PALETTE[sum(map(ord, name or "x")) % len(PALETTE)]


def rgba(hex_, a):
    c = QColor(hex_)
    return "rgba(%d,%d,%d,%s)" % (c.red(), c.green(), c.blue(), a)


STYLE = """
* { font-family: "Segoe UI", "Inter", "Helvetica Neue", sans-serif; color: %(text)s; font-size: 13px; }
QMainWindow, QWidget#root, QWidget#pagebody, QDialog { background: %(bg)s; }
QLabel { background: transparent; }
QToolTip { background: %(card2)s; color: %(text)s; border: 1px solid %(border)s; padding: 6px 8px; }

QFrame#sidebar { background: %(sidebar)s; border-right: 1px solid %(border)s; }
QLabel#brand { font-size: 20px; font-weight: 700; }
QLabel#brandSub { color: %(muted)s; font-size: 11px; }
QLabel#logo { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #34D399,stop:1 #059669);
              color: #04210F; border-radius: 13px; font-size: 22px; font-weight: 800; }
QPushButton#nav { text-align: left; padding: 12px 16px; border: none; border-radius: 12px;
                  color: %(muted)s; font-size: 14px; font-weight: 600; background: transparent; }
QPushButton#nav:hover { background: %(card2)s; color: %(text)s; }
QPushButton#nav:checked { background: rgba(46,204,113,0.14); color: %(accent)s; }
QFrame#balbox { background: %(card)s; border: 1px solid %(border)s; border-radius: 14px; }
QLabel#balLabel { color: %(muted)s; font-size: 10px; font-weight: 700; }
QLabel#balValue { color: %(accent)s; font-size: 18px; font-weight: 700; }

QLabel#h1 { font-size: 26px; font-weight: 700; }
QLabel#h1sub { color: %(muted)s; font-size: 13px; }
QLabel#clockDate { color: %(muted)s; font-size: 12px; }
QLabel#clockTime { font-size: 20px; font-weight: 700; }
QLabel#sectionTitle { font-size: 15px; font-weight: 700; }
QLabel#cardTitle { color: %(muted)s; font-size: 12px; font-weight: 600; }
QLabel#cardValue { font-size: 21px; font-weight: 700; }
QLabel#cardSub { color: %(muted)s; font-size: 11px; }
QLabel#muted { color: %(muted)s; }
QLabel#monthTitle { font-size: 16px; font-weight: 700; }

QFrame#card { background: %(card)s; border: 1px solid %(border)s; border-radius: 16px; }
QFrame#txrow { border: none; border-bottom: 1px solid %(border)s; background: transparent; }

QPushButton#primary { background: %(accent)s; color: #06210F; border: none; border-radius: 12px;
                      padding: 10px 20px; font-weight: 700; }
QPushButton#primary:hover { background: #4ADE80; }
QPushButton#primary:disabled { background: %(border)s; color: %(muted)s; }
QPushButton#ghost { background: transparent; border: 1px solid %(border)s; border-radius: 10px;
                    padding: 8px 14px; font-weight: 600; }
QPushButton#ghost:hover { background: %(card2)s; }
QPushButton#danger { background: rgba(248,113,113,0.12); color: %(expense)s; border: 1px solid rgba(248,113,113,0.35);
                     border-radius: 10px; padding: 8px 14px; font-weight: 600; }
QPushButton#danger:hover { background: rgba(248,113,113,0.22); }
QPushButton#icon { background: %(card2)s; border: 1px solid %(border)s; border-radius: 10px;
                   padding: 6px 12px; font-size: 16px; font-weight: 700; }
QPushButton#icon:hover { border-color: %(accent)s; }
QPushButton#icon:disabled { color: %(border)s; }
QPushButton#tiny { background: transparent; border: none; color: %(muted)s; padding: 4px 8px; font-weight: 600; }
QPushButton#tiny:hover { color: %(text)s; }
QPushButton#tinyDel:hover { color: %(expense)s; }

QPushButton#segExp, QPushButton#segInc { background: %(card2)s; border: 1px solid %(border)s; padding: 10px;
                                          font-weight: 700; color: %(muted)s; }
QPushButton#segExp { border-top-left-radius: 10px; border-bottom-left-radius: 10px; }
QPushButton#segInc { border-top-right-radius: 10px; border-bottom-right-radius: 10px; }
QPushButton#segExp:checked { background: rgba(248,113,113,0.18); color: %(expense)s; border-color: %(expense)s; }
QPushButton#segInc:checked { background: rgba(52,211,153,0.18); color: %(income)s; border-color: %(income)s; }

QLineEdit, QComboBox, QDoubleSpinBox, QDateTimeEdit, QDateEdit, QPlainTextEdit {
    background: %(card2)s; border: 1px solid %(border)s; border-radius: 10px; padding: 8px 10px;
    selection-background-color: %(accent)s; selection-color: #06210F; }
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QDateTimeEdit:focus, QDateEdit:focus, QPlainTextEdit:focus {
    border: 1px solid %(accent)s; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView { background: %(card2)s; border: 1px solid %(border)s; outline: 0;
                              selection-background-color: rgba(46,204,113,0.25); }
QDateTimeEdit::drop-down, QDateEdit::drop-down { border: none; width: 26px; }
QCalendarWidget QAbstractItemView { background: %(card2)s; selection-background-color: %(accent)s; selection-color: #06210F; }
QCalendarWidget QWidget#qt_calendar_navigationbar { background: %(card)s; }

QTableWidget { background: transparent; border: none; gridline-color: transparent;
               alternate-background-color: rgba(255,255,255,0.02);
               selection-background-color: rgba(46,204,113,0.16); selection-color: %(text)s; outline: 0; }
QTableWidget::item { padding: 6px 10px; border-bottom: 1px solid %(border)s; }
QHeaderView { background: transparent; border: none; }
QTableCornerButton::section { background: transparent; border: none; }
QTableWidget::indicator { width: 16px; height: 16px; border-radius: 5px; border: 1px solid #3a475c; background: %(card2)s; }
QTableWidget::indicator:checked { background: %(accent)s; border: 1px solid %(accent)s; }
QHeaderView::section { background: transparent; color: %(muted)s; border: none; border-bottom: 1px solid %(border)s;
                       padding: 10px; font-weight: 700; font-size: 11px; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: %(border)s; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #34405a; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: %(border)s; border-radius: 4px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
""" % dict(bg=BG, sidebar=SIDEBAR, card=CARD, card2=CARD2, border=BORDER, text=TEXT, muted=MUTED,
           accent=ACCENT, income=INCOME, expense=EXPENSE)


# --------------------------------------------------------------------------- helpers
def kes(v, signed=False):
    s = "KSh {:,.2f}".format(abs(v))
    if signed:
        return ("+" if v >= 0 else "-") + s
    return ("-" + s) if v < 0 else s


def short(v):
    v = float(v)
    if v >= 1_000_000:
        return ("%.1fM" % (v / 1_000_000)).replace(".0M", "M")
    if v >= 1000:
        return ("%.1fK" % (v / 1000)).replace(".0K", "K")
    return "%d" % round(v)


def nice_ceiling(v):
    if v <= 0:
        return 1
    exp = 10 ** int(math.floor(math.log10(v)))
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if v <= m * exp:
            return m * exp
    return 10 * exp


def parse_ts(s):
    return datetime.strptime(s, FMT)


def fmt_time(dt):
    return "%d:%s %s" % (dt.hour % 12 or 12, dt.strftime("%M"), dt.strftime("%p"))


def fmt_dt(dt):
    return "%s %d %s · %s" % (dt.strftime("%a"), dt.day, dt.strftime("%b"), fmt_time(dt))


def month_bounds(y, m):
    start = datetime(y, m, 1)
    end = datetime(y + (m == 12), (m % 12) + 1, 1)
    return start.strftime(FMT), end.strftime(FMT)


def clear_layout(lay):
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif it.layout() is not None:
            clear_layout(it.layout())


def make_card(title, body, sub=None):
    f = QFrame()
    f.setObjectName("card")
    v = QVBoxLayout(f)
    v.setContentsMargins(20, 16, 20, 16)
    v.setSpacing(6)
    t = QLabel(title)
    t.setObjectName("sectionTitle")
    v.addWidget(t)
    if sub:
        s = QLabel(sub)
        s.setObjectName("cardSub")
        v.addWidget(s)
    v.addWidget(body, 1)
    return f


# --------------------------------------------------------------------------- categorisation
KEYWORDS = [
    ("Airtime & Data", ["airtime", "bundles", "data bundle"]),
    ("Bills & Utilities", ["kplc", "kenya power", "nairobi water", "water", "zuku", "dstv", "gotv",
                           "startimes", "wifi", "wi-fi", "internet", "faiba", "jtl", "safaricom home"]),
    ("Transport", ["uber", "bolt", "little cab", "matatu", "fuel", "petrol", "shell", "totalenergies",
                   "total energies", "rubis", "kenol", "oilibya", "parking", "sgr", "fare", "boda", "nduthi"]),
    ("Food & Groceries", ["naivas", "quickmart", "carrefour", "chandarana", "supermarket", "butchery",
                          "restaurant", "kfc", "java", "cafe", "coffee", "hotel", "mboga", "food", "pizza",
                          "kitchen", "eatery", "bakery", "glovo", "chicken", "grocer", "market", "kiosk",
                          "duka", "minimart", "mini mart", "supplies"]),
    ("Health", ["pharmacy", "chemist", "hospital", "clinic", "nhif", "social health", "medical", "dental"]),
    ("Education", ["school", "college", "university", "tuition", "helb", "academy", "books"]),
    ("Entertainment", ["showmax", "netflix", "spotify", "sportpesa", "betika", "odibets", "betway",
                       "cinema", "movie", "lounge", "club"]),
    ("Savings & Investments", ["m-shwari", "mshwari", "ziidi", "sacco", "mmf", "money market", "chama",
                               "lock savings", "invest", "cytonn", "etf"]),
    ("Loans & Fuliza", ["fuliza", "tala", "okolea", "hustler", "loan", "timiza", "zenka"]),
    ("Rent", ["rent", "landlord", "caretaker"]),
    ("Shopping", ["jumia", "kilimall", "mall", "clothes", "fashion", "shoes", "boutique", "electronics",
                  "hardware", "furniture", "amazon", "shein", "aliexpress", "cosmetics", "beauty"]),
]


def guess_category(text, typ="expense"):
    low = (text or "").lower()
    if typ == "income":
        if any(k in low for k in ("salary", "payroll", "wages")):
            return "Salary"
        if any(k in low for k in ("refund", "reversal")):
            return "Refund"
        return "Received Money"
    for cat, words in KEYWORDS:
        if any(w in low for w in words):
            return cat
    return "Other"


# --------------------------------------------------------------------------- M-Pesa SMS parser
class MpesaParser:
    CODE_SPLIT = re.compile(r"(?=(?<![A-Za-z0-9])[A-Z0-9]{10}\s+Confirmed)")
    AMT = re.compile(r"(?:Ksh|KES)\.?\s?([\d,]+(?:\.\d{1,2})?)", re.I)
    BAL = re.compile(r"balance\s+(?:is|was)?\s*:?\s*(?:Ksh|KES)\.?\s?([\d,]+(?:\.\d{1,2})?)", re.I)
    FEE = re.compile(r"Transaction cost,?\s*:?\s*(?:Ksh|KES)\.?\s?([\d,]+(?:\.\d{1,2})?)", re.I)
    DATE = re.compile(r"(\d{1,2}/\d{1,2}/\d{2,4})\s+at\s+(\d{1,2}:\d{2}\s*[AaPp][Mm])")
    ON = r"\s+on\s+\d{1,2}/\d{1,2}/\d{2,4}"

    @classmethod
    def split_messages(cls, text):
        out = []
        for block in re.split(r"\n\s*\n", text.strip()):
            parts = [p for p in cls.CODE_SPLIT.split(block) if p.strip()]
            lines = [l for l in block.splitlines() if l.strip()]
            if len(parts) <= 1 and len(lines) > 1 and all("ksh" in l.lower() for l in lines):
                parts = lines
            for p in parts:
                p = " ".join(p.split())
                if p:
                    out.append(p)
        return out

    @staticmethod
    def _num(s):
        return float(s.replace(",", ""))

    @staticmethod
    def _clean_name(name):
        name = re.sub(r"\s*(\+?254|0)\d{8,9}\s*$", "", name.strip())
        return name.strip(" .")

    @classmethod
    def parse(cls, msg):
        low = msg.lower()
        am = cls.AMT.search(msg)
        if not am or "confirmed" not in low:
            return None
        if "fuliza" in low and "amount is" in low:
            return None  # Fuliza limit notices are not a spend by themselves
        amount = cls._num(am.group(1))

        cm = re.match(r"\s*([A-Z0-9]{10})\s+Confirmed", msg)
        code = cm.group(1) if cm else None

        kind, who = None, ""
        if "you have received" in low:
            kind = "received"
            m = re.search(r"from\s+(.+?)" + cls.ON, msg, re.I)
            who = cls._clean_name(m.group(1)) if m else "M-Pesa"
        elif "you bought" in low and "airtime" in low:
            kind, who = "airtime", "Safaricom Airtime"
        elif re.search(r"withdraw", low):
            kind = "withdrawn"
            m = re.search(r"withdrawn from\s+(.+?)" + cls.ON, msg, re.I)
            who = "Agent withdrawal" + (" - " + cls._clean_name(m.group(1)) if m else "")
        elif " paid to " in low:
            kind = "paid"
            m = re.search(r"paid to\s+(.+?)\.?" + cls.ON, msg, re.I)
            who = cls._clean_name(m.group(1)) if m else "Payment"
        elif " sent to " in low:
            kind = "sent"
            m = re.search(r"sent to\s+(.+?)" + cls.ON, msg, re.I)
            who = cls._clean_name(m.group(1)) if m else "Transfer"
        elif "fuliza" in low and ("pay" in low or "repa" in low):
            kind, who = "fuliza", "Fuliza repayment"
        elif "reversed" in low or "reversal" in low:
            kind, who = "reversal", "M-Pesa reversal"
        elif "give ksh" in low or "deposit" in low:
            kind, who = "deposit", "Cash deposit"
            m = re.search(r"cash to\s+(.+?)\s+New M-PESA", msg, re.I)
            if m:
                who += " - " + cls._clean_name(m.group(1))
        else:
            return None

        dm = cls.DATE.search(msg)
        if dm:
            d, t = dm.group(1), dm.group(2).upper().replace(" ", "")
            dfmt = "%d/%m/%y" if len(d.split("/")[2]) == 2 else "%d/%m/%Y"
            try:
                ts = datetime.strptime(d + " " + t, dfmt + " %I:%M%p")
            except ValueError:
                ts = datetime.now().replace(second=0, microsecond=0)
        else:
            ts = datetime.now().replace(second=0, microsecond=0)

        fm, bm = cls.FEE.search(msg), cls.BAL.search(msg)
        fee = cls._num(fm.group(1)) if fm else 0.0
        balance = cls._num(bm.group(1)) if bm else None
        typ = "income" if kind in ("received", "deposit", "reversal") else "expense"

        if kind == "airtime":
            category = "Airtime & Data"
        elif kind == "fuliza":
            category = "Loans & Fuliza"
        elif kind == "reversal":
            category = "Refund"
        elif kind == "withdrawn":
            category = "Cash Withdrawal"
        else:
            category = guess_category(who, typ)

        key = code or "SMS%s%d" % (ts.strftime("%Y%m%d%H%M"), int(amount * 100))
        return dict(key=key, code=code, ts=ts, type=typ, kind=kind, amount=amount, fee=fee,
                    description=who, category=category, balance=balance, raw=msg)


SAMPLE_SMS = """UCB7K2LM4P Confirmed. Ksh1,250.00 paid to NAIVAS SUPERMARKET. on 8/11/26 at 6:42 PM.New M-PESA balance is Ksh4,310.55. Transaction cost, Ksh0.00.

UCB1X9QZ3A Confirmed. Ksh500.00 sent to JOHN KAMAU 0712345678 on 9/11/26 at 8:05 AM. New M-PESA balance is Ksh3,803.55. Transaction cost, Ksh7.00.

UCB5T8WN2D Confirmed. You have received Ksh6,500.00 from MERCY ACHIENG 0722111222 on 9/11/26 at 10:17 AM. New M-PESA balance is Ksh10,303.55.

UCB9R4HV6E Confirmed. Ksh2,000.00 withdrawn from 654321 - Mwangi Agency on 9/11/26 at 12:30 PM. New M-PESA balance is Ksh8,275.55. Transaction cost, Ksh28.00.

UCB3P7DS8F Confirmed. You bought Ksh100.00 of airtime on 9/11/26 at 1:55 PM. New M-PESA balance is Ksh8,175.55. Transaction cost, Ksh0.00.

UCB6M2JK1G Confirmed. Ksh1,500.00 sent to KPLC PREPAID for account 14400123 on 9/11/26 at 3:20 PM. New M-PESA balance is Ksh6,661.55. Transaction cost, Ksh13.00."""


# --------------------------------------------------------------------------- database
class Database:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS transactions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('income','expense')),
                amount REAL NOT NULL CHECK(amount >= 0),
                category TEXT NOT NULL,
                description TEXT DEFAULT '',
                source TEXT DEFAULT 'Cash',
                mpesa_code TEXT UNIQUE,
                balance_after REAL
            );
            CREATE INDEX IF NOT EXISTS idx_tx_ts ON transactions(ts);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS accounts(
                name TEXT PRIMARY KEY,
                kind TEXT NOT NULL DEFAULT 'Bank',
                opening_balance REAL NOT NULL DEFAULT 0,
                opened_on TEXT NOT NULL,
                closed_on TEXT,
                sort INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS transfers(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                from_acc TEXT NOT NULL,
                to_acc TEXT NOT NULL,
                amount REAL NOT NULL CHECK(amount > 0),
                note TEXT DEFAULT '',
                code TEXT UNIQUE,
                balance_after REAL
            );
            CREATE INDEX IF NOT EXISTS idx_tr_ts ON transfers(ts);
            CREATE TABLE IF NOT EXISTS budgets(
                category TEXT PRIMARY KEY,
                monthly_limit REAL NOT NULL
            );
        """)
        self.conn.commit()
        self._bootstrap_accounts()

    def _bootstrap_accounts(self):
        """Fresh install: create the default accounts at zero. Old databases: make an account for every
        account name already used by a transaction so nothing is orphaned."""
        today = datetime.now().strftime("%Y-%m-%d 00:00:00")
        if self.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0:
            for i, (n, k) in enumerate(DEFAULT_ACCOUNTS):
                self.conn.execute("INSERT INTO accounts(name,kind,opening_balance,opened_on,sort) VALUES(?,?,0,?,?)",
                                  (n, k, today, i))
        for r in self.conn.execute("SELECT DISTINCT source FROM transactions").fetchall():
            n = r[0]
            if n and not self.conn.execute("SELECT 1 FROM accounts WHERE name=?", (n,)).fetchone():
                kind = n if n in ACCOUNT_KINDS else "Bank" if n == "Card" else "Other"
                first = self.conn.execute("SELECT MIN(ts) FROM transactions WHERE source=?", (n,)).fetchone()[0]
                self.conn.execute("INSERT INTO accounts(name,kind,opening_balance,opened_on,sort) VALUES(?,?,0,?,99)",
                                  (n, kind, first or today))
        self.conn.execute("DELETE FROM settings WHERE key='starting_balance'")
        self.conn.commit()

    # -- atomic writes
    _in_atomic = False

    def _commit(self):
        if not self._in_atomic:
            self.conn.commit()

    @contextmanager
    def atomic(self):
        """One all-or-nothing transaction; nested use joins the outer one."""
        if self._in_atomic:
            yield
            return
        self._in_atomic = True
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            self._in_atomic = False

    # -- accounts
    def accounts(self, open_only=False):
        sql = "SELECT * FROM accounts" + (" WHERE closed_on IS NULL" if open_only else "") + " ORDER BY sort, name"
        return [dict(r) for r in self.conn.execute(sql)]

    def account(self, name):
        r = self.conn.execute("SELECT * FROM accounts WHERE name=?", (name,)).fetchone()
        return dict(r) if r else None

    def account_names(self, open_only=True):
        return [a["name"] for a in self.accounts(open_only)]

    def account_kind(self, name):
        a = self.account(name)
        return a["kind"] if a else "Other"

    def first_account_of_kind(self, kind):
        for a in self.accounts(open_only=True):
            if a["kind"] == kind:
                return a["name"]
        return None

    def add_account(self, name, kind, opening, opened_on):
        n = self.conn.execute("SELECT COALESCE(MAX(sort),0)+1 FROM accounts").fetchone()[0]
        self.conn.execute("INSERT INTO accounts(name,kind,opening_balance,opened_on,sort) VALUES(?,?,?,?,?)",
                          (name, kind, opening, opened_on, n))
        self.conn.commit()

    def update_account(self, old, name, kind, opening, opened_on):
        self.conn.execute("UPDATE accounts SET name=?,kind=?,opening_balance=?,opened_on=? WHERE name=?",
                          (name, kind, opening, opened_on, old))
        if name != old:
            self.conn.execute("UPDATE transactions SET source=? WHERE source=?", (name, old))
            self.conn.execute("UPDATE transfers SET from_acc=? WHERE from_acc=?", (name, old))
            self.conn.execute("UPDATE transfers SET to_acc=? WHERE to_acc=?", (name, old))
        self.conn.commit()

    def close_account(self, name, ts):
        self.conn.execute("UPDATE accounts SET closed_on=? WHERE name=?", (ts, name))
        self.conn.commit()

    def reopen_account(self, name):
        self.conn.execute("UPDATE accounts SET closed_on=NULL WHERE name=?", (name,))
        self.conn.commit()

    def account_in_use(self, name):
        a = self.conn.execute("SELECT COUNT(*) FROM transactions WHERE source=?", (name,)).fetchone()[0]
        b = self.conn.execute("SELECT COUNT(*) FROM transfers WHERE from_acc=? OR to_acc=?", (name, name)).fetchone()[0]
        return a + b

    def delete_account(self, name):
        self.conn.execute("DELETE FROM accounts WHERE name=?", (name,))
        self.conn.commit()

    def _flows(self, name, lo, hi):
        """(income, expense, transfers_in, transfers_out) for an account in [lo, hi)."""
        t = self.conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN type='income' THEN amount END),0),"
            "       COALESCE(SUM(CASE WHEN type='expense' THEN amount END),0) "
            "FROM transactions WHERE source=? AND ts>=? AND ts<?", (name, lo, hi)).fetchone()
        i = self.conn.execute("SELECT COALESCE(SUM(amount),0) FROM transfers WHERE to_acc=? AND ts>=? AND ts<?",
                              (name, lo, hi)).fetchone()[0]
        o = self.conn.execute("SELECT COALESCE(SUM(amount),0) FROM transfers WHERE from_acc=? AND ts>=? AND ts<?",
                              (name, lo, hi)).fetchone()[0]
        return t[0], t[1], i, o

    def balance_at(self, acc, ts):
        """Balance of one account at instant ts: opening balance + everything since it was opened."""
        inc, exp, tin, tout = self._flows(acc["name"], acc["opened_on"], ts) if ts > acc["opened_on"] else (0, 0, 0, 0)
        return acc["opening_balance"] + inc - exp + tin - tout

    def month_summary(self, start, end):
        """Per-account opening / movements / closing for [start, end). Accounts that did not exist yet
        or were closed before the period are left out."""
        out = []
        for a in self.accounts():
            if a["opened_on"] >= end:
                continue
            if a["closed_on"] and a["closed_on"] < start:
                continue
            lo = max(start, a["opened_on"])
            inc, exp, tin, tout = self._flows(a["name"], lo, end)
            opening = self.balance_at(a, start) if start > a["opened_on"] else a["opening_balance"]
            out.append(dict(a, opening=opening, income=inc, expense=exp, tin=tin, tout=tout,
                            closing=opening + inc - exp + tin - tout))
        return out

    def total_now(self):
        now = (datetime.now() + timedelta(seconds=1)).strftime(FMT)
        return sum(self.balance_at(a, now) for a in self.accounts())

    # -- transfers
    def add_transfer(self, ts, frm, to, amount, note="", code=None, balance=None, fee=0.0):
        with self.atomic():
            cur = self.conn.execute("INSERT INTO transfers(ts,from_acc,to_acc,amount,note,code,balance_after) "
                                    "VALUES(?,?,?,?,?,?,?)", (ts, frm, to, amount, note, code or None, balance))
            if fee and fee > 0:
                self.add(ts, "expense", fee, "Transaction Fees", ("Fee - " + (note or "transfer"))[:80], frm,
                         (code + "-FEE") if code else None)
        return cur.lastrowid

    def transfers(self, start=None, end=None, limit=None):
        sql, args = "SELECT * FROM transfers WHERE 1=1", []
        if start:
            sql += " AND ts>=?"; args.append(start)
        if end:
            sql += " AND ts<?"; args.append(end)
        sql += " ORDER BY ts DESC, id DESC"
        if limit:
            sql += " LIMIT %d" % int(limit)
        return self.conn.execute(sql, args).fetchall()

    def delete_transfer(self, tid):
        r = self.conn.execute("SELECT code FROM transfers WHERE id=?", (tid,)).fetchone()
        self.conn.execute("DELETE FROM transfers WHERE id=?", (tid,))
        if r and r[0]:
            self.conn.execute("DELETE FROM transactions WHERE mpesa_code=?", (r[0] + "-FEE",))
        self.conn.commit()

    def start_fresh(self):
        """Wipe every transaction, transfer and budget and put all accounts back to zero."""
        for t in ("transactions", "transfers", "budgets", "accounts"):
            self.conn.execute("DELETE FROM " + t)
        self.conn.commit()
        self._bootstrap_accounts()

    def import_parsed(self, entries, acc):
        """entries: [(parsed, category)]. Each message (plus its fee row) is its own all-or-nothing unit.
        Returns dict(imported, duplicates, early)."""
        cash = self.first_account_of_kind("Cash")
        opened = (self.account(acc) or {}).get("opened_on", "")
        res = dict(imported=0, duplicates=0, early=0)
        for p, cat in entries:
            if self.code_exists(p["key"]):
                res["duplicates"] += 1
                continue
            ts = p["ts"].strftime(FMT)
            try:
                with self.atomic():
                    if p["kind"] in ("withdrawn", "deposit") and cash and cash != acc:
                        if p["kind"] == "withdrawn":
                            self.add_transfer(ts, acc, cash, p["amount"], p["description"], p["key"], p["balance"], p["fee"])
                        else:
                            self.add_transfer(ts, cash, acc, p["amount"], p["description"], p["key"], p["balance"])
                    else:
                        if p["kind"] in ("withdrawn", "deposit"):
                            cat = "Cash Withdrawal" if p["kind"] == "withdrawn" else "Received Money"
                        self.add(ts, p["type"], p["amount"], cat, p["description"], acc, p["key"], p["fee"], p["balance"])
            except sqlite3.IntegrityError:
                res["duplicates"] += 1
                continue
            res["imported"] += 1
            if ts < opened:
                res["early"] += 1
        return res

    def import_mpesa_text(self, text, acc):
        """Parse + dedupe + import raw SMS text. Same result shape the phone API returns."""
        entries, seen, bad, dups = [], set(), [], 0
        for m in MpesaParser.split_messages(text or ""):
            p = MpesaParser.parse(m)
            if not p:
                bad.append(m[:160])
                continue
            if p["key"] in seen:
                dups += 1
                continue
            seen.add(p["key"])
            entries.append((p, p["category"]))
        res = self.import_parsed(entries, acc)
        res["duplicates"] += dups
        res["unrecognised"] = bad
        return res

    # -- writes
    def add(self, ts, typ, amount, category, description="", source="Cash", code=None, fee=0.0, balance=None):
        code = code or None
        with self.atomic():
            cur = self.conn.execute(
                "INSERT INTO transactions(ts,type,amount,category,description,source,mpesa_code,balance_after) "
                "VALUES (?,?,?,?,?,?,?,?)", (ts, typ, amount, category, description, source, code, balance))
            if fee and fee > 0 and typ == "expense":
                self.conn.execute(
                    "INSERT INTO transactions(ts,type,amount,category,description,source,mpesa_code,balance_after) "
                    "VALUES (?,?,?,?,?,?,?,NULL)",
                    (ts, "expense", fee, "Transaction Fees", ("Fee - " + description)[:80], source,
                     (code + "-FEE") if code else None))
        return cur.lastrowid

    def update(self, tid, ts, typ, amount, category, description, source, code):
        self.conn.execute(
            "UPDATE transactions SET ts=?,type=?,amount=?,category=?,description=?,source=?,mpesa_code=? WHERE id=?",
            (ts, typ, amount, category, description, source, code or None, tid))
        self.conn.commit()

    def delete(self, tid):
        row = self.get(tid)
        self.conn.execute("DELETE FROM transactions WHERE id=?", (tid,))
        if row and row["mpesa_code"]:
            self.conn.execute("DELETE FROM transactions WHERE mpesa_code=?", (row["mpesa_code"] + "-FEE",))
        self.conn.commit()

    def set_budget(self, cat, limit):
        self.conn.execute("INSERT INTO budgets(category,monthly_limit) VALUES(?,?) "
                          "ON CONFLICT(category) DO UPDATE SET monthly_limit=excluded.monthly_limit", (cat, limit))
        self.conn.commit()

    def delete_budget(self, cat):
        self.conn.execute("DELETE FROM budgets WHERE category=?", (cat,))
        self.conn.commit()

    # -- reads
    def get(self, tid):
        return self.conn.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()

    def code_exists(self, code):
        return (self.conn.execute("SELECT 1 FROM transactions WHERE mpesa_code=?", (code,)).fetchone() is not None
                or self.conn.execute("SELECT 1 FROM transfers WHERE code=?", (code,)).fetchone() is not None)

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    def query(self, start=None, end=None, typ=None, source=None, category=None, text=None, limit=None):
        sql, args = "SELECT * FROM transactions WHERE 1=1", []
        if start:
            sql += " AND ts>=?"; args.append(start)
        if end:
            sql += " AND ts<?"; args.append(end)
        if typ:
            sql += " AND type=?"; args.append(typ)
        if source:
            sql += " AND source=?"; args.append(source)
        if category:
            sql += " AND category=?"; args.append(category)
        if text:
            t = "%" + text + "%"
            sql += " AND (description LIKE ? OR category LIKE ? OR IFNULL(mpesa_code,'') LIKE ?)"
            args += [t, t, t]
        sql += " ORDER BY ts DESC, id DESC"
        if limit:
            sql += " LIMIT %d" % int(limit)
        return self.conn.execute(sql, args).fetchall()

    def totals(self, start, end):
        r = self.conn.execute("""
            SELECT COALESCE(SUM(CASE WHEN type='income' THEN amount END),0) inc,
                   COALESCE(SUM(CASE WHEN type='expense' THEN amount END),0) exp,
                   COALESCE(SUM(CASE WHEN type='expense' AND source IN (SELECT name FROM accounts WHERE kind='M-Pesa') THEN amount END),0) mexp,
                   COALESCE(SUM(CASE WHEN category='Transaction Fees' THEN amount END),0) fees,
                   COUNT(CASE WHEN type='income' THEN 1 END) n_inc,
                   COUNT(CASE WHEN type='expense' THEN 1 END) n_exp
            FROM transactions WHERE ts>=? AND ts<?""", (start, end)).fetchone()
        return dict(r)

    def by_category(self, typ, start, end):
        return self.conn.execute(
            "SELECT category, SUM(amount) t FROM transactions WHERE type=? AND ts>=? AND ts<? "
            "GROUP BY category ORDER BY t DESC", (typ, start, end)).fetchall()

    def daily(self, start, end):
        rows = self.conn.execute(
            "SELECT substr(ts,1,10) d, type, SUM(amount) t FROM transactions WHERE ts>=? AND ts<? "
            "GROUP BY d, type", (start, end)).fetchall()
        return {(r["d"], r["type"]): r["t"] for r in rows}

    def by_hour(self, start, end):
        out = [0.0] * 24
        for r in self.conn.execute(
                "SELECT CAST(substr(ts,12,2) AS INTEGER) h, SUM(amount) t FROM transactions "
                "WHERE type='expense' AND ts>=? AND ts<? GROUP BY h", (start, end)):
            out[r["h"]] = r["t"]
        return out

    def budgets(self):
        return {r["category"]: r["monthly_limit"] for r in self.conn.execute("SELECT * FROM budgets")}

    def get_setting(self, key, default=None):
        r = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def set_setting(self, key, value):
        self.conn.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        self.conn.commit()

    def last_mpesa_balance(self):
        r = self.conn.execute(
            "SELECT balance_after FROM (SELECT ts, balance_after FROM transactions WHERE balance_after IS NOT NULL "
            "UNION ALL SELECT ts, balance_after FROM transfers WHERE balance_after IS NOT NULL) "
            "ORDER BY ts DESC LIMIT 1").fetchone()
        return r[0] if r else None


def seed_demo(db):
    db.conn.execute("UPDATE accounts SET opened_on=?", ((datetime.now() - timedelta(days=75)).strftime("%Y-%m-%d 00:00:00"),))
    db.conn.commit()
    rnd = random.Random(11)
    now = datetime.now()
    alnum = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789"

    def code():
        return "S" + "".join(rnd.choices(alnum, k=9))

    pool = [
        ("Naivas Supermarket", "Food & Groceries", 300, 2800, "M-Pesa"),
        ("Mama Mboga", "Food & Groceries", 80, 450, "M-Pesa"),
        ("Java House", "Food & Groceries", 450, 1400, "Card"),
        ("Uber", "Transport", 250, 900, "M-Pesa"),
        ("Matatu fare", "Transport", 60, 200, "Cash"),
        ("Shell Fuel", "Transport", 1500, 4000, "M-Pesa"),
        ("Safaricom Airtime", "Airtime & Data", 50, 500, "M-Pesa"),
        ("KPLC Prepaid", "Bills & Utilities", 500, 2500, "M-Pesa"),
        ("Zuku Wi-Fi", "Bills & Utilities", 2000, 3000, "M-Pesa"),
        ("Netflix", "Entertainment", 800, 1100, "Card"),
        ("Jumia", "Shopping", 800, 5000, "M-Pesa"),
        ("Pharmacy", "Health", 200, 1800, "M-Pesa"),
    ]
    weights = [6, 5, 2, 4, 5, 1, 4, 2, 1, 1, 2, 1]
    hours = [7, 8, 9, 12, 13, 14, 17, 18, 19, 20, 21, 22]
    items = []
    for d in range(60, -1, -1):
        day = (now - timedelta(days=d)).date()
        for _ in range(rnd.choices([0, 1, 2, 3], [2, 5, 4, 2])[0]):
            desc, cat, lo, hi, src = rnd.choices(pool, weights)[0]
            ts = datetime(day.year, day.month, day.day, rnd.choice(hours), rnd.randint(0, 59), rnd.randint(0, 59))
            if ts > now:
                continue
            amt = max(10, round(rnd.uniform(lo, hi) / 10) * 10)
            fee = rnd.choice([0, 5, 7, 13, 23]) if src == "M-Pesa" and "Airtime" not in desc else 0
            items.append((ts, "expense", amt, cat, desc, src, fee))
    for i in range(3):
        m, y = now.month - i, now.year
        if m <= 0:
            m += 12; y -= 1
        ts = datetime(y, m, 1, 9, 15)
        if ts <= now:
            items.append((ts, "income", 78000, "Salary", "Salary - Acme Ltd", "Bank", 0))
    names = ["Jane Wanjiku", "Brian Otieno", "Mercy Achieng", "Kevin Mwangi"]
    for _ in range(9):
        ts = (now - timedelta(days=rnd.randint(0, 58))).replace(hour=rnd.choice([9, 10, 11, 15, 16]),
                                                               minute=rnd.randint(0, 59), second=0, microsecond=0)
        if ts <= now:
            items.append((ts, "income", round(rnd.uniform(1500, 9000) / 50) * 50,
                          rnd.choice(["Side Hustle", "Deals", "Gifts", "Received Money"]), "Received from " + rnd.choice(names),
                          "M-Pesa", 0))
    items.sort(key=lambda x: x[0])
    last_mp = max((i for i, it in enumerate(items) if it[5] == "M-Pesa"), default=None)
    for i, (ts, typ, amt, cat, desc, src, fee) in enumerate(items):
        db.add(ts.strftime(FMT), typ, amt, cat, desc, {"Bank": "Bank Account 1", "Card": "Bank Account 1"}.get(src, src),
               code() if src == "M-Pesa" else None, fee,
               4260.35 if i == last_mp else None)
    for cat, lim in [("Food & Groceries", 15000), ("Transport", 7000), ("Airtime & Data", 2500),
                     ("Bills & Utilities", 8000), ("Entertainment", 3000), ("Shopping", 6000)]:
        db.set_budget(cat, lim)


# --------------------------------------------------------------------------- custom charts
class DonutChart(QWidget):
    def __init__(self):
        super().__init__()
        self.data, self.hover, self.center_label = [], -1, "Spent"
        self.setMinimumHeight(250)
        self.setMouseTracking(True)

    def set_data(self, items, center_label="Spent"):
        self.data, self.center_label, self.hover = items, center_label, -1
        self.update()

    def _rect(self):
        size = max(60, min(self.height() - 16, self.width() * 0.32))
        return QRectF(8, (self.height() - size) / 2, size, size)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self._rect()
        total = sum(v for _, v, _ in self.data)
        if total <= 0:
            p.setPen(QPen(QColor(BORDER), rect.width() * 0.16))
            p.drawEllipse(rect.adjusted(rect.width() * 0.08, rect.width() * 0.08,
                                        -rect.width() * 0.08, -rect.width() * 0.08))
            p.setPen(QColor(MUTED))
            p.drawText(rect, Qt.AlignCenter, "No spending\nthis month")
            return
        start = 90 * 16
        for i, (name, v, color) in enumerate(self.data):
            span = -int(round(v / total * 360 * 16))
            r = rect.adjusted(-4, -4, 4, 4) if i == self.hover else rect
            p.setBrush(QColor(color))
            p.setPen(QPen(QColor(CARD), 2))
            p.drawPie(r, start, span)
            start += span
        t = rect.width() * 0.2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(CARD))
        p.drawEllipse(rect.adjusted(t, t, -t, -t))
        f = QFont(self.font()); f.setPointSize(9)
        p.setFont(f); p.setPen(QColor(MUTED))
        p.drawText(QRectF(rect.left(), rect.center().y() - 26, rect.width(), 20), Qt.AlignCenter, self.center_label)
        f.setPointSize(15); f.setBold(True)
        p.setFont(f); p.setPen(QColor(TEXT))
        p.drawText(QRectF(rect.left(), rect.center().y() - 8, rect.width(), 28), Qt.AlignCenter, short(total))
        # legend
        f.setPointSize(9); f.setBold(False)
        p.setFont(f)
        fm = QFontMetrics(f)
        x0, y = rect.right() + 26, max(6, (self.height() - len(self.data) * 28) / 2)
        for i, (name, v, color) in enumerate(self.data):
            p.setPen(Qt.NoPen); p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(x0, y + 8, 10, 10), 3, 3)
            amt = short(v)
            pct = "%d%%" % round(v / total * 100)
            right = self.width() - 8
            p.setPen(QColor(TEXT if i == self.hover else MUTED))
            p.drawText(QRectF(right - 84, y, 84, 26), Qt.AlignRight | Qt.AlignVCenter, "%s · %s" % (amt, pct))
            avail = max(30, right - 88 - (x0 + 18))
            p.setPen(QColor(TEXT))
            p.drawText(QRectF(x0 + 18, y, avail, 26), Qt.AlignLeft | Qt.AlignVCenter,
                       fm.elidedText(name, Qt.ElideRight, int(avail)))
            y += 28

    def _index_at(self, pos):
        total = sum(v for _, v, _ in self.data)
        rect = self._rect()
        if total <= 0:
            return -1
        dx, dy = pos.x() - rect.center().x(), pos.y() - rect.center().y()
        dist = math.hypot(dx, dy)
        if dist > rect.width() / 2 + 4 or dist < rect.width() * 0.3:
            return -1
        ang = (90 - math.degrees(math.atan2(-dy, dx))) % 360
        acc = 0
        for i, (_, v, _) in enumerate(self.data):
            acc += v / total * 360
            if ang <= acc:
                return i
        return len(self.data) - 1

    def mouseMoveEvent(self, ev):
        i = self._index_at(ev.pos())
        if i != self.hover:
            self.hover = i
            self.update()
        if i >= 0:
            name, v, _ = self.data[i]
            total = sum(x for _, x, _ in self.data)
            QToolTip.showText(ev.globalPos(), "%s\n%s  (%d%%)" % (name, kes(v), round(v / total * 100)), self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, ev):
        self.hover = -1
        self.update()


class BarChart(QWidget):
    def __init__(self, min_h=250):
        super().__init__()
        self.labels, self.series, self.titles, self.hover = [], [], [], -1
        self.setMinimumHeight(min_h)
        self.setMouseTracking(True)

    def set_data(self, labels, series, titles=None):
        """series: list of (name, color, [values])"""
        self.labels, self.series, self.hover = labels, series, -1
        self.titles = titles or labels
        self.update()

    def _plot(self):
        top = 28 if len(self.series) > 1 else 8
        return QRectF(46, top, self.width() - 46 - 8, self.height() - top - 26)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        plot = self._plot()
        f = QFont(self.font()); f.setPointSize(8)
        p.setFont(f)
        maxv = max([max(v) if v else 0 for _, _, v in self.series] + [0])
        ceil_v = nice_ceiling(maxv)
        for i in range(5):
            y = plot.bottom() - plot.height() * i / 4
            p.setPen(QPen(QColor(BORDER), 1, Qt.SolidLine if i == 0 else Qt.DashLine))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(0, y - 9, plot.left() - 8, 18), Qt.AlignRight | Qt.AlignVCenter, short(ceil_v * i / 4))
        n, ns = len(self.labels), len(self.series)
        if n == 0 or maxv <= 0:
            p.setPen(QColor(MUTED))
            p.drawText(plot, Qt.AlignCenter, "No data yet")
            return
        gw = plot.width() / n
        if ns > 1:  # legend
            fm = QFontMetrics(f)
            x = plot.left()
            for name, color, _ in self.series:
                p.setPen(Qt.NoPen); p.setBrush(QColor(color))
                p.drawRoundedRect(QRectF(x, 7, 10, 10), 3, 3)
                p.setPen(QColor(MUTED))
                p.drawText(QPointF(x + 15, 16), name)
                x += fm.horizontalAdvance(name) + 34
        if self.hover >= 0:
            p.fillRect(QRectF(plot.left() + self.hover * gw, plot.top(), gw, plot.height()), QColor(255, 255, 255, 12))
        bar_w = max(3.0, min(26.0, gw * 0.72 / ns - 2))
        total_w = bar_w * ns + 3 * (ns - 1)
        step = max(1, int(math.ceil(n / max(1, int(plot.width() / 34)))))
        for gi in range(n):
            x0 = plot.left() + gi * gw + (gw - total_w) / 2
            for si, (_, color, vals) in enumerate(self.series):
                v = vals[gi]
                if v <= 0:
                    continue
                h = max(2.0, v / ceil_v * plot.height())
                x = x0 + si * (bar_w + 3)
                r = min(5.0, bar_w / 2, h / 2)
                path = QPainterPath()
                path.addRoundedRect(QRectF(x, plot.bottom() - h, bar_w, h), r, r)
                g = QLinearGradient(0, plot.bottom() - h, 0, plot.bottom())
                c2 = QColor(color); c2.setAlpha(110)
                g.setColorAt(0, QColor(color)); g.setColorAt(1, c2)
                p.setPen(Qt.NoPen); p.setBrush(QBrush(g))
                p.drawPath(path)
            if gi % step == 0:
                p.setPen(QColor(MUTED))
                p.drawText(QRectF(plot.left() + gi * gw - 10, plot.bottom() + 5, gw + 20, 16),
                           Qt.AlignHCenter | Qt.AlignTop, self.labels[gi])

    def mouseMoveEvent(self, ev):
        plot = self._plot()
        n = len(self.labels)
        gi = -1
        if n and plot.contains(ev.pos()):
            gi = min(n - 1, int((ev.x() - plot.left()) / (plot.width() / n)))
        if gi != self.hover:
            self.hover = gi
            self.update()
        if gi >= 0:
            lines = [self.titles[gi]] + ["%s: %s" % (name, kes(vals[gi])) for name, _, vals in self.series]
            QToolTip.showText(ev.globalPos(), "\n".join(lines), self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, ev):
        self.hover = -1
        self.update()


class Meter(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(10)
        self.ratio, self.color = 0.0, INCOME

    def set(self, ratio, color):
        self.ratio, self.color = ratio, color
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0, 0, self.width(), self.height())
        p.setPen(Qt.NoPen); p.setBrush(QColor("#263041"))
        p.drawRoundedRect(r, 5, 5)
        w = max(0.0, min(1.0, self.ratio)) * self.width()
        if w > 0:
            g = QLinearGradient(0, 0, self.width(), 0)
            c = QColor(self.color); c2 = QColor(self.color); c2.setAlpha(170)
            g.setColorAt(0, c2); g.setColorAt(1, c)
            p.setBrush(QBrush(g))
            p.drawRoundedRect(QRectF(0, 0, max(w, 10), self.height()), 5, 5)


# --------------------------------------------------------------------------- reusable widgets
class StatCard(QFrame):
    def __init__(self, title, accent, icon):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(2)
        top = QHBoxLayout()
        self.title = QLabel(title)
        self.title.setObjectName("cardTitle")
        badge = QLabel(icon)
        badge.setFixedSize(36, 36)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet("background:%s; border-radius:11px; font-size:16px;" % rgba(accent, 0.16))
        top.addWidget(self.title)
        top.addStretch()
        top.addWidget(badge)
        self.value = QLabel("-")
        self.value.setObjectName("cardValue")
        self.sub = QLabel("")
        self.sub.setObjectName("cardSub")
        self.sub.setWordWrap(True)
        self.sub.setMinimumWidth(10)
        lay.addLayout(top)
        lay.addWidget(self.value)
        lay.addWidget(self.sub)

    def set(self, value, sub="", color=None):
        self.value.setText(value)
        self.value.setStyleSheet("color:%s;" % color if color else "")
        self.sub.setText(sub)


class MonthNav(QWidget):
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        t = date.today()
        self.year, self.month = t.year, t.month
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.prev = QPushButton("‹"); self.prev.setObjectName("icon")
        self.next = QPushButton("›"); self.next.setObjectName("icon")
        self.label = QLabel(); self.label.setObjectName("monthTitle")
        self.label.setMinimumWidth(140); self.label.setAlignment(Qt.AlignCenter)
        self.today = QPushButton("This month"); self.today.setObjectName("ghost")
        for w in (self.prev, self.label, self.next, self.today):
            lay.addWidget(w)
        self.prev.clicked.connect(lambda: self.step(-1))
        self.next.clicked.connect(lambda: self.step(1))
        self.today.clicked.connect(self.reset)
        self._sync()

    def step(self, d):
        m = self.month + d
        y = self.year
        if m < 1:
            m, y = 12, y - 1
        if m > 12:
            m, y = 1, y + 1
        self.year, self.month = y, m
        self._sync(); self.changed.emit()

    def reset(self):
        t = date.today()
        self.year, self.month = t.year, t.month
        self._sync(); self.changed.emit()

    def _sync(self):
        self.label.setText(date(self.year, self.month, 1).strftime("%B %Y"))
        t = date.today()
        self.next.setEnabled((self.year, self.month) < (t.year, t.month))

    def bounds(self):
        return month_bounds(self.year, self.month)


class TxRow(QFrame):
    def __init__(self, row):
        super().__init__()
        self.setObjectName("txrow")
        dt = parse_ts(row["ts"])
        name = row["description"] or row["category"]
        color = cat_color(row["category"])
        h = QHBoxLayout(self)
        h.setContentsMargins(4, 9, 4, 9)
        h.setSpacing(12)
        letter = next((c for c in name if c.isalnum()), "?").upper()
        badge = QLabel(letter)
        badge.setFixedSize(40, 40)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet("background:%s; color:%s; border-radius:12px; font-weight:700; font-size:15px;"
                            % (rgba(color, 0.16), color))
        mid = QVBoxLayout(); mid.setSpacing(1)
        a = QLabel(name); a.setStyleSheet("font-weight:600;")
        b = QLabel("%s  ·  %s  ·  %s" % (row["category"], row["source"], fmt_dt(dt)))
        b.setObjectName("cardSub")
        mid.addWidget(a); mid.addWidget(b)
        inc = row["type"] == "income"
        amt = QLabel(kes(row["amount"] if inc else -row["amount"], signed=True))
        amt.setStyleSheet("font-weight:700; color:%s;" % (INCOME if inc else EXPENSE))
        h.addWidget(badge); h.addLayout(mid, 1); h.addWidget(amt)


class AccountLine(QFrame):
    def __init__(self, a, total=False):
        super().__init__()
        self.setObjectName("txrow")
        h = QHBoxLayout(self)
        h.setContentsMargins(4, 9, 4, 9); h.setSpacing(12)
        icon = QLabel(KIND_ICON.get(a["kind"], "∑") if not total else "∑")
        icon.setFixedSize(36, 36); icon.setAlignment(Qt.AlignCenter)
        color = PURPLE if total else cat_color(a["name"])
        icon.setStyleSheet("background:%s; border-radius:11px; font-size:16px;" % rgba(color, 0.16))
        mid = QVBoxLayout(); mid.setSpacing(0)
        n = QLabel(a["name"] + ("  (closed)" if a.get("closed_on") else ""))
        n.setStyleSheet("font-weight:700;" if total else "font-weight:600;")
        sub = QLabel(("Opening %s" % kes(a["opening"])) if not total else "Opening %s" % kes(a["opening"]))
        sub.setObjectName("cardSub")
        mid.addWidget(n); mid.addWidget(sub)
        amt = QLabel(kes(a["closing"]))
        amt.setStyleSheet("font-weight:700; font-size:14px; color:%s;" % (EXPENSE if a["closing"] < 0 else TEXT))
        h.addWidget(icon); h.addLayout(mid, 1); h.addWidget(amt)


class BudgetBar(QFrame):
    edit_clicked = pyqtSignal(str)
    delete_clicked = pyqtSignal(str)

    def __init__(self, cat, spent, limit, actions=True):
        super().__init__()
        self.cat = cat
        ratio = spent / limit if limit > 0 else 0
        color = INCOME if ratio < 0.75 else (WARN if ratio < 1 else EXPENSE)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 6, 0, 6)
        v.setSpacing(5)
        top = QHBoxLayout()
        dot = QLabel(); dot.setFixedSize(10, 10)
        dot.setStyleSheet("background:%s; border-radius:5px;" % cat_color(cat))
        name = QLabel(cat); name.setStyleSheet("font-weight:600;")
        top.addWidget(dot); top.addWidget(name); top.addStretch()
        amt = QLabel("%s of %s" % (kes(spent), kes(limit)))
        amt.setObjectName("cardSub")
        top.addWidget(amt)
        if actions:
            e = QPushButton("Edit"); e.setObjectName("tiny")
            d = QPushButton("✕"); d.setObjectName("tiny"); d.setProperty("x", 1)
            d.setStyleSheet("QPushButton:hover{color:%s;}" % EXPENSE)
            e.clicked.connect(lambda: self.edit_clicked.emit(self.cat))
            d.clicked.connect(lambda: self.delete_clicked.emit(self.cat))
            top.addWidget(e); top.addWidget(d)
        v.addLayout(top)
        m = Meter(); m.set(ratio, color)
        v.addWidget(m)
        left = limit - spent
        txt = ("%s left  ·  %d%% used" % (kes(left), round(ratio * 100))) if left >= 0 else \
              ("Over budget by %s" % kes(-left))
        note = QLabel(txt); note.setObjectName("cardSub")
        if left < 0:
            note.setStyleSheet("color:%s; font-weight:600;" % EXPENSE)
        v.addWidget(note)


# --------------------------------------------------------------------------- dialogs
class TransactionDialog(QDialog):
    def __init__(self, parent=None, db=None, row=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Edit transaction" if row else "Add transaction")
        self.setMinimumWidth(460)
        self.editing = row is not None
        self._cat_touched = self.editing
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 22)
        v.setSpacing(14)
        head = QLabel("Edit transaction" if row else "New transaction")
        head.setObjectName("h1"); head.setStyleSheet("font-size:20px;")
        v.addWidget(head)

        seg = QHBoxLayout(); seg.setSpacing(0)
        self.b_exp = QPushButton("−  Expense"); self.b_exp.setObjectName("segExp"); self.b_exp.setCheckable(True)
        self.b_inc = QPushButton("+  Income"); self.b_inc.setObjectName("segInc"); self.b_inc.setCheckable(True)
        grp = QButtonGroup(self); grp.setExclusive(True)
        grp.addButton(self.b_exp); grp.addButton(self.b_inc)
        self.b_exp.setChecked(True)
        seg.addWidget(self.b_exp); seg.addWidget(self.b_inc)
        v.addLayout(seg)

        self.form = QFormLayout()
        self.form.setSpacing(12)
        self.form.setLabelAlignment(Qt.AlignLeft)

        self.amount = QDoubleSpinBox()
        self.amount.setRange(0, 1_000_000_000); self.amount.setDecimals(2)
        self.amount.setPrefix("KSh "); self.amount.setGroupSeparatorShown(True)
        self.amount.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.fee = QDoubleSpinBox()
        self.fee.setRange(0, 1_000_000); self.fee.setDecimals(2)
        self.fee.setPrefix("KSh "); self.fee.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.desc = QLineEdit(); self.desc.setPlaceholderText("e.g. Naivas, KPLC tokens, Salary…")
        self.category = QComboBox(); self.category.setEditable(True)
        self.source = QComboBox()
        names = db.account_names(open_only=True)
        if row is not None and row["source"] not in names:
            names.append(row["source"])
        self.source.addItems(names)
        self.when = QDateTimeEdit(QDateTime.currentDateTime())
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("ddd, dd MMM yyyy  •  hh:mm AP")
        self.code = QLineEdit(); self.code.setPlaceholderText("Optional, e.g. UCB7K2LM4P")

        self.form.addRow("Amount", self.amount)
        self.form.addRow("Description", self.desc)
        self.form.addRow("Category", self.category)
        self.form.addRow("Account", self.source)
        self.form.addRow("Date & time", self.when)
        self.form.addRow("M-Pesa fee", self.fee)
        self.form.addRow("M-Pesa code", self.code)
        v.addLayout(self.form)

        btns = QHBoxLayout(); btns.addStretch()
        cancel = QPushButton("Cancel"); cancel.setObjectName("ghost")
        ok = QPushButton("Save changes" if row else "Add transaction"); ok.setObjectName("primary")
        btns.addWidget(cancel); btns.addWidget(ok)
        v.addLayout(btns)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self._accept)

        self.b_exp.toggled.connect(self._fill_cats)
        self.source.currentTextChanged.connect(self._toggle_mpesa)
        self.category.activated.connect(lambda _: setattr(self, "_cat_touched", True))
        self.desc.textChanged.connect(self._auto_cat)
        self._fill_cats()

        if row:
            self.b_inc.setChecked(row["type"] == "income")
            self.b_exp.setChecked(row["type"] == "expense")
            self._fill_cats()
            self.amount.setValue(row["amount"])
            self.desc.setText(row["description"] or "")
            self.category.setCurrentText(row["category"])
            self.source.setCurrentText(row["source"])
            dt = parse_ts(row["ts"])
            self.when.setDateTime(QDateTime(QDate(dt.year, dt.month, dt.day), QTime(dt.hour, dt.minute, dt.second)))
            self.code.setText(row["mpesa_code"] or "")
        self._toggle_mpesa()

    def _is_expense(self):
        return self.b_exp.isChecked()

    def _fill_cats(self):
        cur = self.category.currentText()
        self.category.clear()
        self.category.addItems(EXPENSE_CATS if self._is_expense() else INCOME_CATS)
        if cur in (EXPENSE_CATS if self._is_expense() else INCOME_CATS):
            self.category.setCurrentText(cur)
        else:
            self.category.setCurrentIndex(self.category.count() - 1 if self._is_expense() else 0)
        self._toggle_mpesa()

    def _toggle_mpesa(self):
        mp = self.db.account_kind(self.source.currentText()) == "M-Pesa"
        show_fee = mp and self._is_expense() and not self.editing
        for w, vis in ((self.fee, show_fee), (self.code, mp)):
            w.setVisible(vis)
            lab = self.form.labelForField(w)
            if lab:
                lab.setVisible(vis)

    def _auto_cat(self, text):
        if not self._cat_touched:
            g = guess_category(text, "expense" if self._is_expense() else "income")
            self.category.setCurrentText(g)

    def _accept(self):
        if self.amount.value() <= 0:
            QMessageBox.warning(self, APP_NAME, "Please enter an amount greater than zero.")
            return
        self.accept()

    def values(self):
        return dict(
            ts=self.when.dateTime().toPyDateTime().strftime(FMT),
            typ="expense" if self._is_expense() else "income",
            amount=self.amount.value(),
            category=self.category.currentText().strip() or "Other",
            description=self.desc.text().strip(),
            source=self.source.currentText(),
            code=self.code.text().strip().upper() or None,
            fee=self.fee.value() if self.fee.isVisible() else 0.0,
        )


class AccountDialog(QDialog):
    def __init__(self, parent, db, acc=None):
        super().__init__(parent)
        self.db, self.acc = db, acc
        self.setWindowTitle("Edit account" if acc else "Add account")
        self.setMinimumWidth(440)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 22); v.setSpacing(14)
        h = QLabel("Edit account" if acc else "New account"); h.setObjectName("h1"); h.setStyleSheet("font-size:20px;")
        v.addWidget(h)
        form = QFormLayout(); form.setSpacing(12)
        self.name = QLineEdit(); self.name.setPlaceholderText("e.g. KCB, Equity, Co-op Bank")
        self.kind = QComboBox(); self.kind.addItems(ACCOUNT_KINDS)
        self.opening = QDoubleSpinBox()
        self.opening.setRange(-1_000_000_000, 1_000_000_000); self.opening.setDecimals(2)
        self.opening.setPrefix("KSh "); self.opening.setGroupSeparatorShown(True)
        self.opening.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.opened = QDateEdit(QDate.currentDate()); self.opened.setCalendarPopup(True)
        self.opened.setDisplayFormat("ddd, dd MMM yyyy")
        form.addRow("Account name", self.name)
        form.addRow("Type", self.kind)
        form.addRow("Opening balance", self.opening)
        form.addRow("Opening date", self.opened)
        v.addLayout(form)
        n = QLabel("The opening balance is what the account held at 12:00 AM on the opening date. Only "
                   "transactions from that date onward count towards its balance, so enter an earlier "
                   "date if you want to record older transactions.")
        n.setObjectName("cardSub"); n.setWordWrap(True)
        v.addWidget(n)
        row = QHBoxLayout(); row.addStretch()
        c = QPushButton("Cancel"); c.setObjectName("ghost")
        o = QPushButton("Save account"); o.setObjectName("primary")
        row.addWidget(c); row.addWidget(o)
        v.addLayout(row)
        c.clicked.connect(self.reject); o.clicked.connect(self._ok)
        if acc:
            self.name.setText(acc["name"]); self.kind.setCurrentText(acc["kind"])
            self.opening.setValue(acc["opening_balance"])
            d = parse_ts(acc["opened_on"])
            self.opened.setDate(QDate(d.year, d.month, d.day))

    def _ok(self):
        name = self.name.text().strip()
        if not name:
            QMessageBox.warning(self, APP_NAME, "Please give the account a name."); return
        if self.db.account(name) and (not self.acc or self.acc["name"] != name):
            QMessageBox.warning(self, APP_NAME, "You already have an account called “%s”." % name); return
        self.accept()

    def values(self):
        d = self.opened.date().toPyDate()
        return dict(name=self.name.text().strip(), kind=self.kind.currentText(), opening=self.opening.value(),
                    opened_on=datetime(d.year, d.month, d.day).strftime(FMT))


class TransferDialog(QDialog):
    def __init__(self, parent, db):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Transfer between accounts")
        self.setMinimumWidth(440)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 22); v.setSpacing(14)
        h = QLabel("Transfer money"); h.setObjectName("h1"); h.setStyleSheet("font-size:20px;")
        n = QLabel("Moving money between your own accounts (bank → M-Pesa, M-Pesa → cash…) is not income "
                   "or spending, so it only changes the balances.")
        n.setObjectName("cardSub"); n.setWordWrap(True)
        v.addWidget(h); v.addWidget(n)
        names = db.account_names(open_only=True)
        form = QFormLayout(); form.setSpacing(12)
        self.frm = QComboBox(); self.frm.addItems(names)
        self.to = QComboBox(); self.to.addItems(names)
        if len(names) > 1:
            self.to.setCurrentIndex(1)
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0, 1_000_000_000); self.amount.setDecimals(2)
        self.amount.setPrefix("KSh "); self.amount.setGroupSeparatorShown(True)
        self.amount.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.fee = QDoubleSpinBox()
        self.fee.setRange(0, 1_000_000); self.fee.setDecimals(2)
        self.fee.setPrefix("KSh "); self.fee.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.when = QDateTimeEdit(QDateTime.currentDateTime()); self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("ddd, dd MMM yyyy  •  hh:mm AP")
        self.note = QLineEdit(); self.note.setPlaceholderText("Optional note")
        form.addRow("From", self.frm); form.addRow("To", self.to); form.addRow("Amount", self.amount)
        form.addRow("Fee charged", self.fee); form.addRow("Date & time", self.when); form.addRow("Note", self.note)
        v.addLayout(form)
        row = QHBoxLayout(); row.addStretch()
        c = QPushButton("Cancel"); c.setObjectName("ghost")
        o = QPushButton("Transfer"); o.setObjectName("primary")
        row.addWidget(c); row.addWidget(o)
        v.addLayout(row)
        c.clicked.connect(self.reject); o.clicked.connect(self._ok)

    def _ok(self):
        if self.frm.currentText() == self.to.currentText():
            QMessageBox.warning(self, APP_NAME, "Choose two different accounts."); return
        if self.amount.value() <= 0:
            QMessageBox.warning(self, APP_NAME, "Please enter an amount greater than zero."); return
        self.accept()

    def values(self):
        return dict(ts=self.when.dateTime().toPyDateTime().strftime(FMT), frm=self.frm.currentText(),
                    to=self.to.currentText(), amount=self.amount.value(), fee=self.fee.value(),
                    note=self.note.text().strip())


class BudgetDialog(QDialog):
    def __init__(self, parent=None, cat=None, limit=0.0):
        super().__init__(parent)
        self.setWindowTitle("Set budget")
        self.setMinimumWidth(380)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 22)
        v.setSpacing(14)
        h = QLabel("Monthly budget"); h.setObjectName("h1"); h.setStyleSheet("font-size:20px;")
        v.addWidget(h)
        form = QFormLayout(); form.setSpacing(12)
        self.cat = QComboBox()
        self.cat.addItems([c for c in EXPENSE_CATS])
        if cat:
            self.cat.setCurrentText(cat); self.cat.setEnabled(False)
        self.limit = QDoubleSpinBox()
        self.limit.setRange(0, 1_000_000_000); self.limit.setDecimals(2)
        self.limit.setPrefix("KSh "); self.limit.setGroupSeparatorShown(True)
        self.limit.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.limit.setValue(limit)
        form.addRow("Category", self.cat)
        form.addRow("Limit / month", self.limit)
        v.addLayout(form)
        row = QHBoxLayout(); row.addStretch()
        c = QPushButton("Cancel"); c.setObjectName("ghost")
        o = QPushButton("Save budget"); o.setObjectName("primary")
        row.addWidget(c); row.addWidget(o)
        v.addLayout(row)
        c.clicked.connect(self.reject)
        o.clicked.connect(self._ok)

    def _ok(self):
        if self.limit.value() <= 0:
            QMessageBox.warning(self, APP_NAME, "Please enter a limit greater than zero.")
            return
        self.accept()


# --------------------------------------------------------------------------- pages
class DashboardPage(QScrollArea):
    changed = pyqtSignal()
    goto_accounts = pyqtSignal()

    def __init__(self, db):
        super().__init__()
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.db = db
        self.setWidgetResizable(True)
        body = QWidget(); body.setObjectName("pagebody")
        self.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(28, 4, 28, 28)
        lay.setSpacing(16)

        self.nav = MonthNav()
        self.nav.changed.connect(self.refresh)
        top = QHBoxLayout(); top.addWidget(self.nav); top.addStretch()
        lay.addLayout(top)

        cards = QHBoxLayout(); cards.setSpacing(16)
        self.c_net = StatCard("NET THIS MONTH", BLUE, "⚖")
        self.c_inc = StatCard("INCOME", INCOME, "↗")
        self.c_exp = StatCard("EXPENSES", EXPENSE, "↘")
        self.c_mp = StatCard("M-PESA SPENT", ACCENT, "📲")
        for c in (self.c_net, self.c_inc, self.c_exp, self.c_mp):
            cards.addWidget(c, 1)
        lay.addLayout(cards)

        cards2 = QHBoxLayout(); cards2.setSpacing(16)
        self.c_open = StatCard("OPENING BALANCE", PURPLE, "🔓")
        self.c_close = StatCard("CLOSING BALANCE", PURPLE, "🔒")
        cards2.addWidget(self.c_open, 1); cards2.addWidget(self.c_close, 1)
        sb = QPushButton("Manage accounts  →"); sb.setObjectName("ghost")
        sb.clicked.connect(self.goto_accounts.emit)
        side = QVBoxLayout(); side.addStretch(); side.addWidget(sb); side.addStretch()
        cards2.addLayout(side)
        lay.addLayout(cards2)

        self.donut = DonutChart()
        self.trend = BarChart()
        r2 = QHBoxLayout(); r2.setSpacing(16)
        r2.addWidget(make_card("Where your money goes", self.donut, "Expenses by category"), 5)
        r2.addWidget(make_card("Daily spending", self.trend, "Hover a day to see spending and any income"), 7)
        lay.addLayout(r2)

        self.inc_donut = DonutChart()
        self.acc_box = QWidget()
        self.acc_lay = QVBoxLayout(self.acc_box)
        self.acc_lay.setContentsMargins(0, 0, 0, 0); self.acc_lay.setSpacing(0)
        rA = QHBoxLayout(); rA.setSpacing(16)
        rA.addWidget(make_card("Where your money comes from", self.inc_donut,
                               "Income by source: salary, side hustle, deals, gifts…"), 5)
        rA.addWidget(make_card("Account balances", self.acc_box, "Closing balance of each account"), 7)
        lay.addLayout(rA)

        self.hours = BarChart(230)
        self.budget_box = QWidget()
        self.budget_lay = QVBoxLayout(self.budget_box)
        self.budget_lay.setContentsMargins(0, 0, 0, 0)
        self.budget_lay.setSpacing(2)
        r3 = QHBoxLayout(); r3.setSpacing(16)
        r3.addWidget(make_card("When you spend", self.hours, "Expenses by hour of the day"), 6)
        r3.addWidget(make_card("Budget status", self.budget_box, "Closest to their limit first"), 5)
        lay.addLayout(r3)

        self.recent_box = QWidget()
        self.recent_lay = QVBoxLayout(self.recent_box)
        self.recent_lay.setContentsMargins(0, 0, 0, 0)
        self.recent_lay.setSpacing(0)
        lay.addWidget(make_card("Recent activity", self.recent_box, "Latest transactions with date and time"))
        lay.addStretch()

    def refresh(self):
        db = self.db
        s, e = self.nav.bounds()
        y, m = self.nav.year, self.nav.month
        t = db.totals(s, e)
        net = t["inc"] - t["exp"]
        self.c_net.set(kes(net, signed=True), "All accounts now: " + kes(db.total_now()),
                       INCOME if net >= 0 else EXPENSE)
        self.c_inc.set(kes(t["inc"]), "%d transaction%s" % (t["n_inc"], "" if t["n_inc"] == 1 else "s"))
        self.c_exp.set(kes(t["exp"]), "%d transaction%s" % (t["n_exp"], "" if t["n_exp"] == 1 else "s"))
        share = (t["mexp"] / t["exp"] * 100) if t["exp"] else 0
        self.c_mp.set(kes(t["mexp"]), "%d%% of spending  ·  %s in fees" % (round(share), kes(t["fees"])))

        summ = db.month_summary(s, e)
        opening = sum(a["opening"] for a in summ)
        closing = sum(a["closing"] for a in summ)
        this_month = (y, m) == (date.today().year, date.today().month)
        self.c_open.set(kes(opening), "All accounts · 12:00 AM, %s" % date(y, m, 1).strftime("%d %b %Y"))
        if this_month:
            self.c_close.set(kes(closing), "All accounts · as of now, %s" % fmt_time(datetime.now()),
                             INCOME if closing >= opening else EXPENSE)
        else:
            self.c_close.set(kes(closing), "All accounts · 11:59 PM, %s" % date(y, m, monthrange(y, m)[1]).strftime("%d %b %Y"),
                             INCOME if closing >= opening else EXPENSE)

        inc_items = [(r["category"], r["t"], cat_color(r["category"])) for r in db.by_category("income", s, e)]
        self.inc_donut.set_data(inc_items, "Received")
        clear_layout(self.acc_lay)
        for a in summ:
            self.acc_lay.addWidget(AccountLine(a))
        if not summ:
            l = QLabel("No accounts yet."); l.setObjectName("muted"); self.acc_lay.addWidget(l)
        else:
            self.acc_lay.addWidget(AccountLine(dict(name="Total", kind=None, closing=closing, opening=opening,
                                                    closed_on=None), total=True))

        cats = [(r["category"], r["t"]) for r in db.by_category("expense", s, e)]
        items = [(n, v, cat_color(n)) for n, v in cats[:6]]
        rest = sum(v for _, v in cats[6:])
        if rest > 0:
            items.append(("Others", rest, "#64748B"))
        self.donut.set_data(items, "Spent")

        today = date.today()
        last = monthrange(y, m)[1]
        if (y, m) == (today.year, today.month):
            last = today.day
        daily = db.daily(s, e)
        labels, inc, exp, titles = [], [], [], []
        for d in range(1, last + 1):
            k = "%04d-%02d-%02d" % (y, m, d)
            labels.append(str(d))
            titles.append(date(y, m, d).strftime("%a, %d %b %Y"))
            inc.append(daily.get((k, "income"), 0))
            exp.append(daily.get((k, "expense"), 0))
        self.trend.set_data(labels, [("Expenses", EXPENSE, exp)],
                            ["%s  (income %s)" % (t_, kes(i_)) if i_ else t_
                             for t_, i_ in zip(titles, inc)])

        hv = db.by_hour(s, e)
        self.hours.set_data(["%02d" % h for h in range(24)], [("Spent", PURPLE, hv)],
                            ["%02d:00 - %02d:59" % (h, h) for h in range(24)])

        clear_layout(self.budget_lay)
        budgets, spent = db.budgets(), {n: v for n, v in cats}
        if budgets:
            ordered = sorted(budgets.items(), key=lambda kv: spent.get(kv[0], 0) / kv[1], reverse=True)[:5]
            for cat, lim in ordered:
                self.budget_lay.addWidget(BudgetBar(cat, spent.get(cat, 0), lim, actions=False))
        else:
            lab = QLabel("No budgets yet. Open the Budgets tab to set monthly limits.")
            lab.setObjectName("muted"); lab.setWordWrap(True)
            self.budget_lay.addWidget(lab)
        self.budget_lay.addStretch()

        clear_layout(self.recent_lay)
        rows = db.query(limit=8)
        if rows:
            for r in rows:
                self.recent_lay.addWidget(TxRow(r))
        else:
            lab = QLabel("Nothing here yet. Add a transaction or import your M-Pesa messages.")
            lab.setObjectName("muted")
            self.recent_lay.addWidget(lab)


class TransactionsPage(QWidget):
    changed = pyqtSignal()

    def __init__(self, db):
        super().__init__()
        self.db = db
        self.rows = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 4, 28, 28)
        lay.setSpacing(14)

        fc = QFrame(); fc.setObjectName("card")
        fl = QHBoxLayout(fc); fl.setContentsMargins(16, 12, 16, 12); fl.setSpacing(10)
        self.search = QLineEdit(); self.search.setPlaceholderText("🔎  Search description, category or M-Pesa code")
        self.type_f = QComboBox(); self.type_f.addItems(["All types", "Income", "Expense"])
        self.src_f = QComboBox(); self.src_f.addItems(["All accounts"] + db.account_names(False))
        self.cat_f = QComboBox(); self.cat_f.addItems(["All categories"] + ALL_CATS)
        self.range_f = QComboBox()
        self.range_f.addItems(["This month", "Last 7 days", "Last 30 days", "This year", "All time", "Custom"])
        self.d_from = QDateEdit(QDate.currentDate().addMonths(-1)); self.d_from.setCalendarPopup(True)
        self.d_to = QDateEdit(QDate.currentDate()); self.d_to.setCalendarPopup(True)
        for d in (self.d_from, self.d_to):
            d.setDisplayFormat("dd MMM yyyy"); d.setVisible(False)
        fl.addWidget(self.search, 1)
        for w in (self.type_f, self.src_f, self.cat_f, self.range_f, self.d_from, self.d_to):
            fl.addWidget(w)
        lay.addWidget(fc)

        tc = QFrame(); tc.setObjectName("card")
        tl = QVBoxLayout(tc); tl.setContentsMargins(12, 10, 12, 12); tl.setSpacing(8)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["DATE", "TIME", "DESCRIPTION", "CATEGORY", "ACCOUNT", "AMOUNT"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        for i in (0, 1, 3, 4, 5):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        hh.setHighlightSections(False)
        tl.addWidget(self.table, 1)

        bar = QHBoxLayout()
        self.summary = QLabel(""); self.summary.setObjectName("muted")
        self.b_edit = QPushButton("Edit"); self.b_edit.setObjectName("ghost")
        self.b_del = QPushButton("Delete"); self.b_del.setObjectName("danger")
        self.b_csv = QPushButton("Export CSV"); self.b_csv.setObjectName("ghost")
        bar.addWidget(self.summary, 1)
        for b in (self.b_csv, self.b_edit, self.b_del):
            bar.addWidget(b)
        tl.addLayout(bar)
        lay.addWidget(tc, 1)

        self.search.textChanged.connect(self.refresh)
        for c in (self.type_f, self.src_f, self.cat_f):
            c.currentIndexChanged.connect(self.refresh)
        self.range_f.currentIndexChanged.connect(self._range_changed)
        self.d_from.dateChanged.connect(self.refresh)
        self.d_to.dateChanged.connect(self.refresh)
        self.table.doubleClicked.connect(self.edit_selected)
        self.b_edit.clicked.connect(self.edit_selected)
        self.b_del.clicked.connect(self.delete_selected)
        self.b_csv.clicked.connect(self.export_csv)

    def _range_changed(self):
        custom = self.range_f.currentText() == "Custom"
        self.d_from.setVisible(custom); self.d_to.setVisible(custom)
        self.refresh()

    def _range(self):
        k, t = self.range_f.currentText(), date.today()
        if k == "This month":
            s, e = datetime(t.year, t.month, 1), None
        elif k == "Last 7 days":
            s, e = datetime(t.year, t.month, t.day) - timedelta(days=6), None
        elif k == "Last 30 days":
            s, e = datetime(t.year, t.month, t.day) - timedelta(days=29), None
        elif k == "This year":
            s, e = datetime(t.year, 1, 1), None
        elif k == "Custom":
            a, b = self.d_from.date().toPyDate(), self.d_to.date().toPyDate()
            s, e = datetime(a.year, a.month, a.day), datetime(b.year, b.month, b.day) + timedelta(days=1)
        else:
            s, e = None, None
        return (s.strftime(FMT) if s else None), (e.strftime(FMT) if e else None)

    def refresh(self):
        names = self.db.account_names(False)
        if [self.src_f.itemText(i) for i in range(1, self.src_f.count())] != names:
            cur = self.src_f.currentText()
            self.src_f.blockSignals(True)
            self.src_f.clear(); self.src_f.addItems(["All accounts"] + names)
            if cur in names:
                self.src_f.setCurrentText(cur)
            self.src_f.blockSignals(False)
        kinds = {a["name"]: a["kind"] for a in self.db.accounts()}
        s, e = self._range()
        typ = {"Income": "income", "Expense": "expense"}.get(self.type_f.currentText())
        src = self.src_f.currentText() if self.src_f.currentIndex() > 0 else None
        cat = self.cat_f.currentText() if self.cat_f.currentIndex() > 0 else None
        self.rows = self.db.query(s, e, typ, src, cat, self.search.text().strip() or None)
        self.table.setRowCount(len(self.rows))
        inc = exp = 0.0
        for i, r in enumerate(self.rows):
            dt = parse_ts(r["ts"])
            is_inc = r["type"] == "income"
            inc += r["amount"] if is_inc else 0
            exp += 0 if is_inc else r["amount"]
            cells = [dt.strftime("%d %b %Y"), fmt_time(dt), r["description"] or "-",
                     "●  " + r["category"], r["source"],
                     kes(r["amount"] if is_inc else -r["amount"], signed=True)]
            for c, txt in enumerate(cells):
                it = QTableWidgetItem(txt)
                if c == 0:
                    it.setData(Qt.UserRole, r["id"])
                if c == 3:
                    it.setForeground(QColor(cat_color(r["category"])))
                if c == 4 and kinds.get(r["source"]) == "M-Pesa":
                    it.setForeground(QColor(ACCENT))
                if c == 5:
                    it.setForeground(QColor(INCOME if is_inc else EXPENSE))
                    f = it.font(); f.setBold(True); it.setFont(f)
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c in (1, 4):
                    it.setForeground(QColor(it.foreground().color() if c == 4 and kinds.get(r["source"]) == "M-Pesa" else MUTED))
                self.table.setItem(i, c, it)
        self.summary.setText("%d transaction%s   ·   Income %s   ·   Expenses %s   ·   Net %s" %
                             (len(self.rows), "" if len(self.rows) == 1 else "s",
                              kes(inc), kes(exp), kes(inc - exp, signed=True)))

    def _selected_ids(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.table.item(r, 0).data(Qt.UserRole) for r in rows]

    def edit_selected(self, *_):
        ids = self._selected_ids()
        if not ids:
            return
        row = self.db.get(ids[0])
        dlg = TransactionDialog(self, self.db, row)
        if dlg.exec_() == QDialog.Accepted:
            v = dlg.values()
            self.db.update(ids[0], v["ts"], v["typ"], v["amount"], v["category"], v["description"],
                           v["source"], v["code"])
            self.changed.emit()

    def delete_selected(self):
        ids = self._selected_ids()
        if not ids:
            return
        if QMessageBox.question(self, APP_NAME, "Delete %d selected transaction%s?" %
                                (len(ids), "" if len(ids) == 1 else "s")) != QMessageBox.Yes:
            return
        for i in ids:
            self.db.delete(i)
        self.changed.emit()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Delete:
            self.delete_selected()
        else:
            super().keyPressEvent(ev)

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "pesaplan_export.csv", "CSV files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Date", "Time", "Type", "Category", "Description", "Account", "M-Pesa code", "Amount"])
            for r in self.rows:
                dt = parse_ts(r["ts"])
                w.writerow([dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S"), r["type"], r["category"],
                            r["description"], r["source"], r["mpesa_code"] or "", r["amount"]])
        QMessageBox.information(self, APP_NAME, "Exported %d rows to:\n%s" % (len(self.rows), path))


class AccountCard(QFrame):
    edit = pyqtSignal(str)
    toggle = pyqtSignal(str)
    remove = pyqtSignal(str)

    def __init__(self, a, in_use):
        super().__init__()
        self.setObjectName("card")
        self.name = a["name"]
        closed = bool(a["closed_on"])
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16); v.setSpacing(8)
        top = QHBoxLayout(); top.setSpacing(12)
        color = cat_color(a["name"])
        icon = QLabel(KIND_ICON.get(a["kind"], "👛")); icon.setFixedSize(40, 40); icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("background:%s; border-radius:12px; font-size:18px;" % rgba(color, 0.16))
        nm = QVBoxLayout(); nm.setSpacing(0)
        t = QLabel(a["name"]); t.setStyleSheet("font-size:15px; font-weight:700;")
        d = parse_ts(a["opened_on"])
        sub = "%s · opened %s" % (a["kind"], d.strftime("%d %b %Y"))
        if closed:
            sub += " · closed %s" % parse_ts(a["closed_on"]).strftime("%d %b %Y")
        st = QLabel(sub); st.setObjectName("cardSub")
        nm.addWidget(t); nm.addWidget(st)
        badge = QLabel("CLOSED" if closed else "OPEN")
        badge.setStyleSheet("background:%s; color:%s; border-radius:8px; padding:3px 9px; font-size:10px; "
                            "font-weight:700;" % (rgba(MUTED, 0.18) if closed else rgba(INCOME, 0.16),
                                                  MUTED if closed else INCOME))
        top.addWidget(icon); top.addLayout(nm, 1); top.addWidget(badge, 0, Qt.AlignTop)
        v.addLayout(top)

        def line(label, value, color=None, bold=False):
            r = QHBoxLayout()
            l = QLabel(label); l.setObjectName("cardSub")
            x = QLabel(value)
            x.setStyleSheet("%s%s" % ("font-weight:700;" if bold else "", "color:%s;" % color if color else ""))
            r.addWidget(l); r.addStretch(); r.addWidget(x)
            return r
        net_t = a["tin"] - a["tout"]
        v.addLayout(line("Opening balance", kes(a["opening"])))
        v.addLayout(line("Money in", "+" + kes(a["income"]), INCOME))
        v.addLayout(line("Money out", "-" + kes(a["expense"]), EXPENSE))
        v.addLayout(line("Transfers (net)", kes(net_t, signed=True), BLUE if net_t else MUTED))
        sep = QFrame(); sep.setFixedHeight(1); sep.setStyleSheet("background:%s;" % BORDER)
        v.addWidget(sep)
        v.addLayout(line("Closing balance", kes(a["closing"]), EXPENSE if a["closing"] < 0 else TEXT, True))

        br = QHBoxLayout(); br.setSpacing(6)
        e = QPushButton("Edit"); e.setObjectName("tiny")
        c = QPushButton("Reopen" if closed else "Close account"); c.setObjectName("tiny")
        br.addWidget(e); br.addWidget(c)
        if not in_use:
            d_ = QPushButton("Delete"); d_.setObjectName("tiny")
            d_.setStyleSheet("QPushButton:hover{color:%s;}" % EXPENSE)
            d_.clicked.connect(lambda: self.remove.emit(self.name))
            br.addWidget(d_)
        br.addStretch()
        v.addLayout(br)
        e.clicked.connect(lambda: self.edit.emit(self.name))
        c.clicked.connect(lambda: self.toggle.emit(self.name))
        if closed:
            self.setStyleSheet("QFrame#card{background:%s;}" % SIDEBAR)


class AccountsPage(QScrollArea):
    changed = pyqtSignal()

    def __init__(self, db):
        super().__init__()
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.db = db
        self.setWidgetResizable(True)
        body = QWidget(); body.setObjectName("pagebody")
        self.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(28, 4, 28, 28); lay.setSpacing(16)

        self.nav = MonthNav()
        self.nav.changed.connect(self.refresh)
        add = QPushButton("＋  Add account"); add.setObjectName("primary")
        add.clicked.connect(self.add_account)
        top = QHBoxLayout(); top.addWidget(self.nav); top.addStretch(); top.addWidget(add)
        lay.addLayout(top)

        cards = QHBoxLayout(); cards.setSpacing(16)
        self.t_open = StatCard("TOTAL OPENING", PURPLE, "🔓")
        self.t_in = StatCard("MONEY IN", INCOME, "↗")
        self.t_out = StatCard("MONEY OUT", EXPENSE, "↘")
        self.t_close = StatCard("TOTAL CLOSING", BLUE, "🔒")
        for c in (self.t_open, self.t_in, self.t_out, self.t_close):
            cards.addWidget(c, 1)
        lay.addLayout(cards)

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0); self.grid.setSpacing(16)
        lay.addWidget(self.grid_host)

        self.tr_box = QWidget()
        self.tr_lay = QVBoxLayout(self.tr_box)
        self.tr_lay.setContentsMargins(0, 0, 0, 0); self.tr_lay.setSpacing(0)
        lay.addWidget(make_card("Transfers between your accounts", self.tr_box,
                                "Moving your own money around is not income or spending"))

        dz = QFrame(); dz.setObjectName("card")
        dl = QHBoxLayout(dz); dl.setContentsMargins(20, 14, 20, 14)
        txt = QVBoxLayout(); txt.setSpacing(0)
        a = QLabel("Start fresh"); a.setObjectName("sectionTitle")
        b = QLabel("Erase every transaction, transfer and budget, and reset all accounts to KSh 0.")
        b.setObjectName("cardSub")
        txt.addWidget(a); txt.addWidget(b)
        self.b_reset = QPushButton("Erase everything"); self.b_reset.setObjectName("danger")
        self.b_reset.clicked.connect(self.start_fresh)
        dl.addLayout(txt, 1); dl.addWidget(self.b_reset)
        lay.addWidget(dz)
        lay.addStretch()

    # -- actions
    def add_account(self):
        dlg = AccountDialog(self, self.db)
        if dlg.exec_() == QDialog.Accepted:
            v = dlg.values()
            self.db.add_account(v["name"], v["kind"], v["opening"], v["opened_on"])
            self.changed.emit()

    def edit_account(self, name):
        dlg = AccountDialog(self, self.db, self.db.account(name))
        if dlg.exec_() == QDialog.Accepted:
            v = dlg.values()
            self.db.update_account(name, v["name"], v["kind"], v["opening"], v["opened_on"])
            self.changed.emit()

    def toggle_account(self, name):
        a = self.db.account(name)
        if a["closed_on"]:
            self.db.reopen_account(name)
        else:
            now = (datetime.now() + timedelta(seconds=1)).strftime(FMT)
            bal = self.db.balance_at(a, now)
            msg = "Close “%s” as of now? It will no longer appear when adding transactions, and its history is kept." % name
            if abs(bal) >= 0.005:
                msg = ("“%s” still holds %s.\n\nTip: transfer the remaining money to another account first.\n\n"
                       "Close it anyway?" % (name, kes(bal)))
            if QMessageBox.question(self, APP_NAME, msg) != QMessageBox.Yes:
                return
            self.db.close_account(name, datetime.now().strftime(FMT))
        self.changed.emit()

    def remove_account(self, name):
        if QMessageBox.question(self, APP_NAME, "Delete the empty account “%s”?" % name) == QMessageBox.Yes:
            self.db.delete_account(name)
            self.changed.emit()

    def transfer(self):
        if len(self.db.account_names(open_only=True)) < 2:
            QMessageBox.information(self, APP_NAME, "You need at least two open accounts to make a transfer.")
            return
        dlg = TransferDialog(self, self.db)
        if dlg.exec_() == QDialog.Accepted:
            v = dlg.values()
            self.db.add_transfer(v["ts"], v["frm"], v["to"], v["amount"], v["note"], fee=v["fee"])
            self.changed.emit()

    def del_transfer(self, tid):
        if QMessageBox.question(self, APP_NAME, "Delete this transfer?") == QMessageBox.Yes:
            self.db.delete_transfer(tid)
            self.changed.emit()

    def start_fresh(self):
        if QMessageBox.question(self, APP_NAME, "This permanently erases ALL transactions, transfers, budgets "
                                "and accounts, then recreates your accounts at KSh 0.\n\nContinue?") != QMessageBox.Yes:
            return
        if QMessageBox.question(self, APP_NAME, "Are you absolutely sure? This cannot be undone.") != QMessageBox.Yes:
            return
        self.db.start_fresh()
        self.changed.emit()

    # -- render
    def refresh(self):
        s, e = self.nav.bounds()
        summ = self.db.month_summary(s, e)
        self.t_open.set(kes(sum(a["opening"] for a in summ)), "%d account%s" % (len(summ), "" if len(summ) == 1 else "s"))
        self.t_in.set(kes(sum(a["income"] for a in summ)), "Income received")
        self.t_out.set(kes(sum(a["expense"] for a in summ)), "Spending and fees")
        close = sum(a["closing"] for a in summ)
        self.t_close.set(kes(close), "Across all accounts", EXPENSE if close < 0 else None)

        clear_layout(self.grid)
        for idx, a in enumerate(summ):
            card = AccountCard(a, self.db.account_in_use(a["name"]) == 0)
            card.edit.connect(self.edit_account); card.toggle.connect(self.toggle_account)
            card.remove.connect(self.remove_account)
            self.grid.addWidget(card, idx // 2, idx % 2)
        self.grid.setColumnStretch(0, 1); self.grid.setColumnStretch(1, 1)
        if not summ:
            l = QLabel("No accounts in this month. Click “Add account”."); l.setObjectName("muted")
            self.grid.addWidget(l, 0, 0)

        clear_layout(self.tr_lay)
        rows = self.db.transfers(s, e)
        if not rows:
            l = QLabel("No transfers this month."); l.setObjectName("muted"); self.tr_lay.addWidget(l)
        for r in rows:
            f = QFrame(); f.setObjectName("txrow")
            h = QHBoxLayout(f); h.setContentsMargins(4, 9, 4, 9); h.setSpacing(12)
            ic = QLabel("⇄"); ic.setFixedSize(40, 40); ic.setAlignment(Qt.AlignCenter)
            ic.setStyleSheet("background:%s; color:%s; border-radius:12px; font-size:18px; font-weight:700;"
                             % (rgba(BLUE, 0.16), BLUE))
            mid = QVBoxLayout(); mid.setSpacing(1)
            a1 = QLabel("%s  →  %s" % (r["from_acc"], r["to_acc"])); a1.setStyleSheet("font-weight:600;")
            b1 = QLabel("%s%s" % (fmt_dt(parse_ts(r["ts"])), ("  ·  " + r["note"]) if r["note"] else ""))
            b1.setObjectName("cardSub")
            mid.addWidget(a1); mid.addWidget(b1)
            amt = QLabel(kes(r["amount"])); amt.setStyleSheet("font-weight:700; color:%s;" % BLUE)
            x = QPushButton("✕"); x.setObjectName("tiny")
            x.setStyleSheet("QPushButton:hover{color:%s;}" % EXPENSE)
            tid = r["id"]
            x.clicked.connect(lambda _, t=tid: self.del_transfer(t))
            h.addWidget(ic); h.addLayout(mid, 1); h.addWidget(amt); h.addWidget(x)
            self.tr_lay.addWidget(f)


class BudgetsPage(QScrollArea):
    changed = pyqtSignal()

    def __init__(self, db):
        super().__init__()
        self.db = db
        self.setWidgetResizable(True)
        body = QWidget(); body.setObjectName("pagebody")
        self.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(28, 4, 28, 28)
        lay.setSpacing(16)

        self.nav = MonthNav()
        self.nav.changed.connect(self.refresh)
        add = QPushButton("＋  Set a budget"); add.setObjectName("primary")
        add.clicked.connect(lambda: self.edit_budget(None))
        top = QHBoxLayout(); top.addWidget(self.nav); top.addStretch(); top.addWidget(add)
        lay.addLayout(top)

        cards = QHBoxLayout(); cards.setSpacing(16)
        self.s_total = StatCard("TOTAL BUDGETED", BLUE, "🎯")
        self.s_spent = StatCard("SPENT (BUDGETED)", EXPENSE, "💸")
        self.s_left = StatCard("REMAINING", INCOME, "🛟")
        for c in (self.s_total, self.s_spent, self.s_left):
            cards.addWidget(c, 1)
        lay.addLayout(cards)

        self.list_box = QWidget()
        self.list_lay = QVBoxLayout(self.list_box)
        self.list_lay.setContentsMargins(0, 0, 0, 0); self.list_lay.setSpacing(8)
        lay.addWidget(make_card("Category budgets", self.list_box, "Green is healthy, amber is close, red is over"))

        self.un_box = QWidget()
        self.un_lay = QVBoxLayout(self.un_box)
        self.un_lay.setContentsMargins(0, 0, 0, 0); self.un_lay.setSpacing(4)
        lay.addWidget(make_card("Spending without a budget", self.un_box, "Categories you've spent in this month"))
        lay.addStretch()

    def edit_budget(self, cat):
        limit = self.db.budgets().get(cat, 0.0) if cat else 0.0
        dlg = BudgetDialog(self, cat, limit)
        if dlg.exec_() == QDialog.Accepted:
            self.db.set_budget(dlg.cat.currentText(), dlg.limit.value())
            self.changed.emit()

    def remove_budget(self, cat):
        if QMessageBox.question(self, APP_NAME, "Remove the budget for “%s”?" % cat) == QMessageBox.Yes:
            self.db.delete_budget(cat)
            self.changed.emit()

    def refresh(self):
        s, e = self.nav.bounds()
        spent = {r["category"]: r["t"] for r in self.db.by_category("expense", s, e)}
        budgets = self.db.budgets()
        tot = sum(budgets.values())
        sp = sum(spent.get(c, 0) for c in budgets)
        self.s_total.set(kes(tot), "%d categor%s" % (len(budgets), "y" if len(budgets) == 1 else "ies"))
        self.s_spent.set(kes(sp), "%d%% of total budget" % (round(sp / tot * 100) if tot else 0))
        self.s_left.set(kes(tot - sp), "Over budget overall" if tot - sp < 0 else "Still available",
                        INCOME if tot - sp >= 0 else EXPENSE)

        clear_layout(self.list_lay)
        if budgets:
            for cat, lim in sorted(budgets.items(), key=lambda kv: spent.get(kv[0], 0) / kv[1], reverse=True):
                b = BudgetBar(cat, spent.get(cat, 0), lim)
                b.edit_clicked.connect(self.edit_budget)
                b.delete_clicked.connect(self.remove_budget)
                self.list_lay.addWidget(b)
        else:
            l = QLabel("No budgets yet. Click “Set a budget” to create your first monthly limit.")
            l.setObjectName("muted"); self.list_lay.addWidget(l)

        clear_layout(self.un_lay)
        un = [(c, v) for c, v in spent.items() if c not in budgets]
        if not un:
            l = QLabel("Every category you spent in has a budget. Nice."); l.setObjectName("muted")
            self.un_lay.addWidget(l)
        for c, v in un:
            row = QHBoxLayout()
            dot = QLabel(); dot.setFixedSize(10, 10)
            dot.setStyleSheet("background:%s; border-radius:5px;" % cat_color(c))
            row.addWidget(dot); row.addWidget(QLabel(c)); row.addStretch()
            a = QLabel(kes(v)); a.setObjectName("cardSub"); row.addWidget(a)
            btn = QPushButton("Set budget"); btn.setObjectName("tiny")
            btn.clicked.connect(lambda _, cc=c: self.preset_budget(cc))
            row.addWidget(btn)
            w = QWidget(); w.setLayout(row)
            self.un_lay.addWidget(w)

    def preset_budget(self, cat):
        dlg = BudgetDialog(self, None, 0.0)
        dlg.cat.setCurrentText(cat)
        if dlg.exec_() == QDialog.Accepted:
            self.db.set_budget(dlg.cat.currentText(), dlg.limit.value())
            self.changed.emit()


class MpesaPage(QWidget):
    changed = pyqtSignal()
    imported = pyqtSignal()

    def __init__(self, db):
        super().__init__()
        self.db = db
        self.items = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 4, 28, 28)
        lay.setSpacing(14)

        ic = QFrame(); ic.setObjectName("card")
        il = QVBoxLayout(ic); il.setContentsMargins(20, 16, 20, 16); il.setSpacing(8)
        t = QLabel("Paste your M-Pesa messages"); t.setObjectName("sectionTitle")
        hint = QLabel("Open Messages → M-PESA on your phone, copy one or many confirmation SMS and paste them below. "
                      "PesaPlan reads the amount, fee, date and time, picks a category, and skips anything you "
                      "already imported (matched by transaction code).")
        hint.setObjectName("cardSub"); hint.setWordWrap(True)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("UCB7K2LM4P Confirmed. Ksh1,250.00 paid to NAIVAS SUPERMARKET. "
                                      "on 8/11/26 at 6:42 PM. New M-PESA balance is Ksh4,310.55 …")
        self.input.setFixedHeight(130)
        accrow = QHBoxLayout()
        al = QLabel("Import into account:"); al.setObjectName("cardSub")
        self.acc_combo = QComboBox(); self.acc_combo.setMinimumWidth(200)
        accrow.addWidget(al); accrow.addWidget(self.acc_combo); accrow.addStretch()
        br = QHBoxLayout()
        self.b_parse = QPushButton("Read messages"); self.b_parse.setObjectName("primary")
        b_paste = QPushButton("Paste from clipboard"); b_paste.setObjectName("ghost")
        b_sample = QPushButton("Try sample SMS"); b_sample.setObjectName("ghost")
        self.b_quick = QPushButton("Read && import all new"); self.b_quick.setObjectName("primary")
        b_clear = QPushButton("Clear"); b_clear.setObjectName("ghost")
        for b in (self.b_parse, self.b_quick, b_paste, b_sample, b_clear):
            br.addWidget(b)
        br.addStretch()
        il.addWidget(t); il.addWidget(hint); il.addWidget(self.input); il.addLayout(accrow); il.addLayout(br)
        lay.addWidget(ic)

        pc = QFrame(); pc.setObjectName("card")
        pl = QVBoxLayout(pc); pl.setContentsMargins(12, 10, 12, 12); pl.setSpacing(8)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["", "DATE & TIME", "TYPE", "DESCRIPTION", "CATEGORY",
                                              "AMOUNT", "FEE", "STATUS"])
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(50)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        for i in (0, 1, 2, 4, 5, 6, 7):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        pl.addWidget(self.table, 1)
        self.unrec_label = QLabel("These messages were NOT recognised, so they will not be saved. "
                                  "Send me one and I will add support:")
        self.unrec_label.setStyleSheet("color:%s; font-weight:600;" % WARN)
        self.unrec = QPlainTextEdit(); self.unrec.setReadOnly(True); self.unrec.setFixedHeight(90)
        self.unrec_label.hide(); self.unrec.hide()
        pl.addWidget(self.unrec_label); pl.addWidget(self.unrec)
        bar = QHBoxLayout()
        self.summary = QLabel("Nothing parsed yet."); self.summary.setObjectName("muted")
        self.b_import = QPushButton("Import selected"); self.b_import.setObjectName("primary")
        self.b_import.setEnabled(False)
        bar.addWidget(self.summary, 1); bar.addWidget(self.b_import)
        pl.addLayout(bar)
        lay.addWidget(pc, 1)

        self.b_parse.clicked.connect(self.parse)
        b_paste.clicked.connect(lambda: self.input.setPlainText(QApplication.clipboard().text()))
        b_sample.clicked.connect(lambda: (self.input.setPlainText(SAMPLE_SMS), self.parse()))
        b_clear.clicked.connect(self.clear)
        self.b_import.clicked.connect(self.do_import)
        self.b_quick.clicked.connect(self.quick_import)

    def clear(self):
        self.input.clear()
        self.items = []
        self.table.setRowCount(0)
        self.summary.setText("Nothing parsed yet.")
        self.b_import.setEnabled(False)

    def refresh(self):
        names = self.db.account_names(open_only=True)
        have = [self.acc_combo.itemText(i) for i in range(self.acc_combo.count())]
        if have != names:
            cur = self.acc_combo.currentText()
            self.acc_combo.clear(); self.acc_combo.addItems(names)
            pick = cur if cur in names else (self.db.first_account_of_kind("M-Pesa") or (names[0] if names else ""))
            self.acc_combo.setCurrentText(pick)

    def parse(self):
        msgs = MpesaParser.split_messages(self.input.toPlainText())
        items, seen, unparsed, bad = [], set(), 0, []
        for m in msgs:
            p = MpesaParser.parse(m)
            if not p:
                unparsed += 1
                bad.append(m)
                continue
            p["dup"] = self.db.code_exists(p["key"]) or p["key"] in seen
            seen.add(p["key"])
            items.append(p)
        items.sort(key=lambda x: x["ts"], reverse=True)
        self.items = items
        self.table.setRowCount(len(items))
        for i, p in enumerate(items):
            chk = QTableWidgetItem()
            if p["dup"]:
                chk.setFlags(Qt.NoItemFlags)
                chk.setCheckState(Qt.Unchecked)
            else:
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                chk.setCheckState(Qt.Checked)
            self.table.setItem(i, 0, chk)
            inc = p["type"] == "income"
            xfer = p["kind"] in ("withdrawn", "deposit")
            vals = [fmt_dt(p["ts"]), {"received": "Received", "withdrawn": "Withdrawal", "airtime": "Airtime",
                                      "paid": "Paid", "sent": "Sent", "fuliza": "Fuliza", "deposit": "Deposit",
                                      "reversal": "Reversal"}.get(p["kind"], p["kind"].title()),
                    p["description"], None,
                    ("⇄ " + kes(p["amount"])) if xfer else kes(p["amount"] if inc else -p["amount"], signed=True),
                    kes(p["fee"]) if p["fee"] else "-", "Already imported" if p["dup"] else "New"]
            col_map = [1, 2, 3, None, 5, 6, 7]
            for v, c in zip(vals, col_map):
                if c is None:
                    continue
                it = QTableWidgetItem(v)
                if c == 5:
                    it.setForeground(QColor(BLUE if xfer else (INCOME if inc else EXPENSE)))
                    f = it.font(); f.setBold(True); it.setFont(f)
                if c == 7:
                    it.setForeground(QColor(MUTED if p["dup"] else ACCENT))
                self.table.setItem(i, c, it)
            combo = QComboBox()
            if xfer:
                combo.addItem("Transfer " + ("M-Pesa → Cash" if p["kind"] == "withdrawn" else "Cash → M-Pesa"))
                combo.setEnabled(False)
            else:
                combo.addItems(INCOME_CATS if inc else EXPENSE_CATS)
                combo.setCurrentText(p["category"])
                combo.setEnabled(not p["dup"])
            self.table.setCellWidget(i, 4, combo)
        self.unrec.setPlainText("\n\n".join(bad))
        self.unrec.setVisible(bool(bad)); self.unrec_label.setVisible(bool(bad))
        n_new = sum(1 for p in items if not p["dup"])
        self.summary.setText("%d new   ·   %d already imported   ·   %d message%s not recognised" %
                             (n_new, len(items) - n_new, unparsed, "" if unparsed == 1 else "s"))
        self.b_import.setEnabled(n_new > 0)

    def do_import(self):
        acc = self.acc_combo.currentText()
        if not acc:
            QMessageBox.warning(self, APP_NAME, "Add an account first (Accounts page).")
            return 0
        entries = []
        for i, p in enumerate(self.items):
            it = self.table.item(i, 0)
            if p["dup"] or it.checkState() != Qt.Checked:
                continue
            entries.append((p, self.table.cellWidget(i, 4).currentText()))
        res = self.db.import_parsed(entries, acc)
        n, early = res["imported"], res["early"]
        opened = (self.db.account(acc) or {}).get("opened_on", "")
        bad = self.unrec.toPlainText().strip()
        self.input.clear(); self.items = []; self.table.setRowCount(0); self.b_import.setEnabled(False)
        self.summary.setText("Imported %d transaction%s." % (n, "" if n == 1 else "s"))
        if not bad:
            self.unrec.hide(); self.unrec_label.hide()
        self.changed.emit()
        if early:
            QMessageBox.information(
                self, APP_NAME,
                "%d imported transaction%s dated before “%s” was opened (%s), so %s not counted in its balance.\n\n"
                "Edit the account on the Accounts page and set an earlier opening date if you want %s included."
                % (early, "" if early == 1 else "s", acc, parse_ts(opened).strftime("%d %b %Y"),
                   "it is" if early == 1 else "they are", "it" if early == 1 else "them"))
        if n:
            self.imported.emit()
        return n

    def quick_import(self):
        self.parse()
        if not self.items:
            QMessageBox.warning(self, APP_NAME, "No M-Pesa messages were recognised in the text you pasted.")
            return
        n = self.do_import()
        if n == 0:
            QMessageBox.information(self, APP_NAME, "Everything you pasted was already imported.")


# --------------------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.setWindowTitle("%s - Budget Tracker" % APP_NAME)
        self.resize(1280, 820)
        self.setMinimumSize(1200, 700)

        root = QWidget(); root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # sidebar
        sb = QFrame(); sb.setObjectName("sidebar"); sb.setFixedWidth(240)
        sl = QVBoxLayout(sb); sl.setContentsMargins(18, 24, 18, 20); sl.setSpacing(6)
        brand = QHBoxLayout(); brand.setSpacing(12)
        logo = QLabel("P"); logo.setObjectName("logo"); logo.setFixedSize(42, 42); logo.setAlignment(Qt.AlignCenter)
        bt = QVBoxLayout(); bt.setSpacing(0)
        b1 = QLabel(APP_NAME); b1.setObjectName("brand")
        b2 = QLabel("Budget · M-Pesa · Time"); b2.setObjectName("brandSub")
        bt.addWidget(b1); bt.addWidget(b2)
        brand.addWidget(logo); brand.addLayout(bt); brand.addStretch()
        sl.addLayout(brand)
        sl.addSpacing(22)

        self.pages = [DashboardPage(db), AccountsPage(db), TransactionsPage(db), BudgetsPage(db), MpesaPage(db)]
        meta = [("📊", "Dashboard", "Your money at a glance"),
                ("🏦", "Accounts", "Bank accounts, M-Pesa and cash: opening and closing balances"),
                ("💸", "Transactions", "Every shilling, with date & time"),
                ("🎯", "Budgets", "Monthly limits by category"),
                ("📲", "M-Pesa Import", "Paste SMS messages and import them")]
        self.meta = meta
        self.nav_btns = []
        grp = QButtonGroup(self); grp.setExclusive(True)
        self.stack = QStackedWidget()
        for i, (ic, name, _) in enumerate(meta):
            b = QPushButton("  %s    %s" % (ic, name)); b.setObjectName("nav"); b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self.nav_btns.append(b)
            grp.addButton(b)
            b.clicked.connect(lambda _, k=i: self.go(k))
            sl.addWidget(b)
            if i == 0:
                b.setChecked(True)
            self.stack.addWidget(self.pages[i])
            self.pages[i].changed.connect(self.refresh_all)
        self.pages[4].imported.connect(self.after_import)
        self.pages[0].goto_accounts.connect(lambda: self.go_nav(1))
        self._sl, self._grp, self._nav_at = sl, grp, sl.count()
        sl.addStretch()

        bal = QFrame(); bal.setObjectName("balbox")
        bl = QVBoxLayout(bal); bl.setContentsMargins(16, 12, 16, 12); bl.setSpacing(2)
        l1 = QLabel("LAST M-PESA BALANCE"); l1.setObjectName("balLabel")
        self.bal_value = QLabel("-"); self.bal_value.setObjectName("balValue")
        bl.addWidget(l1); bl.addWidget(self.bal_value)
        sl.addWidget(bal)
        outer.addWidget(sb)

        # right side
        right = QVBoxLayout(); right.setContentsMargins(0, 0, 0, 0); right.setSpacing(0)
        head = QHBoxLayout(); head.setContentsMargins(28, 24, 28, 14)
        ht = QVBoxLayout(); ht.setSpacing(0)
        self.h_title = QLabel(); self.h_title.setObjectName("h1")
        self.h_sub = QLabel(); self.h_sub.setObjectName("h1sub")
        ht.addWidget(self.h_title); ht.addWidget(self.h_sub)
        head.addLayout(ht); head.addStretch()
        ct = QVBoxLayout(); ct.setSpacing(0)
        self.clock_date = QLabel(); self.clock_date.setObjectName("clockDate"); self.clock_date.setAlignment(Qt.AlignRight)
        self.clock_time = QLabel(); self.clock_time.setObjectName("clockTime"); self.clock_time.setAlignment(Qt.AlignRight)
        ct.addWidget(self.clock_date); ct.addWidget(self.clock_time)
        head.addLayout(ct)
        head.addSpacing(20)
        xfer = QPushButton("⇄  Transfer"); xfer.setObjectName("ghost")
        xfer.setCursor(Qt.PointingHandCursor)
        xfer.clicked.connect(self.pages[1].transfer)
        head.addWidget(xfer)
        add = QPushButton("＋  Add transaction"); add.setObjectName("primary")
        add.setCursor(Qt.PointingHandCursor)
        add.clicked.connect(self.add_transaction)
        head.addWidget(add)
        right.addLayout(head)
        right.addWidget(self.stack, 1)
        outer.addLayout(right, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        self._tick()
        self.go(0)
        self.refresh_all()

    def add_nav_page(self, icon, name, subtitle, widget):
        """Register an extra sidebar page. widget needs refresh(); a `changed` signal is optional."""
        i = self.stack.count()
        b = QPushButton("  %s    %s" % (icon, name)); b.setObjectName("nav"); b.setCheckable(True)
        b.setCursor(Qt.PointingHandCursor)
        self._grp.addButton(b)
        b.clicked.connect(lambda _, k=i: self.go(k))
        self._sl.insertWidget(self._nav_at, b)
        self._nav_at += 1
        self.nav_btns.append(b)
        self.meta.append((icon, name, subtitle))
        self.pages.append(widget)
        self.stack.addWidget(widget)
        if hasattr(widget, "changed"):
            widget.changed.connect(self.refresh_all)
        widget.refresh()
        return i

    def _tick(self):
        n = datetime.now()
        self.clock_date.setText(n.strftime("%A, %d %B %Y"))
        self.clock_time.setText(n.strftime("%I:%M:%S %p").lstrip("0"))

    def go(self, i):
        self.stack.setCurrentIndex(i)
        self.h_title.setText(self.meta[i][1])
        self.h_sub.setText(self.meta[i][2])

    def go_nav(self, i):
        self.nav_btns[i].setChecked(True)
        self.go(i)

    def after_import(self):
        tp = self.pages[2]
        tp.type_f.setCurrentIndex(0); tp.src_f.setCurrentIndex(0); tp.cat_f.setCurrentIndex(0)
        tp.search.clear()
        tp.range_f.setCurrentText("All time")
        self.refresh_all()
        self.go_nav(2)

    def refresh_all(self):
        for p in self.pages:
            p.refresh()
        bal = self.db.last_mpesa_balance()
        self.bal_value.setText(kes(bal) if bal is not None else "Import an SMS")

    def add_transaction(self):
        if not self.db.account_names(open_only=True):
            QMessageBox.information(self, APP_NAME, "Add an account first, on the Accounts page.")
            self.go_nav(1)
            return
        dlg = TransactionDialog(self, self.db)
        if dlg.exec_() == QDialog.Accepted:
            v = dlg.values()
            try:
                self.db.add(v["ts"], v["typ"], v["amount"], v["category"], v["description"], v["source"],
                            v["code"], v["fee"])
            except sqlite3.IntegrityError:
                QMessageBox.warning(self, APP_NAME, "A transaction with that M-Pesa code already exists.")
                return
            self.refresh_all()


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    f = QFont("Segoe UI", 10)
    app.setFont(f)
    db = Database(DB_PATH)
    win = MainWindow(db)
    try:                                    # optional phone access
        import pesaplan_phone
        pesaplan_phone.install(win, sys.modules[__name__], DB_PATH)
    except ImportError:
        pass
    try:                                    # optional Excel export
        import pesaplan_export
        pesaplan_export.install(win, sys.modules[__name__])
    except ImportError:
        pass

    try:                                    # optional Sync
        import pesaplan_syncui
        pesaplan_syncui.install(win, sys.modules[__name__], DB_PATH)
    except ImportError:
        pass

    win.show()
    if db.count() == 0 and not db.get_setting("welcomed"):
        db.set_setting("welcomed", "1")

        def welcome():
            QMessageBox.information(win, APP_NAME,
                                    "Welcome to %s!\n\nEverything starts at KSh 0. You have an M-Pesa, a Cash and "
                                    "four Bank accounts ready. Rename them and enter each one's opening balance "
                                    "on the Accounts page, then add transactions or import your M-Pesa SMS."
                                    % APP_NAME)
            win.go_nav(1)
        QTimer.singleShot(350, welcome)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
