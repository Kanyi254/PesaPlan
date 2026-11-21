"""
pesaplan_phone.py - phone access for PesaPlan.

Put this file next to pesaplan.py. It adds:
  * a small web server inside PesaPlan (open it from your phone's browser)
  * a mobile page to view balances/charts, add, edit and delete transactions, and make transfers
  * POST /api/sms - lets an Android SMS-forwarder app push M-Pesa messages in automatically
  * a "Phone" page in the desktop app with the link, QR code and setup steps

The server only runs while PesaPlan is open, and every request needs the secret token in your link.
"""
import json
import secrets
import socket
import sqlite3
import threading
from calendar import monthrange
from datetime import datetime, date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from PyQt5.QtCore import Qt, QObject, pyqtSignal, QRectF
from PyQt5.QtGui import QImage, QPainter, QColor, QPixmap
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
                             QSpinBox, QScrollArea, QFrame, QMessageBox)

DEFAULT_PORT = 8765


# --------------------------------------------------------------------------- SMS import (shared logic)
def import_mpesa_text(db, P, text, account=None):
    """Thin wrapper: pick the account, then use the shared importer in pesaplan.Database."""
    acc = account if account and db.account(account) else (
        db.first_account_of_kind("M-Pesa") or (db.account_names(True) or [None])[0])
    if not acc:
        return {"imported": 0, "duplicates": 0, "unrecognised": [], "error": "No account to import into"}
    return db.import_mpesa_text(text, acc)


# --------------------------------------------------------------------------- HTTP server
class _Ctx:
    """Everything the request handler needs. The DB connection is created lazily inside the server thread."""

    def __init__(self, P, db_path, token, notify):
        self.P, self.db_path, self.token, self.notify = P, db_path, token, notify
        self._db = None

    @property
    def db(self):
        if self._db is None:
            self._db = self.P.Database(self.db_path)
        return self._db


class ApiError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def _dt(P, value):
    if not value:
        return datetime.now().strftime(P.FMT)
    v = str(value).strip()
    if v[-1:] in ("Z", "z"):
        v = v[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(v.replace(" ", "T", 1))
    except ValueError:
        raise ApiError(400, "Bad date/time: %s" % value)
    if d.tzinfo is not None:
        d = d.astimezone()              # convert to this PC's local zone before dropping the offset
    return d.replace(tzinfo=None, microsecond=0).strftime(P.FMT)


def _num(v, name="amount"):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ApiError(400, "Bad %s" % name)
    if x <= 0:
        raise ApiError(400, "%s must be greater than zero" % name.capitalize())
    return x


def _month(P, s):
    t = date.today()
    y, m = t.year, t.month
    if s:
        try:
            y, m = [int(x) for x in s.split("-")]
            datetime(y, m, 1)
        except Exception:
            raise ApiError(400, "Bad month")
    return y, m


def make_handler(ctx):
    P = ctx.P

    class Handler(BaseHTTPRequestHandler):
        server_version = "PesaPlan"
        timeout = 15

        def log_message(self, *a):
            pass

        # -- plumbing
        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                             "img-src 'self' data:; connect-src 'self'; manifest-src 'self'; "
                             "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj))

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(min(n, 2_000_000)) if n else b""

        def _authorised(self, q):
            tok = (q.get("t") or [""])[0] or self.headers.get("X-Token", "")
            auth = self.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                tok = tok or auth[7:].strip()
            return bool(tok) and secrets.compare_digest(tok, ctx.token)

        def _route(self, method):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            path = u.path.rstrip("/") or "/"
            try:
                if method == "GET" and path == "/":
                    return self._send(200, INDEX_HTML, "text/html; charset=utf-8")
                if method == "GET" and path == "/manifest.json":
                    return self._send(200, MANIFEST, "application/manifest+json")
                if method == "GET" and path == "/icon.svg":
                    return self._send(200, ICON_SVG, "image/svg+xml")
                if not path.startswith("/api/"):
                    raise ApiError(404, "Not found")
                if not self._authorised(q):
                    raise ApiError(401, "Unauthorised - open the link shown in PesaPlan's Phone page")
                if method == "GET" and path == "/api/export":
                    return self._export(q)
                out = self._api(method, path, q)
                self._json(out)
            except ApiError as e:
                self._json({"error": e.msg}, e.code)
            except sqlite3.IntegrityError as e:
                self._json({"error": "Duplicate or invalid data: %s" % e}, 400)
            except Exception as e:  # never kill the server thread
                self._json({"error": "Server error: %s" % e}, 500)

        def _export(self, q):
            try:
                import pesaplan_export
                data, name = pesaplan_export.from_query(ctx.db, q)
            except ImportError:
                raise ApiError(501, "Put pesaplan_export.py next to pesaplan.py on the PC, then restart PesaPlan")
            except pesaplan_export.ExportError as e:
                raise ApiError(400, str(e).replace("\n", " "))
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % name)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_PUT(self):
            self._route("PUT")

        def do_DELETE(self):
            self._route("DELETE")

        # -- API
        def _json_body(self):
            raw = self._body()
            try:
                return json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                raise ApiError(400, "Body must be JSON")

        def _api(self, method, path, q):
            db = ctx.db
            parts = path.split("/")[2:]          # after /api/
            head = parts[0] if parts else ""
            tid = parts[1] if len(parts) > 1 else None

            if method == "GET" and head == "meta":
                return {"accounts": [{"name": a["name"], "kind": a["kind"], "closed": bool(a["closed_on"])}
                                     for a in db.accounts()],
                        "expense_cats": P.EXPENSE_CATS, "income_cats": P.INCOME_CATS,
                        "now": datetime.now().strftime(P.FMT)}

            if method == "GET" and head == "summary":
                y, m = _month(P, (q.get("month") or [""])[0])
                s, e = P.month_bounds(y, m)
                summ = db.month_summary(s, e)
                t = db.totals(s, e)
                budgets, spent = db.budgets(), {r["category"]: r["t"] for r in db.by_category("expense", s, e)}
                return {
                    "month": "%04d-%02d" % (y, m), "label": date(y, m, 1).strftime("%B %Y"),
                    "opening": sum(a["opening"] for a in summ), "closing": sum(a["closing"] for a in summ),
                    "income": t["inc"], "expense": t["exp"], "fees": t["fees"],
                    "accounts": [{k: a[k] for k in ("name", "kind", "opening", "income", "expense", "tin", "tout",
                                                    "closing", "opened_on", "closed_on")} for a in summ],
                    "expense_by_cat": [{"name": r["category"], "total": r["t"], "color": P.cat_color(r["category"])}
                                       for r in db.by_category("expense", s, e)],
                    "income_by_cat": [{"name": r["category"], "total": r["t"], "color": P.cat_color(r["category"])}
                                      for r in db.by_category("income", s, e)],
                    "budgets": [{"category": c, "limit": l, "spent": spent.get(c, 0)} for c, l in budgets.items()],
                    "mpesa_balance": db.last_mpesa_balance(),
                }

            if method == "GET" and head == "transactions":
                y, m = _month(P, (q.get("month") or [""])[0])
                s, e = P.month_bounds(y, m)
                text = (q.get("q") or [""])[0].strip() or None
                acc = (q.get("account") or [""])[0] or None
                typ = (q.get("type") or [""])[0] or None
                items = [{"kind": "tx", "id": r["id"], "ts": r["ts"], "type": r["type"], "amount": r["amount"],
                          "category": r["category"], "description": r["description"] or "", "account": r["source"],
                          "code": r["mpesa_code"] or ""} for r in db.query(s, e, typ, acc, None, text)]
                if not typ and not text:
                    for r in db.transfers(s, e):
                        if acc and acc not in (r["from_acc"], r["to_acc"]):
                            continue
                        items.append({"kind": "transfer", "id": r["id"], "ts": r["ts"], "amount": r["amount"],
                                      "from": r["from_acc"], "to": r["to_acc"], "description": r["note"] or ""})
                items.sort(key=lambda x: (x["ts"], x["id"]), reverse=True)
                return {"items": items[:500]}

            if method == "POST" and head == "transactions":
                b = self._json_body()
                typ = b.get("type")
                if typ not in ("income", "expense"):
                    raise ApiError(400, "type must be income or expense")
                acc = b.get("account")
                if not db.account(acc):
                    raise ApiError(400, "Unknown account")
                fee = float(b.get("fee") or 0)
                rid = db.add(_dt(P, b.get("ts")), typ, _num(b.get("amount")), (b.get("category") or "Other").strip(),
                             (b.get("description") or "").strip(), acc, (b.get("code") or "").strip().upper() or None,
                             fee if typ == "expense" else 0.0)
                ctx.notify()
                return {"ok": True, "id": rid}

            if method == "PUT" and head == "transactions" and tid:
                b = self._json_body()
                row = db.get(int(tid))
                if not row:
                    raise ApiError(404, "No such transaction")
                typ = b.get("type", row["type"])
                acc = b.get("account", row["source"])
                if typ not in ("income", "expense") or not db.account(acc):
                    raise ApiError(400, "Bad type or account")
                db.update(int(tid), _dt(P, b.get("ts", row["ts"])), typ, _num(b.get("amount", row["amount"])),
                          (b.get("category", row["category"]) or "Other").strip(),
                          (b.get("description", row["description"]) or "").strip(), acc, row["mpesa_code"])
                ctx.notify()
                return {"ok": True}

            if method == "DELETE" and head == "transactions" and tid:
                db.delete(int(tid))
                ctx.notify()
                return {"ok": True}

            if method == "POST" and head == "transfers":
                b = self._json_body()
                frm, to = b.get("from"), b.get("to")
                if not db.account(frm) or not db.account(to) or frm == to:
                    raise ApiError(400, "Choose two different accounts")
                rid = db.add_transfer(_dt(P, b.get("ts")), frm, to, _num(b.get("amount")),
                                      (b.get("note") or "").strip(), fee=float(b.get("fee") or 0))
                ctx.notify()
                return {"ok": True, "id": rid}

            if method == "DELETE" and head == "transfers" and tid:
                db.delete_transfer(int(tid))
                ctx.notify()
                return {"ok": True}

            if method == "POST" and head == "sms":
                raw = self._body().decode("utf-8", "replace")
                ctype = (self.headers.get("Content-Type") or "").lower()
                text, account = raw, (q.get("account") or [None])[0] or self.headers.get("X-Account")
                if "json" in ctype or raw.lstrip().startswith("{"):
                    try:
                        obj = json.loads(raw)
                        text = next((obj[k] for k in ("text", "message", "body", "sms", "content") if obj.get(k)), "")
                        account = obj.get("account") or account
                    except Exception:
                        pass
                elif "x-www-form-urlencoded" in ctype:
                    f = parse_qs(raw)
                    text = next((f[k][0] for k in ("text", "message", "body", "sms", "content") if f.get(k)), raw)
                res = import_mpesa_text(db, P, text, account)
                if res["imported"]:
                    ctx.notify()
                res["ok"] = "error" not in res
                return res

            raise ApiError(404, "Unknown API call")

    return Handler


