"""
End-to-end sync tests: a real PesaPlan database on two simulated devices talking to the real server.

Run (PowerShell), from this folder, with pesaplan.py + pesaplan_sync.py one level up:
    $env:PESAPLAN_DIR = ".."
    python test_sync.py
"""
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.abspath(os.environ.get("PESAPLAN_DIR", os.path.join(HERE, "..")))
sys.path.insert(0, APP_DIR)
sys.path.insert(0, HERE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pesaplan as P                      # noqa: E402
import pesaplan_sync as S                 # noqa: E402
from app import create_app                # noqa: E402

TMP = tempfile.mkdtemp(prefix="pp_sync_")
FM = P.FMT
PASSED = []


def check(cond, label):
    if not cond:
        raise AssertionError("FAILED: " + label)
    PASSED.append(label)
    print("  ok  ", label)


def db_url(name):
    """SQLite by default; a fresh PostgreSQL database per call when TEST_PG_URL is set."""
    pg = os.environ.get("TEST_PG_URL")
    if not pg:
        return "sqlite:///" + os.path.join(TMP, name + ".db")
    import psycopg2
    from urllib.parse import urlparse
    u = urlparse(pg)
    dbname = "pptest_" + name
    con = psycopg2.connect(pg); con.autocommit = True
    cur = con.cursor()
    cur.execute('DROP DATABASE IF EXISTS "%s"' % dbname)
    cur.execute('CREATE DATABASE "%s"' % dbname)
    con.close()
    return "postgresql://%s:%s@%s:%s/%s" % (u.username, u.password, u.hostname, u.port or 5432, dbname)


class Device:
    def __init__(self, name, client, token, db=None):
        self.name = name
        self.db = db or P.Database(os.path.join(TMP, name + ".db"))
        self.conn = self.db.conn
        self.client, self.token = client, token

    def post(self, path, payload):
        r = self.client.post(path, json=payload, headers={"Authorization": "Bearer " + self.token})
        if r.status_code != 200:
            raise S.SyncError("HTTP %d %s" % (r.status_code, r.get_data(as_text=True)[:120]))
        return r.get_json()

    def sync(self, **kw):
        return S.sync_once(self.conn, self.post, **kw)

    def state(self):
        """Everything that should be identical on every device, keyed by uuid."""
        out = {}
        for kind, spec in S.KINDS.items():
            cols = ", ".join(spec["cols"])
            for r in S._rows(self.conn, "SELECT uuid, %s FROM %s" % (cols, kind)):
                out[(kind, r.pop("uuid"))] = tuple(sorted(r.items()))
        return out

    def closing(self):
        s, e = P.month_bounds(datetime.now().year, datetime.now().month)
        return {a["name"]: round(a["closing"], 2) for a in self.db.month_summary(s, e)}


def new_device(app, name, email="me@example.com", pw="correct horse"):
    c = app.test_client()
    r = c.post("/api/auth/login", json={"email": email, "password": pw, "device_name": name})
    assert r.status_code == 200, r.get_data(as_text=True)
    return Device(name, c, r.get_json()["token"])


def first_of_month():
    return datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def ts(day, hour=10):
    return (first_of_month() + timedelta(days=day, hours=hour)).strftime(FM)


def main():
    print("\n[1] Server auth")
    app = create_app(db_url("server"))
    c = app.test_client()
    check(c.post("/api/auth/register", json={"email": "me@example.com", "password": "short"}).status_code == 400,
          "weak password refused")
    check(c.post("/api/auth/register", json={"email": "me@example.com", "password": "correct horse"}).status_code == 201,
          "first account registers")
    check(c.post("/api/auth/register", json={"email": "evil@example.com", "password": "correct horse"}).status_code == 403,
          "second registration closed")
    check(c.post("/api/auth/login", json={"email": "me@example.com", "password": "wrong"}).status_code == 401,
          "wrong password refused")
    check(c.post("/api/sync", json={}).status_code == 401, "sync without token refused")
    check(c.post("/api/sync", json={}, headers={"Authorization": "Bearer nope"}).status_code == 401,
          "sync with bad token refused")
    app2 = create_app(db_url("throttle"))
    c2 = app2.test_client()
    c2.post("/api/auth/register", json={"email": "a@b.co", "password": "longenough"})
    codes = [c2.post("/api/auth/login", json={"email": "a@b.co", "password": "x%d" % i}).status_code for i in range(10)]
    check(codes[-1] == 429, "login throttled after repeated failures")

    print("\n[2] Migrating an existing (pre-sync) desktop database")
    sys.modules["pesaplan_sync"] = None                        # simulate the old app: no migration yet
    legacy_path = os.path.join(TMP, "desktop.db")
    old = P.Database(legacy_path)
    for n, bal in (("M-Pesa", 1000), ("Cash", 500), ("Bank Account 1", 20000)):
        a = old.account(n)
        old.update_account(n, n, a["kind"], bal, first_of_month().strftime(FM))
    old.add(ts(1), "income", 50000, "Salary", "Salary", "Bank Account 1")
    old.add(ts(2), "expense", 1200, "Food & Groceries", "Naivas", "M-Pesa", "ABC1234567", 13)
    old.add(ts(3), "expense", 450, "Transport", "Uber", "M-Pesa")
    old.add_transfer(ts(4), "Bank Account 1", "M-Pesa", 5000, "top up")
    old.set_budget("Food & Groceries", 5000)
    old.conn.close()
    sys.modules["pesaplan_sync"] = S                           # the new app: migration now available
    desk = Device("desktop", c, c.post("/api/auth/login", json={"email": "me@example.com", "password": "correct horse",
                                                                  "device_name": "desktop"}).get_json()["token"],
                  db=P.Database(legacy_path))
    n_rows = sum(desk.conn.execute("SELECT COUNT(*) FROM %s" % k).fetchone()[0] for k in S.KINDS)
    n_uuid = sum(desk.conn.execute("SELECT COUNT(*) FROM %s WHERE uuid IS NOT NULL AND updated_at IS NOT NULL" % k).fetchone()[0] for k in S.KINDS)
    check(n_rows == n_uuid and n_rows > 10, "every existing row got a uuid and timestamp (%d rows)" % n_rows)
    before = desk.closing()
    check(before["M-Pesa"] == 1000 - 1200 - 13 - 450 + 5000 and before["Bank Account 1"] == 65000,
          "existing balances unchanged by migration")
    check(desk.db.conn.execute("SELECT COUNT(*) FROM sync_tombstones").fetchone()[0] == 0, "migration made no tombstones")
    S.migrate(desk.conn)                                           # idempotent
    check(True, "migration can run again safely")

    print("\n[3] First sync: desktop -> server -> brand-new phone")
    st = desk.sync()
    check(st["pushed"] == n_rows and st["pulled"] == 0, "desktop pushed everything (%d)" % st["pushed"])
    phone = new_device(app, "phone")
    st = phone.sync()
    check(st["pulled"] > 0, "phone pulled %d records" % st["pulled"])
    names = [a["name"] for a in phone.db.accounts()]
    check(len(names) == len(set(names)) == 6, "default accounts de-duplicated (phone had its own M-Pesa/Cash/Banks)")
    check(phone.closing() == desk.closing(), "phone balances == desktop balances")
    check(phone.db.budgets() == {"Food & Groceries": 5000}, "budgets synced")
    desk.sync()
    check(desk.state() == phone.state(), "both devices hold identical records")

    print("\n[3b] A device with real balances joins a server that only holds blank default accounts")
    app_b = create_app(db_url("server_b"))
    cb = app_b.test_client(); cb.post("/api/auth/register", json={"email": "me@example.com", "password": "correct horse"})
    fresh_phone = new_device(app_b, "fresh_phone"); fresh_phone.sync()
    legacy = Device("legacy", cb, cb.post("/api/auth/login", json={"email": "me@example.com", "password": "correct horse",
                    "device_name": "legacy"}).get_json()["token"], db=P.Database(os.path.join(TMP, "legacy2.db")))
    for n, bal in (("M-Pesa", 1000), ("Cash", 500)):
        a = legacy.db.account(n); legacy.db.update_account(n, n, a["kind"], bal, first_of_month().strftime(FM))
    legacy.db.add(ts(2), "expense", 100, "Other", "x", "M-Pesa")
    legacy.sync(); fresh_phone.sync(); legacy.sync()
    check(legacy.closing()["M-Pesa"] == 900 and legacy.closing()["Cash"] == 500, "legacy device kept its real balances")
    check(fresh_phone.closing() == legacy.closing(), "fresh phone shows the real balances too: %s" % legacy.closing()["M-Pesa"])
    check(legacy.state() == fresh_phone.state(), "no ghost or duplicate accounts")

    print("\n[4] Edits on both sides, then converge")
    phone.db.add(ts(5), "income", 3000, "Side Hustle", "Logo gig", "M-Pesa")
    phone.db.add_transfer(ts(6), "M-Pesa", "Cash", 700, "withdraw", fee=0)
    tid = desk.conn.execute("SELECT id FROM transactions WHERE description='Uber'").fetchone()[0]
    r = desk.conn.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
    desk.db.update(tid, r["ts"], r["type"], 500, r["category"], "Uber ride", r["source"], r["mpesa_code"])
    desk.sync(); phone.sync(); desk.sync()
    check(desk.state() == phone.state(), "records identical after two-way edits")
    check(desk.closing() == phone.closing(), "balances identical: %s" % desk.closing())
    check(desk.conn.execute("SELECT amount FROM transactions WHERE description='Uber ride'").fetchone()[0] == 500,
          "edit made on the desktop reached both devices")

    print("\n[5] Deletes travel")
    pid = phone.conn.execute("SELECT id FROM transactions WHERE description='Logo gig'").fetchone()[0]
    phone.db.delete(pid)
    phone.sync(); desk.sync()
    check(desk.conn.execute("SELECT COUNT(*) FROM transactions WHERE description='Logo gig'").fetchone()[0] == 0,
          "delete on phone removed it on desktop")
    check(desk.state() == phone.state(), "still identical after delete")

    print("\n[6] Same M-Pesa SMS imported on both devices before syncing")
    sms = P.MpesaParser.parse("UDD5N8K2QW Confirmed. Ksh350.00 paid to QUICKMART. on %s at 6:42 PM. New M-PESA balance is Ksh1,000.00. "
                              "Transaction cost, Ksh0.00." % datetime.now().strftime("%-d/%-m/%y"))
    for dev in (desk, phone):
        dev.db.add(sms["ts"].strftime(FM), "expense", sms["amount"], sms["category"], sms["description"], "M-Pesa", sms["key"], 0, sms["balance"])
    desk.sync(); phone.sync(); desk.sync(); phone.sync()
    nd = desk.conn.execute("SELECT COUNT(*) FROM transactions WHERE mpesa_code='UDD5N8K2QW'").fetchone()[0]
    npn = phone.conn.execute("SELECT COUNT(*) FROM transactions WHERE mpesa_code='UDD5N8K2QW'").fetchone()[0]
    check(nd == 1 and npn == 1, "the duplicate SMS ended up as exactly one transaction on each device")
    check(desk.state() == phone.state() and desk.closing() == phone.closing(), "devices agree after the duplicate")

    print("\n[7] Conflicting edits: the later edit wins")
    tid_d = desk.conn.execute("SELECT id FROM transactions WHERE description='Naivas'").fetchone()[0]
    tid_p = phone.conn.execute("SELECT id FROM transactions WHERE description='Naivas'").fetchone()[0]
    rd = desk.conn.execute("SELECT * FROM transactions WHERE id=?", (tid_d,)).fetchone()
    rp = phone.conn.execute("SELECT * FROM transactions WHERE id=?", (tid_p,)).fetchone()
    desk.db.update(tid_d, rd["ts"], rd["type"], rd["amount"], rd["category"], "Naivas (desktop edit)", rd["source"], rd["mpesa_code"])
    time.sleep(0.01)
    phone.db.update(tid_p, rp["ts"], rp["type"], rp["amount"], rp["category"], "Naivas (phone edit, later)", rp["source"], rp["mpesa_code"])
    desk.sync(); phone.sync(); desk.sync()
    dd = desk.conn.execute("SELECT description FROM transactions WHERE mpesa_code='ABC1234567'").fetchone()[0]
    check(dd == "Naivas (phone edit, later)", "later edit won on the desktop")
    check(desk.state() == phone.state(), "devices agree on the winner")

    print("\n[8] Renaming an account")
    desk.db.update_account("Bank Account 2", "Equity", "Bank", 0, first_of_month().strftime(FM))
    desk.sync(); phone.sync()
    check("Equity" in [a["name"] for a in phone.db.accounts()] and "Bank Account 2" not in [a["name"] for a in phone.db.accounts()],
          "rename reached the phone")

    print("\n[9] Idempotence and no phantom changes")
    s1, s2 = desk.sync(), phone.sync()
    check(s1["pushed"] == 0 and s2["pushed"] == 0, "nothing left to push after syncing")
    check(s1["pulled"] == 0 and s2["pulled"] == 0, "nothing to pull either")
    check(S.pending(desk.conn) == [] and S.pending(phone.conn) == [], "applying remote data creates no local 'edits'")

    print("\n[10] Interrupted sync resumes without losing anything")
    for i in range(350):
        desk.db.add(ts(8, 1) if i % 2 else ts(9, 2), "expense", 10 + i, "Other", "bulk %d" % i, "Cash")
    calls = {"n": 0}
    real_post = desk.post

    def flaky(path, payload):
        calls["n"] += 1
        if calls["n"] == 3:
            raise S.SyncError("connection dropped")
        return real_post(path, payload)
    try:
        S.sync_once(desk.conn, flaky, batch=100)
        raise AssertionError("expected an interruption")
    except S.SyncError:
        pass
    check(len(S.pending(desk.conn)) > 0, "interrupted push left work pending")
    desk.sync(batch=100); phone.sync(batch=100)
    check(phone.conn.execute("SELECT COUNT(*) FROM transactions WHERE description LIKE 'bulk %'").fetchone()[0] == 350,
          "all 350 bulk transactions arrived after resuming")
    check(desk.state() == phone.state(), "identical after the interrupted sync")

    print("\n[11] Pagination with a small page size")
    third = new_device(app, "tablet")
    st = third.sync(batch=50)
    check(st["rounds"] > 5, "pulled in %d rounds" % st["rounds"])
    check(third.state() == desk.state(), "third device identical to the others")

    print("\n[12] Malformed input is rejected, not fatal")
    r = desk.post("/api/sync", {"since": 0, "limit": 1, "changes": [
        {"kind": "nope", "uuid": "x" * 20, "updated_at": "2026-01-01T00:00:00.000Z", "data": {}},
        {"kind": "transactions", "uuid": "abcdef0123456789", "updated_at": "yesterday", "data": {}},
        {"kind": "transactions", "uuid": "abcdef0123456789", "updated_at": "2026-01-01T00:00:00.000Z", "data": {"amount": {"x": 1}}},
        "garbage"]})
    check(r["rejected"] == 4 and r["accepted"] == 0, "4 bad changes rejected, none stored")
    check(desk.client.post("/api/sync", data="not json", headers={"Authorization": "Bearer " + desk.token}).status_code == 400,
          "non-JSON body gives a clean 400")

    print("\n[13] 'Erase everything' on one device clears the others")
    desk.db.start_fresh()
    desk.sync(); phone.sync(); third.sync(); desk.sync()
    for d in (desk, phone, third):
        check(d.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0 and
              d.conn.execute("SELECT COUNT(*) FROM transfers").fetchone()[0] == 0, "%s has no transactions/transfers" % d.name)
        n = [a["name"] for a in d.db.accounts()]
        check(len(n) == len(set(n)) == 6, "%s has exactly 6 accounts, no duplicates" % d.name)
    check(desk.state() == phone.state() == third.state(), "all three identical after the wipe")

    print("\n[14] Over real HTTP with the urllib client")
    from werkzeug.serving import make_server
    srv_app = create_app(db_url("http"))
    srv = make_server("127.0.0.1", 18999, srv_app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:18999"
    S.register(base, "http@example.com", "long enough pw")
    tok = S.login(base, "http@example.com", "long enough pw", "laptop")["token"]
    d1 = P.Database(os.path.join(TMP, "h1.db")); d2 = P.Database(os.path.join(TMP, "h2.db"))
    d1.add(ts(1), "income", 1234, "Gifts", "Gift", "Cash")
    post = S.http_transport(base, tok)
    S.sync_once(d1.conn, post)
    tok2 = S.login(base, "http@example.com", "long enough pw", "phone")["token"]
    S.sync_once(d2.conn, S.http_transport(base, tok2))
    check(d2.conn.execute("SELECT amount FROM transactions WHERE description='Gift'").fetchone()[0] == 1234, "record crossed real HTTP")
    try:
        S.sync_once(d2.conn, S.http_transport(base, "wrong"))
        check(False, "bad token should fail")
    except S.SyncError as e:
        check("401" in str(e), "bad token -> clear SyncError (%s)" % e)
    try:
        S.sync_once(d2.conn, S.http_transport("http://127.0.0.1:1", tok2, timeout=2))
        check(False, "unreachable server should fail")
    except S.SyncError as e:
        check("reach" in str(e).lower(), "unreachable server -> clear SyncError")
    srv.shutdown()

    print("\nAll %d checks passed." % len(PASSED))


if __name__ == "__main__":
    main()
