
import os, sys, tempfile, unittest
from datetime import datetime
from pathlib import Path

os.environ["Aria_DB"] = str(Path(tempfile.mkdtemp()) / "t.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["LLM_BACKEND"] = "none"

from app import assistant, db, report, spam  # noqa: E402
from app.dates_it import parse  # noqa: E402

NOW = datetime(2026, 10, 20, 11, 38)  # martedì


class Dates(unittest.TestCase):
    def t(self, s): return parse(s, NOW)
    def test_domani_alle(self):
        dt, title, ht = self.t("ricordami di chiamare Marco domani alle 15")
        self.assertEqual(dt, datetime(2026, 10, 21, 15, 0)); self.assertEqual(title, "Chiamare Marco"); self.assertTrue(ht)
    def test_relativo(self): self.assertEqual(self.t("tra 2 ore chiama Luca")[0], datetime(2026, 10, 20, 13, 38))
    def test_mezzora(self): self.assertEqual(self.t("tra mezz'ora pasta")[0], datetime(2026, 10, 20, 12, 8))
    def test_e_mezza(self): self.assertEqual(self.t("alle 10 e mezza caffè")[0], datetime(2026, 10, 21, 10, 30))
    def test_weekday_prossimo(self): self.assertEqual(self.t("lunedì prossimo alle 9:30 riunione")[0], datetime(2026, 10, 26, 9, 30))
    def test_data_mese(self):
        dt, _, ht = self.t("il 25 ottobre cena"); self.assertEqual(dt.date(), datetime(2026, 10, 25).date()); self.assertFalse(ht)
    def test_stasera(self): self.assertEqual(self.t("stasera alle 8 palestra")[0], datetime(2026, 10, 20, 20, 0))
    def test_senza_data(self): self.assertIsNone(self.t("comprare il latte")[0])
    def test_consegna_non_perde_pezzi(self): self.assertEqual(self.t("tra 3 giorni consegna documenti")[1], "Consegna documenti")


class Spam(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.x("INSERT OR REPLACE INTO contacts(number,name,vip) VALUES('+393331234567','Marco',1)")
    def test_contatto_mai_bloccato(self): self.assertEqual(spam.analyze("call", "333 1234567")["action"], "allow")
    def test_a_pagamento(self): self.assertEqual(spam.analyze("call", "+39 899 123456")["action"], "block")
    def test_one_ring(self): self.assertEqual(spam.analyze("call", "+225 07 123456")["action"], "block")
    def test_phishing(self):
        r = spam.analyze("sms", "Poste", "Il tuo conto è bloccato. Verifica i tuoi dati: bit.ly/x")
        self.assertEqual(r["action"], "block")
    def test_sms_normale(self): self.assertEqual(spam.analyze("sms", "+393405550011", "Ci vediamo alle 8?")["action"], "allow")
    def test_lista_nera_prefisso(self):
        db.x("INSERT OR REPLACE INTO blocklist(pattern,reason,created) VALUES('+3902999*','test','x')")
        self.assertEqual(spam.analyze("call", "+390299912345")["score"], 100)


class Assistente(unittest.TestCase):
    def test_agenda_e_elenco(self):
        r = assistant.handle("ricordami domani alle 15 il dentista")
        self.assertEqual(r["intent"], "agenda")
        self.assertIn("dentista", assistant.handle("cosa ho domani?")["reply"].lower())
    def test_registra(self):
        self.assertEqual(assistant.handle("registra")["action"], "start_recording")
        self.assertEqual(assistant.handle("ferma la registrazione")["action"], "stop_recording")
    def test_ingest_duplicato(self):
        assistant.ingest("email", "a@b.it", "ciao", "x", ext_id="id-1")
        self.assertTrue(assistant.ingest("email", "a@b.it", "ciao", "x", ext_id="id-1").get("duplicate"))
    def test_risposte_senza_llm(self):
        assistant.ingest("sms", "+393479876543", "Stasera cena fuori? Che ne dici?")
        r = assistant.handle("come rispondo?")
        self.assertEqual(len(r["data"]["replies"]), 3); self.assertEqual(r["data"]["source"], "regole")
    def test_report(self):
        self.assertIn("chiamat", report.build("24h")["spoken"].lower() + "chiamat")


if __name__ == "__main__":
    unittest.main()
