
from __future__ import annotations

import email
import imaplib
import os
import re
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from html import unescape

from . import assistant


def configured() -> bool:
    return all(os.getenv(k) for k in ("IMAP_HOST", "IMAP_USER", "IMAP_PASS"))


def _dec(v: str | None) -> str:
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:
        return v or ""


def parse_message(raw: bytes) -> dict:
    msg = email.message_from_bytes(raw)
    name, addr = parseaddr(_dec(msg.get("From")))
    body = ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() != "text" or part.get_filename():
            continue
        try:
            txt = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        except Exception:
            continue
        if part.get_content_type() == "text/html":
            txt = unescape(re.sub(r"<(script|style).*?</\1>|<[^>]+>", " ", txt, flags=re.S | re.I))
        if part.get_content_type() == "text/plain" or not body:
            body = txt
        if part.get_content_type() == "text/plain":
            break
    body = re.sub(r"\s+", " ", body).strip()[:1500]
    try:
        ts = parsedate_to_datetime(msg.get("Date")).astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
    except Exception:
        ts = None
    return {"sender": addr or name, "name": name or None, "subject": _dec(msg.get("Subject")), "body": body,
            "ts": ts, "ext_id": (msg.get("Message-ID") or "").strip() or None}


def sync(limit: int = 30) -> dict:
    if not configured():
        return {"ok": False, "error": "IMAP non configurato (vedi .env)"}
    new = 0
    try:
        M = imaplib.IMAP4_SSL(os.environ["IMAP_HOST"], int(os.getenv("IMAP_PORT", "993")), timeout=20)
        M.login(os.environ["IMAP_USER"], os.environ["IMAP_PASS"])
        M.select(os.getenv("IMAP_FOLDER", "INBOX"), readonly=True)
        typ, data = M.search(None, "UNSEEN")
        ids = (data[0].split() if typ == "OK" and data and data[0] else [])[-limit:]
        for i in ids:
            typ, d = M.fetch(i, "(BODY.PEEK[])")
            if typ != "OK" or not d or not isinstance(d[0], tuple):
                continue
            m = parse_message(d[0][1])
            r = assistant.ingest("email", m["sender"], m["body"], m["subject"], m["name"], ts=m["ts"], ext_id=m["ext_id"])
            new += 0 if r.get("duplicate") else 1
        M.logout()
        return {"ok": True, "new": new}
    except Exception as e:  # rete, credenziali, ecc.
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
