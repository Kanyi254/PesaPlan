"""
pesaplan_export.py - download your income and expenses as an Excel (.xlsx) spreadsheet.

Put this file next to pesaplan.py. It needs one extra package:   pip install openpyxl

What you get in the workbook
  Summary    key figures, every account's opening / closing balance, income and expenses by category
  Income     one row per income entry (real Excel dates, filterable, totals follow your filter)
  Expenses   one row per expense
  Transfers  money moved between your own accounts (not income or spending)
  Monthly    income, expenses and net for each month, with a chart

On the PC it adds an "Export" page to the sidebar; the phone page can download the same file.
"""
import io
import os
from datetime import date, datetime, timedelta

FMT = "%Y-%m-%d %H:%M:%S"
MIN_TS, MAX_TS = "0000-01-01 00:00:00", "9999-12-31 23:59:59"

GREEN, DARK, SOFT = "1F8A4C", "0E1117", "E8F5EE"
MONEY = '#,##0.00;[Red]-#,##0.00'


class ExportError(Exception):
    pass


def _openpyxl():
    try:
        import openpyxl
        return openpyxl
    except ImportError:
        raise ExportError("Excel export needs the 'openpyxl' package.\n\nInstall it with:\n    pip install openpyxl")


# --------------------------------------------------------------------------- choosing the period
PERIODS = ["This month", "Last month", "Last 30 days", "This year", "Last year", "All time", "Custom"]


def resolve_period(kind, today=None, month=None, d_from=None, d_to=None):
    """Returns (start, end, label, slug). start/end are 'YYYY-MM-DD HH:MM:SS' (end excluded) or None for no limit."""
    t = today or date.today()
    kind = (kind or "").strip().lower().replace("_", " ")

    def span(a, b, label, slug):          # a, b: dates, b excluded
        return a.strftime("%Y-%m-%d 00:00:00"), b.strftime("%Y-%m-%d 00:00:00"), label, slug

    if month:                                                   # "2026-11"
        try:
            y, m = [int(x) for x in str(month).split("-")]
            a = date(y, m, 1)
        except Exception:
            raise ExportError("Bad month: %s" % month)
        b = date(y + (m == 12), (m % 12) + 1, 1)
        return span(a, b, a.strftime("%B %Y"), a.strftime("%Y-%m"))
    if kind in ("this month", "thismonth", ""):
        a = date(t.year, t.month, 1)
        return resolve_period("", month=a.strftime("%Y-%m"))
    if kind in ("last month", "lastmonth"):
        a = date(t.year, t.month, 1) - timedelta(days=1)
        return resolve_period("", month="%04d-%02d" % (a.year, a.month))
    if kind in ("last 30 days", "last30"):
        a = t - timedelta(days=29)
        return span(a, t + timedelta(days=1), "Last 30 days", "last-30-days")
    if kind in ("this year", "year"):
        return span(date(t.year, 1, 1), date(t.year + 1, 1, 1), str(t.year), str(t.year))
    if kind in ("last year", "lastyear"):
        return span(date(t.year - 1, 1, 1), date(t.year, 1, 1), str(t.year - 1), str(t.year - 1))
    if kind in ("all", "all time", "alltime"):
        return None, None, "All time", "all-time"
    if kind == "custom":
        try:
            a = d_from if isinstance(d_from, date) else datetime.strptime(str(d_from), "%Y-%m-%d").date()
            b = d_to if isinstance(d_to, date) else datetime.strptime(str(d_to), "%Y-%m-%d").date()
        except ValueError:
            raise ExportError("Choose valid from and to dates")
        if b < a:
            a, b = b, a
        lab = "%s to %s" % (a.strftime("%d %b %Y"), b.strftime("%d %b %Y"))
        return span(a, b + timedelta(days=1), lab, "%s_to_%s" % (a.isoformat(), b.isoformat()))
    raise ExportError("Unknown period: %s" % kind)