class PhoneService(QObject):
    """Starts/stops the web server. `changed` fires (on the GUI thread) whenever the phone modifies data."""
    changed = pyqtSignal()

    def __init__(self, P, db_path):
        super().__init__()
        self.P, self.db_path = P, db_path
        self.server, self.thread, self.port, self.token = None, None, None, ""

    @property
    def running(self):
        return self.server is not None

    def start(self, port, token):
        if self.running:
            return
        ctx = _Ctx(self.P, self.db_path, token, self.changed.emit)
        self.server = HTTPServer(("0.0.0.0", port), make_handler(ctx))   # raises OSError if the port is taken
        self.port, self.token = port, token
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.3}, daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=3)
        self.server, self.thread, self.port = None, None, None


def local_ips():
    """LAN addresses, best first: default-route address, then 192.168/10/172.16, then Tailscale (100.64/10)."""
    import ipaddress
    found, primary = [], None

    def add(ip):
        if ip and ip not in found:
            found.append(ip)

    for target in ("8.8.8.8", "10.255.255.255"):           # connect() on UDP sends nothing
        try:
            sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sk.settimeout(0.3)
            sk.connect((target, 1))
            ip = sk.getsockname()[0]
            sk.close()
            primary = primary or ip
            add(ip)
        except OSError:
            pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass
    try:                                                    # optional: pip install psutil
        import psutil
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family == socket.AF_INET:
                    add(a.address)
    except Exception:
        pass

    def rank(ip):
        if ip == primary:
            return 0
        a = ipaddress.ip_address(ip)
        if a.is_loopback or a.is_link_local:
            return 99
        if a in ipaddress.ip_network("192.168.0.0/16"):
            return 1
        if a in ipaddress.ip_network("10.0.0.0/8"):
            return 2
        if a in ipaddress.ip_network("172.16.0.0/12"):
            return 3
        if a in ipaddress.ip_network("100.64.0.0/10"):
            return 4
        return 5

    good = sorted((ip for ip in found if rank(ip) < 99), key=rank)
    return good or ["127.0.0.1"]


# --------------------------------------------------------------------------- desktop "Phone" page
def _qr_pixmap(text, scale=6):
    try:
        import qrcode
    except ImportError:
        return None
    q = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(text)
    q.make(fit=True)
    m = q.get_matrix()
    n = len(m)
    img = QImage(n * scale, n * scale, QImage.Format_RGB32)
    img.fill(QColor("#FFFFFF"))
    p = QPainter(img)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#0B0F14"))
    for y, row in enumerate(m):
        for x, v in enumerate(row):
            if v:
                p.drawRect(x * scale, y * scale, scale, scale)
    p.end()
    return QPixmap.fromImage(img)


