"""
pesaplan_sync.py - desktop side of PesaPlan sync. Pure standard library; put it next to pesaplan.py.

What it does
  migrate(conn)        one-time, additive database change: gives every record a uuid + updated_at, and uses
                       SQLite triggers to keep them current and to remember deletions. Your existing queries are
                       untouched, and all of it is invisible until you actually sync.
  sync_once(conn, post) push local changes, pull remote ones, repeat until both sides agree.
  login / register     talk to the sync server (see pesaplan_server/).

Conflict rule: last edit wins, per record. Deletions travel as tombstones.
"""
import json
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone

KINDS = {
    "transactions": dict(cols=["ts", "type", "amount", "category", "description", "source", "mpesa_code",
                               "balance_after"], nkey="mpesa_code"),
    "transfers": dict(cols=["ts", "from_acc", "to_acc", "amount", "note", "code", "balance_after"], nkey="code"),
    "accounts": dict(cols=["name", "kind", "opening_balance", "opened_on", "closed_on", "sort"], nkey="name"),
    "budgets": dict(cols=["category", "monthly_limit"], nkey="category"),
}
NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
NOT_APPLYING = "(SELECT value FROM settings WHERE key='sync_applying') IS NOT '1'"
SCHEMA_VERSION = "2"


class SyncError(Exception):
    pass


# --------------------------------------------------------------------------- small settings helpers
def get_setting(conn, key, default=None):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def set_setting(conn, key, value):
    conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, str(value)))


def utc_now():
    d = datetime.now(timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (d.microsecond // 1000)


# --------------------------------------------------------------------------- schema migration
def migrate(conn):
    """Idempotent and cheap after the first run. Safe to call on every connection."""
    if get_setting(conn, "sync_schema") == SCHEMA_VERSION:
        return
    conn.execute("CREATE TABLE IF NOT EXISTS sync_tombstones("
                 "kind TEXT NOT NULL, uuid TEXT NOT NULL, deleted_at TEXT NOT NULL, PRIMARY KEY(kind, uuid))")
    conn.execute("CREATE TABLE IF NOT EXISTS sync_dirty(kind TEXT NOT NULL, rid INTEGER NOT NULL, "
                 "PRIMARY KEY(kind, rid))")                           # rows that still need pushing
    for t in KINDS:
        have = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % t)}
        if "uuid" not in have:
            conn.execute("ALTER TABLE %s ADD COLUMN uuid TEXT" % t)
        if "updated_at" not in have:
            conn.execute("ALTER TABLE %s ADD COLUMN updated_at TEXT" % t)
        conn.execute("UPDATE %s SET uuid = lower(hex(randomblob(16))) WHERE uuid IS NULL" % t)
        conn.execute("UPDATE %s SET updated_at = %s WHERE updated_at IS NULL" % (t, NOW))
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_%s_uuid ON %s(uuid)" % (t, t))
        conn.execute("INSERT OR IGNORE INTO sync_dirty(kind, rid) SELECT '%s', rowid FROM %s" % (t, t))
        for suffix in ("ai", "au", "ad"):
            conn.execute("DROP TRIGGER IF EXISTS trg_%s_%s" % (t, suffix))
        conn.execute("""CREATE TRIGGER trg_%(t)s_ai AFTER INSERT ON %(t)s WHEN %(na)s
            BEGIN
              INSERT OR IGNORE INTO sync_dirty(kind, rid) VALUES('%(t)s', NEW.rowid);
              UPDATE %(t)s SET uuid = COALESCE(uuid, lower(hex(randomblob(16)))),
                               updated_at = COALESCE(updated_at, %(now)s) WHERE rowid = NEW.rowid;
            END""" % dict(t=t, na=NOT_APPLYING, now=NOW))
        conn.execute("""CREATE TRIGGER trg_%(t)s_au AFTER UPDATE ON %(t)s WHEN %(na)s
            BEGIN
              INSERT OR IGNORE INTO sync_dirty(kind, rid) VALUES('%(t)s', NEW.rowid);
              UPDATE %(t)s SET updated_at = %(now)s WHERE rowid = NEW.rowid;
            END""" % dict(t=t, na=NOT_APPLYING, now=NOW))
        conn.execute("""CREATE TRIGGER trg_%(t)s_ad AFTER DELETE ON %(t)s WHEN %(na)s
            BEGIN
              DELETE FROM sync_dirty WHERE kind = '%(t)s' AND rid = OLD.rowid;
              INSERT OR REPLACE INTO sync_tombstones(kind, uuid, deleted_at)
                SELECT '%(t)s', OLD.uuid, %(now)s WHERE OLD.uuid IS NOT NULL;
            END""" % dict(t=t, na=NOT_APPLYING, now=NOW))
    set_setting(conn, "sync_schema", SCHEMA_VERSION)
    conn.commit()