# --------------------------------------------------------------------------- building the workbook
def _styles():
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    thin = Side(style="thin", color="D5DBE3")
    return dict(
        head_font=Font(bold=True, color="FFFFFF"),
        head_fill=PatternFill("solid", fgColor=GREEN),
        soft=PatternFill("solid", fgColor=SOFT),
        bold=Font(bold=True),
        title=Font(bold=True, size=16, color=DARK),
        sub=Font(color="6B7785", size=10),
        h2=Font(bold=True, size=12, color=GREEN),
        center=Alignment(horizontal="center", vertical="center"),
        right=Alignment(horizontal="right"),
        top=Border(top=Side(style="medium", color=GREEN)),
        box=Border(bottom=thin),
    )


def _header(ws, row, headers, S, widths=None):
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=row, column=i, value=h)
        c.font, c.fill, c.alignment = S["head_font"], S["head_fill"], S["center"]
    if widths:
        from openpyxl.utils import get_column_letter
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w


def _split_ts(ts):
    d = datetime.strptime(ts, FMT)
    return d.date(), d.time().replace(microsecond=0)


def _ledger(wb, name, rows, S):
    """Income / Expenses sheet. Oldest first, totals use SUBTOTAL so they follow any filter you apply."""
    ws = wb.create_sheet(name)
    _header(ws, 1, ["Date", "Time", "Category", "Description", "Account", "M-Pesa code", "Amount (KSh)"], S,
            [12, 9, 24, 40, 18, 15, 17])
    for r in rows:
        d, t = _split_ts(r["ts"])
        ws.append([d, t, r["category"], r["description"] or "", r["source"], r["mpesa_code"] or "", float(r["amount"])])
    n = len(rows)
    for row in ws.iter_rows(min_row=2, max_row=n + 1):
        row[0].number_format = "yyyy-mm-dd"
        row[1].number_format = "hh:mm"
        row[6].number_format = MONEY
    tr = n + 3
    ws.cell(row=tr, column=6, value="Total").font = S["bold"]
    c = ws.cell(row=tr, column=7, value=("=SUBTOTAL(109,G2:G%d)" % (n + 1)) if n else 0)
    c.number_format, c.font = MONEY, S["bold"]
    for col in range(1, 8):
        ws.cell(row=tr, column=col).border = S["top"]
    ws.freeze_panes = "A2"
    if n:
        ws.auto_filter.ref = "A1:G%d" % (n + 1)
    return ws


def _transfers(wb, rows, S):
    ws = wb.create_sheet("Transfers")
    _header(ws, 1, ["Date", "Time", "From", "To", "Amount (KSh)", "Note", "M-Pesa code"], S, [12, 9, 20, 20, 17, 38, 15])
    for r in rows:
        d, t = _split_ts(r["ts"])
        ws.append([d, t, r["from_acc"], r["to_acc"], float(r["amount"]), r["note"] or "", r["code"] or ""])
    n = len(rows)
    for row in ws.iter_rows(min_row=2, max_row=n + 1):
        row[0].number_format, row[1].number_format, row[4].number_format = "yyyy-mm-dd", "hh:mm", MONEY
    tr = n + 3
    ws.cell(row=tr, column=4, value="Total moved").font = S["bold"]
    c = ws.cell(row=tr, column=5, value=("=SUBTOTAL(109,E2:E%d)" % (n + 1)) if n else 0)
    c.number_format, c.font = MONEY, S["bold"]
    ws.freeze_panes = "A2"
    if n:
        ws.auto_filter.ref = "A1:F%d" % (n + 1)
    ws.sheet_properties.tabColor = "60A5FA"
    return ws


def _months_between(first, last):
    y, m = int(first[:4]), int(first[5:7])
    ly, lm = int(last[:4]), int(last[5:7])
    out = []
    while (y, m) <= (ly, lm):
        out.append("%04d-%02d" % (y, m))
        y, m = y + (m == 12), (m % 12) + 1
    return out


