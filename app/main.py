
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import assistant, brain, db, mail, report, seed, spam, voice

BASE = Path(__file__).resolve().parent.parent
WEB = BASE / "web"
REC_DIR = BASE / "archivio" / "registrazioni"
OUT_DIR = BASE / "output"
for d in (REC_DIR, OUT_DIR):
    d.mkdir(parents=True, exist_ok=True)

TOKEN = os.getenv("Aria_TOKEN", "")


def J(data, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


async def body(request: Request) -> dict:
    try:
        d = await request.json()
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


# ------------------------------------------------------------------ sicurezza (opzionale)
class TokenGuard(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if TOKEN and request.url.path.startswith("/api/") and request.url.path != "/api/ping":
            sent = request.headers.get("x-token") or request.query_params.get("token")
            if sent != TOKEN:
                return J({"error": "token mancante o errato"}, 401)
        return await call_next(request)


# ------------------------------------------------------------------ stato e impostazioni
async def ping(request):
    return J({"ok": True, "auth": bool(TOKEN)})


async def status(request):
    n = datetime.now()
    s = assistant.settings()
    llm = brain.status()
    t = voice.tts_engine()
    r = report.build("24h", n)
    return J({
        "settings": s, "now": n.isoformat(timespec="seconds"),
        "llm": llm, "tts": t, "stt_server": voice.stt_available(), "mail": mail.configured(),
        "counts": r["counts"], "empty": db.q("SELECT COUNT(*) c FROM events", one=True)["c"] == 0
        and db.q("SELECT COUNT(*) c FROM agenda", one=True)["c"] == 0,
    })


async def settings_api(request):
    if request.method == "POST":
        d = await body(request)
        for k in assistant.DEFAULTS:
            if k in d and isinstance(d[k], (str, int)):
                db.put(k, str(d[k]).strip()[:60])
    return J(assistant.settings())


# ------------------------------------------------------------------ chat
async def chat(request):
    d = await body(request)
    text = str(d.get("text", "")).strip()
    if not text:
        return J({"error": "testo vuoto"}, 400)
    hist = [h for h in d.get("history", []) if isinstance(h, dict) and h.get("role") in ("user", "assistant")]
    out = await asyncio.get_running_loop().run_in_executor(None, assistant.handle, text, hist)
    return J(out)


# ------------------------------------------------------------------ eventi (chiamate / messaggi / email)
async def events_api(request):
    if request.method == "POST":  # ponte dal telefono (app Android / Tasker / Termux) e simulatore
        d = await body(request)
        try:
            r = assistant.ingest(d.get("kind", "call"), d.get("sender") or d.get("number", ""),
                                 d.get("body") or d.get("text", ""), d.get("subject", ""), d.get("name"),
                                 d.get("status"), d.get("ts"), int(d.get("duration") or 0), d.get("ext_id"))
        except ValueError as e:
            return J({"error": str(e)}, 400)
        return J(r)
    kind = request.query_params.get("kind")
    lim = min(int(request.query_params.get("limit", 60)), 300)
    where, args = "", []
    if kind == "msg":
        where = "WHERE kind IN ('sms','whatsapp')"
    elif kind in ("call", "email"):
        where, args = "WHERE kind=?", [kind]
    rows = db.q(f"SELECT * FROM events {where} ORDER BY ts DESC LIMIT {lim}", args)
    for e in rows:
        e["reasons"] = [x for x in (e["reasons"] or "").split(" | ") if x]
    return J(rows)


async def event_item(request):
    eid = int(request.path_params["id"])
    if request.method == "DELETE":
        db.x("DELETE FROM events WHERE id=?", (eid,))
    else:
        d = await body(request)
        if d.get("read") is not None:
            db.x("UPDATE events SET read=? WHERE id=?", (int(bool(d["read"])), eid))
        if d.get("not_spam"):  # falso positivo: fidati di questo mittente
            e = db.q("SELECT * FROM events WHERE id=?", (eid,), one=True)
            if e:
                db.x("UPDATE events SET verdict='ok', spam_score=0, status=CASE WHEN status='blocked' THEN 'missed' ELSE status END WHERE id=?", (eid,))
                db.x("DELETE FROM blocklist WHERE pattern=?", (e["sender"],))
                db.x("INSERT OR IGNORE INTO contacts(number,name,vip) VALUES(?,?,0)", (e["sender"], e["name"] or e["sender"]))
        if d.get("block"):
            e = db.q("SELECT * FROM events WHERE id=?", (eid,), one=True)
            if e:
                db.x("INSERT OR REPLACE INTO blocklist(pattern,reason,created) VALUES(?,?,?)",
                     (e["sender"], "bloccato dall'utente", datetime.now().isoformat()))
                db.x("UPDATE events SET verdict='spam', spam_score=100 WHERE id=?", (eid,))
    return J({"ok": True})


async def mark_all_read(request):
    db.x("UPDATE events SET read=1")
    return J({"ok": True})


async def suggest(request):
    d = await body(request)
    e = db.q("SELECT * FROM events WHERE id=?", (int(d.get("event_id", 0)),), one=True) if d.get("event_id") else None
    if not e and d.get("text"):
        e = {"name": d.get("name") or "", "sender": d.get("name") or "", "body": d["text"], "subject": ""}
    if not e:
        return J({"error": "serve event_id oppure text"}, 400)
    s = assistant.settings()
    out = await asyncio.get_running_loop().run_in_executor(
        None, brain.suggest_replies, e, d.get("tone"), s["assistente"], s["utente"])
    return J(out)


# ------------------------------------------------------------------ rubrica e lista nera
async def contacts_api(request):
    if request.method == "POST":
        d = await body(request)
        if d.get("number") and d.get("name"):
            db.x("INSERT OR REPLACE INTO contacts(number,name,vip) VALUES(?,?,?)",
                 (db.normalize(d["number"]), d["name"][:60], int(bool(d.get("vip")))))
    if request.method == "DELETE":
        db.x("DELETE FROM contacts WHERE number=?", (db.normalize(request.query_params.get("number", "")),))
    return J(db.q("SELECT * FROM contacts ORDER BY name"))


async def blocklist_api(request):
    if request.method == "POST":
        d = await body(request)
        if d.get("pattern"):
            p = d["pattern"].strip()
            p = p if p.endswith("*") else db.normalize(p)
            db.x("INSERT OR REPLACE INTO blocklist(pattern,reason,created) VALUES(?,?,?)",
                 (p, d.get("reason") or "aggiunto a mano", datetime.now().isoformat()))
    if request.method == "DELETE":
        db.x("DELETE FROM blocklist WHERE pattern=?", (request.query_params.get("pattern", ""),))
    return J(db.q("SELECT * FROM blocklist ORDER BY created DESC"))


async def spam_check(request):
    d = await body(request)
    return J(spam.analyze(d.get("kind", "call"), d.get("sender", ""), d.get("text", "")))


# ------------------------------------------------------------------ agenda
async def agenda_api(request):
    if request.method == "POST":
        d = await body(request)
        from .dates_it import parse
        text = str(d.get("text") or d.get("title") or "").strip()
        if not text:
            return J({"error": "testo vuoto"}, 400)
        if d.get("when"):
            title, dt, ht = text, datetime.fromisoformat(d["when"]), bool(d.get("has_time", True))
        else:
            dt, title, ht = parse(text)
            title = title or text
        return J(assistant.agenda_add(title, dt, ht, d.get("notes", "")))
    show_done = request.query_params.get("done") == "1"
    rows = db.q("SELECT * FROM agenda WHERE done=? ORDER BY when_ts IS NULL, when_ts", (int(show_done),))
    from .dates_it import format_when
    n = datetime.now()
    for a in rows:
        a["label"] = format_when(datetime.fromisoformat(a["when_ts"]), bool(a["has_time"]), n) if a["when_ts"] else "Da fare (senza data)"
    return J(rows)


async def agenda_item(request):
    aid = int(request.path_params["id"])
    if request.method == "DELETE":
        db.x("DELETE FROM agenda WHERE id=?", (aid,))
    else:
        d = await body(request)
        if "done" in d:
            db.x("UPDATE agenda SET done=? WHERE id=?", (int(bool(d["done"])), aid))
    return J({"ok": True})


async def due(request):
    return J(assistant.due_reminders())


# ------------------------------------------------------------------ report
async def report_api(request):
    r = report.build(request.query_params.get("period", "24h"))
    if request.query_params.get("save") == "1":
        name = f"report_{datetime.now():%Y%m%d_%H%M}.md"
        (OUT_DIR / name).write_text(r["markdown"], encoding="utf-8")
        r["saved_as"] = f"output/{name}"
    return J(r)


# ------------------------------------------------------------------ registrazioni
EXT = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/mpeg": ".mp3",
       "audio/wav": ".wav", "audio/x-m4a": ".m4a", "audio/aac": ".aac"}


async def recordings_api(request):
    if request.method == "POST":
        form = await request.form()
        up = form.get("audio")
        if up is None or not hasattr(up, "read"):
            return J({"error": "manca il campo audio"}, 400)
        ctype = (up.content_type or "audio/webm").split(";")[0].lower()
        fname = f"rec_{datetime.now():%Y%m%d_%H%M%S}{EXT.get(ctype, '.webm')}"
        data = await up.read()
        if len(data) > 200 * 1024 * 1024:
            return J({"error": "file troppo grande"}, 413)
        (REC_DIR / fname).write_bytes(data)
        title = str(form.get("title") or "").strip()[:80] or f"Registrazione {datetime.now():%d/%m %H:%M}"
        rid = db.x("INSERT INTO recordings(ts,filename,duration,title) VALUES(?,?,?,?)",
                   (datetime.now().isoformat(timespec="seconds"), fname, int(float(form.get("duration") or 0)), title))
        if voice.stt_available():
            def work():
                t = voice.transcribe(str(REC_DIR / fname))
                if t:
                    db.x("UPDATE recordings SET transcript=? WHERE id=?", (t, rid))
            threading.Thread(target=work, daemon=True).start()
        return J({"id": rid, "title": title, "transcribing": voice.stt_available()})
    return J(db.q("SELECT * FROM recordings ORDER BY ts DESC"))


async def recording_item(request):
    rid = int(request.path_params["id"])
    r = db.q("SELECT * FROM recordings WHERE id=?", (rid,), one=True)
    if not r:
        return J({"error": "non trovata"}, 404)
    if request.method == "DELETE":
        with contextlib.suppress(OSError):
            (REC_DIR / r["filename"]).unlink()
        db.x("DELETE FROM recordings WHERE id=?", (rid,))
        return J({"ok": True})
    if request.method == "PATCH":
        d = await body(request)
        if d.get("title"):
            db.x("UPDATE recordings SET title=? WHERE id=?", (str(d["title"])[:80], rid))
        return J({"ok": True})
    return J(r)


async def recording_audio(request):
    r = db.q("SELECT * FROM recordings WHERE id=?", (int(request.path_params["id"]),), one=True)
    f = REC_DIR / r["filename"] if r else None
    if not f or not f.exists():
        return J({"error": "non trovata"}, 404)
    mt = {v: k for k, v in EXT.items()}.get(f.suffix, "audio/webm")
    return FileResponse(f, media_type=mt, filename=f.name)


# ------------------------------------------------------------------ voce
async def tts(request):
    d = await body(request)
    wav = await asyncio.get_running_loop().run_in_executor(None, voice.synth, str(d.get("text", "")), d.get("voice"))
    if not wav:
        return Response(status_code=204)
    return Response(wav, media_type="audio/wav")


async def transcribe(request):
    form = await request.form()
    up = form.get("audio")
    if up is None or not hasattr(up, "read"):
        return J({"error": "manca audio"}, 400)
    if not voice.stt_available():
        return J({"error": "faster-whisper non installato sul server"}, 501)
    import tempfile
    suffix = EXT.get((up.content_type or "").split(";")[0], ".webm")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await up.read())
    try:
        text = await asyncio.get_running_loop().run_in_executor(None, voice.transcribe, tmp.name)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp.name)
    return J({"text": text or ""})


