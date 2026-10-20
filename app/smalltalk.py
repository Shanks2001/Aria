"""Risposte "da compagnia" e utilità veloci, tutte con regole (nessun modello, nessuna chiave).

Viene chiamato da assistant.handle() DOPO i comandi importanti (agenda, spam, report...) e PRIMA del modello locale.
Restituisce un dict {"reply":..., "intent":...} oppure None se non riconosce la frase.
"""
from __future__ import annotations

import ast
import operator
import random
import re
from datetime import date, datetime, timedelta

from .dates_it import NOMI_GIORNO, NOMI_MESE, parse


def _pick(*opts: str) -> str:
    return random.choice(opts)


BARZELLETTE = [
    "Perché il computer va dal dottore? Perché ha preso un virus!",
    "Sai qual è l'animale più veloce a scuola? Il bidello: ti dice sempre «un attimo» e sparisce.",
    "Un pomodoro attraversa la strada e viene investito. Il giorno dopo? Passata.",
    "Cosa dice un muro a un altro muro? Ci vediamo all'angolo.",
    "Perché gli scheletri non vanno alle feste? Perché non hanno corpo per ballare.",
    "Che differenza c'è tra un pianoforte e un pesce? Il pianoforte lo accordi, il pesce lo scordi.",
    "Qual è il colmo per un elettricista? Non avere corrente di pensiero.",
    "Qual è il colmo per un giardiniere? Avere un diavolo per capello… e mandare tutto a rotoli.",
    "Perché il libro di matematica è triste? Perché ha troppi problemi.",
    "Cosa fa un ragno al computer? Crea un sito web.",
    "Qual è il colmo per un sarto? Perdere il filo del discorso.",
    "Come si chiama un cane senza zampe? Non importa, tanto non viene quando lo chiami.",
]
CURIOSITA = [
    "Il miele non scade mai: ne hanno trovato di commestibile in tombe egizie di oltre tremila anni fa.",
    "Il cuore di un gambero si trova nella testa.",
    "Una giornata su Venere dura più di un anno venusiano.",
    "I polpi hanno tre cuori e il sangue blu.",
    "La Torre Eiffel d'estate diventa più alta di qualche centimetro, per via della dilatazione del metallo.",
    "Gli squali esistono da prima degli alberi.",
    "Le banane sono leggermente radioattive, ma del tutto innocue.",
    "In Italia ci sono più di 500 tipi di formaggio riconosciuti.",
    "Il sole rappresenta circa il 99,8 per cento della massa del sistema solare.",
    "Le api possono riconoscere i volti umani.",
]
MOTIVAZIONE = [
    "Un passo alla volta: oggi fai la cosa più importante, il resto viene dopo.",
    "Non devi essere perfetto, devi solo cominciare.",
    "Le grandi cose sono fatte di piccole abitudini ripetute.",
    "Respira. Ce la fai, l'hai già fatto in passato.",
    "Fai una cosa sola e falla bene: poi vedi come ti senti.",
]

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.Mod: operator.mod}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        a, b = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(b) > 12 or abs(a) > 1e6):
            raise ValueError("troppo grande")
        return _OPS[type(node.op)](a, b)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("non valido")