def _monthly(wb, db, s, e, S):
    ws = wb.create_sheet("Monthly")
    rows = db.conn.execute("SELECT substr(ts,1,7) m, type, SUM(amount) t FROM transactions WHERE ts>=? AND ts<? "
                           "GROUP BY m, type", (s, e)).fetchall()
    data = {}
    for r in rows:
        data.setdefault(r["m"], {})[r["type"]] = float(r["t"] or 0)
    _header(ws, 1, ["Month", "Income (KSh)", "Expenses (KSh)", "Net (KSh)", "Saved %"], S, [14, 17, 17, 17, 10])
    months = _months_between(min(data), max(data)) if data else []
    for m in months:
        inc, exp = data.get(m, {}).get("income", 0.0), data.get(m, {}).get("expense", 0.0)
        ws.append([datetime(int(m[:4]), int(m[5:7]), 1).date(), inc, exp, inc - exp, ((inc - exp) / inc) if inc else None])
    n = len(months)
    for row in ws.iter_rows(min_row=2, max_row=n + 1):
        row[0].number_format = "mmm yyyy"
        for c in row[1:4]:
            c.number_format = MONEY
        row[4].number_format = "0%"
    if n:
        tr = n + 3
        ws.cell(row=tr, column=1, value="Total").font = S["bold"]
        for col, L in ((2, "B"), (3, "C"), (4, "D")):
            c = ws.cell(row=tr, column=col, value="=SUM(%s2:%s%d)" % (L, L, n + 1))
            c.number_format, c.font = MONEY, S["bold"]
        for col in range(1, 6):
            ws.cell(row=tr, column=col).border = S["top"]
        from openpyxl.chart import BarChart, Reference
        ch = BarChart()
        ch.type, ch.grouping = "col", "clustered"
        ch.title, ch.height, ch.width = "Income vs expenses", 8.5, 17
        ch.add_data(Reference(ws, min_col=2, max_col=3, min_row=1, max_row=n + 1), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=2, max_row=n + 1))
        ch.y_axis.numFmt = "#,##0"
        ch.y_axis.delete = False
        ch.x_axis.delete = False
        ch.x_axis.number_format = "mmm yy"
        ws.add_chart(ch, "G2")
    ws.freeze_panes = "A2"
    ws.sheet_properties.tabColor = "FBBF24"
    return ws


def _summary(ws, db, s, e, label, S):
    ws.sheet_view.showGridLines = False
    for col, w in zip("ABCDEFG", (30, 17, 17, 17, 17, 17, 17)):
        ws.column_dimensions[col].width = w
    ws["A1"] = "PesaPlan report"
    ws["A1"].font = S["title"]
    ws["A2"] = "Period: %s" % label
    ws["A3"] = "Created %s" % datetime.now().strftime("%d %b %Y, %H:%M")
    ws["A2"].font = ws["A3"].font = S["sub"]

    t = db.totals(s, e)
    inc, exp = float(t["inc"]), float(t["exp"])
    net = inc - exp
    r = 5
    ws.cell(row=r, column=1, value="Key figures").font = S["h2"]
    figs = [("Total income", inc, MONEY), ("Total expenses", exp, MONEY), ("Net (income - expenses)", net, MONEY),
            ("Saved (net / income)", (net / inc) if inc else None, "0.0%"),
            ("Transaction fees paid", float(t["fees"]), MONEY),
            ("Income entries", t["n_inc"], "0"), ("Expense entries", t["n_exp"], "0")]
    for i, (k, v, nf) in enumerate(figs, 1):
        ws.cell(row=r + i, column=1, value=k)
        c = ws.cell(row=r + i, column=2, value=v if v is not None else "-")
        c.number_format, c.font = nf, S["bold"]
        c.alignment = S["right"]
        if i <= 3:
            for col in (1, 2):
                ws.cell(row=r + i, column=col).fill = S["soft"]
    ws.cell(row=r + 3, column=2).font = S["bold"]
    r += len(figs) + 2

    # accounts
    ws.cell(row=r, column=1, value="Accounts").font = S["h2"]
    r += 1
    _header(ws, r, ["Account", "Opening", "Income", "Expenses", "Transfers in", "Transfers out", "Closing"], S)
    first = r + 1
    summ = db.month_summary(s, e)
    for a in summ:
        r += 1
        vals = [a["name"], a["opening"], a["income"], a["expense"], a["tin"], a["tout"], a["closing"]]
        for i, v in enumerate(vals, 1):
            c = ws.cell(row=r, column=i, value=v)
            if i > 1:
                c.number_format = MONEY
        ws.cell(row=r, column=7).font = S["bold"]
    if summ:
        r += 1
        ws.cell(row=r, column=1, value="All accounts").font = S["bold"]
        for col in range(2, 8):
            L = "BCDEFG"[col - 2]
            c = ws.cell(row=r, column=col, value="=SUM(%s%d:%s%d)" % (L, first, L, r - 1))
            c.number_format, c.font = MONEY, S["bold"]
        for col in range(1, 8):
            ws.cell(row=r, column=col).border = S["top"]
        r += 1
        ws.cell(row=r, column=1, value="Transfers between your own accounts net to zero; they are not income or spending.").font = S["sub"]
    else:
        r += 1
        ws.cell(row=r, column=1, value="No accounts yet").font = S["sub"]
    r += 2

    # categories
    for typ, title, total in (("income", "Income by category", inc), ("expense", "Expenses by category", exp)):
        ws.cell(row=r, column=1, value=title).font = S["h2"]
        r += 1
        _header(ws, r, ["Category", "Amount (KSh)", "Share"], S)
        cats = db.by_category(typ, s, e)
        for c_ in cats:
            r += 1
            ws.cell(row=r, column=1, value=c_["category"])
            a = ws.cell(row=r, column=2, value=float(c_["t"]))
            a.number_format = MONEY
            p = ws.cell(row=r, column=3, value=(float(c_["t"]) / total) if total else 0)
            p.number_format = "0.0%"
        if not cats:
            r += 1
            ws.cell(row=r, column=1, value="Nothing in this period").font = S["sub"]
        r += 2
    ws.sheet_properties.tabColor = GREEN