def build_page(P, svc, db):
    class PhonePage(QScrollArea):
        changed = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.setWidgetResizable(True)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            body = QWidget(); body.setObjectName("pagebody")
            self.setWidget(body)
            lay = QVBoxLayout(body)
            lay.setContentsMargins(28, 4, 28, 28); lay.setSpacing(16)

            # status / controls
            top = QWidget()
            tl = QVBoxLayout(top); tl.setContentsMargins(0, 0, 0, 0); tl.setSpacing(10)
            self.status = QLabel(); self.status.setStyleSheet("font-size:15px; font-weight:700;")
            row = QHBoxLayout(); row.setSpacing(10)
            self.btn = QPushButton(); self.btn.setObjectName("primary")
            pl = QLabel("Port"); pl.setObjectName("cardSub")
            self.port = QSpinBox(); self.port.setRange(1024, 65535)
            self.port.setValue(int(db.get_setting("phone_port", DEFAULT_PORT)))
            self.port.setStyleSheet("QSpinBox{background:%s;border:1px solid %s;border-radius:10px;padding:8px 10px;}"
                                    % (P.CARD2, P.BORDER))
            regen = QPushButton("New secret link"); regen.setObjectName("ghost")
            row.addWidget(self.btn); row.addWidget(pl); row.addWidget(self.port); row.addStretch(); row.addWidget(regen)
            tl.addWidget(self.status); tl.addLayout(row)
            lay.addWidget(P.make_card("Phone access", top,
                                      "Open PesaPlan on your phone's browser while both are on the same Wi-Fi"))

            # links + qr
            self.link_box = QWidget()
            self.link_lay = QVBoxLayout(self.link_box); self.link_lay.setContentsMargins(0, 0, 0, 0)
            self.link_lay.setSpacing(8)
            lay.addWidget(P.make_card("Your phone link", self.link_box,
                                      "Open it once on your phone, then use “Add to Home screen” in Chrome's menu"))

            # sms
            self.sms_box = QLabel(); self.sms_box.setWordWrap(True)
            self.sms_box.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.sms_box.setObjectName("cardSub")
            lay.addWidget(P.make_card("Auto-import M-Pesa SMS (Android)", self.sms_box,
                                      "Your phone sends each M-Pesa message to PesaPlan the moment it arrives"))

            tips = QLabel(
                "<b>Use it away from home:</b> install the free <i>Tailscale</i> app on both this PC and your phone "
                "(same login). Then use the PC's Tailscale address (100.x.x.x) instead of the Wi-Fi address; "
                "the link works from anywhere, encrypted, while this PC is on and PesaPlan is open.<br><br>"
                "<b>Windows firewall:</b> the first time you start sharing, Windows asks whether to allow access. "
                "Tick <i>Private networks</i> only.<br><br>"
                "<b>Safety:</b> anyone who has your link can see and change your data, so don't share it. "
                "“New secret link” invalidates the old one.")
            tips.setWordWrap(True); tips.setObjectName("cardSub"); tips.setTextFormat(Qt.RichText)
            lay.addWidget(P.make_card("Good to know", tips))
            lay.addStretch()

            self.btn.clicked.connect(self.toggle)
            regen.clicked.connect(self.regen)
            self.port.valueChanged.connect(lambda v: db.set_setting("phone_port", v))
            self.refresh()

        def token(self):
            t = db.get_setting("phone_token")
            if not t:
                t = secrets.token_urlsafe(18)
                db.set_setting("phone_token", t)
            return t

        def toggle(self):
            if svc.running:
                svc.stop(); db.set_setting("phone_enabled", "0")
            else:
                try:
                    svc.start(self.port.value(), self.token()); db.set_setting("phone_enabled", "1")
                except OSError as e:
                    QMessageBox.warning(self, P.APP_NAME, "Couldn't start sharing on port %d:\n%s\n\nTry another port."
                                        % (self.port.value(), e))
            self.refresh()

        def regen(self):
            if QMessageBox.question(self, P.APP_NAME, "Make a new secret link? Your phone link and any SMS "
                                    "forwarder will stop working until you update them.") != QMessageBox.Yes:
                return
            db.set_setting("phone_token", secrets.token_urlsafe(18))
            if svc.running:
                svc.stop(); svc.start(self.port.value(), self.token())
            self.refresh()

        def refresh(self):
            run = svc.running
            self.status.setText(("●  Sharing is ON  ·  port %d" % svc.port) if run else "●  Sharing is OFF")
            self.status.setStyleSheet("font-size:15px; font-weight:700; color:%s;" % (P.INCOME if run else P.MUTED))
            self.btn.setText("Stop sharing" if run else "Start sharing")
            self.port.setEnabled(not run)
            P.clear_layout(self.link_lay)
            if not run:
                l = QLabel("Click “Start sharing” to get your link."); l.setObjectName("muted")
                self.link_lay.addWidget(l)
                self.sms_box.setText("Start sharing first.")
                return
            tok = self.token()
            links = ["http://%s:%d/?t=%s" % (ip, svc.port, tok) for ip in local_ips()]
            for url in links:
                r = QHBoxLayout()
                e = QLineEdit(url); e.setReadOnly(True)
                c = QPushButton("Copy"); c.setObjectName("ghost")
                c.clicked.connect(lambda _, u=url: QApplication.clipboard().setText(u))
                r.addWidget(e, 1); r.addWidget(c)
                w = QWidget(); w.setLayout(r)
                self.link_lay.addWidget(w)
            pm = _qr_pixmap(links[0])
            if pm:
                q = QLabel(); q.setPixmap(pm.scaled(190, 190, Qt.KeepAspectRatio, Qt.FastTransformation))
                self.link_lay.addWidget(q)
                h = QLabel("Scan with your phone's camera. The first address is your Wi-Fi address.")
                h.setObjectName("cardSub"); self.link_lay.addWidget(h)
            else:
                h = QLabel("Tip: run  pip install qrcode  and restart to show a QR code you can scan.")
                h.setObjectName("cardSub"); self.link_lay.addWidget(h)
            sms_url = "http://%s:%d/api/sms?t=%s" % (local_ips()[0], svc.port, tok)
            self.sms_box.setText(
                "In an Android automation or SMS-forwarder app (MacroDroid, Tasker, “SMS Forwarder”…), create a rule:\n"
                "  When: SMS received from  MPESA\n"
                "  Do:   HTTP POST to  %s\n"
                "        body = the message text  (plain text, or JSON like {\"text\": \"<message>\"})\n\n"
                "Duplicates are ignored automatically, and messages are saved to your M-Pesa account "
                "(add  &account=Name  to the URL to use a different one)." % sms_url)

    return PhonePage()


