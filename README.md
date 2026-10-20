# Aria — segretaria AI personale (senza chiavi API)

Prototipo che gira sul PC e, con lo stesso codice, sul cellulare. Interfaccia ispirata agli HUD di fantascienza.
Il nome si cambia in Impostazioni (o in `.env`). Nessun servizio a pagamento, nessuna chiave: tutto è locale.

## Avvio in 2 minuti

```bash
pip install -r requirements.txt
cp .env.example .env          # facoltativo: imposta nome, numero, ecc.
python run.py                 # si apre http://localhost:8000
```

Al primo avvio clicca **Carica dati demo** (a sinistra): vedrai chiamate spam, messaggi, email e impegni di prova
già classificati dal vero motore. Poi prova a dire o scrivere:

- «ricordami domani alle 15 di chiamare il dentista»  ·  «cosa ho oggi?»
- «fammi il report»  ·  «come rispondo ad Anna?»  ·  «come rispondo a Marco in modo formale»
- «registra la riunione col cliente»  ·  «ferma la registrazione»
- «blocca il numero 333 123 4567»  ·  «blocca l'ultimo numero»  ·  «quanto spam hai fermato?»

Il pulsante ⚡ in alto è il **simulatore**: invia al motore una chiamata o un SMS finto e mostra se verrebbe bloccato e perché.

Test automatici: `python -m unittest discover -s tests -v`

## Come funzionano le 6 funzioni

| # | Funzione | Come | Stato |
|---|----------|------|-------|
| 1 | **Blocco spam** | Punteggio 0-100 locale: rubrica/VIP, lista nera (anche prefissi `+3906*`), numero nascosto, numeri a pagamento (899, 144…), squilli da estero "one ring", spoofing "vicino" al tuo numero, chiamate ripetute, parole e link da phishing negli SMS/email. Regole in `config/spam_rules.json`. | Il **motore** è completo. Il **blocco reale** delle chiamate richiede la parte Android (vedi sotto). |
| 2 | **Report** chiamate/messaggi/email | Riepilogo parlato + markdown (oggi / 24 h / 7 giorni), importanti in evidenza, salvabile in `output/`. Le email arrivano via **IMAP** (password per app, nessuna API). | Completo sul PC. Chiamate/SMS reali richiedono la parte Android. |
| 3 | **Voce** (parli e risponde) | Ascolto: riconoscimento vocale del browser; modalità "ascolto continuo" con parola d'attivazione (il suo nome). Risposta: voce del browser, oppure **voci Piper offline** messe in `voci/` (la "voce che daremo noi"). | Completo. |
| 4 | **Registrazione** | Su comando vocale o pulsante, salva l'audio in `archivio/registrazioni/`; con `faster-whisper` la trascrive offline. | Completo (nel browser). |
| 5 | **Impegni** | Capisce l'italiano naturale («giovedì alle 18 e mezza», «tra 2 ore», «il 3/11»), avvisa dei conflitti, ti ricorda a voce + notifica con preavviso. | Completo. |
| 6 | **Risposte consigliate** | 3 proposte per messaggio/email (breve, cordiale, formale…), con stile scelto: accetta/declina/formale. Senza modello usa modelli di risposta; con il modello locale le scrive su misura. | Completo. |

## "Niente chiavi API": come ragiona

Tutto ciò che deve essere **affidabile** (agenda, spam, report, comandi) è fatto da regole: istantaneo, gratis, non si inventa nulla.
Solo la **conversazione libera** e la **scrittura su misura delle risposte** usano un modello linguistico, che gira sul tuo computer:

1. Installa [Ollama](https://ollama.com) e scarica un modello: `ollama pull llama3.2:3b` (leggero) o `ollama pull qwen2.5:7b` (migliore in italiano, serve più RAM).
2. Riavvia `python run.py`: in alto il segnale **Cervello** diventa verde con il nome del modello. Nessuna configurazione.

Senza Ollama l'app funziona lo stesso (il segnale è arancione: "regole base").
In alternativa va bene qualsiasi server locale compatibile OpenAI (llama.cpp, LM Studio): `LLM_BACKEND=openai` + `LLM_URL`.

## La voce

- **Subito**: usa le voci italiane del sistema (scegli nelle Impostazioni, regola velocità e tono).
- **Voce tua / personalizzata**: scarica una voce italiana [Piper](https://github.com/rhasspy/piper) (file `.onnx` + `.onnx.json`),
  mettila in `voci/`, `pip install piper-tts`, riavvia e scegli «Voce del server» nelle Impostazioni.
  Per una voce **clonata** (es. la tua) si addestra/affina un modello Piper con le tue registrazioni e si copia il file in `voci/`:
  l'app lo userà senza altre modifiche.
- Ascolto con il **server** (Firefox/iPhone, dove il riconoscimento del browser manca): `pip install faster-whisper`.

## Dal PC al cellulare, in tre passi

**Passo 1 — Interfaccia sul telefono (30 secondi).** Sul PC: `python run.py --lan --https` e imposta `Aria_TOKEN` nel `.env`.
Sul telefono (stessa Wi-Fi) apri l'indirizzo mostrato, accetta l'avviso del certificato, poi *Aggiungi a schermata Home*.
Microfono, voce, registrazioni, agenda e notifiche funzionano. (HTTPS serve: i browser bloccano il microfono su HTTP.)

**Passo 2 — Tutto sul telefono, senza PC.** Android: installa Termux, poi `pkg install python openssl` e
`pip install starlette uvicorn python-multipart`, copia la cartella e lancia `python run.py --https`. Per il modello: `llama.cpp` in Termux
con `LLM_BACKEND=openai`. Dettagli in [`phone/ANDROID.md`](phone/ANDROID.md).

**Passo 3 — Blocco chiamate e lettura SMS (serve una piccola app Android).** Una pagina web **non può** bloccare chiamate né leggere SMS in
background: è una regola del sistema operativo. La soluzione è una micro-app Android che, a ogni chiamata/SMS, fa un `POST /api/events`
al tuo server e applica la risposta (`block` / `warn` / `allow`). Il server è già pronto: ti basta un'app che parli con
quell'endpoint. In `phone/ANDROID.md` c'è lo scheletro (CallScreeningService). Su iPhone il blocco di terze parti è limitato a liste di
numeri (Call Directory extension).

### Protocollo per l'app del telefono

```http
POST /api/events           (header  X-Token: <Aria_TOKEN>)
{"kind":"call"|"sms"|"whatsapp"|"email", "sender":"+39333...", "text":"...", "subject":"...",
 "status":"answered|missed", "duration":120, "ts": 1760950000000, "ext_id":"id-univoco-opzionale"}

→ {"action":"block|warn|allow", "verdict":"spam|suspect|ok", "score":85, "reasons":["..."], "important":false}
```

Altri endpoint utili: `POST /api/contacts` (sincronizza la rubrica: i contatti non vengono mai bloccati), `GET /api/due` (promemoria scaduti),
`GET /api/report?period=24h`.

## Cartelle

```
run.py               avvio (--lan, --https)
app/                 server: assistant.py (comandi), spam.py, brain.py (LLM locale), report.py, mail.py, voice.py, dates_it.py
web/                 interfaccia (HTML/CSS/JS, installabile come app: PWA)
config/              profilo_segretaria.md (carattere e tono), spam_rules.json (regole)
archivio/            database, registrazioni, cache voce      (dati tuoi, non condividerli)
output/              report salvati
voci/                voci Piper (.onnx)
phone/               guida e scheletro Android
tests/
```

## Privacy e limiti onesti

- Tutti i dati restano sul tuo dispositivo (`archivio/`). Eccezione: il riconoscimento vocale **del browser** (Chrome/Edge/Safari) può usare i server del
  produttore del browser; per un ascolto 100% offline usa `faster-whisper`.
- Registrare conversazioni a cui partecipi è in genere lecito per uso personale in Italia, ma diffonderle o registrare conversazioni altrui no:
  avvisa sempre l'interlocutore. Android limita inoltre la registrazione delle chiamate telefoniche.
- Il modello piccolo può sbagliare: per questo agenda, spam e report non dipendono da lui, e le risposte suggerite vanno sempre riviste prima di inviarle.
- Gli SMS/chiamate "reali" arrivano solo con la parte Android (passo 3). Prima, usa il simulatore.
- Se usi `--lan`, imposta sempre `Aria_TOKEN`: senza, chiunque sulla tua Wi-Fi vedrebbe i tuoi messaggi.