def build_workbook(db, start, end, label):
    """db is a pesaplan.Database. start/end: 'YYYY-MM-DD HH:MM:SS' (end excluded) or None for no limit."""
    ox = _openpyxl()
    S = _styles()
    s, e = start or MIN_TS, end or MAX_TS
    wb = ox.Workbook()
    _summary(wb.active, db, s, e, label, S)
    wb.active.title = "Summary"
    inc_rows = list(reversed(db.query(s, e, "income")))
    exp_rows = list(reversed(db.query(s, e, "expense")))
    _ledger(wb, "Income", inc_rows, S).sheet_properties.tabColor = "34D399"
    _ledger(wb, "Expenses", exp_rows, S).sheet_properties.tabColor = "F87171"
    _transfers(wb, list(reversed(db.transfers(s, e))), S)
    _monthly(wb, db, s, e, S)
    wb.properties.title = "PesaPlan - %s" % label
    wb.properties.creator = "PesaPlan"
    return wb


def save_workbook(db, path, start, end, label):
    wb = build_workbook(db, start, end, label)
    wb.save(path)
    return path


def workbook_bytes(db, start, end, label):
    buf = io.BytesIO()
    build_workbook(db, start, end, label).save(buf)
    return buf.getvalue()


def from_query(db, q):
    """For the phone web server. q is a parse_qs-style dict. Returns (bytes, suggested_filename)."""
    def one(k):
        return (q.get(k) or [""])[0].strip()
    start, end, label, slug = resolve_period(one("range"), month=one("month") or None,
                                             d_from=one("from") or None, d_to=one("to") or None)
    return workbook_bytes(db, start, end, label), "PesaPlan_%s.xlsx" % slug


def counts(db, start, end):
    s, e = start or MIN_TS, end or MAX_TS
    t = db.totals(s, e)
    return t["n_inc"], t["n_exp"], float(t["inc"]), float(t["exp"])


