
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from . import db

RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "spam_rules.json"
_rules_cache: tuple[float, dict] | None = None


def rules() -> dict:
    global _rules_cache
    try:
        mtime = RULES_PATH.stat().st_mtime
        if not _rules_cache or _rules_cache[0] != mtime:
            _rules_cache = (mtime, json.loads(RULES_PATH.read_text(encoding="utf-8")))
        return _rules_cache[1]
    except Exception:
        return {"blocco_da": 70, "sospetto_da": 40, "prefissi_a_pagamento": [], "prefissi_one_ring": [],
                "parole_sospette": {}, "parole_importanti": []}


def _blocklisted(n: str) -> dict | None:
    for r in db.q("SELECT * FROM blocklist"):
        p = r["pattern"]
        if p == n or (p.endswith("*") and n.startswith(p[:-1])):
            return r
    return None


def _common_prefix(a: str, b: str) -> int:
    i = 0
    for x, y in zip(a, b):
        if x != y:
            break
        i += 1
    return i


def analyze(kind: str, sender: str, text: str = "", now: datetime | None = None) -> dict:
    R = rules()
    now = now or datetime.now()
    n = db.normalize(sender)
    score, why = 0, []

    bl = _blocklisted(n)
    if bl:
        return _verdict(100, [f"Nella tua lista nera ({bl.get('reason') or 'bloccato da te'})"], R)

    c = db.contact(n)
    if c:
        tag = "VIP" if c["vip"] else "in rubrica"
        return {"score": 0, "verdict": "ok", "action": "allow",
                "reasons": [f"Contatto {tag}: {c['name']}"], "known": True}

    is_phone = n.startswith("+") or n.isdigit()
    national = n[3:] if n.startswith("+39") else n.lstrip("+")

    if n == "anonimo":
        score += 25; why.append("Numero nascosto")
    elif is_phone:
        for p in R.get("prefissi_a_pagamento", []):
            if national.startswith(p):
                score += 70; why.append(f"Numero a pagamento/sovrattariffato ({p})"); break
        if not n.startswith("+39"):
            for p in R.get("prefissi_one_ring", []):
                if n.startswith(p):
                    score += 55 if kind == "call" else 30
                    why.append(f"Prefisso estero spesso usato nelle truffe 'one ring' ({p})"); break
        own = db.get("own_number") or os.getenv("OWN_NUMBER", "")
        own = db.normalize(own) if own else ""
        if own and n != own and _common_prefix(n, own) >= max(len(own) - 4, 8):
            score += 35; why.append("Numero quasi identico al tuo (tipico spoofing 'vicino')")
        if kind == "call":
            score += 15; why.append("Numero sconosciuto")
            since = (now - timedelta(hours=3)).isoformat(timespec="seconds")
            rep = db.q("SELECT COUNT(*) c FROM events WHERE sender=? AND kind='call' AND ts>=?", (n, since), one=True)["c"]
            if rep >= 1:
                score += 15 if rep == 1 else 30
                why.append(f"Ha già chiamato {rep} {'volta' if rep == 1 else 'volte'} nelle ultime 3 ore")
        elif re.fullmatch(r"\+39\d{9,10}", n) is None:
            score += 5

    if text:
        low = text.lower()
        hit = 0
        for w, pts in R.get("parole_sospette", {}).items():
            if w in low:
                hit += pts; why.append(f"Parola sospetta: «{w}»")
        score += min(hit, 70)
        if re.search(r"https?://|www\.|\b\w+\.(?:com|it|net|xyz|top|click|info)/\S*", low):
            score += 10; why.append("Contiene un link")
        if re.search(r"\b(?:bit\.ly|tinyurl|t\.co|cutt\.ly|is\.gd|shorturl)\b", low):
            score += 20; why.append("Link accorciato (nasconde la destinazione)")
        if kind in ("sms", "whatsapp") and n != "anonimo" and not is_phone:
            score += 10; why.append("Mittente alfanumerico")

    return _verdict(score, why, R)


def _verdict(score: int, why: list[str], R: dict) -> dict:
    score = max(0, min(100, score))
    if score >= R.get("blocco_da", 70):
        v, a = "spam", "block"
    elif score >= R.get("sospetto_da", 40):
        v, a = "suspect", "warn"
    else:
        v, a = "ok", "allow"
    return {"score": score, "verdict": v, "action": a, "reasons": why or ["Nessun segnale sospetto"], "known": False}


def is_important(sender_contact: dict | None, text: str, kind: str, status: str) -> bool:
    if sender_contact and sender_contact.get("vip"):
        return True
    if kind == "call" and status == "missed" and sender_contact:
        return True
    low = (text or "").lower()
    return any(w in low for w in rules().get("parole_importanti", []))
