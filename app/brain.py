
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PERSONA_PATH = BASE / "config" / "profilo_segretaria.md"

BACKEND = os.getenv("LLM_BACKEND", "ollama").lower()          # ollama | openai | none
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
OPENAI_URL = os.getenv("LLM_URL", "http://127.0.0.1:8080").rstrip("/")
OPENAI_MODEL = os.getenv("LLM_MODEL", "local")
PREFERRED = ("qwen2.5", "llama3", "gemma", "phi", "mistral")
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "1024"))
OLLAMA_NUM_THREAD = os.getenv("OLLAMA_NUM_THREAD", "")
MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "180"))

_status: dict = {"t": 0.0, "data": {"available": False, "backend": "regole", "model": ""}}


def _http(url: str, payload: dict | None = None, timeout: float = 60.0) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def status(force: bool = False) -> dict:
    """Rileva il modello locale (cache 10 s)."""
    if not force and time.time() - _status["t"] < 10:
        return _status["data"]
    info = {"available": False, "backend": "regole", "model": ""}
    try:
        if BACKEND == "ollama":
            tags = _http(OLLAMA_URL + "/api/tags", timeout=0.8)
            names = [m["name"] for m in tags.get("models", [])]
            if names:
                pick = next((n for n in names if n == OLLAMA_MODEL or n.split(":")[0] == OLLAMA_MODEL.split(":")[0]), None)
                pick = pick or next((n for p in PREFERRED for n in names if p in n), names[0])
                info = {"available": True, "backend": "ollama", "model": pick}
        elif BACKEND == "openai":
            _http(OPENAI_URL + "/v1/models", timeout=0.8)
            info = {"available": True, "backend": "openai", "model": OPENAI_MODEL}
    except Exception:
        pass
    _status.update(t=time.time(), data=info)
    return info


def persona(assistente: str, utente: str) -> str:
    try:
        txt = PERSONA_PATH.read_text(encoding="utf-8")
    except OSError:
        txt = "Sei {assistente}, la segretaria personale di {utente}. Rispondi in italiano, in modo breve."
    return txt.replace("{assistente}", assistente).replace("{utente}", utente)


def complete(messages: list[dict], max_tokens: int = MAX_TOKENS, temperature: float = 0.4, timeout: float = 120.0) -> str | None:
    st = status()
    if not st["available"]:
        return None
    try:
        if st["backend"] == "ollama":
            options = {"temperature": temperature, "num_predict": max_tokens, "num_ctx": OLLAMA_NUM_CTX}
            if OLLAMA_NUM_THREAD:
                options["num_thread"] = int(OLLAMA_NUM_THREAD)
            r = _http(OLLAMA_URL + "/api/chat", {
                "model": st["model"], "messages": messages, "stream": False, "keep_alive": OLLAMA_KEEP_ALIVE,
                "options": options}, timeout)
            return (r.get("message", {}).get("content") or "").strip() or None
        r = _http(OPENAI_URL + "/v1/chat/completions", {
            "model": st["model"], "messages": messages, "max_tokens": max_tokens,
            "temperature": temperature}, timeout)
        return (r["choices"][0]["message"]["content"] or "").strip() or None
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError, OSError):
        _status["t"] = 0  # forza un nuovo rilevamento al prossimo giro
        return None


def chat(history: list[dict], text: str, context: str, assistente: str, utente: str) -> str | None:
    system = persona(assistente, utente) + "\n\nCONTESTO ATTUALE (usa solo questi dati):\n" + context
    msgs = [{"role": "system", "content": system}] + history[-4:] + [{"role": "user", "content": text}]
    out = complete(msgs)
    return re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip() if out else None


# ----------------------------------------------------------------------------------------
# Suggerimenti di risposta
# ----------------------------------------------------------------------------------------
TONES = {
    "breve": "molto breve e diretta (max 12 parole)",
    "cordiale": "cordiale e calda, 1-2 frasi",
    "formale": "formale e professionale, 2 frasi",
    "declina": "gentile ma che rifiuta o rimanda",
    "accetta": "positiva, che accetta o conferma",
}

