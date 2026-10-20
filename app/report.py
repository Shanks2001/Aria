
from __future__ import annotations

from datetime import datetime, timedelta

from . import db
from .dates_it import format_when

PERIODI = {"oggi": "oggi", "24h": "nelle ultime 24 ore", "7d": "negli ultimi 7 giorni"}


def pl(n: int, uno: str, molti: str) -> str:
    return f"{n} {uno if n == 1 else molti}"


def _since(period: str, now: datetime) -> datetime:
    if period == "oggi":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "7d":
        return now - timedelta(days=7)
    return now - timedelta(hours=24)


def label(e: dict) -> str:
    return e.get("name") or e.get("sender") or "Sconosciuto"


def build(period: str = "24h", now: datetime | None = None) -> dict:
    now = now or datetime.now()
    period = period if period in PERIODI else "24h"
    since = _since(period, now).isoformat(timespec="seconds")
    ev = db.q("SELECT * FROM events WHERE ts>=? ORDER BY ts DESC", (since,))

    calls = [e for e in ev if e["kind"] == "call"]
    msgs = [e for e in ev if e["kind"] in ("sms", "whatsapp")]
    mails = [e for e in ev if e["kind"] == "email"]
    c = {
        "calls": len(calls),
        "calls_missed": sum(1 for e in calls if e["status"] == "missed" and e["verdict"] != "spam"),
        "calls_answered": sum(1 for e in calls if e["status"] == "answered"),
        "calls_blocked": sum(1 for e in calls if e["status"] == "blocked" or e["verdict"] == "spam"),
        "messages": len(msgs), "messages_spam": sum(1 for e in msgs if e["verdict"] == "spam"),
        "emails": len(mails), "emails_spam": sum(1 for e in mails if e["verdict"] == "spam"),
        "suspect": sum(1 for e in ev if e["verdict"] == "suspect"),
        "unread": sum(1 for e in ev if not e["read"] and e["verdict"] != "spam" and e["kind"] != "call"),
    }
    c["spam_total"] = c["calls_blocked"] + c["messages_spam"] + c["emails_spam"]
    important, seen = [], set()
    for e in ev:
        if e["important"] and e["verdict"] != "spam" and e["sender"] not in seen:
            seen.add(e["sender"]); important.append(e)
    important = important[:6]
    upcoming = db.q("SELECT * FROM agenda WHERE done=0 AND when_ts>=? ORDER BY when_ts LIMIT 5",
                    (now.replace(hour=0, minute=0).isoformat(timespec="seconds"),))

    # ---- testo parlabile
    parts = []
    if not ev:
        parts.append(f"Non ci sono comunicazioni {PERIODI[period]}.")
    else:
        s = f"{PERIODI[period].capitalize()} hai ricevuto {pl(c['calls'], 'chiamata', 'chiamate')}"
        bits = []
        if c["calls_missed"]: bits.append(f"{c['calls_missed']} senza risposta")
        if c["calls_blocked"]: bits.append(f"{c['calls_blocked']} {'bloccata' if c['calls_blocked'] == 1 else 'bloccate'} come spam")
        s += (" (" + ", ".join(bits) + ")" if bits else "")
        s += f", {pl(c['messages'], 'messaggio', 'messaggi')} e {pl(c['emails'], 'email', 'email')}."
        parts.append(s)
        if important:
            parts.append("Da non perdere: " + "; ".join(
                f"{label(e)}" + (f", {e['subject']}" if e.get("subject") else
                                 (f", «{(e.get('body') or '')[:60].strip()}»" if e["kind"] != "call" else ", chiamata persa"))
                for e in important[:3]) + ".")
        if c["suspect"]:
            parts.append(f"{pl(c['suspect'], 'contatto sospetto da controllare', 'contatti sospetti da controllare')}.")
    if upcoming:
        n0 = upcoming[0]
        when = datetime.fromisoformat(n0["when_ts"])
        parts.append(f"Prossimo impegno: {n0['title']}, {format_when(when, bool(n0['has_time']), now)}.")
    spoken = " ".join(parts)

    # ---- markdown
    md = [f"# Report {PERIODI[period]}", f"_Generato il {now:%d/%m/%Y alle %H:%M}_", "", "## Riepilogo",
          f"- Chiamate: {c['calls']} (risposte {c['calls_answered']}, perse {c['calls_missed']}, spam bloccate {c['calls_blocked']})",
          f"- Messaggi: {c['messages']} (spam {c['messages_spam']})",
          f"- Email: {c['emails']} (spam/promo {c['emails_spam']})",
          f"- Da controllare (sospetti): {c['suspect']}", ""]
    if important:
        md += ["## Da non perdere"]
        for e in important:
            md.append(f"- **{label(e)}** ({e['kind']}, {e['ts'][11:16]}): {e.get('subject') or (e.get('body') or '')[:100] or 'chiamata persa'}")
        md.append("")
    if upcoming:
        md += ["## Prossimi impegni"] + [
            f"- {format_when(datetime.fromisoformat(a['when_ts']), bool(a['has_time']), now)} — {a['title']}" for a in upcoming] + [""]
    blocked = [e for e in ev if e["verdict"] == "spam"]
    if blocked:
        md += ["## Spam intercettato"] + [f"- {e['ts'][5:16].replace('T', ' ')} {label(e)} — {e['reasons'].split(' | ')[0]}" for e in blocked[:12]] + [""]

    return {"period": period, "label": PERIODI[period], "counts": c, "important": important,
            "upcoming": upcoming, "spoken": spoken, "markdown": "\n".join(md)}