# --------------------------------------------------------------------------- collecting local changes
def _rows(conn, sql, args=()):
    cur = conn.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def pending(conn):
    """Everything changed locally and not yet pushed, oldest first."""
    out = []
    for kind, spec in KINDS.items():
        cols = ", ".join(spec["cols"])
        for r in _rows(conn, "SELECT uuid, updated_at, %s FROM %s WHERE uuid IS NOT NULL AND rowid IN "
                             "(SELECT rid FROM sync_dirty WHERE kind=?)" % (cols, kind), (kind,)):
            out.append({"kind": kind, "uuid": r["uuid"], "updated_at": r["updated_at"], "deleted": False,
                        "data": {c: r[c] for c in spec["cols"]}})
    out.sort(key=lambda c: c["updated_at"])
    dead = [{"kind": r["kind"], "uuid": r["uuid"], "updated_at": r["deleted_at"], "deleted": True, "data": {}}
            for r in _rows(conn, "SELECT kind, uuid, deleted_at FROM sync_tombstones ORDER BY deleted_at")]
    return dead + out          # deletions first, so re-created names (e.g. after "Erase everything") are not seen as duplicates


def _mark_pushed(conn, batch):
    """Forget what the server has acknowledged. A row edited again during the sync keeps its mark."""
    for ch in batch:
        if ch["deleted"]:
            conn.execute("DELETE FROM sync_tombstones WHERE kind=? AND uuid=? AND deleted_at=?",
                         (ch["kind"], ch["uuid"], ch["updated_at"]))
        else:
            conn.execute("DELETE FROM sync_dirty WHERE kind=? AND rid=(SELECT rowid FROM %s WHERE uuid=? "
                         "AND updated_at=?)" % ch["kind"], (ch["kind"], ch["uuid"], ch["updated_at"]))
    conn.commit()


# --------------------------------------------------------------------------- applying remote changes
def apply_changes(conn, changes):
    """Write changes pulled from the server. The sync_applying flag stops our own triggers from treating them
    as local edits. Returns how many rows were actually changed."""
    set_setting(conn, "sync_applying", "1")
    n = 0
    try:
        for ch in changes:
            n += _apply_one(conn, ch)
    finally:
        set_setting(conn, "sync_applying", "0")
        conn.commit()
    return n


def _apply_one(conn, ch):
    kind = ch.get("kind")
    if kind not in KINDS:
        return 0
    spec, uuid, ua = KINDS[kind], ch["uuid"], ch["updated_at"]
    cols = spec["cols"]
    local = conn.execute("SELECT updated_at, rowid FROM %s WHERE uuid=?" % kind, (uuid,)).fetchone()
    if ch.get("deleted"):
        if local and local[0] <= ua:
            conn.execute("DELETE FROM %s WHERE uuid=?" % kind, (uuid,))
            conn.execute("DELETE FROM sync_dirty WHERE kind=? AND rid=?", (kind, local[1]))
            return 1
        return 0
    data = ch.get("data") or {}
    vals = [data.get(c) for c in cols]
    try:
        if local:
            if local[0] >= ua:
                return 0                                            # our copy is newer or identical
            conn.execute("UPDATE %s SET %s, updated_at=? WHERE uuid=?" % (kind, ", ".join(c + "=?" for c in cols)),
                         vals + [ua, uuid])
            conn.execute("DELETE FROM sync_dirty WHERE kind=? AND rid=?", (kind, local[1]))
            return 1
        nk = data.get(spec["nkey"])
        if nk not in (None, ""):
            other = conn.execute("SELECT rowid FROM %s WHERE %s=?" % (kind, spec["nkey"]), (nk,)).fetchone()
            if other:       # same real-world record created on two devices: adopt the server's identity
                if kind == "accounts" and not data.get("opening_balance"):
                    mine = conn.execute("SELECT opening_balance FROM accounts WHERE rowid=?", (other[0],)).fetchone()
                    if mine and mine[0]:        # we hold a real balance, the server only a blank default: keep ours
                        conn.execute("UPDATE accounts SET uuid=?, updated_at=? WHERE rowid=?", (uuid, utc_now(), other[0]))
                        conn.execute("INSERT OR IGNORE INTO sync_dirty(kind, rid) VALUES('accounts', ?)", (other[0],))
                        return 1
                conn.execute("UPDATE %s SET %s, uuid=?, updated_at=? WHERE rowid=?" %
                             (kind, ", ".join(c + "=?" for c in cols)), vals + [uuid, ua, other[0]])
                conn.execute("DELETE FROM sync_dirty WHERE kind=? AND rid=?", (kind, other[0]))
                return 1
        conn.execute("INSERT INTO %s(%s, uuid, updated_at) VALUES(%s)" %
                     (kind, ", ".join(cols), ", ".join("?" * (len(cols) + 2))), vals + [uuid, ua])
        return 1
    except sqlite3.IntegrityError:
        return 0


