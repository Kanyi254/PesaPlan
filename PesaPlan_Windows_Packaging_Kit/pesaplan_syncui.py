"""PesaPlan - Sync page. Optional: lives next to pesaplan.py and pesaplan_sync.py.

Adds a "Sync" page to the sidebar: sign in to your PesaPlan server once, then every edit is pushed within about
half a minute and changes from your other devices are pulled every few minutes. Syncing runs on a background
thread with its own database connection, so the window never freezes and works offline (it just retries).
"""
import platform
import sqlite3
import threading
from datetime import datetime, timezone

from PyQt5.QtCore import Qt, QObject, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                             QSpinBox, QVBoxLayout, QWidget)

import pesaplan_sync as S

PUSH_CHECK_SECS = 30          # how often we look for unsent local edits
DEFAULT_MINUTES = 5           # how often we pull even with nothing to send


def _local(ts):
    """'2026-11-12T06:30:00.123Z' -> '12 Nov 09:30' in the PC's own time zone."""
    try:
        d = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).astimezone()
        return d.strftime("%d %b %H:%M")
    except Exception:
        return ts or "never"


class SyncService(QObject):
    done = pyqtSignal(object, str)          # stats-or-None, error text ("" when fine)

    def __init__(self, db, db_path):
        super().__init__()
        self.db, self.db_path = db, db_path
        self.busy = False
        self.last_error = ""
        self.last_stats = None
        self._last_run = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    # ---- settings ---------------------------------------------------------------------------------
    def url(self):      return (self.db.get_setting("sync_url") or "").strip()
    def token(self):    return self.db.get_setting("sync_token") or ""
    def email(self):    return self.db.get_setting("sync_email") or ""
    def signed_in(self):return bool(self.url() and self.token())
    def auto(self):     return self.db.get_setting("sync_auto", "1") == "1"
    def minutes(self):
        try:
            return max(1, min(120, int(self.db.get_setting("sync_minutes", DEFAULT_MINUTES))))
        except ValueError:
            return DEFAULT_MINUTES

    def unsent(self):
        try:
            c = self.db.conn
            return (c.execute("SELECT COUNT(*) FROM sync_dirty").fetchone()[0]
                    + c.execute("SELECT COUNT(*) FROM sync_tombstones").fetchone()[0])
        except sqlite3.Error:
            return 0

    # ---- running ----------------------------------------------------------------------------------
    def start(self):
        self.timer.start(PUSH_CHECK_SECS * 1000)
        QTimer.singleShot(3000, self.sync_now)

    def stop(self):
        self.timer.stop()

    def _tick(self):
        if not (self.signed_in() and self.auto()) or self.busy:
            return
        import time
        due = time.time() - self._last_run >= self.minutes() * 60
        if due or self.unsent() > 0 and time.time() - self._last_run >= PUSH_CHECK_SECS:
            self.sync_now()

    def sync_now(self):
        if self.busy or not self.signed_in():
            return False
        import time
        self.busy = True
        self._last_run = time.time()
        url, token = self.url(), self.token()
        threading.Thread(target=self._work, args=(url, token), daemon=True).start()
        return True

    def _work(self, url, token):
        stats, err = None, ""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=15)
            S.migrate(conn)
            stats = S.sync_once(conn, S.http_transport(url, token))
        except S.SyncError as e:
            err = str(e)
        except sqlite3.OperationalError as e:
            err = "Database busy, will retry (%s)" % e
        except Exception as e:                       # never let the thread die silently
            err = "Sync failed: %s" % e
        finally:
            if conn is not None:
                conn.close()
        self.busy = False
        self.done.emit(stats, err)

    # ---- sign in / out ------------------------------------------------------------------------------
    @staticmethod
    def sign_in_blocking(url, email, password, device, create=False):
        """Network only (runs on a worker thread); touches no database."""
        url = url.strip().rstrip("/")
        if create:
            S.register(url, email, password)
        r = S.login(url, email, password, device)
        return {"url": url, "email": email, "device": device, "token": r["token"]}

    def finish_sign_in(self, r):
        """Main thread: remember the session. cursor 0 makes the first sync pull before it pushes."""
        for k, v in (("sync_url", r["url"]), ("sync_email", r["email"]), ("sync_token", r["token"]),
                     ("sync_device", r["device"]), ("sync_cursor", "0"), ("sync_auto", "1"), ("sync_last_ok", "")):
            self.db.set_setting(k, v)

    def sign_out(self):
        url, tok = self.url(), self.token()
        if url and tok:                                 # best effort, in the background
            def bye():
                try:
                    S.http_transport(url, tok, timeout=8)("/api/auth/logout", {})
                except Exception:
                    pass                                # unreachable: the token is just forgotten locally
            threading.Thread(target=bye, daemon=True).start()
        for k in ("sync_token", "sync_last_ok"):
            self.db.set_setting(k, "")
        self.db.set_setting("sync_cursor", "0")
        # so a later sign-in (maybe to another server) uploads everything again
        c = self.db.conn
        for t in S.KINDS:
            c.execute("INSERT OR IGNORE INTO sync_dirty(kind, rid) SELECT ?, rowid FROM %s" % t, (t,))
        c.commit()


