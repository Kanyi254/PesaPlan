"""
PesaPlan sync server.

A small Flask API that keeps one copy of your records so the desktop app and the phone app stay in step.
It is a dumb, careful relay: it stores each record as JSON, resolves conflicts by "last edit wins", and never
computes balances (each device does that itself).

Run locally (PowerShell):
    pip install -r requirements.txt
    $env:FLASK_APP = "app.py"; flask run --port 8000

Production: see README.md (gunicorn + PostgreSQL behind HTTPS).

Config (environment variables)
    DATABASE_URL     default sqlite:///pesaplan_server.db   (use postgresql://user:pass@host/db in production)
    ALLOW_REGISTER   "1" lets more accounts register; by default only the very first account can
"""
import json
import os
import re
import secrets
import hashlib
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, g, jsonify, request
from sqlalchemy import (Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
                        create_engine, func, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from werkzeug.security import check_password_hash, generate_password_hash

# What may be synced and which fields each kind carries (must match pesaplan_sync.py).
KINDS = {
    "transactions": dict(cols=["ts", "type", "amount", "category", "description", "source", "mpesa_code",
                               "balance_after"], nkey="mpesa_code"),
    "transfers": dict(cols=["ts", "from_acc", "to_acc", "amount", "note", "code", "balance_after"], nkey="code"),
    "accounts": dict(cols=["name", "kind", "opening_balance", "opened_on", "closed_on", "sort"], nkey="name"),
    "budgets": dict(cols=["category", "monthly_limit"], nkey="category"),
}
UUID_RE = re.compile(r"^[0-9a-fA-F-]{16,40}$")
TS_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")     # fixed width so strings sort like times
MAX_CHANGES = 5000


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    pw_hash: Mapped[str] = mapped_column(String(300))
    seq: Mapped[int] = mapped_column(Integer, default=0)            # last change number handed out
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: utcnow().replace(tzinfo=None))


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: utcnow().replace(tzinfo=None))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Record(Base):
    __tablename__ = "records"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(20))
    uuid: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(30))
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    data: Mapped[str] = mapped_column(Text, default="{}")
    nkey: Mapped[str | None] = mapped_column(String(200), nullable=True)   # real-world identity (M-Pesa code, ...)
    seq: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("user_id", "kind", "uuid"), Index("ix_rec_user_seq", "user_id", "seq"),
                      Index("ix_rec_nkey", "user_id", "kind", "nkey"))


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _bump(ts):
    d = datetime.strptime(ts[:-1], "%Y-%m-%dT%H:%M:%S.%f") + timedelta(milliseconds=1)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (d.microsecond // 1000)


def _nkey(kind, data):
    v = data.get(KINDS[kind]["nkey"])
    return str(v) if v not in (None, "") else None


def _clean(kind, data):
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    out = {c: data.get(c) for c in KINDS[kind]["cols"]}
    for k, v in out.items():
        if v is not None and not isinstance(v, (str, int, float)):
            raise ValueError("bad value for " + k)
    if len(json.dumps(out)) > 4000:
        raise ValueError("record too large")
    return out


def create_app(db_url=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
    url = db_url or os.environ.get("DATABASE_URL", "sqlite:///pesaplan_server.db")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):                 # pin the driver listed in requirements.txt
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    kw = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
    engine = create_engine(url, **kw)
    Base.metadata.create_all(engine)
    Sess = sessionmaker(engine, expire_on_commit=False)
    app.extensions["sess"] = Sess
    fails = {}                                                      # ip -> [timestamps of failed logins]

    def err(code, msg):
        return jsonify({"error": msg}), code

    @app.after_request
    def headers(r):
        r.headers["Cache-Control"] = "no-store"
        r.headers["X-Content-Type-Options"] = "nosniff"
        return r

    def auth(f):
        @wraps(f)
        def wrapper(*a, **k):
            h = request.headers.get("Authorization", "")
            if not h.lower().startswith("bearer "):
                return err(401, "Missing token")
            with Sess.begin() as s:
                dev = s.scalar(select(Device).where(Device.token_hash == _hash(h[7:].strip())))
                if not dev:
                    return err(401, "Invalid or revoked token")
                dev.last_seen = utcnow().replace(tzinfo=None)
                g.user_id, g.device_id = dev.user_id, dev.id
            return f(*a, **k)
        return wrapper

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True})

    # ---- accounts / devices
    @app.post("/api/auth/register")
    def register():
        b = request.get_json(silent=True) or {}
        email, pw = str(b.get("email", "")).strip().lower(), str(b.get("password", ""))
        if "@" not in email or len(pw) < 8:
            return err(400, "Give a valid email and a password of at least 8 characters")
        with Sess.begin() as s:
            if s.scalar(select(func.count()).select_from(User)) and os.environ.get("ALLOW_REGISTER") != "1":
                return err(403, "Registration is closed")
            if s.scalar(select(User).where(User.email == email)):
                return err(409, "That email is already registered")
            s.add(User(email=email, pw_hash=generate_password_hash(pw)))
        return jsonify({"ok": True}), 201

    @app.post("/api/auth/login")
    def login():
        ip, now = request.remote_addr or "?", time.time()
        fails[ip] = [t for t in fails.get(ip, []) if now - t < 600]
        if len(fails[ip]) >= 8:
            return err(429, "Too many attempts - wait a few minutes")
        b = request.get_json(silent=True) or {}
        email, pw = str(b.get("email", "")).strip().lower(), str(b.get("password", ""))
        with Sess.begin() as s:
            u = s.scalar(select(User).where(User.email == email))
            if not u or not check_password_hash(u.pw_hash, pw):
                fails[ip].append(now)
                return err(401, "Wrong email or password")
            token = secrets.token_urlsafe(32)
            d = Device(user_id=u.id, name=str(b.get("device_name") or "device")[:100], token_hash=_hash(token))
            s.add(d)
            s.flush()
            res = {"token": token, "device_id": d.id}
        fails.pop(ip, None)
        return jsonify(res)

    @app.post("/api/auth/logout")
    @auth
    def logout():
        with Sess.begin() as s:
            d = s.get(Device, g.device_id)
            if d:
                s.delete(d)
        return jsonify({"ok": True})

    @app.get("/api/status")
    @auth
    def status():
        with Sess() as s:
            rows = s.execute(select(Record.kind, func.count()).where(Record.user_id == g.user_id,
                                                                     Record.deleted == False)  # noqa: E712
                             .group_by(Record.kind)).all()
            u = s.get(User, g.user_id)
            devs = s.scalars(select(Device).where(Device.user_id == g.user_id)).all()
            return jsonify({"records": {k: n for k, n in rows}, "seq": u.seq,
                            "devices": [{"id": d.id, "name": d.name,
                                         "last_seen": d.last_seen.isoformat() + "Z" if d.last_seen else None}
                                        for d in devs]})

    # ---- the sync call
    @app.post("/api/sync")
    @auth
    def sync():
        b = request.get_json(silent=True)
        if not isinstance(b, dict):
            return err(400, "Body must be JSON")
        try:
            since = max(0, int(b.get("since") or 0))
            limit = min(max(int(b.get("limit") or 500), 1), 2000)
        except (TypeError, ValueError):
            return err(400, "Bad since/limit")
        changes = b.get("changes") or []
        if not isinstance(changes, list) or len(changes) > MAX_CHANGES:
            return err(413, "Too many changes in one request (max %d)" % MAX_CHANGES)

        accepted, rejected, stale = set(), 0, 0
        with Sess.begin() as s:
            user = s.execute(select(User).where(User.id == g.user_id).with_for_update()).scalar_one()
            for ch in changes:
                try:
                    kind, uuid, ua = ch["kind"], str(ch["uuid"]), str(ch["updated_at"])
                    if kind not in KINDS or not UUID_RE.match(uuid) or not TS_RE.match(ua):
                        raise ValueError("bad change")
                    deleted = bool(ch.get("deleted"))
                    data = {} if deleted else _clean(kind, ch.get("data"))
                except (KeyError, ValueError, TypeError):
                    rejected += 1
                    continue
                rec = s.scalar(select(Record).where(Record.user_id == user.id, Record.kind == kind,
                                                    Record.uuid == uuid))
                if rec is not None and rec.updated_at >= ua:
                    stale += 1                                      # we already hold this or something newer
                    continue
                nk = None if deleted else _nkey(kind, data)
                dup = False
                if nk:                                              # same real-world record from another device?
                    other = s.scalar(select(Record).where(
                        Record.user_id == user.id, Record.kind == kind, Record.nkey == nk,
                        Record.deleted == False, Record.uuid != uuid))  # noqa: E712
                    if other is not None:
                        deleted, data, nk, ua, dup = True, {}, None, _bump(ua), True
                if rec is None:
                    rec = Record(user_id=user.id, kind=kind, uuid=uuid)
                    s.add(rec)
                rec.updated_at, rec.deleted, rec.data, rec.nkey = ua, deleted, json.dumps(data), nk
                user.seq += 1
                rec.seq = user.seq
                if not dup:
                    accepted.add((kind, uuid))
            s.flush()
            rows = s.scalars(select(Record).where(Record.user_id == user.id, Record.seq > since)
                             .order_by(Record.seq).limit(limit + 1)).all()
            more = len(rows) > limit
            rows = rows[:limit]
            cursor = rows[-1].seq if more else user.seq
            out = [{"kind": r.kind, "uuid": r.uuid, "updated_at": r.updated_at, "deleted": r.deleted,
                    "data": json.loads(r.data), "seq": r.seq}
                   for r in rows if (r.kind, r.uuid) not in accepted]
        return jsonify({"cursor": cursor, "more": more, "changes": out, "accepted": len(accepted),
                        "rejected": rejected, "stale": stale})

    return app


app = None


def get_app():
    global app
    if app is None:
        app = create_app()
    return app


if __name__ == "__main__":
    get_app().run(host="127.0.0.1", port=int(os.environ.get("PORT", 8000)))