# --------------------------------------------------------------------------- desktop page
def build_page(P, win, db):
    from PyQt5.QtCore import QDate, Qt, QUrl
    from PyQt5.QtGui import QDesktopServices
    from PyQt5.QtWidgets import (QComboBox, QDateEdit, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                                 QScrollArea, QVBoxLayout, QWidget)

    class ExportPage(QScrollArea):
        def __init__(self):
            super().__init__()
            self.setWidgetResizable(True)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            body = QWidget(); body.setObjectName("pagebody")
            self.setWidget(body)
            lay = QVBoxLayout(body)
            lay.setContentsMargins(28, 4, 28, 28); lay.setSpacing(16)

            box = QWidget()
            bl = QVBoxLayout(box); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(10)
            row = QHBoxLayout(); row.setSpacing(10)
            self.range = QComboBox(); self.range.addItems(PERIODS)
            self.d_from, self.d_to = QDateEdit(), QDateEdit()
            for d in (self.d_from, self.d_to):
                d.setCalendarPopup(True); d.setDisplayFormat("dd MMM yyyy"); d.setDate(QDate.currentDate())
                d.setVisible(False); d.setMinimumWidth(135)
            self.d_from.setDate(QDate.currentDate().addDays(-29))
            row.addWidget(QLabel("Period")); row.addWidget(self.range); row.addWidget(self.d_from)
            row.addWidget(self.d_to); row.addStretch()
            self.info = QLabel(); self.info.setObjectName("cardSub"); self.info.setWordWrap(True)
            self.btn = QPushButton("Download Excel…"); self.btn.setObjectName("primary")
            self.btn.setMinimumHeight(38)
            bl.addLayout(row); bl.addWidget(self.info)
            hb = QHBoxLayout(); hb.addWidget(self.btn); hb.addStretch(); bl.addLayout(hb)
            lay.addWidget(P.make_card("Export to Excel", box, "Income and expenses for any period, as a spreadsheet"))

            what = QLabel(
                "<b>Summary</b>: key figures, each account's opening and closing balance, income and spending by category<br>"
                "<b>Income</b> and <b>Expenses</b>: one row per entry with real dates, filters, and totals that follow your filter<br>"
                "<b>Transfers</b>: money moved between your own accounts<br>"
                "<b>Monthly</b>: income vs expenses for each month, with a chart")
            what.setObjectName("cardSub"); what.setWordWrap(True); what.setTextFormat(Qt.RichText)
            lay.addWidget(P.make_card("What's in the file", what))
            lay.addStretch(1)

            self.range.currentIndexChanged.connect(self._changed)
            self.d_from.dateChanged.connect(self.refresh)
            self.d_to.dateChanged.connect(self.refresh)
            self.btn.clicked.connect(self.download)
            self.refresh()

        def _period(self):
            custom = self.range.currentText() == "Custom"
            self.d_from.setVisible(custom); self.d_to.setVisible(custom)
            return resolve_period(self.range.currentText(),
                                  d_from=self.d_from.date().toPyDate(), d_to=self.d_to.date().toPyDate())

        def _changed(self):
            self.refresh()

        def refresh(self):
            try:
                s, e, label, _ = self._period()
                ni, ne, inc, exp = counts(db, s, e)
                self.info.setText("%s: %d income %s (KSh %s) and %d expense %s (KSh %s)."
                                  % (label, ni, "entry" if ni == 1 else "entries", format(inc, ",.2f"),
                                     ne, "entry" if ne == 1 else "entries", format(exp, ",.2f")))
                self.btn.setEnabled(True)
            except ExportError as ex:
                self.info.setText(str(ex))
                self.btn.setEnabled(False)

        def download(self):
            try:
                s, e, label, slug = self._period()
                docs = os.path.expanduser("~/Documents")
                folder = docs if os.path.isdir(docs) else os.path.expanduser("~")
                path, _ = QFileDialog.getSaveFileName(self, "Save Excel file",
                                                      os.path.join(folder, "PesaPlan_%s.xlsx" % slug),
                                                      "Excel workbook (*.xlsx)")
                if not path:
                    return
                if not path.lower().endswith(".xlsx"):
                    path += ".xlsx"
                save_workbook(db, path, s, e, label)
            except ExportError as ex:
                QMessageBox.warning(self, P.APP_NAME, str(ex))
                return
            except OSError as ex:
                QMessageBox.warning(self, P.APP_NAME, "Couldn't save the file:\n%s\n\nIs it open in Excel?" % ex)
                return
            if QMessageBox.question(self, P.APP_NAME, "Saved:\n%s\n\nOpen it now?" % path) == QMessageBox.Yes:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    return ExportPage()


def install(win, P):
    """Called once from pesaplan.main(): adds the Export page to the sidebar."""
    page = build_page(P, win, win.db)
    win.add_nav_page("📥", "Export", "Download income and expenses as an Excel spreadsheet", page)
    return page