def _num(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return f"{int(round(x)):,}".replace(",", ".")
    return f"{x:.4f}".rstrip("0").rstrip(".").replace(".", ",")


def _calc(low: str) -> str | None:
    t = low
    m = re.search(r"(?:quanto fa|quanto è|quanto e'|calcola|calcolami|dimmi quanto fa|risultato di)\s+(.+)$", t)
    if not m:
        return None
    expr = m.group(1).strip(" ?.!")
    # percentuale: "il 20 per cento di 150"
    p = re.fullmatch(r"(?:il\s+)?(\d+(?:[.,]\d+)?)\s*(?:%|per cento|percento)\s+di\s+(\d+(?:[.,]\d+)?)", expr)
    if p:
        a, b = float(p.group(1).replace(",", ".")), float(p.group(2).replace(",", "."))
        return f"Il {_num(a)} per cento di {_num(b)} è {_num(a * b / 100)}."
    for w, s in ((" più ", "+"), (" meno ", "-"), (" per ", "*"), (" x ", "*"), (" diviso ", "/"), (" fratto ", "/"),
                 (" elevato a ", "**"), (" alla ", "**"), (" modulo ", "%")):
        expr = expr.replace(w, s)
    expr = expr.replace("×", "*").replace("÷", "/").replace("^", "**").replace(",", ".")
    expr = re.sub(r"(\d)\s+(?=\d)", r"\1", expr)
    if not re.fullmatch(r"[\d+\-*/().% \s]+", expr) or not re.search(r"\d", expr):
        return None
    try:
        val = _eval(ast.parse(expr.strip(), mode="eval"))
    except ZeroDivisionError:
        return "Dividere per zero non si può: nemmeno io ci riesco."
    except Exception:
        return None
    return f"Fa {_num(val)}."


_UNITS = [  # (regex unità di partenza, nome, fattore/funzione, unità di arrivo)
    (r"km|chilometri?", "km", lambda v: v * 0.621371, "miglia"),
    (r"miglia|miglio", "miglia", lambda v: v / 0.621371, "chilometri"),
    (r"kg|chili|chilogrammi?|chilo", "kg", lambda v: v * 2.20462, "libbre"),
    (r"libbre|libbra|lb", "libbre", lambda v: v / 2.20462, "chili"),
    (r"metri|metro|m", "m", lambda v: v * 3.28084, "piedi"),
    (r"piedi|piede|ft", "piedi", lambda v: v / 3.28084, "metri"),
    (r"cm|centimetri", "cm", lambda v: v / 2.54, "pollici"),
    (r"pollici|pollice", "pollici", lambda v: v * 2.54, "centimetri"),
    (r"litri|litro|l", "litri", lambda v: v * 0.264172, "galloni"),
    (r"gradi celsius|celsius|°c", "°C", lambda v: v * 9 / 5 + 32, "gradi Fahrenheit"),
    (r"gradi fahrenheit|fahrenheit|°f", "°F", lambda v: (v - 32) * 5 / 9, "gradi Celsius"),
]


def _convert(low: str) -> str | None:
    m = re.search(r"(?:quant[oi] (?:sono|fanno|fa)|converti|convertimi|trasforma)\s+(\d+(?:[.,]\d+)?)\s+([a-z°]+(?:\s[a-z]+)?)", low)
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    unit = m.group(2)
    for rx, name, f, to in _UNITS:
        if re.fullmatch(rx, unit) or re.fullmatch(rx, unit.split()[0]):
            return f"{_num(v)} {name} sono circa {_num(round(f(v), 2))} {to}."
    return None


FESTE = {"natale": (12, 25), "capodanno": (1, 1), "ferragosto": (8, 15), "halloween": (10, 31), "epifania": (1, 6),
         "san valentino": (2, 14), "la festa della repubblica": (6, 2), "il primo maggio": (5, 1), "la befana": (1, 6)}


def _countdown(low: str, raw: str, n: datetime) -> str | None:
    m = re.search(r"(?:quanti giorni (?:mancano|ci sono|restano|rimangono)|quanto manca)\s+(?:a|al|alla|all'|fino a|fino al|per|per il|per la)\s*(.+)$", low)
    if not m:
        return None
    what = m.group(1).strip(" ?.!")
    target = None
    for k, (mm, dd) in FESTE.items():
        if k in what:
            y = n.year
            target = date(y, mm, dd)
            if target < n.date():
                target = date(y + 1, mm, dd)
            break
    if target is None:
        dt, _, _ = parse("il " + what if not re.match(r"(il|domani|lunedì|martedì|mercoledì|giovedì|venerdì|sabato|domenica|\d)", what) else what, n)
        if dt:
            target = dt.date()
    if not target:
        return None
    d = (target - n.date()).days
    nm = f"{target.day} {NOMI_MESE[target.month]}"
    if d == 0:
        return "È oggi!"
    if d == 1:
        return f"Manca un giorno: è domani, {nm}."
    if d < 0:
        return f"Era {-d} giorni fa, il {nm}."
    w = f" (circa {d // 7} settimane)" if d >= 14 else ""
    return f"Mancano {d} giorni{w}: {NOMI_GIORNO[target.weekday()]} {nm}."


def reply(low: str, raw: str, s: dict, n: datetime, history: list[dict] | None = None) -> dict | None:
    name, user = s["assistente"], s["utente"]
    h = n.hour

    # ---- controllo dell'assistente stesso
    if re.fullmatch(r"(zitta|zitto|silenzio|taci|stai zitta|basta parlare|smettila|fermati)[ !.]*", low):
        return {"reply": "Ok.", "intent": "controllo", "action": "stop_speaking"}
    if re.search(r"(smetti|spegni|disattiva|non) (di )?ascoltar|spegni (il )?microfono|disattiva (il )?microfono|chiudi (il )?microfono", low):
        return {"reply": "Va bene, smetto di ascoltare. Riattivami con il pulsante del microfono.", "intent": "controllo", "action": "mic_off"}
    if re.search(r"(ripeti|ridimmi|non ho capito|puoi ripetere|come hai detto)", low):
        last = next((m["content"] for m in reversed(history or []) if m.get("role") == "assistant"), None)
        return {"reply": last or "Non ho ancora detto niente da ripetere.", "intent": "ripeti"}

    # ---- identità
    if re.search(r"come ti chiami|qual è il tuo nome|chi sei|presentati", low):
        return {"reply": f"Sono {name}, la tua segretaria personale. Giro sul tuo computer e non mando i tuoi dati a nessuno.", "intent": "identita"}
    if re.search(r"chi ti ha (creat|fatt|costruit|programmat)|chi è il tuo (creatore|padre)", low):
        return {"reply": f"Mi ha costruita {user}, insieme a Claude di Anthropic, pezzo per pezzo.", "intent": "identita"}
    if re.search(r"quanti anni hai|quando sei nata|che età hai", low):
        return {"reply": "Sono molto giovane: sono nata da poco, come tutti i programmi. Ma imparo in fretta.", "intent": "identita"}
    if re.search(r"sei (un |una )?(robot|umana|umano|viva|reale|intelligente|un'ia|un'intelligenza)|sei una persona", low):
        return {"reply": "Sono un programma: ti rispondo con regole e, se lo attivi, con un modello locale. Niente di magico, ma faccio del mio meglio.", "intent": "identita"}
    if re.search(r"dove (vivi|abiti|sei)", low):
        return {"reply": "Vivo nel tuo computer, in una cartella che si chiama Aria. Mi piace, è ordinata.", "intent": "identita"}

    # ---- stato d'animo / convenevoli
    if re.fullmatch(r"(?:ciao\W+)?(?:come (?:stai|va|ti senti|butta)|tutto bene)(?: oggi| aria| tutto bene)?[ ?!.]*", low):
        return {"reply": _pick("Sto benissimo, grazie! Pronta ad aiutarti.", "Tutto a posto! E tu come stai?", "Bene, grazie. Hai qualcosa per me?")}
    if re.search(r"\b(sto bene|tutto bene|bene grazie|alla grande|non c'è male)\b", low):
        return {"reply": _pick("Ne sono felice!", "Ottimo, allora si lavora volentieri.", "Bene così!")}
    if re.search(r"\b(sono stanc|sono triste|sono stress|sono nervos|che giornata|non ce la faccio)\b", low):
        return {"reply": _pick(f"Mi dispiace, {user}. Se vuoi, tolgo di mezzo qualcosa dall'agenda o ti preparo un riepilogo veloce.",
                               "Capita. Fai una pausa di cinque minuti: ti avviso io quando è finita, vuoi?")}
    if re.search(r"ho noia|mi annoio|sono annoiat", low):
        return {"reply": _pick("Ti racconto una curiosità: " + random.choice(CURIOSITA), "Una barzelletta? " + random.choice(BARZELLETTE))}
    if re.fullmatch(r"(grazie|ti ringrazio|grazie mille|molte grazie|grazie tante|ti ringrazio tanto)[ !.]*", low):
        return {"reply": _pick("Di nulla!", "Prego!", "Figurati, è il mio lavoro.", "Sono qui per questo.")}
    if re.fullmatch(r"(perfetto|ok|okay|va bene|bene|ottimo|capito|d'accordo|certo)[ !.]*", low):
        return {"reply": _pick("Perfetto.", "Va bene.", "Ricevuto.", "Sono qui se ti serve altro.")}
    if re.search(r"\b(sei brava|sei bravissima|sei fantastica|sei un genio|complimenti|ottimo lavoro|bel lavoro|sei utile|sei la migliore)\b", low):
        return {"reply": _pick("Grazie, mi fa piacere!", "Ti ringrazio, è il tuo incoraggiamento che mi migliora.", "Troppo gentile!")}
    if re.search(r"\b(scusa|scusami|mi dispiace|perdonami)\b", low) and len(low.split()) < 6:
        return {"reply": _pick("Non c'è nessun problema.", "Figurati, nessun problema.")}
    if re.search(r"\b(stupida|scema|inutile|non funzioni|fai schifo)\b", low):
        return {"reply": "Hai ragione, a volte sbaglio. Dimmi cosa non ha funzionato e provo a farlo meglio."}
    if re.search(r"ti voglio bene|ti amo|sei simpatica", low):
        return {"reply": _pick("Che carino! Anche io ti sono affezionata, a modo mio.", "Grazie, mi hai fatto sorridere. Si fa per dire.")}
    if re.fullmatch(r"(buonanotte|buona notte|notte|a domani|ci sentiamo|ci vediamo)[ !.]*", low):
        return {"reply": _pick(f"Buonanotte, {user}. Domani ti ricordo io gli impegni.", "Riposa bene, ci sentiamo domani.")}
    if re.fullmatch(r"(arrivederci|addio|a dopo|a presto|ciao ciao|ci vediamo dopo|alla prossima|saluti|bye|ci sentiamo dopo)[ !.]*", low):
        return {"reply": _pick("A presto!", "A dopo! Io resto qui.", "Ciao ciao!")}
    if re.search(r"buon(a)? (appetito|pranzo|cena|giornata|weekend|fine settimana|lavoro|viaggio)", low):
        return {"reply": _pick("Grazie, altrettanto!", "Grazie! Anche a te.")}

    # ---- intrattenimento
    if re.search(r"barzelletta|battuta|raccontami (una cosa )?(divertente|che fa ridere)|fammi ridere", low):
        return {"reply": random.choice(BARZELLETTE), "intent": "barzelletta"}
    if re.search(r"curiosit|dimmi (qualcosa|una cosa) (di )?(interessante|nuov)|lo sapevi", low):
        return {"reply": random.choice(CURIOSITA), "intent": "curiosita"}
    if re.search(r"motivami|frase motivazionale|dammi (la )?carica|incoraggiami|fammi coraggio", low):
        return {"reply": random.choice(MOTIVAZIONE), "intent": "motivazione"}
    if re.search(r"lancia (una )?moneta|testa o croce", low):
        return {"reply": random.choice(["È uscita testa.", "È uscita croce."]), "intent": "gioco"}
    if re.search(r"lancia (un |il )?dado|tira (un |il )?dado", low):
        return {"reply": f"È uscito {random.randint(1, 6)}.", "intent": "gioco"}
    m = re.search(r"numero (a caso|casuale) (?:tra|fra) (\d+) e (\d+)", low)
    if m:
        a, b = sorted((int(m.group(2)), int(m.group(3))))
        return {"reply": f"Il numero è {random.randint(a, b)}.", "intent": "gioco"}
    if re.search(r"scegli (tu|per me)|decidi tu", low):
        m = re.search(r"tra (.+?) (?:e|o) (.+?)[?.! ]*$", low)
        if m:
            return {"reply": f"Scelgo {random.choice([m.group(1), m.group(2)])}.", "intent": "gioco"}

    # ---- utilità
    c = _calc(low)
    if c:
        return {"reply": c, "intent": "calcolo"}
    c = _convert(low)
    if c:
        return {"reply": c, "intent": "conversione"}
    c = _countdown(low, raw, n)
    if c:
        return {"reply": c, "intent": "countdown"}
    m = re.search(r"che giorno (?:sarà|è) (?:il )?(\d{1,2})(?:/| )(\d{1,2}|\w+)", low)
    if m:
        dt, _, _ = parse("il " + m.group(0).split("il ", 1)[-1] if "il " in m.group(0) else m.group(0), n)
        if dt:
            return {"reply": f"Sarà {NOMI_GIORNO[dt.weekday()]} {dt.day} {NOMI_MESE[dt.month]}.", "intent": "data"}
    m = re.search(r"che giorno (?:sarà|è) tra (\d+) (giorni|settimane|mesi)", low)
    if m:
        k = int(m.group(1)) * {"giorni": 1, "settimane": 7, "mesi": 30}[m.group(2)]
        d = n + timedelta(days=k)
        return {"reply": f"Tra {m.group(1)} {m.group(2)} sarà {NOMI_GIORNO[d.weekday()]} {d.day} {NOMI_MESE[d.month]}.", "intent": "data"}
    if re.search(r"che (mese|anno) è|in che (mese|anno) siamo|che anno siamo", low):
        return {"reply": f"Siamo a {NOMI_MESE[n.month]} {n.year}.", "intent": "data"}
    if re.search(r"che settimana è|numero della settimana", low):
        return {"reply": f"È la settimana numero {n.isocalendar()[1]} dell'anno.", "intent": "data"}
    if re.search(r"che stagione è|in che stagione siamo", low):
        st = "inverno" if n.month in (12, 1, 2) else "primavera" if n.month in (3, 4, 5) else "estate" if n.month in (6, 7, 8) else "autunno"
        return {"reply": f"Siamo in {st}.", "intent": "data"}
    if re.search(r"è (mattina|pomeriggio|sera|notte)|che momento della giornata", low):
        mom = "mattina" if 5 <= h < 13 else "pomeriggio" if 13 <= h < 18 else "sera" if 18 <= h < 23 else "notte"
        return {"reply": f"Sono le {n:%H:%M}: è {mom}.", "intent": "ora"}
    m = re.search(r"(?:imposta|metti|avvia|fai partire|crea)\s+(?:un |il )?(?:timer|sveglia|conto alla rovescia)\s+(?:di|da|per)\s+(\d+)\s*(minut|or)", low)
    if m:
        unit = "minuti" if m.group(2) == "minut" else "ore"
        return {"reply": "", "intent": "timer", "rewrite": f"ricordami tra {m.group(1)} {unit} che il timer è finito"}
    if re.search(r"che tempo fa|meteo|previsioni|piove|fa freddo|fa caldo", low):
        return {"reply": "Il meteo richiede internet, e per ora lavoro solo con quello che c'è sul tuo computer. Posso però ricordarti di controllare le previsioni: dimmi quando.", "intent": "meteo"}
    if re.search(r"accendi la luce|spegni la luce|apri la porta|accendi il|spegni il", low):
        return {"reply": "Non sono collegata a dispositivi di casa. Per ora gestisco agenda, chiamate, messaggi, registrazioni e risposte.", "intent": "domotica"}
    if re.search(r"cerca su (google|internet|il web)|vai su|apri (youtube|google|il browser)", low):
        return {"reply": "Non navigo in internet da sola. Posso gestire agenda, messaggi, report e registrazioni.", "intent": "web"}
    if re.search(r"suggeriscimi (una )?ricetta|cosa (mangio|cucino)|cosa preparo", low):
        return {"reply": _pick("Una pasta aglio, olio e peperoncino: veloce e sempre buona.", "Che ne dici di un'omelette con quello che hai in frigo?", "Una bella insalata di riso, se fa caldo; una zuppa, se fa freddo."), "intent": "cucina"}
    if re.search(r"sapresti|puoi (anche )?(imparare|ricordare)", low):
        return {"reply": "Posso imparare tutto ciò che è scritto nelle regole. Se vuoi che risponda a una cosa nuova, dimmela e la aggiungiamo.", "intent": "aiuto"}
    return None