class _Job(QObject):
    finished = pyqtSignal(object, str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def go(self):
        def run():
            try:
                self.finished.emit(self.fn(), "")
            except S.SyncError as e:
                self.finished.emit(None, str(e))
            except Exception as e:
                self.finished.emit(None, "Failed: %s" % e)
        threading.Thread(target=run, daemon=True).start()


def build_page(P, svc, win):
    box_css = ("QLineEdit,QSpinBox{background:%s;border:1px solid %s;border-radius:10px;padding:8px 10px;}"
               % (P.CARD2, P.BORDER))

    class SyncPage(QScrollArea):
        def __init__(self):
            super().__init__()
            self.setWidgetResizable(True)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            body = QWidget(); body.setObjectName("pagebody")
            self.setWidget(body)
            lay = QVBoxLayout(body)
            lay.setContentsMargins(28, 4, 28, 28); lay.setSpacing(16)
            self.jobs = []

            # --- status (signed in)
            st = QWidget()
            sl = QVBoxLayout(st); sl.setContentsMargins(0, 0, 0, 0); sl.setSpacing(8)
            self.status = QLabel(); self.status.setWordWrap(True)
            self.status.setStyleSheet("font-size:15px; font-weight:700;")
            self.detail = QLabel(); self.detail.setObjectName("cardSub")
            self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row = QHBoxLayout(); row.setSpacing(10)
            self.now_btn = QPushButton("Sync now"); self.now_btn.setObjectName("primary"); self.now_btn.setMinimumHeight(38)
            self.auto_cb = QCheckBox("Sync automatically")
            self.mins = QSpinBox(); self.mins.setRange(1, 120); self.mins.setSuffix(" min"); self.mins.setStyleSheet(box_css); self.mins.setMinimumHeight(38)
            out = QPushButton("Sign out"); out.setObjectName("ghost"); out.setMinimumHeight(38)
            row.addWidget(self.now_btn); row.addWidget(self.auto_cb); row.addWidget(QLabel("pull every"))
            row.addWidget(self.mins); row.addStretch(); row.addWidget(out)
            sl.addWidget(self.status); sl.addWidget(self.detail); sl.addLayout(row)
            self.status_card = P.make_card("Sync", st, "Keeps this PC, your other PCs and your phone in step")
            lay.addWidget(self.status_card)

            # --- sign in (signed out)
            fm = QWidget()
            fl = QVBoxLayout(fm); fl.setContentsMargins(0, 0, 0, 0); fl.setSpacing(8)
            self.url = QLineEdit(); self.url.setPlaceholderText("https://pesaserver.your-tailnet.ts.net")
            self.email = QLineEdit(); self.email.setPlaceholderText("Email")
            self.pw = QLineEdit(); self.pw.setPlaceholderText("Password"); self.pw.setEchoMode(QLineEdit.Password)
            self.dev = QLineEdit(platform.node() or "This PC")
            for w, cap in ((self.url, "Server address"), (self.email, "Email"), (self.pw, "Password"),
                           (self.dev, "Name of this device")):
                w.setStyleSheet(box_css)
                c = QLabel(cap); c.setObjectName("cardSub")
                fl.addWidget(c); fl.addWidget(w)
            r2 = QHBoxLayout(); r2.setSpacing(10)
            self.in_btn = QPushButton("Sign in"); self.in_btn.setObjectName("primary")
            self.new_btn = QPushButton("Create the account"); self.new_btn.setObjectName("ghost")
            r2.addWidget(self.in_btn); r2.addWidget(self.new_btn); r2.addStretch()
            self.msg = QLabel(); self.msg.setWordWrap(True)
            fl.addLayout(r2); fl.addWidget(self.msg)
            self.form_card = P.make_card("Connect to your server", fm,
                                         "Use the https address from your home-server setup. Create the account only "
                                         "the very first time; every other device just signs in")
            lay.addWidget(self.form_card)

            tips = QLabel(
                "<b>How it behaves:</b> every change is sent within about 30 seconds and changes from other devices "
                "arrive every few minutes. If the server is unreachable, nothing is lost: PesaPlan keeps working and "
                "sends everything later.<br><br>"
                "<b>First sign-in on a PC that already has data:</b> its accounts, transactions and budgets are merged "
                "into the server. Duplicates (same M-Pesa code, same account name) are joined, not doubled.<br><br>"
                "<b>Away from home or work:</b> keep the Tailscale app running on this PC.")
            tips.setWordWrap(True); tips.setObjectName("cardSub"); tips.setTextFormat(Qt.RichText)
            lay.addWidget(P.make_card("Good to know", tips))
            lay.addStretch(1)

            self.now_btn.clicked.connect(self.manual)
            out.clicked.connect(self.sign_out)
            self.in_btn.clicked.connect(lambda: self.sign_in(False))
            self.new_btn.clicked.connect(lambda: self.sign_in(True))
            self.auto_cb.toggled.connect(lambda on: win.db.set_setting("sync_auto", "1" if on else "0"))
            self.mins.valueChanged.connect(lambda v: win.db.set_setting("sync_minutes", v))
            svc.done.connect(self.on_done)
            self.url.setText(svc.url())
            self.email.setText(svc.email())
            self.refresh()

        # ---- actions
        def manual(self):
            if not svc.sync_now():
                self.detail.setText("Already syncing, one moment...")
            self.refresh()

        def sign_in(self, create):
            url, email, pw = self.url.text().strip(), self.email.text().strip(), self.pw.text()
            if not (url and email and pw):
                self.say("Fill in the server address, email and password.", bad=True); return
            if not url.lower().startswith(("http://", "https://")):
                url = "https://" + url; self.url.setText(url)
            self.say("Connecting...")
            self.in_btn.setEnabled(False); self.new_btn.setEnabled(False)
            dev = self.dev.text().strip() or "PC"
            job = _Job(lambda: svc.sign_in_blocking(url, email, pw, dev, create))
            job.finished.connect(self.signed)
            self.jobs.append(job); job.go()

        def signed(self, res, err):
            self.in_btn.setEnabled(True); self.new_btn.setEnabled(True)
            if err:
                self.say(err, bad=True)
                return
            svc.finish_sign_in(res)
            self.pw.clear(); self.say("")
            svc.sync_now()
            self.refresh()

        def sign_out(self):
            svc.sign_out(); svc.last_error = ""; svc.last_stats = None
            self.refresh()

        def on_done(self, stats, err):
            svc.last_stats, svc.last_error = stats, err
            if err and "401" in err:
                svc.db.set_setting("sync_token", "")
                svc.last_error = "This device was signed out by the server. Please sign in again."
            if stats and (stats["pulled"] or stats["pushed"]):
                win.refresh_all()
            self.refresh()

        # ---- display
        def say(self, text, bad=False):
            self.msg.setStyleSheet("color:%s;" % ("#F87171" if bad else "#8B98A9"))
            self.msg.setText(text)

        def refresh(self):
            on = svc.signed_in()
            self.status_card.setVisible(on)
            self.form_card.setVisible(not on)
            if svc.last_error and not on:
                self.say(svc.last_error, bad=True)
            if not on:
                return
            self.auto_cb.blockSignals(True); self.mins.blockSignals(True)
            self.auto_cb.setChecked(svc.auto()); self.mins.setValue(svc.minutes())
            self.auto_cb.blockSignals(False); self.mins.blockSignals(False)
            last = win.db.get_setting("sync_last_ok") or ""
            if svc.busy:
                head, col = "Syncing...", "#60A5FA"
            elif svc.last_error:
                head, col = "Not synced: " + svc.last_error, "#F87171"
            else:
                head, col = "Up to date" if svc.unsent() == 0 else "Changes waiting to send", "#34D399"
            self.status.setStyleSheet("font-size:15px; font-weight:700; color:%s;" % col)
            self.status.setText(head)
            bits = ["Server: %s" % svc.url(), "Signed in as %s" % svc.email(),
                    "Last synced: %s" % (_local(last) if last else "never")]
            if svc.last_stats:
                bits.append("Last run: sent %d, received %d" % (svc.last_stats["pushed"], svc.last_stats["pulled"]))
            self.detail.setText("\n".join(bits))
            self.now_btn.setEnabled(not svc.busy)
            self.detail.setMinimumHeight(self.detail.fontMetrics().lineSpacing() * (len(bits) + 1))
            self.status_card.setMinimumHeight(self.status_card.sizeHint().height() + 40)

    return SyncPage()


def install(win, P, db_path):
    """Called once from pesaplan.main() after pesaplan_phone.install()."""
    db = win.db
    S.migrate(db.conn)
    svc = SyncService(db, db_path)
    page = build_page(P, svc, win)

    sub = "Keep your PCs and phone in step through your own server"
    if hasattr(win, "add_nav_page"):
        win.add_nav_page("🔄", "Sync", sub, page)
    else:                                              # older pesaplan.py without add_nav_page
        idx = len(win.pages)
        win.pages.append(page)
        win.meta.append(("🔄", "Sync", sub))
        win.stack.addWidget(page)
        b = QPushButton("  🔄    Sync"); b.setObjectName("nav"); b.setCheckable(True)
        b.setCursor(Qt.PointingHandCursor)
        win.nav_btns[0].group().addButton(b)
        b.clicked.connect(lambda _, k=idx: win.go(k))
        win.nav_btns.append(b)
        sl = win.centralWidget().layout().itemAt(0).widget().layout()
        sl.insertWidget(sl.count() - 2, b)

    svc.done.connect(lambda *_: None)
    svc.start()
    app = QApplication.instance()
    if app:
        app.aboutToQuit.connect(svc.stop)
    win._sync = svc
    return svc