# --------------------------------------------------------------------------- the sync loop
def _drop_ghost_defaults(conn):
    """After a first pull: if the server already has accounts, any local account that is still an untouched
    default (zero balance, never used) and unknown to the server is a ghost: remove it so it is not pushed."""
    known = conn.execute("SELECT COUNT(*) FROM accounts WHERE rowid NOT IN "
                         "(SELECT rid FROM sync_dirty WHERE kind='accounts')").fetchone()[0]
    if not known:
        return
    set_setting(conn, "sync_applying", "1")
    try:
        for rid, name in conn.execute("SELECT a.rowid, a.name FROM accounts a WHERE a.rowid IN "
                                      "(SELECT rid FROM sync_dirty WHERE kind='accounts') "
                                      "AND COALESCE(a.opening_balance,0)=0 AND a.closed_on IS NULL").fetchall():
            used = conn.execute("SELECT (SELECT COUNT(*) FROM transactions WHERE source=?) + "
                                "(SELECT COUNT(*) FROM transfers WHERE from_acc=? OR to_acc=?)",
                                (name, name, name)).fetchone()[0]
            if not used:
                conn.execute("DELETE FROM accounts WHERE rowid=?", (rid,))
                conn.execute("DELETE FROM sync_dirty WHERE kind='accounts' AND rid=?", (rid,))
    finally:
        set_setting(conn, "sync_applying", "0")
        conn.commit()


def sync_once(conn, post, batch=500, max_rounds=200):
    """`post(path, payload) -> dict` is the transport (see http_transport). Returns counts."""
    stats = {"pushed": 0, "pulled": 0, "rounds": 0}
    cursor = int(get_setting(conn, "sync_cursor", "0") or 0)
    if cursor == 0:                                  # brand-new device: look at what the server has first
        while True:
            stats["rounds"] += 1
            resp = post("/api/sync", {"since": cursor, "changes": [], "limit": batch})
            stats["pulled"] += apply_changes(conn, resp.get("changes", []))
            cursor = int(resp["cursor"])
            set_setting(conn, "sync_cursor", cursor)
            conn.commit()
            if not resp.get("more"):
                break
        _drop_ghost_defaults(conn)
    todo = pending(conn)
    while True:
        stats["rounds"] += 1
        if stats["rounds"] > max_rounds:
            raise SyncError("Sync did not settle - try again")
        chunk, todo = todo[:batch], todo[batch:]
        resp = post("/api/sync", {"since": cursor, "changes": chunk, "limit": batch})
        if chunk:
            _mark_pushed(conn, chunk)
            stats["pushed"] += len(chunk)
        stats["pulled"] += apply_changes(conn, resp.get("changes", []))
        cursor = int(resp["cursor"])
        set_setting(conn, "sync_cursor", cursor)
        conn.commit()
        if not resp.get("more") and not todo:
            break
    set_setting(conn, "sync_last_ok", utc_now())
    conn.commit()
    return stats


# --------------------------------------------------------------------------- talking to the server
def http_transport(base_url, token, timeout=30):
    base = base_url.rstrip("/")

    def post(path, payload):
        req = urllib.request.Request(base + path, data=json.dumps(payload).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + token})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode("utf-8")).get("error", "")
            except Exception:
                msg = ""
            raise SyncError("Server said %d%s" % (e.code, (": " + msg) if msg else ""))
        except (urllib.error.URLError, OSError) as e:
            raise SyncError("Can't reach the sync server (%s)" % getattr(e, "reason", e))
    return post


def _auth_call(base_url, path, payload, timeout=30):
    return http_transport(base_url, "", timeout)(path, payload)


def register(base_url, email, password):
    return _auth_call(base_url, "/api/auth/register", {"email": email, "password": password})


def login(base_url, email, password, device_name):
    """Returns {'token': ..., 'device_id': ...}. Store the token; never store the password."""
    return _auth_call(base_url, "/api/auth/login", {"email": email, "password": password,
                                                    "device_name": device_name})
