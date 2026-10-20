
from __future__ import annotations

import os
import re
import sqlite3
import threading
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ARCHIVIO = BASE / "archivio"
ARCHIVIO.mkdir(exist_ok=True)
DB_PATH = Path(os.getenv("Aria_DB", ARCHIVIO / "Aria.db"))

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,                 -- call | sms | email | whatsapp
  sender TEXT, name TEXT, subject TEXT, body TEXT,
  status TEXT DEFAULT 'received',     -- received | answered | missed | blocked
  duration INTEGER DEFAULT 0,
  spam_score INTEGER DEFAULT 0, verdict TEXT DEFAULT 'ok', reasons TEXT DEFAULT '',
  important INTEGER DEFAULT 0, read INTEGER DEFAULT 0,
  ext_id TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS agenda(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL, when_ts TEXT, has_time INTEGER DEFAULT 1,
  notes TEXT DEFAULT '', done INTEGER DEFAULT 0, reminded INTEGER DEFAULT 0,
  created TEXT
);
CREATE TABLE IF NOT EXISTS recordings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, filename TEXT, duration INTEGER DEFAULT 0, title TEXT, transcript TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS contacts(number TEXT PRIMARY KEY, name TEXT, vip INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS blocklist(pattern TEXT PRIMARY KEY, reason TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
"""


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.executescript(SCHEMA)
            _conn.commit()
        return _conn


def q(sql: str, args=(), one: bool = False):
    with _lock:
        cur = conn().execute(sql, args)
        rows = [dict(r) for r in cur.fetchall()]
    return (rows[0] if rows else None) if one else rows


def x(sql: str, args=()) -> int:
    with _lock:
        cur = conn().execute(sql, args)
        conn().commit()
        return cur.lastrowid or cur.rowcount


def get(key: str, default=None):
    r = q("SELECT v FROM kv WHERE k=?", (key,), one=True)
    return r["v"] if r else default


def put(key: str, value) -> None:
    x("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, str(value)))


def normalize(raw: str | None) -> str:
    """Numeri -> formato +39...; email -> minuscolo; vuoto/privato -> 'anonimo'."""
    s = (raw or "").strip()
    if not s or s.lower() in {"anonimo", "privato", "private", "unknown", "hidden", "sconosciuto", "-1", "null"}:
        return "anonimo"
    if "@" in s:
        m = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", s)
        return (m.group(0) if m else s).lower()
    if not re.search(r"\d", s):
        return s  # mittente alfanumerico (es. "Poste", "Amazon")
    d = re.sub(r"[^\d+]", "", s)
    if d.startswith("00"):
        d = "+" + d[2:]
    if not d.startswith("+") and d[:1] in "03":
        d = "+39" + d
    return d


def contact(number: str) -> dict | None:
    return q("SELECT * FROM contacts WHERE number=?", (normalize(number),), one=True)