_TEMPLATES = [
    ("auguri", r"auguri|buon compleanno|buon natale|buona pasqua|felice anno|buone feste",
     [("Grazie!", "Grazie mille {n}, sei carissimo/a! 🙏"),
      ("Calorosa", "Grazie di cuore {n}, il tuo pensiero mi fa tanto piacere. Ricambio gli auguri!"),
      ("Formale", "La ringrazio sentitamente per gli auguri, che ricambio con piacere.")]),
    ("ringraziamento", r"\bgrazie\b|ti ringrazio|la ringrazio",
     [("Breve", "Di nulla {n}! 😊"), ("Cordiale", "Figurati {n}, è stato un piacere!"),
      ("Formale", "Prego, resto a disposizione per qualsiasi necessità.")]),
    ("pagamento", r"fattura|pagament|sollecit|scadenz|bonifico|saldo|importo|preventivo",
     [("Confermo e verifico", "Ciao {n}, ricevuto. Verifico e ti aggiorno entro oggi."),
      ("Formale", "Buongiorno, ho preso in carico la sua comunicazione. Effettuerò le verifiche necessarie e le darò riscontro al più presto."),
      ("Chiedo dettagli", "Ciao {n}, puoi rimandarmi il documento/importo esatto così controllo subito?")]),
    ("richiamo", r"chiamami|richiam|puoi chiamare|ti chiamo|sentiamoci|telefon",
     [("Ti chiamo io", "Ciao {n}, ti richiamo appena sono libero/a."),
      ("Non posso ora", "Ora non posso parlare, ti scrivo io appena possibile. Va bene se ci sentiamo più tardi?"),
      ("Formale", "Buongiorno, la ricontatterò telefonicamente appena mi sarà possibile. Grazie per la pazienza.")]),
    ("invito", r"sei libero|sei libera|ti va|che ne dici|ci vediamo|vediamoci|cena|pranzo|aperitivo|caffè|uscire|stasera|weekend|possiamo vederci|disponibil",
     [("Accetto", "Volentieri {n}! Dimmi tu orario e posto."),
      ("Rimando", "Ciao {n}, grazie dell'invito! Oggi non riesco, possiamo sentirci per un altro giorno?"),
      ("Formale (declino)", "La ringrazio per l'invito; purtroppo non mi è possibile partecipare. Spero in una prossima occasione.")]),
    ("orario", r"a che ora|quando|che ore|orario|a che punto",
     [("Chiedo conferma", "Ciao {n}, ti confermo a breve l'orario esatto."),
      ("Propongo", "Per me va bene nel pomeriggio, ti scrivo l'ora precisa tra poco."),
      ("Formale", "Le confermerò l'orario nel più breve tempo possibile.")]),
    ("appuntamento", r"appuntamento|riunione|meeting|incontro|conferma|call\b|videochiamata",
     [("Confermo", "Ciao {n}, confermo l'appuntamento. A presto!"),
      ("Propongo altro orario", "Ciao {n}, purtroppo l'orario non mi è comodo: puoi proporre un'alternativa?"),
      ("Formale", "Buongiorno, confermo la mia presenza all'incontro. Grazie e a presto.")]),
]
_GENERIC = [("Ricevuto", "Ciao {n}, ricevuto, ti rispondo con calma appena posso."),
            ("Cordiale", "Ciao {n}, grazie per il messaggio! Ci sento più tardi e ti scrivo meglio."),
            ("Formale", "Buongiorno, ho ricevuto il suo messaggio e le risponderò al più presto.")]


def _first_name(name: str | None) -> str:
    if not name:
        return ""
    s = re.sub(r"[\w.+-]+@[\w.-]+", "", name).strip()
    return s.split()[0].capitalize() if s and not s.startswith("+") else ""


def _rules_replies(msg: dict, tone: str | None) -> list[dict]:
    text = f"{msg.get('subject') or ''} {msg.get('body') or ''}".lower()
    n = _first_name(msg.get("name") or msg.get("sender"))
    chosen = _GENERIC
    for _, pat, opts in _TEMPLATES:
        if re.search(pat, text):
            chosen = opts; break
    out = [{"label": l, "text": re.sub(r"\s+([,!.])", r"\1", t.replace("{n}", n).replace("  ", " ")).replace("Ciao ,", "Ciao,")}
           for l, t in chosen]
    if tone == "declina":
        out.sort(key=lambda r: 0 if any(k in r["label"].lower() for k in ("declin", "rimand", "non posso")) else 1)
    elif tone == "accetta":
        out.sort(key=lambda r: 0 if any(k in r["label"].lower() for k in ("accett", "confermo", "ti chiamo")) else 1)
    elif tone == "formale":
        out.sort(key=lambda r: 0 if "formal" in r["label"].lower() else 1)
    elif tone == "breve":
        out.sort(key=lambda r: len(r["text"]))
    return out


def suggest_replies(msg: dict, tone: str | None, assistente: str, utente: str) -> dict:
    """Restituisce {source, replies:[{label,text}]}. Usa l'LLM locale se c'è, altrimenti regole."""
    if status()["available"]:
        stile = TONES.get(tone or "", "una versione breve, una cordiale e una formale")
        prompt = (
            f"{utente} ha ricevuto questo messaggio da {msg.get('name') or msg.get('sender')}"
            f"{' (oggetto: ' + msg['subject'] + ')' if msg.get('subject') else ''}:\n«{(msg.get('body') or '')[:700]}»\n\n"
            f"Scrivi 3 possibili risposte che {utente} può inviare, in italiano, in prima persona. Stile richiesto: {stile}. "
            "Non inventare orari, importi o fatti. Rispondi SOLO con un array JSON di 3 oggetti "
            '[{"label":"2-3 parole","text":"..."}], senza altro testo.')
        out = complete([{"role": "system", "content": persona(assistente, utente)},
                            {"role": "user", "content": prompt}], max_tokens=min(420, MAX_TOKENS * 2), temperature=0.6)
        if out:
            m = re.search(r"\[.*\]", out, re.S)
            try:
                arr = json.loads(m.group(0)) if m else []
                arr = [{"label": str(a["label"])[:30], "text": str(a["text"]).strip()}
                       for a in arr if isinstance(a, dict) and a.get("text")]
                if arr:
                    return {"source": "llm", "replies": arr[:3]}
            except (ValueError, KeyError, TypeError):
                pass
    return {"source": "regole", "replies": _rules_replies(msg, tone)}