def install(win, P, db_path):
    """Called once from pesaplan.main(): adds the Phone page to the sidebar and auto-starts sharing if enabled."""
    db = win.db
    svc = PhoneService(P, db_path)
    svc.changed.connect(win.refresh_all)
    page = build_page(P, svc, db)

    win.add_nav_page("📱", "Phone", "Use PesaPlan on your phone and auto-import M-Pesa SMS", page)

    if db.get_setting("phone_enabled") == "1":
        try:
            svc.start(page.port.value(), page.token())
        except OSError:
            db.set_setting("phone_enabled", "0")
    page.refresh()
    app = QApplication.instance()
    if app:
        app.aboutToQuit.connect(svc.stop)
    win._phone = svc
    return svc


# --------------------------------------------------------------------------- web app assets
MANIFEST = json.dumps({
    "name": "PesaPlan", "short_name": "PesaPlan", "start_url": "/", "display": "standalone",
    "background_color": "#0E1117", "theme_color": "#0E1117",
    "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}],
})

ICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96"><defs><linearGradient id="g" x1="0" y1="0" '
            'x2="1" y2="1"><stop offset="0" stop-color="#34D399"/><stop offset="1" stop-color="#059669"/>'
            '</linearGradient></defs><rect width="96" height="96" rx="22" fill="url(#g)"/>'
            '<text x="48" y="66" text-anchor="middle" font-family="Arial" font-weight="800" font-size="56" '
            'fill="#04210F">P</text></svg>')