async def mail_sync(request):
    return J(await asyncio.get_running_loop().run_in_executor(None, mail.sync))


async def seed_api(request):
    return J(await asyncio.get_running_loop().run_in_executor(None, seed.load))


async def reset_api(request):
    for t in ("events", "agenda", "recordings", "contacts", "blocklist"):
        db.x(f"DELETE FROM {t}")
    return J({"ok": True})


# ------------------------------------------------------------------ file statici / PWA
async def index(request):
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


async def sw(request):
    return FileResponse(WEB / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})


async def manifest(request):
    return FileResponse(WEB / "manifest.webmanifest", media_type="application/manifest+json")


@contextlib.asynccontextmanager
async def lifespan(app):
    db.conn()
    task = None
    if mail.configured():
        async def loop():
            every = max(1, int(os.getenv("IMAP_POLL_MIN", "5"))) * 60
            while True:
                await asyncio.get_running_loop().run_in_executor(None, mail.sync)
                await asyncio.sleep(every)
        task = asyncio.create_task(loop())
    yield
    if task:
        task.cancel()


routes = [
    Route("/", index), Route("/sw.js", sw), Route("/manifest.webmanifest", manifest),
    Route("/api/ping", ping), Route("/api/status", status),
    Route("/api/settings", settings_api, methods=["GET", "POST"]),
    Route("/api/chat", chat, methods=["POST"]),
    Route("/api/events", events_api, methods=["GET", "POST"]),
    Route("/api/events/read-all", mark_all_read, methods=["POST"]),
    Route("/api/events/{id:int}", event_item, methods=["PATCH", "DELETE"]),
    Route("/api/suggest", suggest, methods=["POST"]),
    Route("/api/contacts", contacts_api, methods=["GET", "POST", "DELETE"]),
    Route("/api/blocklist", blocklist_api, methods=["GET", "POST", "DELETE"]),
    Route("/api/spam/check", spam_check, methods=["POST"]),
    Route("/api/agenda", agenda_api, methods=["GET", "POST"]),
    Route("/api/agenda/{id:int}", agenda_item, methods=["PATCH", "DELETE"]),
    Route("/api/due", due),
    Route("/api/report", report_api),
    Route("/api/recordings", recordings_api, methods=["GET", "POST"]),
    Route("/api/recordings/{id:int}", recording_item, methods=["GET", "PATCH", "DELETE"]),
    Route("/api/recordings/{id:int}/audio", recording_audio),
    Route("/api/tts", tts, methods=["POST"]),
    Route("/api/transcribe", transcribe, methods=["POST"]),
    Route("/api/mail/sync", mail_sync, methods=["POST"]),
    Route("/api/demo", seed_api, methods=["POST"]),
    Route("/api/reset", reset_api, methods=["POST"]),
    Mount("/static", StaticFiles(directory=WEB, check_dir=False), name="static"),
]

app = Starlette(routes=routes, lifespan=lifespan)
app.add_middleware(TokenGuard)
