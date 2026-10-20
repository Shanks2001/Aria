
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta

from . import brain, db, report, smalltalk, spam
from .dates_it import format_when, parse

DEFAULTS = {"assistente": os.getenv("NOME_ASSISTENTE", "Aria"), "utente": os.getenv("NOME_UTENTE", "Capo"),
            "own_number": os.getenv("OWN_NUMBER", ""), "remind_before": "10"}


def settings() -> dict:
    return {k: db.get(k, v) for k, v in DEFAULTS.items()}


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ------------------------------------------------------------------ eventi in arrivo
def to_ts(v) -> str:
    if v in (None, ""):
        return _iso(now())
    if isinstance(v, (int, float)):
        return _iso(datetime.fromtimestamp(v / 1000 if v > 1e11 else v))
    try:
        return _iso(datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None))
    except ValueError:
        return _iso(now())


def ingest(kind: str, sender: str, body: str = "", subject: str = "", name: str | None = None,
           status: str | None = None, ts=None, duration: int = 0, ext_id: str | None = None) -> dict:
    """Registra una chiamata/SMS/email, la valuta con l'anti-spam e dice cosa fare."""
    kind = {"message": "sms", "mail": "email", "text": "sms"}.get(kind, kind)
    if kind not in ("call", "sms", "whatsapp", "email"):
        raise ValueError("kind deve essere call, sms, whatsapp o email")
    if ext_id and db.q("SELECT id FROM events WHERE ext_id=?", (ext_id,), one=True):
        return {"duplicate": True, "action": "allow"}
    n = db.normalize(sender)
    when = to_ts(ts)
    c = db.contact(n)
    res = spam.analyze(kind, n, f"{subject} {body}", datetime.fromisoformat(when))
    st = status or ("missed" if kind == "call" else "received")
    if kind == "call" and res["action"] == "block":
        st = "blocked"
    important = int(res["verdict"] != "spam" and spam.is_important(c, f"{subject} {body}", kind, st))
    eid = db.x("""INSERT INTO events(ts,kind,sender,name,subject,body,status,duration,spam_score,verdict,reasons,important,ext_id)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (when, kind, n, name or (c or {}).get("name"), subject, body[:4000], st, duration,
                res["score"], res["verdict"], " | ".join(res["reasons"]), important, ext_id))
    return {"id": eid, "action": res["action"], "verdict": res["verdict"], "score": res["score"],
            "reasons": res["reasons"], "status": st, "important": bool(important)}


# ------------------------------------------------------------------ agenda
def agenda_add(title: str, dt: datetime | None, has_time: bool = True, notes: str = "") -> dict:
    aid = db.x("INSERT INTO agenda(title,when_ts,has_time,notes,created) VALUES(?,?,?,?,?)",
               (title, _iso(dt) if dt else None, int(has_time), notes, _iso(now())))
    conflict = None
    if dt and has_time:
        lo, hi = _iso(dt - timedelta(minutes=59)), _iso(dt + timedelta(minutes=59))
        conflict = db.q("SELECT * FROM agenda WHERE id!=? AND done=0 AND has_time=1 AND when_ts BETWEEN ? AND ?",
                        (aid, lo, hi), one=True)
    return {"id": aid, "conflict": conflict}


def agenda_between(a: datetime, b: datetime) -> list[dict]:
    return db.q("SELECT * FROM agenda WHERE done=0 AND when_ts>=? AND when_ts<? ORDER BY when_ts", (_iso(a), _iso(b)))


def due_reminders() -> list[dict]:
    """Promemoria scaduti da mostrare/leggere ora. Stati: 0 nessuno, 1 preavviso dato, 2 finale dato."""
    n, out = now(), []
    pre = int(settings()["remind_before"] or 10)
    for a in db.q("SELECT * FROM agenda WHERE done=0 AND reminded<2 AND when_ts IS NOT NULL AND when_ts<=?",
                  (_iso(n + timedelta(minutes=pre)),)):
        w = datetime.fromisoformat(a["when_ts"])
        if w <= n and a["reminded"] < 2:
            phase = "ora"
            db.x("UPDATE agenda SET reminded=2 WHERE id=?", (a["id"],))
        elif a["has_time"] and a["reminded"] < 1 and pre > 0:
            phase = f"tra {max(1, round((w - n).total_seconds() / 60))} minuti"
            db.x("UPDATE agenda SET reminded=1 WHERE id=?", (a["id"],))
        else:
            continue
        # non rileggere impegni scaduti da più di 6 ore (es. app rimasta chiusa)
        if (n - w) > timedelta(hours=6):
            db.x("UPDATE agenda SET reminded=2 WHERE id=?", (a["id"],)); continue
        out.append({"id": a["id"], "title": a["title"], "phase": phase,
                    "when": format_when(w, bool(a["has_time"]), n)})
    return out


def _fmt_item(a: dict, n: datetime) -> str:
    if not a["when_ts"]:
        return a["title"]
    w = datetime.fromisoformat(a["when_ts"])
    return f"{a['title']} {format_when(w, bool(a['has_time']), n)}"


# ------------------------------------------------------------------ contesto per l'LLM
def context() -> str:
    n = now()
    s = settings()
    ag = db.q("SELECT * FROM agenda WHERE done=0 ORDER BY when_ts IS NULL, when_ts LIMIT 8")
    r = report.build("24h", n)
    recent = db.q("SELECT * FROM events WHERE verdict!='spam' ORDER BY ts DESC LIMIT 6")
    lines = [f"Adesso: {n:%A %d/%m/%Y %H:%M}.", f"Utente: {s['utente']}.",
             "Impegni aperti: " + ("; ".join(_fmt_item(a, n) for a in ag) or "nessuno") + ".",
             "Ultime 24h: " + r["spoken"]]
    for e in recent:
        txt = e.get("subject") or (e.get("body") or "")[:80]
        lines.append(f"- {e['ts'][5:16]} {e['kind']} da {report.label(e)}: {txt or e['status']}")
    return "\n".join(lines)


# ------------------------------------------------------------------ comprensione comandi
def _find_event(low: str, only_msgs: bool = True) -> dict | None:
    cond = "kind IN ('sms','whatsapp','email')" if only_msgs else "1=1"
    rows = db.q(f"SELECT * FROM events WHERE {cond} AND verdict!='spam' ORDER BY ts DESC LIMIT 60")
    for e in rows:
        nm = (e.get("name") or "").lower()
        if nm and any(len(p) > 2 and p in low for p in re.split(r"\W+", nm)):
            return e
    return rows[0] if rows else None


def _tone(low: str) -> str | None:
    for key, pat in [("declina", r"rifiut|decl|di no|non posso|rimand"), ("accetta", r"accett|confer|di sì|di si\b|volentieri"),
                     ("formale", r"formal|professional"), ("breve", r"brev|corta|veloce|in poche parole")]:
        if re.search(pat, low):
            return key
    return None


def _num_from(text: str) -> str | None:
    m = re.search(r"\+?\d[\d\s\-.]{5,}\d", text)
    return db.normalize(m.group(0)) if m else None


def _greeting(s: dict) -> str:
    h = now().hour
    g = "Buongiorno" if 5 <= h < 13 else "Buon pomeriggio" if 13 <= h < 18 else "Buonasera"
    n = now()
    today = agenda_between(n.replace(hour=0, minute=0), n.replace(hour=0, minute=0) + timedelta(days=1))
    r = report.build("24h", n)["counts"]
    bits = []
    bits.append(f"oggi hai {report.pl(len(today), 'impegno', 'impegni')}" if today else "oggi non hai impegni in agenda")
    if r["calls_missed"]: bits.append(report.pl(r["calls_missed"], "chiamata persa", "chiamate perse"))
    if r["unread"]: bits.append(report.pl(r["unread"], "messaggio da leggere", "messaggi da leggere"))
    if r["spam_total"]: bits.append(f"ho fermato {report.pl(r['spam_total'], 'spam', 'spam')}")
    return f"{g} {s['utente']}, {', '.join(bits)}."


HELP = ("Posso: bloccare lo spam, farti il report di chiamate, messaggi ed email, segnare e ricordarti impegni "
        "(«ricordami domani alle 15 il dentista»), registrare («registra»), e suggerirti risposte "
        "(«come rispondo a Marco?»). Chiedimi anche «cosa ho oggi?» o «blocca il numero ...».")

def _unknown(s: dict) -> str:
    import random
    esempi = ["«ricordami domani alle 15 il dentista»", "«cosa ho oggi?»", "«fammi il report»", "«come rispondo a Marco?»",
              "«registra la riunione»", "«quanto fa 12 per 7»", "«raccontami una barzelletta»", "«quanti giorni mancano a Natale?»"]
    return (random.choice(["Questa non l'ho capita.", "Qui mi hai fregata.", "Non so ancora rispondere a questo."]) +
            " Provami con " + ", ".join(random.sample(esempi, 3)) +
            ". Per le conversazioni libere installa Ollama (vedi README): lo uso solo in locale.")

def handle(text: str, history: list[dict] | None = None) -> dict:
    s = settings()
    n = now()
    raw = text.strip()
    low = raw.lower()
    low = re.sub(rf"^(?:ehi|ehy|ok|hey|ciao|salve)?[\s,]*{re.escape(s['assistente'].lower())}\b[\s,:.!]*", "", low).strip()
    if not low:
        if re.match(r"(ciao|salve|buongiorno|buonasera|buon pomeriggio)", raw.lower()):
            return {"reply": _greeting(s), "intent": "saluto"}
        return {"reply": f"Dimmi pure, {s['utente']}.", "intent": "ascolto"}

    # --- registrazione
    if re.search(r"\b(ferma|smetti|stop|termina|chiudi|finisci|interrompi|basta)\b.*\b(registr)", low) or low in ("stop", "ferma", "basta così"):
        return {"reply": "Registrazione fermata. La trovi nel pannello Registrazioni.", "action": "stop_recording", "intent": "registra"}
    if re.match(r"^(registra|avvia|inizia|comincia|fai partire|attiva)\b", low) and re.search(r"registr|audio|conversazione|riunione|chiamata|lezione", low) or low.startswith("registra"):
        titolo = re.sub(r"^(registra(?:mi)?|avvia|inizia|comincia|fai partire|attiva)\s*(la |il |una |un |l')?(registrazione|audio)?\s*(di |della |del |dello )?", "", low).strip(" .")
        return {"reply": "Ok, sto registrando. Dì «ferma registrazione» quando hai finito.", "action": "start_recording",
                "data": {"title": titolo.capitalize() if titolo else ""}, "intent": "registra"}

    # --- suggerimenti di risposta (prima del report: contiene la parola «messaggio»)
    if re.search(r"rispond|risposta|rispondere|bozza", low):
        e = _find_event(low)
        if not e:
            return {"reply": "Non ho messaggi a cui rispondere al momento.", "intent": "risposte"}
        tone = _tone(low)
        sug = brain.suggest_replies(e, tone, s["assistente"], s["utente"])
        who = report.label(e)
        first = sug["replies"][0]["text"] if sug["replies"] else ""
        quoted = (e.get("subject") or e.get("body") or "")[:90]
        return {"reply": f"Per il messaggio di {who} («{quoted}») ti propongo: «{first}». Le altre le vedi qui sotto.",
                "intent": "risposte", "data": {"event": e, **sug}}

    # --- spam / blocco
    if re.match(r"^(blocca|bloccami|metti in lista nera|aggiungi alla lista nera)\b", low):
        num = _num_from(low)
        if not num and re.search(r"ultim|quest[oa]|appena", low):
            last = db.q("SELECT sender FROM events WHERE kind IN ('call','sms') AND sender!='anonimo' AND sender NOT IN (SELECT number FROM contacts) AND sender NOT IN (SELECT pattern FROM blocklist) ORDER BY ts DESC LIMIT 1", one=True)
            num = last["sender"] if last else None
        if not num:
            return {"reply": "Dimmi il numero da bloccare, oppure «blocca l'ultimo numero».", "intent": "spam"}
        db.x("INSERT OR REPLACE INTO blocklist(pattern,reason,created) VALUES(?,?,?)", (num, "bloccato su tua richiesta", n.isoformat()))
        return {"reply": f"Fatto, ho bloccato {num}.", "intent": "spam", "action": "refresh"}
    if re.match(r"^(sblocca|togli dalla lista nera|rimuovi dalla lista nera)\b", low):
        num = _num_from(low)
        if num and db.x("DELETE FROM blocklist WHERE pattern=?", (num,)):
            return {"reply": f"{num} non è più bloccato.", "intent": "spam", "action": "refresh"}
        return {"reply": "Non trovo quel numero nella lista nera.", "intent": "spam"}
    if re.search(r"\bspam\b|lista nera|numeri bloccati", low) and not re.search(r"\b(ricordami|segna)\b", low):
        cnt = report.build("7d", n)["counts"]
        bl = db.q("SELECT COUNT(*) c FROM blocklist", one=True)["c"]
        return {"reply": f"Negli ultimi 7 giorni ho fermato {cnt['spam_total']} tentativi di spam "
                         f"({report.pl(cnt['calls_blocked'], 'chiamata', 'chiamate')}, {report.pl(cnt['messages_spam'], 'messaggio', 'messaggi')}, "
                         f"{report.pl(cnt['emails_spam'], 'email', 'email')}). "
                         f"In lista nera {'c' + chr(39) + 'è' if bl == 1 else 'ci sono'} {report.pl(bl, 'numero', 'numeri')}.", "intent": "spam", "action": "open:inbox"}

    # --- timer: "imposta un timer di 10 minuti" -> promemoria tra 10 minuti
    mt = re.search(r"(?:imposta|metti|avvia|fai partire|crea)\s+(?:un |il )?(?:timer|sveglia|conto alla rovescia)\s+(?:di|da|per)\s+(\d+)\s*(minut|or)", low)
    if mt:
        unit = "minuti" if mt.group(2) == "minut" else "ore"
        r = handle(f"ricordami tra {mt.group(1)} {unit} che il timer è finito", history)
        if r.get("data", {}).get("id"):
            db.x("UPDATE agenda SET reminded=1 WHERE id=?", (r["data"]["id"],))   # niente preavviso: suona solo allo scadere
            r["reply"] = f"Timer di {mt.group(1)} {unit} avviato. Ti avviso allo scadere."
        return r
    # --- agenda: elenco
    listing = re.search(r"(cosa|quali|che|quanti)\b.*\b(ho|impegni|appuntament|programma|agenda|da fare)|\bagenda\b|miei impegni|"
                        r"prossimi impegni|promemoria\b$|dammi gli impegni|leggi.*(agenda|impegni)", low)
    creating = re.search(r"\b(ricordami|ricordati|segna|segnami|metti|aggiungi|imposta|crea|prenota|ho (un|una|il|l'))\b", low)
    if listing and not creating:
        a, b, lab = n.replace(hour=0, minute=0), None, "nei prossimi 7 giorni"
        if "domani" in low and "dopodomani" not in low:
            a = a + timedelta(days=1); b = a + timedelta(days=1); lab = "domani"
        elif "dopodomani" in low:
            a = a + timedelta(days=2); b = a + timedelta(days=1); lab = "dopodomani"
        elif re.search(r"\boggi\b|stasera|stamattina", low):
            b = a + timedelta(days=1); lab = "oggi"
        else:
            b = a + timedelta(days=7)
        items = agenda_between(a, b)
        todo = db.q("SELECT * FROM agenda WHERE done=0 AND when_ts IS NULL ORDER BY id") if lab.startswith("nei") else []
        if not items and not todo:
            return {"reply": f"{lab.capitalize()} non hai impegni.", "intent": "agenda", "action": "open:agenda"}
        txt = f"{lab.capitalize()} hai {report.pl(len(items), 'impegno', 'impegni')}: " + "; ".join(
            (f"alle {datetime.fromisoformat(i['when_ts']):%H:%M} {i['title']}" if i["has_time"] and lab in ("oggi", "domani", "dopodomani")
             else _fmt_item(i, n)) for i in items[:6]) + "." if items else f"{lab.capitalize()} niente di fissato."
        if todo:
            txt += f" Senza data: {', '.join(t['title'] for t in todo[:4])}."
        return {"reply": txt, "intent": "agenda", "action": "open:agenda"}

    # --- agenda: aggiungi
    if creating and not re.search(r"\b(blocca|registra)\b", low):
        dt, title, has_time = parse(raw, n)
        title = title or "Promemoria"
        r = agenda_add(title, dt, has_time)
        if dt:
            msg = f"Segnato: {title}, {format_when(dt, has_time, n)}."
            if has_time:
                msg += f" Ti avviso {settings()['remind_before']} minuti prima."
        else:
            msg = f"Segnato «{title}» tra le cose da fare, senza data. Dimmi quando vuoi che te lo ricordi."
        if r["conflict"]:
            msg += f" Attenzione: nello stesso orario hai già «{r['conflict']['title']}»."
        return {"reply": msg, "intent": "agenda", "action": "refresh", "data": {"id": r["id"]}}

    # --- agenda: completa / cancella
    m = re.match(r"^(cancella|elimina|rimuovi|annulla|togli|completa|fatto|ho fatto|completato|spunta)\s+(?:l'|il |lo |la |i |gli |le )?(?:impegno |appuntamento |promemoria )?(.+)$", low)
    if m:
        verbo, cosa = m.group(1), m.group(2).strip(" .")
        done = verbo in ("completa", "fatto", "ho fatto", "completato", "spunta")
        items = db.q("SELECT * FROM agenda WHERE done=0")
        hit = [i for i in items if any(len(w) > 2 and w in i["title"].lower() for w in re.split(r"\W+", cosa))]
        if len(hit) == 1:
            if done: db.x("UPDATE agenda SET done=1 WHERE id=?", (hit[0]["id"],))
            else: db.x("DELETE FROM agenda WHERE id=?", (hit[0]["id"],))
            return {"reply": f"{'Completato' if done else 'Cancellato'}: {hit[0]['title']}.", "intent": "agenda", "action": "refresh"}
        if len(hit) > 1:
            return {"reply": "Ne ho trovati più di uno: " + "; ".join(i["title"] for i in hit[:4]) + ". Quale intendi?", "intent": "agenda"}
        return {"reply": f"Non trovo nessun impegno che somigli a «{cosa}».", "intent": "agenda"}

    # --- report
    if re.search(r"report|riepilogo|resoconto|riassunt|cosa mi sono perso|novit|nuovi messaggi|chiamate|messaggi|email|\bmail\b|posta|cosa è successo|aggiornamenti", low):
        per = "7d" if re.search(r"settimana|7 giorni|sette giorni", low) else "oggi" if re.search(r"\boggi\b", low) else "24h"
        r = report.build(per, n)
        return {"reply": r["spoken"], "intent": "report", "action": "show_report", "data": {"period": per}}

    # --- ora / data
    if re.search(r"che ore sono|che ora è|ora esatta", low):
        return {"reply": f"Sono le {n:%H:%M}.", "intent": "ora"}
    if re.search(r"che giorno è|che data|data di oggi|quanti ne abbiamo", low):
        from .dates_it import NOMI_GIORNO, NOMI_MESE
        return {"reply": f"Oggi è {NOMI_GIORNO[n.weekday()]} {n.day} {NOMI_MESE[n.month]} {n.year}.", "intent": "data"}

    # --- convenevoli
    if re.fullmatch(r"(ciao|salve|buongiorno|buonasera|buon pomeriggio|ehi|hey|buondì|ci sei\??)[ !.]*", low):
        return {"reply": _greeting(s), "intent": "saluto"}
    if re.search(r"cosa sai fare|aiuto|help|cosa puoi fare|come funzioni", low):
        return {"reply": HELP, "intent": "aiuto"}
    # --- chiacchiere, calcoli, conversioni, timer, barzellette... (app/smalltalk.py)
    st = smalltalk.reply(low, raw, s, n, history)
    if st:
        if st.get("rewrite"):
            return handle(st["rewrite"], history)
        return st

    # --- conversazione libera: modello locale
    ans = brain.chat(history or [], raw, context(), s["assistente"], s["utente"])
    if ans:
        return {"reply": ans, "intent": "llm", "source": brain.status()["model"]}
    return {"reply": _unknown(s), "intent": "sconosciuto"}