INDEX_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0E1117"><meta name="referrer" content="no-referrer">
<link rel="manifest" href="/manifest.json"><link rel="icon" href="/icon.svg">
<title>PesaPlan</title>
<style>
:root{--bg:#0E1117;--side:#0A0D12;--card:#161B22;--card2:#1D2430;--bd:#252E3C;--tx:#E6EDF3;--mu:#8B98A9;
--ac:#2ECC71;--inc:#34D399;--exp:#F87171;--warn:#FBBF24;--blue:#60A5FA;--pur:#A78BFA}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.35 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
body{padding-bottom:calc(84px + env(safe-area-inset-bottom))}
button{font:inherit;color:inherit;cursor:pointer}
.top{position:sticky;top:0;z-index:5;background:linear-gradient(var(--bg) 85%,transparent);padding:calc(14px + env(safe-area-inset-top)) 16px 10px}
.row{display:flex;align-items:center;gap:10px}.sp{flex:1}
.brand{font-weight:800;font-size:20px;display:flex;align-items:center;gap:9px}
.logo{width:30px;height:30px;border-radius:9px;background:linear-gradient(135deg,#34D399,#059669);color:#04210F;display:grid;place-items:center;font-weight:800}
.ibtn{background:var(--card2);border:1px solid var(--bd);border-radius:12px;min-width:40px;height:40px;font-size:17px;font-weight:700}
.ibtn:active{border-color:var(--ac)}
.month{display:flex;align-items:center;gap:6px;margin-top:10px}
.month b{flex:1;text-align:center;font-size:16px}
html,body{max-width:100vw;overflow-x:hidden}
.wrap{padding:0 16px 8px;display:grid;grid-template-columns:minmax(0,1fr);gap:14px}
.wrap>*{min-width:0}
.card{background:var(--card);border:1px solid var(--bd);border-radius:18px;padding:16px}
.card h3{margin:0 0 2px;font-size:15px}.sub{color:var(--mu);font-size:12px}
.hero{background:linear-gradient(135deg,#143a2b,#161B22 70%);}
.big{font-size:30px;font-weight:800;letter-spacing:-.5px;margin:4px 0}
.tiles{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px}
.tile{background:var(--card);border:1px solid var(--bd);border-radius:16px;padding:13px 14px}
.tile .v{font-size:18px;font-weight:800;margin-top:2px}
.pos{color:var(--inc)}.neg{color:var(--exp)}
.line{display:flex;align-items:center;gap:12px;padding:11px 0;border-bottom:1px solid var(--bd)}
.line:last-child{border-bottom:0}
.badge{width:40px;height:40px;border-radius:12px;display:grid;place-items:center;font-weight:800;flex:none}
.grow{flex:1;min-width:0}.grow .t{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.amt{font-weight:800;white-space:nowrap}
.meter{height:9px;background:#263041;border-radius:6px;overflow:hidden;margin:6px 0 3px}.meter i{display:block;height:100%;border-radius:6px}
.legend{display:grid;gap:7px;margin-top:10px}.legend div{display:flex;align-items:center;gap:8px;font-size:13px}
.dot{width:10px;height:10px;border-radius:4px;flex:none}
.tabs{position:fixed;left:0;right:0;bottom:0;z-index:6;background:rgba(10,13,18,.96);backdrop-filter:blur(10px);border-top:1px solid var(--bd);
display:grid;grid-template-columns:1fr 1fr 1fr;padding:6px 8px calc(8px + env(safe-area-inset-bottom))}
.tabs button{background:none;border:0;padding:7px 4px;color:var(--mu);font-weight:700;font-size:12px;display:grid;gap:2px;justify-items:center;border-radius:12px}
.tabs button span{font-size:19px}.tabs button.on{color:var(--ac)}
.fab{position:fixed;right:18px;bottom:calc(78px + env(safe-area-inset-bottom));z-index:7;width:58px;height:58px;border-radius:20px;border:0;
background:var(--ac);color:#06210F;font-size:30px;font-weight:700;box-shadow:0 8px 24px rgba(46,204,113,.35)}
.fab:active{transform:scale(.95)}
.search{width:100%;background:var(--card2);border:1px solid var(--bd);border-radius:12px;padding:11px 13px;color:var(--tx);font:inherit}
.chips{display:flex;gap:8px;overflow:auto;padding:2px 0}.chip{flex:none;background:var(--card2);border:1px solid var(--bd);border-radius:999px;padding:6px 13px;font-size:13px;font-weight:600;color:var(--mu)}
.chip.on{border-color:var(--ac);color:var(--ac);background:rgba(46,204,113,.12)}
.ov{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:20;display:none;align-items:flex-end}
.ov.on{display:flex}
.sheet{background:var(--card);width:100%;max-height:92vh;overflow:auto;border-radius:24px 24px 0 0;padding:18px 16px calc(20px + env(safe-area-inset-bottom));border:1px solid var(--bd);animation:up .18s ease-out}
@keyframes up{from{transform:translateY(40px)}to{transform:none}}
.sheet h2{margin:0 0 12px;font-size:19px}
.seg{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;margin-bottom:14px}
.seg button{background:var(--card2);border:1px solid var(--bd);padding:11px;font-weight:700;color:var(--mu)}
.seg button:first-child{border-radius:12px 0 0 12px}.seg button:last-child{border-radius:0 12px 12px 0}
.seg button.on.e{background:rgba(248,113,113,.18);color:var(--exp);border-color:var(--exp)}
.seg button.on.i{background:rgba(52,211,153,.18);color:var(--inc);border-color:var(--inc)}
.seg button.on.t{background:rgba(96,165,250,.18);color:var(--blue);border-color:var(--blue)}
label{display:block;font-size:12px;color:var(--mu);font-weight:600;margin:11px 0 5px}
input,select,textarea{width:100%;background:var(--card2);border:1px solid var(--bd);border-radius:12px;padding:12px 13px;color:var(--tx);font:inherit;appearance:none;-webkit-appearance:none}
input:focus,select:focus,textarea:focus{outline:0;border-color:var(--ac)}
.amount{font-size:26px;font-weight:800}
.btns{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:18px}
.btn{border-radius:13px;padding:13px;border:1px solid var(--bd);background:var(--card2);font-weight:700}
.btn.p{background:var(--ac);border-color:var(--ac);color:#06210F}.btn.d{background:rgba(248,113,113,.12);border-color:rgba(248,113,113,.4);color:var(--exp)}
.toast{position:fixed;left:50%;top:calc(14px + env(safe-area-inset-top));transform:translate(-50%,-90px);background:var(--card2);border:1px solid var(--bd);padding:11px 18px;border-radius:14px;z-index:40;font-weight:600;transition:transform .25s;max-width:90%}
.toast.on{transform:translate(-50%,0)}.toast.err{border-color:var(--exp);color:var(--exp)}
.lock{padding:70px 24px;text-align:center}.lock .logo{width:60px;height:60px;border-radius:18px;margin:0 auto 18px;font-size:30px}
.empty{color:var(--mu);text-align:center;padding:22px 0}
.dayh{color:var(--mu);font-size:12px;font-weight:700;letter-spacing:.4px;margin:8px 2px -4px}
.acc .k{display:flex;justify-content:space-between;font-size:13px;color:var(--mu);margin-top:5px}.acc .k b{color:var(--tx)}
.pill{font-size:10px;font-weight:800;border-radius:8px;padding:3px 8px}
</style></head><body>
<div id="app"></div>
<div class="toast" id="toast"></div>
<div class="ov" id="ov"><div class="sheet" id="sheet"></div></div>
<script>
"use strict";
const $=s=>document.querySelector(s);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt=(v,d=2)=>Number(v||0).toLocaleString("en-KE",{minimumFractionDigits:d,maximumFractionDigits:d});
const kes=v=>(v<0?"-":"")+"KSh "+fmt(Math.abs(v));
const short=v=>{v=Math.abs(v);return v>=1e6?(v/1e6).toFixed(1).replace(/\.0$/,"")+"M":v>=1e3?(v/1e3).toFixed(1).replace(/\.0$/,"")+"K":Math.round(v)};
let TOKEN="";try{const p=new URLSearchParams(location.search);TOKEN=p.get("t")||localStorage.getItem("pp_token")||"";
if(p.get("t")){localStorage.setItem("pp_token",TOKEN);history.replaceState({},"",location.pathname)}}catch(e){}
const st={tab:"home",month:null,meta:null,sum:null,items:[],q:"",type:"",acc:""};
const KIND={Bank:"🏦","M-Pesa":"📲",Cash:"💵",Other:"👛"};
const nowLocal=()=>new Date(Date.now()-new Date().getTimezoneOffset()*6e4).toISOString().slice(0,16);
const curMonth=()=>nowLocal().slice(0,7);
st.month=curMonth();

async function api(path,opt={}){
  const sep=path.includes("?")?"&":"?";
  const r=await fetch(path+sep+"t="+encodeURIComponent(TOKEN),{...opt,headers:{"Content-Type":"application/json",...(opt.headers||{})}});
  let j={};try{j=await r.json()}catch(e){}
  if(r.status===401){lock();throw new Error("locked")}
  if(!r.ok)throw new Error(j.error||("Error "+r.status));
  return j;
}
function toast(m,err){const t=$("#toast");t.textContent=m;t.className="toast on"+(err?" err":"");clearTimeout(toast.h);toast.h=setTimeout(()=>t.className="toast",2600)}
function lock(){$("#app").innerHTML=`<div class="lock"><div class="logo">P</div><h2>PesaPlan</h2><p class="sub">Open the link from the <b>Phone</b> page in the PesaPlan desktop app (scan its QR code or copy the link), so this phone can unlock your data.</p></div>`}
function forget(){if(!confirm("Forget this device? You'll need to open the link from PesaPlan again."))return;try{localStorage.removeItem("pp_token")}catch(e){}TOKEN="";closeSheet();lock()}
const monthLabel=m=>{const [y,mm]=m.split("-");return new Date(y,mm-1,1).toLocaleString("en-GB",{month:"long",year:"numeric"})};
const shiftMonth=d=>{let [y,m]=st.month.split("-").map(Number);m+=d;if(m<1){m=12;y--}if(m>12){m=1;y++}st.month=y+"-"+String(m).padStart(2,"0")};
const fmtTs=ts=>{const d=new Date(ts.replace(" ","T"));return d.toLocaleString("en-GB",{weekday:"short",day:"numeric",month:"short"})+" · "+d.toLocaleString("en-US",{hour:"numeric",minute:"2-digit"})};
const dayKey=ts=>{const d=new Date(ts.replace(" ","T"));return d.toLocaleDateString("en-GB",{weekday:"long",day:"numeric",month:"long"})};
const colorOf=n=>{const P=["#34D399","#60A5FA","#F472B6","#FBBF24","#A78BFA","#F87171","#2DD4BF","#FB923C","#818CF8","#A3E635","#22D3EE","#E879F9"];let h=0;for(const c of n)h+=c.charCodeAt(0);return P[h%P.length]};

async function load(){
  try{
    if(!st.meta)st.meta=await api("/api/meta");
    const [s,t]=await Promise.all([api("/api/summary?month="+st.month),
      api("/api/transactions?month="+st.month+"&q="+encodeURIComponent(st.q)+"&type="+st.type+"&account="+encodeURIComponent(st.acc))]);
    st.sum=s;st.items=t.items;render();
  }catch(e){if(e.message!=="locked")$("#app").innerHTML=`<div class="lock"><p class="neg">${esc(e.message)}</p><p class="sub">Is PesaPlan still open on your PC and on the same network?</p><button class="btn p" onclick="load()">Retry</button></div>`}
}

function donut(items){
  const tot=items.reduce((a,b)=>a+b.total,0);if(!tot)return`<div class="empty">Nothing yet this month</div>`;
  let off=25,seg="";const top=items.slice(0,6),rest=items.slice(6).reduce((a,b)=>a+b.total,0);
  const all=rest>0?top.concat([{name:"Others",total:rest,color:"#64748B"}]):top;
  for(const i of all){const p=i.total/tot*100;seg+=`<circle r="15.9155" cx="21" cy="21" fill="none" stroke="${i.color}" stroke-width="5.2" stroke-dasharray="${p} ${100-p}" stroke-dashoffset="${off}"/>`;off-=p}
  return`<div class="row" style="gap:16px"><svg viewBox="0 0 42 42" width="132" height="132" style="flex:none"><circle r="15.9155" cx="21" cy="21" fill="none" stroke="#1D2430" stroke-width="5.2"/>${seg}<text x="21" y="21.8" text-anchor="middle" font-size="5.4" font-weight="800" fill="#E6EDF3">${short(tot)}</text></svg>
  <div class="legend grow">${all.map(i=>`<div><i class="dot" style="background:${i.color}"></i><span class="grow" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(i.name)}</span><b>${short(i.total)}</b> <span class="sub">${Math.round(i.total/tot*100)}%</span></div>`).join("")}</div></div>`;
}
function itemRow(i){
  if(i.kind==="transfer")return`<div class="line" data-k="transfer" data-id="${i.id}"><div class="badge" style="background:rgba(96,165,250,.16);color:var(--blue);font-size:19px">⇄</div>
  <div class="grow"><div class="t">${esc(i.from)} → ${esc(i.to)}</div><div class="sub">Transfer · ${fmtTs(i.ts)}${i.description?" · "+esc(i.description):""}</div></div><div class="amt" style="color:var(--blue)">${kes(i.amount)}</div></div>`;
  const inc=i.type==="income",c=colorOf(i.category),n=i.description||i.category;
  return`<div class="line" data-k="tx" data-id="${i.id}"><div class="badge" style="background:${c}29;color:${c}">${esc((n.match(/[A-Za-z0-9]/)||["?"])[0].toUpperCase())}</div>
  <div class="grow"><div class="t">${esc(n)}</div><div class="sub">${esc(i.category)} · ${esc(i.account)} · ${fmtTs(i.ts)}</div></div><div class="amt ${inc?"pos":"neg"}">${inc?"+":"-"}${fmt(i.amount)}</div></div>`;
}
function render(){
  const y0=window.scrollY,cx0=(document.querySelector(".chips")||{}).scrollLeft||0;
  const s=st.sum,m=st.month,isNow=m===curMonth();
  let body="";
  if(st.tab==="home"){
    const net=s.income-s.expense;
    body=`<div class="card hero"><div class="sub">${isNow?"Total across accounts · now":"Closing balance · "+esc(s.label)}</div><div class="big">${kes(s.closing)}</div>
    <div class="sub">Opening ${kes(s.opening)} · <span class="${net>=0?"pos":"neg"}">${net>=0?"+":"-"}${fmt(Math.abs(net))} this month</span></div></div>
    <div class="tiles"><div class="tile"><div class="sub">Income</div><div class="v pos">${kes(s.income)}</div></div><div class="tile"><div class="sub">Expenses</div><div class="v neg">${kes(s.expense)}</div></div></div>
    <div class="card"><h3>Accounts</h3>${s.accounts.map(a=>`<div class="line"><div class="badge" style="background:${colorOf(a.name)}29">${KIND[a.kind]||"👛"}</div><div class="grow"><div class="t">${esc(a.name)}</div><div class="sub">Opening ${kes(a.opening)}</div></div><div class="amt ${a.closing<0?"neg":""}">${kes(a.closing)}</div></div>`).join("")||`<div class="empty">No accounts</div>`}</div>
    <div class="card"><h3>Where your money goes</h3><div class="sub">Expenses by category</div><div style="margin-top:12px">${donut(s.expense_by_cat)}</div></div>
    <div class="card"><h3>Where your money comes from</h3><div class="sub">Salary, side hustle, deals, gifts…</div><div style="margin-top:12px">${donut(s.income_by_cat)}</div></div>
    ${s.budgets.length?`<div class="card"><h3>Budgets</h3>${s.budgets.sort((a,b)=>b.spent/b.limit-a.spent/a.limit).map(b=>{const r=b.spent/b.limit,c=r<.75?"var(--inc)":r<1?"var(--warn)":"var(--exp)";return`<div style="margin-top:12px"><div class="row"><b class="grow">${esc(b.category)}</b><span class="sub">${fmt(b.spent,0)} / ${fmt(b.limit,0)}</span></div><div class="meter"><i style="width:${Math.min(100,r*100)}%;background:${c}"></i></div><div class="sub">${b.limit-b.spent>=0?kes(b.limit-b.spent)+" left":"Over by "+kes(b.spent-b.limit)}</div></div>`}).join("")}</div>`:""}
    <div class="card"><div class="row"><h3 class="grow">Recent</h3><button class="chip" onclick="setTab('act')">See all</button></div>${st.items.slice(0,6).map(itemRow).join("")||`<div class="empty">No transactions this month</div>`}</div>`;
  }else if(st.tab==="act"){
    let last="",h="";
    for(const i of st.items){const d=dayKey(i.ts);if(d!==last){h+=`<div class="dayh">${esc(d.toUpperCase())}</div>`;last=d}h+=itemRow(i)}
    body=`<input class="search" id="q" placeholder="🔎  Search transactions" value="${esc(st.q)}">
    <div class="chips"><button class="chip ${!st.type?"on":""}" data-type="">All</button><button class="chip ${st.type==="income"?"on":""}" data-type="income">Income</button><button class="chip ${st.type==="expense"?"on":""}" data-type="expense">Expenses</button>
    ${st.meta.accounts.map(a=>`<button class="chip ${st.acc===a.name?"on":""}" data-acc="${esc(a.name)}">${esc(a.name)}</button>`).join("")}</div>
    <div class="card" style="padding:6px 16px">${h||`<div class="empty">Nothing found</div>`}</div>`;
  }else{
    body=s.accounts.map(a=>`<div class="card acc"><div class="row"><div class="badge" style="background:${colorOf(a.name)}29">${KIND[a.kind]||"👛"}</div><div class="grow"><div class="t" style="font-weight:800;font-size:16px">${esc(a.name)}</div><div class="sub">${esc(a.kind)}${a.closed_on?" · closed":""}</div></div></div>
    <div class="k"><span>Opening balance</span><b>${kes(a.opening)}</b></div><div class="k"><span>Money in</span><b class="pos">+${fmt(a.income)}</b></div><div class="k"><span>Money out</span><b class="neg">-${fmt(a.expense)}</b></div>
    <div class="k"><span>Transfers (net)</span><b style="color:var(--blue)">${a.tin-a.tout>=0?"+":"-"}${fmt(Math.abs(a.tin-a.tout))}</b></div>
    <div class="line" style="border-top:1px solid var(--bd);border-bottom:0;margin-top:8px;padding-bottom:0"><span class="grow sub">Closing balance</span><b style="font-size:18px" class="${a.closing<0?"neg":""}">${kes(a.closing)}</b></div></div>`).join("")+`<button class="btn" onclick="openTransfer()">⇄  Transfer between accounts</button><button class="btn d" onclick="forget()">Forget this device</button>`;
  }
  $("#app").innerHTML=`<div class="top"><div class="row"><div class="brand"><span class="logo">P</span>PesaPlan</div><div class="sp"></div>
    <button class="ibtn" onclick="openExport()" title="Download Excel">⬇</button><button class="ibtn" onclick="openSms()" title="Paste M-Pesa SMS">📲</button><button class="ibtn" onclick="load()" title="Refresh">↻</button></div>
    <div class="month"><button class="ibtn" onclick="mv(-1)">‹</button><b>${esc(monthLabel(m))}</b><button class="ibtn" onclick="mv(1)" ${isNow?"disabled style='opacity:.35'":""}>›</button></div></div>
    <div class="wrap">${body}</div>
    <button class="fab" onclick="openTx()">+</button>
    <div class="tabs">${[["home","📊","Home"],["act","💸","Activity"],["acc","🏦","Accounts"]].map(t=>`<button class="${st.tab===t[0]?"on":""}" onclick="setTab('${t[0]}')"><span>${t[1]}</span>${t[2]}</button>`).join("")}</div>`;
  scrollTo(0,y0);const ch=document.querySelector(".chips");if(ch)ch.scrollLeft=cx0;
  const q=$("#q");if(q){let h;q.oninput=()=>{clearTimeout(h);h=setTimeout(()=>{st.q=q.value;const p=q.selectionStart;load().then(()=>{const n=$("#q");if(n){n.focus();n.setSelectionRange(p,p)}})},350)}}
  document.querySelectorAll("[data-type]").forEach(b=>b.onclick=()=>{st.type=b.dataset.type;load()});
  document.querySelectorAll("[data-acc]").forEach(b=>b.onclick=()=>{st.acc=st.acc===b.dataset.acc?"":b.dataset.acc;load()});
  document.querySelectorAll(".line[data-id]").forEach(l=>l.onclick=()=>{const it=st.items.find(x=>x.id==l.dataset.id&&x.kind===l.dataset.k);if(it)it.kind==="tx"?openTx(it):openTransferView(it)});
}
function setTab(t){st.tab=t;render();scrollTo(0,0)}
function mv(d){shiftMonth(d);load()}

/* ---------- sheets ---------- */
const sheet=h=>{$("#sheet").innerHTML=h;$("#ov").classList.add("on")};
const closeSheet=()=>$("#ov").classList.remove("on");
$("#ov").addEventListener("click",e=>{if(e.target.id==="ov")closeSheet()});
const accOpts=(sel,openOnly=true)=>st.meta.accounts.filter(a=>!openOnly||!a.closed||a.name===sel).map(a=>`<option ${a.name===sel?"selected":""}>${esc(a.name)}</option>`).join("");

function openTx(it){
  const edit=!!it;let typ=it?it.type:"expense";
  const catOpts=()=>(typ==="expense"?st.meta.expense_cats:st.meta.income_cats).map(c=>`<option ${it&&it.category===c?"selected":""}>${esc(c)}</option>`).join("");
  sheet(`<h2>${edit?"Edit transaction":"New transaction"}</h2>
  <div class="seg"><button class="e ${typ==="expense"?"on":""}" id="te">− Expense</button><button class="i ${typ==="income"?"on":""}" id="ti">+ Income</button><button class="t" id="tt">⇄ Transfer</button></div>
  <label>Amount</label><input class="amount" id="f_amt" inputmode="decimal" placeholder="0.00" value="${edit?it.amount:""}">
  <label>Description</label><input id="f_desc" placeholder="e.g. Naivas, Salary, Logo gig" value="${esc(it?it.description:"")}">
  <label>Category</label><select id="f_cat">${catOpts()}</select>
  <label>Account</label><select id="f_acc">${accOpts(it?it.account:"M-Pesa")}</select>
  <label>Date &amp; time</label><input type="datetime-local" id="f_ts" value="${edit?it.ts.replace(" ","T").slice(0,16):nowLocal()}">
  <div id="feebox" style="display:none"><label>M-Pesa transaction fee</label><input id="f_fee" inputmode="decimal" placeholder="0"></div>
  <div class="btns">${edit?`<button class="btn d" id="del">Delete</button>`:`<button class="btn" id="cancel">Cancel</button>`}<button class="btn p" id="save">${edit?"Save":"Add"}</button></div>`);
  const fee=()=>{const a=$("#f_acc").value,k=(st.meta.accounts.find(x=>x.name===a)||{}).kind;$("#feebox").style.display=(!edit&&typ==="expense"&&k==="M-Pesa")?"block":"none"};
  const setT=t=>{typ=t;$("#te").classList.toggle("on",t==="expense");$("#ti").classList.toggle("on",t==="income");$("#f_cat").innerHTML=catOpts();fee()};
  $("#te").onclick=()=>setT("expense");$("#ti").onclick=()=>setT("income");$("#tt").onclick=()=>openTransfer();$("#f_acc").onchange=fee;fee();
  if(!edit){const d=$("#f_desc");d.oninput=()=>{const g=guess(d.value,typ);if(g)$("#f_cat").value=g}}
  if($("#cancel"))$("#cancel").onclick=closeSheet;
  if($("#del"))$("#del").onclick=async()=>{if(!confirm("Delete this transaction?"))return;try{await api("/api/transactions/"+it.id,{method:"DELETE"});closeSheet();toast("Deleted");load()}catch(e){toast(e.message,1)}};
  $("#save").onclick=async()=>{
    const body={type:typ,amount:$("#f_amt").value.replace(/,/g,""),description:$("#f_desc").value,category:$("#f_cat").value,account:$("#f_acc").value,ts:$("#f_ts").value,fee:($("#f_fee")||{}).value||0};
    if(!(parseFloat(body.amount)>0))return toast("Enter an amount",1);
    try{await api(edit?"/api/transactions/"+it.id:"/api/transactions",{method:edit?"PUT":"POST",body:JSON.stringify(body)});closeSheet();toast(edit?"Saved":"Added");load()}catch(e){toast(e.message,1)}};
  if(!edit)setTimeout(()=>$("#f_amt").focus(),60);
}
const KW=[["Airtime & Data",["airtime","bundles"]],["Bills & Utilities",["kplc","water","zuku","dstv","gotv","wifi","internet","faiba"]],["Transport",["uber","bolt","matatu","fuel","petrol","shell","fare","boda","parking"]],["Food & Groceries",["naivas","quickmart","carrefour","supermarket","restaurant","kfc","java","cafe","mboga","food","lunch","dinner"]],["Health",["pharmacy","chemist","hospital","clinic","nhif"]],["Education",["school","college","tuition","fees","books"]],["Entertainment",["netflix","showmax","spotify","betika","sportpesa","cinema"]],["Rent",["rent","landlord"]],["Shopping",["jumia","kilimall","mall","clothes","shoes"]]];
function guess(t,typ){t=t.toLowerCase();if(typ==="income")return/salary|payroll/.test(t)?"Salary":/gig|client|freelance|hustle/.test(t)?"Side Hustle":/gift/.test(t)?"Gifts":/deal|commission/.test(t)?"Deals":"";for(const[c,w]of KW)if(w.some(x=>t.includes(x)))return c;return""}

function openTransfer(){
  const names=st.meta.accounts.filter(a=>!a.closed).map(a=>a.name);
  if(names.length<2){closeSheet();return toast("Need two open accounts",1)}
  sheet(`<h2>Transfer money</h2><div class="seg"><button class="e" id="te">− Expense</button><button class="i" id="ti">+ Income</button><button class="t on" id="tt">⇄ Transfer</button></div>
  <label>From</label><select id="t_from">${names.map(n=>`<option>${esc(n)}</option>`).join("")}</select>
  <label>To</label><select id="t_to">${names.map((n,i)=>`<option ${i===1?"selected":""}>${esc(n)}</option>`).join("")}</select>
  <label>Amount</label><input class="amount" id="t_amt" inputmode="decimal" placeholder="0.00">
  <label>Fee charged (optional)</label><input id="t_fee" inputmode="decimal" placeholder="0">
  <label>Date &amp; time</label><input type="datetime-local" id="t_ts" value="${nowLocal()}">
  <label>Note</label><input id="t_note" placeholder="Optional">
  <p class="sub">Moving your own money between accounts is not income or spending.</p>
  <div class="btns"><button class="btn" id="cancel">Cancel</button><button class="btn p" id="save">Transfer</button></div>`);
  $("#te").onclick=()=>openTx();$("#ti").onclick=()=>{openTx();$("#ti").click()};$("#cancel").onclick=closeSheet;
  $("#save").onclick=async()=>{const b={from:$("#t_from").value,to:$("#t_to").value,amount:$("#t_amt").value.replace(/,/g,""),fee:$("#t_fee").value||0,ts:$("#t_ts").value,note:$("#t_note").value};
    if(b.from===b.to)return toast("Choose two different accounts",1);if(!(parseFloat(b.amount)>0))return toast("Enter an amount",1);
    try{await api("/api/transfers",{method:"POST",body:JSON.stringify(b)});closeSheet();toast("Transferred");load()}catch(e){toast(e.message,1)}};
  setTimeout(()=>$("#t_amt").focus(),60);
}
function openTransferView(it){
  sheet(`<h2>Transfer</h2><div class="card" style="background:var(--card2)"><div class="t" style="font-weight:700">${esc(it.from)} → ${esc(it.to)}</div><div class="big" style="color:var(--blue)">${kes(it.amount)}</div><div class="sub">${fmtTs(it.ts)}${it.description?" · "+esc(it.description):""}</div></div>
  <div class="btns"><button class="btn" id="cancel">Close</button><button class="btn d" id="del">Delete</button></div>`);
  $("#cancel").onclick=closeSheet;
  $("#del").onclick=async()=>{if(!confirm("Delete this transfer?"))return;try{await api("/api/transfers/"+it.id,{method:"DELETE"});closeSheet();toast("Deleted");load()}catch(e){toast(e.message,1)}};
}
function openExport(){
  const cur=st.month;
  sheet(`<h2>Download Excel</h2><p class="sub">Income, expenses, transfers and account balances as a spreadsheet.</p>
  <label>Period</label><select id="x_rng"><option value="month">${esc(monthLabel(cur))} (month you're viewing)</option><option value="last month">Last month</option><option value="last 30 days">Last 30 days</option><option value="this year">This year</option><option value="last year">Last year</option><option value="all">All time</option><option value="custom">Custom dates…</option></select>
  <div id="x_cust" style="display:none"><label>From</label><input type="date" id="x_from"><label>To</label><input type="date" id="x_to"></div>
  <div class="btns"><button class="btn" id="cancel">Cancel</button><button class="btn p" id="save">Download</button></div>`);
  const d=nowLocal().slice(0,10);$("#x_from").value=d.slice(0,8)+"01";$("#x_to").value=d;
  $("#x_rng").onchange=()=>{$("#x_cust").style.display=$("#x_rng").value==="custom"?"block":"none"};
  $("#cancel").onclick=closeSheet;
  $("#save").onclick=()=>{const r=$("#x_rng").value;
    let u="/api/export?t="+encodeURIComponent(TOKEN)+(r==="month"?"&month="+cur:"&range="+encodeURIComponent(r));
    if(r==="custom")u+="&from="+$("#x_from").value+"&to="+$("#x_to").value;
    const a=document.createElement("a");a.href=u;a.download="";document.body.appendChild(a);a.click();a.remove();closeSheet();toast("Preparing your file…")};
}
function openSms(){
  sheet(`<h2>Paste M-Pesa SMS</h2><p class="sub">Long-press an M-Pesa message → Copy, then paste here. You can paste several at once.</p>
  <textarea id="sms" rows="6" placeholder="UCB7K2LM4P Confirmed. Ksh1,250.00 paid to …"></textarea>
  <div class="btns"><button class="btn" id="cancel">Cancel</button><button class="btn p" id="save">Import</button></div>`);
  $("#cancel").onclick=closeSheet;
  try{navigator.clipboard&&navigator.clipboard.readText&&navigator.clipboard.readText().then(t=>{if(/Confirmed/i.test(t)&&!$("#sms").value)$("#sms").value=t}).catch(()=>{})}catch(e){}
  $("#save").onclick=async()=>{const t=$("#sms").value.trim();if(!t)return;
    try{const r=await api("/api/sms",{method:"POST",body:JSON.stringify({text:t})});closeSheet();
      toast(`Imported ${r.imported}`+(r.duplicates?` · ${r.duplicates} already saved`:"")+(r.unrecognised.length?` · ${r.unrecognised.length} not recognised`:""),!r.imported&&r.unrecognised.length);load()}catch(e){toast(e.message,1)}};
}
document.addEventListener("visibilitychange",()=>{if(!document.hidden&&!$("#ov").classList.contains("on")&&TOKEN)load()});
setInterval(()=>{if(!document.hidden&&!$("#ov").classList.contains("on")&&TOKEN&&document.activeElement.id!=="q")load()},30000);
if(!TOKEN)lock();else load();
</script></body></html>
"""
