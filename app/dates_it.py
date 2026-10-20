
from __future__ import annotations

import re
from datetime import datetime, timedelta

GIORNI = {"lunedi": 0, "lunedì": 0, "martedi": 1, "martedì": 1, "mercoledi": 2, "mercoledì": 2,
          "giovedi": 3, "giovedì": 3, "venerdi": 4, "venerdì": 4, "sabato": 5, "domenica": 6}
MESI = {"gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6, "luglio": 7,
        "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12}
NUMERI = {"un": 1, "uno": 1, "una": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5, "sei": 6,
          "sette": 7, "otto": 8, "nove": 9, "dieci": 10, "dodici": 12, "quindici": 15, "venti": 20,
          "trenta": 30, "quaranta": 40, "quarantacinque": 45, "sessanta": 60}
NOMI_GIORNO = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
NOMI_MESE = ["", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
             "settembre", "ottobre", "novembre", "dicembre"]

_NUM = r"(\d+|" + "|".join(sorted(NUMERI, key=len, reverse=True)) + r")"


def _n(tok: str) -> int:
    return int(tok) if tok.isdigit() else NUMERI[tok]


def _cut(text: str, span: tuple[int, int]) -> str:
    return (text[:span[0]] + " " + text[span[1]:])


def _tit(orig: str, spans) -> str:
    for sp in sorted(spans, reverse=True):
        orig = _cut(orig, sp)
    return _titolo(orig)


def parse(text: str, now: datetime | None = None):
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    o = (" " + text.strip() + " ").replace("’", "'")
    t = o.lower()

    # ---- 1) relativi: "tra 2 ore", "fra mezz'ora", "tra un'ora e mezza"
    m = re.search(r"\b(?:tra|fra)\s+(?:mezz'ora|mezzora)\b", t)
    if m:
        return now + timedelta(minutes=30), _tit(o, [m.span()]), True
    m = re.search(r"\b(?:tra|fra)\s+" + _NUM + r"\s*(?:'|)\s*(minut[oi]|or[ae]|giorn[oi]|settiman[ae])\b", t)
    if m:
        q, unit = _n(m.group(1)), m.group(2)
        rest_spans = [m.span()]
        delta = (timedelta(minutes=q) if unit.startswith("minut") else
                 timedelta(hours=q) if unit.startswith("or") else
                 timedelta(days=q) if unit.startswith("giorn") else timedelta(weeks=q))
        dt = now + delta
        has_time = unit.startswith(("minut", "or"))
        if not has_time:
            dt = dt.replace(hour=9, minute=0)
        return dt, _tit(o, rest_spans), has_time

    day: datetime | None = None
    weekday_same_day = False
    part = None          # mattina / pomeriggio / sera / notte
    default_hour = None
    spans: list[tuple[int, int]] = []

    # ---- 2) giorno
    for pat, fn in [
        (r"\bdopodomani\b", lambda: now + timedelta(days=2)),
        (r"\bdomani\b", lambda: now + timedelta(days=1)),
        (r"\boggi\b", lambda: now),
    ]:
        m = re.search(pat, t)
        if m:
            day = fn(); spans.append(m.span()); break
    for pat, d, hr, p in [(r"\bdomattina\b", 1, 9, "mattina"), (r"\bstamattina\b|\bstamane\b", 0, 9, "mattina"),
                          (r"\bstasera\b", 0, 21, "sera"), (r"\bstanotte\b", 0, 23, "notte")]:
        m = re.search(pat, t)
        if m:
            day = now + timedelta(days=d); default_hour = hr; part = p; spans.append(m.span()); break

    if day is None:
        m = re.search(r"\b(?:il\s+|di\s+)?(lunedi|lunedì|martedi|martedì|mercoledi|mercoledì|giovedi|giovedì|"
                      r"venerdi|venerdì|sabato|domenica)(?:\s+(?:prossim[oa]|che viene))?\b", t)
        if m:
            target = GIORNI[m.group(1)]
            ahead = (target - now.weekday()) % 7
            if "prossim" in m.group(0) or "che viene" in m.group(0):
                ahead = ahead or 7
            elif ahead == 0:
                weekday_same_day = True
            day = now + timedelta(days=ahead); spans.append(m.span())

    if day is None:
        m = re.search(r"\b(?:il\s+)?(\d{1,2})\s+(" + "|".join(MESI) + r")(?:\s+(\d{4}))?\b", t)
        if m:
            d_, mo, y = int(m.group(1)), MESI[m.group(2)], int(m.group(3) or now.year)
            try:
                day = now.replace(year=y, month=mo, day=d_)
                if day.date() < now.date() and not m.group(3):
                    day = day.replace(year=y + 1)
                spans.append(m.span())
            except ValueError:
                day = None
    if day is None:
        m = re.search(r"\b(?:il\s+)?(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t)
        if m:
            d_, mo = int(m.group(1)), int(m.group(2))
            y = int(m.group(3) or now.year); y += 2000 if y < 100 else 0
            try:
                day = now.replace(year=y, month=mo, day=d_)
                if day.date() < now.date() and not m.group(3):
                    day = day.replace(year=y + 1)
                spans.append(m.span())
            except ValueError:
                day = None

    # ---- 3) parte del giorno
    for pat, p, hr in [(r"\bdi mattina\b|\bla mattina\b|\bmattina\b", "mattina", 9),
                       (r"\bdel pomeriggio\b|\bdi pomeriggio\b|\bpomeriggio\b", "pomeriggio", 15),
                       (r"\bdi sera\b|\bdella sera\b|\bla sera\b|\bsera\b", "sera", 20),
                       (r"\bdi notte\b|\bnotte\b", "notte", 23)]:
        m = re.search(pat, t)
        if m and part is None:
            part, default_hour = p, hr; spans.append(m.span()); break

    # ---- 4) ora
    hour = minute = None
    if re.search(r"\bmezzogiorno\b", t):
        hour, minute = 12, 0; spans.append(re.search(r"\bmezzogiorno\b", t).span())
    elif re.search(r"\bmezzanotte\b", t):
        hour, minute = 0, 0; spans.append(re.search(r"\bmezzanotte\b", t).span())
    else:
        m = (re.search(r"\b(?:alle|all'|ore|h)\s*(?:ore\s*)?(\d{1,2})(?:\s*[:.]\s*(\d{2})|\s+e\s+(mezza|mezzo|un quarto|\d{1,2}))?\b", t)
             or re.search(r"\b(\d{1,2})[:.](\d{2})\b", t))
        if m:
            hour = int(m.group(1))
            g2 = m.group(2)
            g3 = m.group(3) if m.re.groups >= 3 else None
            if g2 and g2.isdigit():
                minute = int(g2)
            elif g3 in ("mezza", "mezzo"):
                minute = 30
            elif g3 == "un quarto":
                minute = 15
            elif g3 and g3.isdigit():
                minute = int(g3)
            else:
                minute = 0
            spans.append(m.span())
        elif re.search(r"\ball'una\b", t):
            m = re.search(r"\ball'una\b", t); hour, minute = 1, 0; spans.append(m.span())

    if hour is not None:
        if hour > 24 or (minute or 0) > 59:
            return None, _tit(o, []), False
        if part in ("pomeriggio", "sera", "notte") and hour < 12:
            hour += 12 if not (part == "notte" and hour in (11, 12)) else 0
        elif part is None and 1 <= hour <= 6:
            hour += 12                      # "alle 3" -> 15:00
        hour %= 24
        has_time = True
    elif default_hour is not None:
        hour, minute, has_time = default_hour, 0, True
    else:
        hour, minute, has_time = 9, 0, False

    if day is None and not spans:
        return None, _tit(o, []), False
    if day is None:                         # solo ora -> oggi, o domani se già passata
        day = now
        cand = day.replace(hour=hour, minute=minute or 0)
        if cand <= now:
            day = now + timedelta(days=1)
    dt = day.replace(hour=hour, minute=minute or 0)

    if weekday_same_day and dt <= now:
        dt += timedelta(days=7)
    return dt, _tit(o, spans), has_time


_FILLER = (r"ricordami(?: di| che)?|ricordati(?: di| che)?|segna(?:mi)?(?: in agenda)?|metti(?:mi)? in agenda|"
           r"aggiungi(?: in agenda)?(?: un)?(?: impegno| promemoria| appuntamento)?|"
           r"crea(?: un)?(?: impegno| promemoria)?|imposta(?: un)?(?: promemoria| sveglia)?|"
           r"ho(?: un)?(?: impegno)?|devo")


def _titolo(rest: str) -> str:
    s = re.sub(r"\b(?:" + _FILLER + r")\b", " ", rest, flags=re.I)
    s = re.sub(r"\b(alle|all'|ore|il|per|di|che|a|un|una|lo|la)\b(?=\s*$)", " ", s, flags=re.I)
    s = re.sub(r"^\s*(di|che|per|:|-|,)\s+", " ", s.strip(), flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" ,.:-")
    s = re.sub(r"^(di|che|per)\s+", "", s, flags=re.I)
    return s[:1].upper() + s[1:] if s else ""


def format_when(dt: datetime, has_time: bool = True, now: datetime | None = None) -> str:
    now = now or datetime.now()
    d = (dt.date() - now.date()).days
    if d == 0:
        base = "oggi"
    elif d == 1:
        base = "domani"
    elif d == 2:
        base = "dopodomani"
    elif 2 < d < 7:
        base = NOMI_GIORNO[dt.weekday()]
    else:
        base = f"{NOMI_GIORNO[dt.weekday()]} {dt.day} {NOMI_MESE[dt.month]}"
    return f"{base} alle {dt:%H:%M}" if has_time else f"{base}, tutto il giorno"
