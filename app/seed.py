
from __future__ import annotations

from datetime import datetime, timedelta

from . import assistant, db


def load() -> dict:
    n = datetime.now().replace(microsecond=0)
    ago = lambda **k: (n - timedelta(**k)).isoformat(timespec="seconds")  # noqa: E731

    for num, name, vip in [("+393331234567", "Marco Rossi", 1), ("+393479876543", "Anna", 1),
                           ("+390212345678", "Studio Dr. Bianchi", 0), ("marco.cliente@azienda.it", "Luca Verdi (cliente)", 1)]:
        db.x("INSERT OR REPLACE INTO contacts(number,name,vip) VALUES(?,?,?)", (db.normalize(num), name, vip))
    db.x("INSERT OR IGNORE INTO kv(k,v) VALUES('own_number','+393281112233')")

    E = assistant.ingest
    E("call", "+393331234567", status="missed", ts=ago(minutes=25))
    for m in (170, 95, 40):                     # telemarketing insistente
        E("call", "+390687371234", ts=ago(minutes=m))
    E("call", "+22507123456", ts=ago(hours=3))  # "one ring"
    E("call", "anonimo", ts=ago(hours=5))
    E("call", "+393281112299", ts=ago(hours=6))  # quasi identico al tuo numero
    E("call", "+390212345678", status="answered", duration=140, ts=ago(hours=8))
    E("sms", "+393479876543", "Stasera cenaci fuori? Che ne dici di quel giapponese?", ts=ago(minutes=50))
    E("sms", "Poste", "Il tuo conto è bloccato. Verifica i tuoi dati entro 24 ore: http://bit.ly/poste-sicuro", ts=ago(hours=2))
    E("sms", "+393405550011", "Ciao! Hai vinto un buono regalo da 500€, clicca qui per ritirarlo www.premio-facile.top/ritira", ts=ago(hours=4))
    E("whatsapp", "+393331234567", "Mi richiami appena puoi? È urgente per il progetto.", ts=ago(minutes=22))
    E("email", "marco.cliente@azienda.it", "Buongiorno, le ricordo che la fattura n. 142 risulta in scadenza venerdì. "
      "Può confermarmi il pagamento? Grazie.", "Sollecito pagamento fattura 142", ts=ago(hours=1, minutes=10))
    E("email", "info@offerte-luce-gas.com", "Cambia gestore e risparmia! Offerta esclusiva luce e gas, sconto gratis per 12 mesi. unsubscribe",
      "Ultima chance: offerta esclusiva", ts=ago(hours=7))
    E("email", "newsletter@giornale.it", "Le notizie della giornata. Annulla iscrizione in fondo.", "Newsletter del mattino", ts=ago(hours=9))

    A = assistant.agenda_add
    A("Dentista, Dr. Bianchi", n.replace(hour=15, minute=0) + timedelta(days=1), True)
    A("Riunione con Luca Verdi", n + timedelta(hours=3), True)
    A("Compleanno di Anna", (n + timedelta(days=6)).replace(hour=9, minute=0), False)
    A("Rinnovare assicurazione auto", None, False)
    return {"ok": True}
