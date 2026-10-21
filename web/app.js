/* Segretaria AI — interfaccia. Nessuna dipendenza esterna. */
"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { } },
};

const S = {
  status: null, inboxKind: "all", events: [], lastEventId: 0, history: [], busy: false,
  speaking: false, speakId: 0, listening: false, srvStop: null, recording: null, audio: null, pendingWake: null,
  prefs: Object.assign({ speak: true, continuous: true, wake: true, voice: "", rate: 1, pitch: 1 }, store.get("prefs", {})),
};
const savePrefs = () => store.set("prefs", S.prefs);
if (!store.get("alwaysOn1", false)) { S.prefs.continuous = true; S.prefs.wake = S.prefs.wake !== false; store.set("alwaysOn1", true); savePrefs(); }   // una tantum: microfono sempre attivo

/* ------------------------------------------------------------------ API */
async function api(path, opt = {}) {
  const headers = Object.assign({}, opt.headers);
  const tk = store.get("token", "");
  if (tk) headers["X-Token"] = tk;
  let body = opt.body;
  if (body && !(body instanceof FormData)) { headers["Content-Type"] = "application/json"; body = JSON.stringify(body); }
  const r = await fetch(path, { method: opt.method || (body ? "POST" : "GET"), headers, body });
  if (r.status === 401) {
    const t = prompt("Questo server richiede un token di accesso (Aria_TOKEN):");
    if (t) { store.set("token", t); return api(path, opt); }
    throw new Error("non autorizzato");
  }
  if (r.status === 204) return null;
  const ct = r.headers.get("content-type") || "";
  if (!r.ok) throw new Error((ct.includes("json") ? (await r.json()).error : await r.text()) || r.statusText);
  return ct.includes("json") ? r.json() : r.blob();
}
const tokQ = () => { const t = store.get("token", ""); return t ? "?token=" + encodeURIComponent(t) : ""; };

/* ------------------------------------------------------------------ UI base */
function setState(st) { document.body.dataset.state = st; $("#btnMic").classList.toggle("stop", st === "speaking"); $("#btnMic").setAttribute("aria-label", st === "speaking" ? "Zittisci e parla" : "Parla"); $("#hudState").textContent = ({ idle: "pronta", listening: "ti ascolto", thinking: "elaboro", speaking: "parlo", recording: "registro" })[st] || st; }
function baseState() { setState(S.recording ? "recording" : S.listening ? "listening" : "idle"); }
function toast(title, text = "", kind = "") {
  const t = document.createElement("div"); t.className = "toast " + kind;
  t.innerHTML = `<b>${esc(title)}</b>${esc(text)}`;
  $("#toasts").append(t); setTimeout(() => t.remove(), 7000);
}
function dotted(n) { return (n || "Aria").toUpperCase().split("").filter(c => c !== " ").join(".") + "."; }
function fmtTime(iso) {
  const d = new Date(iso), n = new Date(), same = d.toDateString() === n.toDateString();
  const y = new Date(n - 864e5).toDateString() === d.toDateString();
  const hm = d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
  return same ? hm : y ? "ieri " + hm : d.toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit" }) + " " + hm;
}
function setTab(t) {
  document.body.dataset.tab = t;
  $$("#bottom button").forEach(b => b.classList.toggle("on", b.dataset.t === t));
}
$("#bottom").addEventListener("click", e => { const b = e.target.closest("button"); if (b) setTab(b.dataset.t); });

/* ------------------------------------------------------------------ voce in uscita */
let voices = [];
function loadVoices() { voices = (speechSynthesis?.getVoices?.() || []); }
if ("speechSynthesis" in window) { loadVoices(); speechSynthesis.onvoiceschanged = loadVoices; }

function cleanForSpeech(t) { return t.replace(/[«»*_`#]/g, "").replace(/https?:\/\/\S+/g, "link").replace(/\s+/g, " ").trim(); }

async function speak(text) {
  if (!text) return;
  stopSpeaking();
  const id = ++S.speakId;
  const clean = cleanForSpeech(text);
  S.speaking = true; setState("speaking"); pauseListening();
  let finished = false;
  const done = () => { if (finished || id !== S.speakId) return; finished = true; S.speaking = false; baseState(); resumeListening(); };
  // Rete di sicurezza: alcuni browser non segnalano mai la fine della frase e la segretaria resterebbe "in parlato" per sempre.
  const watchdog = setTimeout(() => { if (id === S.speakId && S.speaking) { try { speechSynthesis.cancel(); } catch { } S.audio?.pause(); done(); } },
    (2500 + clean.length * 90) / Math.max(.5, S.prefs.rate));
  const end = () => { clearTimeout(watchdog); done(); };
  try {
    const wantServer = (S.prefs.voice || "").startsWith("server:") || (!S.prefs.voice && S.status && S.status.tts.engine !== "browser");
    if (wantServer && S.status.tts.engine !== "browser") {
      const blob = await api("/api/tts", { body: { text: clean, voice: (S.prefs.voice || "").replace("server:", "") } });
      if (id !== S.speakId) return;                      // nel frattempo l'utente ha interrotto
      if (blob) {
        const a = new Audio(URL.createObjectURL(blob)); S.audio = a;
        a.playbackRate = S.prefs.rate;
        a.onended = a.onerror = end;
        await a.play(); return;
      }
    }
  } catch { if (id !== S.speakId) return; }
  if (!("speechSynthesis" in window)) return end();
  const u = new SpeechSynthesisUtterance(clean);
  u.lang = "it-IT"; u.rate = S.prefs.rate; u.pitch = S.prefs.pitch;
  const v = voices.find(v => v.name === S.prefs.voice) || voices.find(v => v.lang?.toLowerCase().startsWith("it"));
  if (v) u.voice = v;
  u.onend = u.onerror = end;
  speechSynthesis.speak(u);
}
function stopSpeaking() {
  S.speakId++;                                           // invalida qualunque parlato in corso o in preparazione
  try { speechSynthesis.cancel(); } catch { }
  if (S.audio) { S.audio.onended = S.audio.onerror = null; S.audio.pause(); S.audio = null; }
  S.speaking = false;
}

/* ------------------------------------------------------------------ voce in ingresso */
// Opera (come Brave e Chromium) espone SpeechRecognition ma senza il servizio vocale: non funziona mai. La escludiamo.
const IS_OPERA = /\bOPR\/|Opera/.test(navigator.userAgent);
const SR = IS_OPERA ? null : (window.SpeechRecognition || window.webkitSpeechRecognition);
let rec = null, restartT = null, srFails = 0, netWarned = false;
const SR_ERR = {
  "not-allowed": "Il browser ha bloccato il microfono. Clicca il lucchetto vicino all'indirizzo, consenti il microfono e ricarica la pagina.",
  "service-not-allowed": "Il browser non permette il riconoscimento vocale su questa pagina: serve localhost o HTTPS.",
  "audio-capture": "Non trovo nessun microfono. Controlla che sia collegato e selezionato come ingresso del sistema.",
  "network": "Il riconoscimento vocale del browser non riesce a collegarsi al suo servizio: serve internet e un browser come Chrome o Edge (non funziona su Brave, Chromium, Firefox).",
  "language-not-supported": "Il browser non supporta il riconoscimento vocale in italiano.",
};
/* modalità di ascolto disponibile: browser (SpeechRecognition) → server (faster-whisper) → nessuna */
function micMode() {
  const srv = !!S.status?.stt_server, ready = !!S.status?.stt?.ready;
  // Il server si usa solo se il suo modello è pronto: finché sta caricando, il browser (se c'è) resta la scelta migliore.
  if (SR && (srFails < 2 || !srv || !ready)) return "browser";
  return srv ? "server" : "none";
}
function fatalListenError(msg, code) {
  S.fatalListen = true; S.prefs.continuous = false; savePrefs();
  toast("Microfono: " + (code || "errore"), msg, "red");
}

function startListening() {
  if (S.listening) return;
  if (S.busy) return;   // Aria sta ancora rispondendo: aspetta prima di riascoltare
  if (S.speaking) stopSpeaking();
  const mode = micMode();
  if (mode === "none") { toast("Per parlarmi serve un passo in più", "Questo browser non ha il riconoscimento vocale. Apro la guida.", "amber"); openMicTest(); return; }
  S.fatalListen = false;
  if (mode === "server") return listenViaServer();
  const old = rec; rec = null; try { old && old.abort(); } catch { }
  const r = new SR(); rec = r;
  r.lang = "it-IT"; r.interimResults = true; r.continuous = false; r.maxAlternatives = 1;
  const mine = () => rec === r;                          // ignora gli eventi tardivi di una sessione già sostituita
  r.onstart = () => { if (!mine()) return; S.listening = true; $("#btnMic").classList.add("on"); if (!S.speaking && !S.busy) baseState(); };
  r.onresult = ev => {
    if (!mine()) return;
    let interim = "", final = "";
    for (let i = ev.resultIndex; i < ev.results.length; i++) { const t = ev.results[i][0].transcript; ev.results[i].isFinal ? final += t : interim += t; }
    $("#live").textContent = interim || final;
    if (final.trim()) { srFails = 0; netWarned = false; onUtterance(final.trim()); }
  };
  r.onerror = e => {
    if (!mine()) return;
    const c = e.error;
    if (c === "no-speech") { if (!S.prefs.continuous) $("#live").textContent = "Non ti ho sentito: riprova."; return; }   // in ascolto continuo il silenzio è normale: niente messaggio
    if (c === "aborted") return;
    if (c === "network") {
      // Un "network" isolato è spesso solo un intoppo passeggero del servizio vocale di Google: non è un errore del
      // microfono né della pagina. Riproviamo da soli un paio di volte prima di disturbarti con un avviso.
      srFails++;
      if (micMode() === "server") { toast("Passo all'ascolto locale", "Il servizio del browser non risponde: uso la trascrizione sul server.", "amber"); return; }
      if (srFails >= 3 && !netWarned) {
        netWarned = true;
        toast("Il microfono non raggiunge il servizio vocale", "Non è un guasto di Aria: succede quando Edge/Chrome non riescono a parlare con il servizio vocale di Google. Controlla la connessione a internet, e se hai un'estensione (ad blocker, VPN, antivirus) che blocca le richieste di rete, provala disattivata su questa pagina. Continuo a riprovare da solo; intanto puoi scrivermi.", "amber");
      }
      return;   // niente fatalListenError: resta in ascolto continuo e riprova, invece di spegnersi
    }
    fatalListenError(SR_ERR[c] || ("Errore " + c), c);
  };
  r.onend = () => {
    if (!mine()) return;
    S.listening = false; $("#btnMic").classList.remove("on");
    if (!S.speaking && !S.busy) baseState();
    $("#live").textContent = "";
    // Dopo errori di rete ripetuti rallentiamo i tentativi (ogni 20s invece che ogni 350ms), per non martellare un servizio che non risponde.
    const delay = srFails >= 3 ? 20000 : 350;
    if (S.prefs.continuous && !S.speaking && !S.busy && !S.fatalListen) { clearTimeout(restartT); restartT = setTimeout(startListening, delay); }
  };
  try { r.start(); }
  catch (e) { rec = null; toast("Non riesco ad avviare l'ascolto", e.message || String(e), "red"); return; }
  // Se il browser non dà nessun segnale (né avvio né errore) lo diciamo: meglio un messaggio che il silenzio.
  let waited = 0;
  const check = async () => {
    if (!mine() || S.listening) return;
    let perm = "?"; try { perm = (await navigator.permissions.query({ name: "microphone" })).state; } catch { }
    if (perm === "prompt" && waited < 20000) { waited += 5000; return setTimeout(check, 5000); }   // sta ancora aspettando il tuo «Consenti»
    if (navigator.userActivation && !navigator.userActivation.hasBeenActive) { rec = null; try { r.abort(); } catch { } return; }   // senza un tuo gesto il browser non lo avvia: non è un errore
    rec = null; try { r.abort(); } catch { } srFails += 2;
    $("#btnMic").classList.remove("on"); baseState();
    if (micMode() === "server") { toast("Passo all'ascolto locale", "Il riconoscimento del browser non è partito.", "amber"); listenViaServer(); }
    else { toast("Il browser non ha avviato l'ascolto", "Probabile Brave/Chromium/Firefox o nessun accesso a internet. Apro il test del microfono.", "red"); openMicTest(); }
  };
  setTimeout(check, 3500);
}

/* Ascolto tramite il server: registra finché smetti di parlare, poi trascrive in locale (faster-whisper). */
async function listenViaServer() {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { toast("Ascolto non disponibile", "Il browser non può registrare l'audio (serve localhost o HTTPS).", "red"); return; }
  if (S.listening) return;
  S.listening = true; $("#btnMic").classList.add("on"); setState("listening"); $("#live").textContent = "Consenti il microfono se il browser lo chiede…";
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch (e) {
    S.listening = false; $("#btnMic").classList.remove("on"); $("#live").textContent = ""; baseState();
    fatalListenError(e.name === "NotAllowedError" ? SR_ERR["not-allowed"] : e.name === "NotFoundError" ? SR_ERR["audio-capture"] : e.message, e.name); return;
  }
  if (!S.listening) { stream.getTracks().forEach(t => t.stop()); return; }   // nel frattempo hai annullato
  const mime = pickMime(), chunks = [];
  let mr, ac, an, tick, hard, finished = false, cancelled = false, spoke = false;
  const t0 = Date.now(); let last = t0;
  const cleanup = () => {
    clearInterval(tick); clearTimeout(hard); stream.getTracks().forEach(t => t.stop()); try { ac && ac.close().catch(() => { }); } catch { }
    S.srvStop = null; S.listening = false; $("#btnMic").classList.remove("on"); $("#hud").classList.remove("lvl"); $("#live").textContent = ""; baseState();
  };
  try {
    mr = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    // L'AudioContext può nascere "sospeso" (regole di autoplay) e allora non sentirebbe mai la voce: lo riattiviamo.
    ac = new (window.AudioContext || window.webkitAudioContext)(); await ac.resume().catch(() => { });
    an = ac.createAnalyser(); an.fftSize = 1024; ac.createMediaStreamSource(stream).connect(an);
  } catch (e) { cleanup(); toast("Ascolto non riuscito", e.message || String(e), "red"); return; }
  const meterOk = ac.state === "running";          // se non è affidabile: finestra fissa, decide il modello (ha il suo filtro silenzio)
  if (!meterOk) { spoke = true; $("#live").textContent = "Parla ora (tocca il microfono per inviare)…"; }
  else $("#live").textContent = "Parla pure…";
  $("#hud").classList.toggle("lvl", meterOk);
  const buf = new Uint8Array(an.fftSize);
  mr.ondataavailable = e => e.data.size && chunks.push(e.data);
  const stopNow = () => { if (mr.state !== "inactive") mr.stop(); else finish(); };
  tick = setInterval(() => {
    const now = Date.now();
    if (meterOk) {
      an.getByteTimeDomainData(buf); let m = 0; for (const v of buf) m = Math.max(m, Math.abs(v - 128));
      const lvl = m / 128; $("#hud").style.setProperty("--lvl", lvl.toFixed(2));
      if (lvl > .06) { spoke = true; last = now; }
      if ((spoke && now - last > 1300) || (!spoke && now - t0 > 7000)) stopNow();
    } else if (now - t0 > 6000) stopNow();
    if (mr.state === "inactive") stopNow();        // il registratore si è fermato da solo
  }, 90);
  hard = setTimeout(stopNow, 15000);                // rete di sicurezza assoluta
  // send=true: invia ciò che hai detto finora (secondo clic sul microfono); false: scarta (es. quando lei inizia a parlare)
  S.srvStop = (send = false) => { cancelled = !send; stopNow(); };
  const finish = async () => {
    if (finished) return; finished = true; cleanup();
    if (cancelled) return;
    if (!spoke || !chunks.length) { if (!S.prefs.continuous) $("#live").textContent = "Non ti ho sentito: riprova."; if (S.prefs.continuous) resumeListening(); return; }
    $("#live").textContent = S.status?.stt?.ready ? "Trascrivo…" : "Carico il modello vocale (solo la prima volta)…"; setState("thinking");
    try {
      const fd = new FormData(); fd.append("audio", new Blob(chunks, { type: mr.mimeType || "audio/webm" }), "voce");
      const r = await api("/api/transcribe", { method: "POST", body: fd });
      $("#live").textContent = ""; baseState();
      if (r.text) onUtterance(r.text); else { $("#live").textContent = "Non ho capito: riprova."; if (S.prefs.continuous) resumeListening(); }
    } catch (e) { $("#live").textContent = ""; baseState(); toast("Trascrizione non riuscita", e.message, "red"); }
  };
  mr.onstop = finish;
  try { mr.start(250); } catch (e) { cleanup(); toast("Registrazione non avviata", e.message || String(e), "red"); }
}

function stopListening() {
  clearTimeout(restartT); if (S.srvStop) { S.srvStop(true); return; }
  const r = rec; rec = null; try { r && r.abort(); } catch { }
  S.listening = false; $("#btnMic").classList.remove("on"); $("#live").textContent = ""; baseState();
}
function pauseListening() {
  clearTimeout(restartT); S.srvStop?.(false);
  const r = rec; rec = null; try { r && r.abort(); } catch { }
  S.listening = false; $("#btnMic").classList.remove("on");
}
function resumeListening() { if (S.prefs.continuous && !S.busy && !S.fatalListen) { clearTimeout(restartT); restartT = setTimeout(startListening, 500); } }

function wakeName() { return (S.status?.settings.assistente || "aria").toLowerCase(); }
function onUtterance(text) {
  if (S.recording) {                                    // in registrazione: unica condizione, solo la frase di stop conta
    const clean = text.trim();
    if (STOP_REC.test(clean) && clean.split(/\s+/).length <= 6) send(text);
    return;                                              // tutto il resto resta nell'audio registrato, non diventa un comando
  }
  if (!S.prefs.continuous) return send(text);          // tocca-e-parla: tutto ciò che dici è un comando
  if (S.prefs.wake === false) return send(text);       // sempre in ascolto, senza bisogno del nome
  const low = text.toLowerCase(), w = wakeName();
  if (S.pendingWake && Date.now() < S.pendingWake) return send(text);   // conversazione già aperta: niente nome
  const i = low.indexOf(w);
  if (i < 0) return resumeListening();                 // ascolto continuo: ignora se non mi chiami
  const rest = text.slice(i + w.length).replace(/^[\s,.:!]+/, "");
  if (rest.length > 1) send(rest);
  else { S.pendingWake = Date.now() + 12000; speak("Dimmi."); }
}
const openConversation = () => { S.pendingWake = Date.now() + 25000; };   // dopo una risposta puoi continuare 25 s senza il nome

/* Il pulsante funziona sempre: se sto parlando mi zittisce e ascolta subito. */
function micClick() {
  if (S.speaking) { stopSpeaking(); baseState(); startListening(); return; }
  if (S.busy) { toast("Un momento", "Sto ancora rispondendo: il microfono riparte da solo appena ho finito."); return; }
  if (S.listening) { S.prefs.continuous = false; savePrefs(); stopListening(); toast("Microfono spento", "Tocca di nuovo per riattivarlo"); }
  else { S.prefs.continuous = true; savePrefs(); startListening(); }
}
$("#btnMic").addEventListener("click", micClick);
$("#hud").addEventListener("click", () => { if (S.speaking) { stopSpeaking(); baseState(); } });

/* Test guidato: dice esattamente cosa blocca il microfono. */
async function openMicTest() {
  let perm = "sconosciuto";
  try { perm = (await navigator.permissions.query({ name: "microphone" })).state; } catch { }
  const srv = !!S.status?.stt_server, brave = !!navigator.brave;
  const rows = [
    ["Pagina sicura (localhost o HTTPS)", window.isSecureContext, window.isSecureContext ? "" : "Apri http://localhost:8000 sul PC, oppure avvia con --https."],
    ["Riconoscimento vocale del browser", !!SR, SR ? (brave ? "Brave lo include ma di solito non funziona: usa Chrome o Edge." : "") : (IS_OPERA ? "Opera non include il servizio vocale di Google: da solo non può ascoltarti." : "Questo browser non lo ha (Firefox, alcuni Safari).") + " Soluzione: installa la trascrizione locale (riga sotto) oppure usa Chrome o Edge."],
    ["Trascrizione locale sul server (faster-whisper)", srv, srv ? (S.status.stt.ready ? "Pronta." : S.status.stt.error ? "Errore nel modello: " + S.status.stt.error : "Sto caricando il modello vocale (la prima volta lo scarica, serve internet: può volerci un minuto).") : "Non installata. Nel terminale: pip install faster-whisper — poi chiudi e riavvia python run.py."],
    ["Permesso del microfono", perm === "granted", perm === "denied" ? "Bloccato: consentilo dal lucchetto vicino all'indirizzo e ricarica." : perm === "prompt" ? "Il browser lo chiederà alla prima prova: premi «Consenti»." : ""],
  ];
  const sh = openModal(`<h3>Test del microfono</h3>
    <div>${rows.map(([l, ok, h]) => `<div class="mini"><span>${ok ? "✅" : "⚠️"} ${esc(l)}${h ? `<br><span class="muted">${esc(h)}</span>` : ""}</span></div>`).join("")}</div>
    <h4>1. Il microfono sente?</h4>
    <div class="meter"><i id="mtBar" style="width:0%;background:var(--cy)"></i></div>
    <div class="acts"><button class="btn" id="mtLvl">Prova per 4 secondi</button></div>
    <h4>2. Il riconoscimento capisce?</h4>
    <div class="acts"><button class="btn" id="mtSr">Prova a dire qualcosa</button></div>
    <div id="mtOut" class="verdict ok" style="display:none;margin-top:12px"></div>`);
  const out = (t, kind = "ok") => { const o = $("#mtOut", sh); o.style.display = ""; o.className = "verdict " + (kind === "bad" ? "spam" : kind); o.textContent = t; };
  $("#mtLvl", sh).onclick = async () => {
    let st; try { st = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch (e) { return out(e.name === "NotAllowedError" ? SR_ERR["not-allowed"] : e.name === "NotFoundError" ? SR_ERR["audio-capture"] : (e.message || String(e)), "bad"); }
    const ac = new (window.AudioContext || window.webkitAudioContext)(), an = ac.createAnalyser(); an.fftSize = 1024; ac.createMediaStreamSource(st).connect(an);
    const buf = new Uint8Array(an.fftSize); let peak = 0; out("Sto ascoltando: parla o batti le mani…");
    const iv = setInterval(() => { an.getByteTimeDomainData(buf); let m = 0; for (const v of buf) m = Math.max(m, Math.abs(v - 128)); peak = Math.max(peak, m / 128); const b = $("#mtBar"); if (b) b.style.width = Math.min(100, m / 128 * 220) + "%"; }, 60);
    setTimeout(() => { clearInterval(iv); st.getTracks().forEach(t => t.stop()); ac.close().catch(() => { });
      out(peak > .05 ? "Il microfono funziona: il segnale arriva." : "Nessun segnale. Controlla che sia il microfono giusto in Impostazioni di sistema > Suono > Ingresso, e che non sia disattivato.", peak > .05 ? "ok" : "bad"); }, 4000);
  };
  $("#mtSr", sh).onclick = async () => {
    if (micMode() === "server") { out("Parla ora: uso la trascrizione sul server (si ferma da sola quando smetti)…", "suspect"); closeModal(); startListening(); return; }
    if (!SR) return out("Questo browser non ha il riconoscimento vocale: " + (srv ? "userò quello del server." : "usa Chrome o Edge, oppure installa faster-whisper."), "bad");
    const t = new SR(); t.lang = "it-IT"; t.interimResults = true;
    out("Parla ora…", "suspect");
    t.onresult = e => out("Ho capito: «" + [...e.results].map(r => r[0].transcript).join(" ") + "»", e.results[e.results.length - 1].isFinal ? "ok" : "suspect");
    t.onerror = e => out(SR_ERR[e.error] || ("Errore: " + e.error + (e.error === "no-speech" ? " (non ho sentito nulla, riprova)" : "")), "bad");
    try { t.start(); } catch (e) { out(String(e), "bad"); }
  };
}
$("#chipMic").addEventListener("click", openMicTest);

/* ------------------------------------------------------------------ chat */
function addMsg(kind, text, extra) {
  const m = document.createElement("div"); m.className = "msg " + kind; m.textContent = text;
  if (extra) m.append(extra);
  const c = $("#convo"); c.append(m); c.scrollTop = c.scrollHeight;
  while (c.children.length > 40) c.firstChild.remove();
  return m;
}
function repliesBox(rs, source) {
  const box = document.createElement("div"); box.className = "rep";
  rs.forEach(r => {
    const d = document.createElement("div"); d.className = "rep-i";
    d.innerHTML = `<b>${esc(r.label)}</b>${esc(r.text)}<div class="acts"><button class="btn sm" data-a="copy">Copia</button><button class="btn sm ghost" data-a="say">Leggi</button></div>`;
    d.addEventListener("click", async e => {
      const a = e.target.dataset.a; if (!a) return;
      if (a === "say") speak(r.text);
      else { try { await navigator.clipboard.writeText(r.text); toast("Copiato", "Incollalo dove vuoi rispondere."); } catch { toast("Copia non riuscita", r.text); } }
    });
    box.append(d);
  });
  const s = document.createElement("div"); s.className = "muted"; s.style.fontSize = "12px";
  s.textContent = source === "llm" ? "Scritte dal modello locale" : "Modelli di risposta (nessun modello attivo)";
  box.append(s); return box;
}
// Mentre registri, una frase corta di stop ferma subito la registrazione, senza passare dal server
// (così funziona anche se il riconoscimento sbaglia una parola o il server è lento).
const STOP_REC = /^\W*(?:ok\W+|aria\W+|per favore\W+)*(?:ferma\w*|stop|basta|fine|termina\w*|smett\w*|chiud\w*|finisci|interrompi)\b(?:.*\b(?:registr\w*|audio|tutto)\b)?\W*$/i;
async function send(text) {
  text = text.trim(); if (!text) return;
  if (S.recording && STOP_REC.test(text) && text.split(/\s+/).length <= 6) {
    addMsg("me", text); $("#txt").value = ""; stopRecording(); stopSpeaking();
    addMsg("ai", "Registrazione fermata. La trovi nel pannello Registrazioni."); return;
  }
  if (S.busy) return;
  addMsg("me", text); $("#txt").value = ""; S.busy = true; setState("thinking");
  try {
    const out = await api("/api/chat", { body: { text, history: S.history.slice(-8) } });
    S.history.push({ role: "user", content: text }, { role: "assistant", content: out.reply });
    const rs = out.data?.replies;
    addMsg("ai", out.reply, rs ? repliesBox(rs, out.data.source) : null);
    S.busy = false; baseState(); openConversation();
    const act = out.action || "";
    if (act === "start_recording") {                 // prima dice la frase, POI comincia a registrare (così non finisce nel file)
      if (S.prefs.speak && !S.recording) await speak(out.reply);
      handleAction(out); resumeListening(); return;
    }
    if (act === "stop_recording") {                  // prima chiude la registrazione, POI risponde
      handleAction(out);
      for (let i = 0; i < 40 && S.recording; i++) await new Promise(r => setTimeout(r, 50));
    } else handleAction(out);
    if (S.prefs.speak && !S.recording) await speak(out.reply); else resumeListening();   // mentre registro resto zitta
  } catch (e) {
    S.busy = false; baseState(); addMsg("ai", "Non riesco a contattare il server: " + e.message); resumeListening();
  }
}
function handleAction(out) {
  const a = out.action || "";
  if (a === "start_recording") startRecording(out.data?.title || "");
  else if (a === "stop_recording") stopRecording();
  else if (a === "stop_speaking") stopSpeaking();
  else if (a === "mic_off") { S.prefs.continuous = false; savePrefs(); stopListening(); }
  else if (a === "refresh") refreshAll();
  else if (a === "show_report") openReport(out.data?.period || "24h", false);
  else if (a.startsWith("open:")) { setTab(a.slice(5)); refreshAll(); }
  if (out.intent === "risposte" && out.data?.event) markRead(out.data.event.id);
}
$("#dock").addEventListener("submit", e => { e.preventDefault(); send($("#txt").value); });
$("#quick").addEventListener("click", e => { const b = e.target.closest("button"); if (b) { setTab("home"); send(b.dataset.q); } });

/* ------------------------------------------------------------------ comunicazioni */
const KIND_ICO = { call: "i-call", sms: "i-msg", whatsapp: "i-msg", email: "i-mail" };
const KIND_LBL = { call: "Chiamata", sms: "SMS", whatsapp: "WhatsApp", email: "Email" };

async function loadInbox(initial) {
  const kind = S.inboxKind === "all" ? "" : "&kind=" + S.inboxKind;
  const [rows] = await Promise.all([api("/api/events?limit=80" + kind)]);
  if (S.inboxKind === "all") announceNew(rows, initial);
  S.events = rows; renderInbox();
}
function announceNew(rows, initial) {
  const max = Math.max(0, ...rows.map(r => r.id));
  if (!initial && S.lastEventId) {
    rows.filter(r => r.id > S.lastEventId).slice(0, 3).forEach(r => {
      const who = r.name || r.sender;
      if (r.verdict === "spam") toast("Spam fermato", `${KIND_LBL[r.kind]} da ${who}`, "red");
      else if (r.important) { toast("Da non perdere", `${KIND_LBL[r.kind]} da ${who}`, "amber"); notify("Da non perdere", `${KIND_LBL[r.kind]} da ${who}`); }
      else toast("Nuovo: " + KIND_LBL[r.kind], who);
    });
  }
  S.lastEventId = Math.max(S.lastEventId, max);
}
function renderInbox() {
  const box = $("#inboxList");
  if (!S.events.length) {
    box.innerHTML = `<div class="empty">Nessuna comunicazione.<br>Le chiamate, i messaggi e le email appariranno qui.${S.status?.empty ? '<br><button class="btn" id="btnDemo">Carica dati demo</button>' : ""}</div>`;
    $("#btnDemo")?.addEventListener("click", loadDemo); return;
  }
  box.innerHTML = S.events.map(e => {
    const cls = [e.verdict === "spam" ? "spam" : e.verdict === "suspect" ? "suspect" : "", e.important ? "important" : "", e.read ? "" : "unread"].join(" ");
    const tags = [];
    if (e.verdict === "spam") tags.push(`<span class="tag red">${e.status === "blocked" ? "Bloccata" : "Spam"}</span>`);
    else if (e.verdict === "suspect") tags.push('<span class="tag amber">Sospetto</span>');
    if (e.important) tags.push('<span class="tag amber">Importante</span>');
    if (e.kind === "call" && e.status === "missed" && e.verdict !== "spam") tags.push('<span class="tag cy">Persa</span>');
    if (e.kind === "call" && e.status === "answered") tags.push('<span class="tag green">Risposta</span>');
    const snip = e.kind === "call" ? (e.status === "answered" ? `Durata ${Math.round(e.duration / 60) || 1} min` : e.status === "blocked" ? "Non ti ha disturbato" : "Chiamata senza risposta") : (e.subject || e.body || "");
    return `<div class="row ${cls}" data-id="${e.id}">
      <button class="row-main" data-act="toggle"><span class="ico"><svg><use href="#${KIND_ICO[e.kind]}"/></svg></span>
        <span><span class="who">${esc(e.name || e.sender)} ${tags.join("")}</span><span class="snip">${esc(snip)}</span></span>
        <span class="time">${fmtTime(e.ts)}</span></button>
      <div class="row-more" hidden></div></div>`;
  }).join("");
}
$("#inboxList").addEventListener("click", async e => {
  const row = e.target.closest(".row"); if (!row) return;
  const id = +row.dataset.id, ev = S.events.find(x => x.id === id), more = $(".row-more", row);
  const act = e.target.closest("[data-act]")?.dataset.act;
  if (act === "toggle") {
    if (!more.hidden) { more.hidden = true; return; }
    $$(".row-more").forEach(m => m.hidden = true);
    const isMsg = ev.kind !== "call";
    more.innerHTML = `${ev.subject ? `<b>${esc(ev.subject)}</b>` : ""}${ev.body ? `<div class="full">${esc(ev.body)}</div>` : ""}
      <div class="muted">${esc(ev.sender)} · ${KIND_LBL[ev.kind]}${ev.spam_score ? ` · rischio ${ev.spam_score}/100` : ""}</div>
      ${ev.reasons.length && ev.verdict !== "ok" || ev.spam_score >= 25 ? `<ul>${ev.reasons.map(r => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
      <div class="acts">
        ${isMsg && ev.verdict !== "spam" ? '<button class="btn sm amber" data-act="suggest">Suggerisci risposta</button>' : ""}
        ${isMsg ? '<button class="btn sm ghost" data-act="read">Leggi ad alta voce</button>' : ""}
        ${ev.verdict === "spam" || ev.verdict === "suspect" ? '<button class="btn sm" data-act="notspam">Non è spam</button>' : ""}
        ${ev.verdict !== "spam" && !ev.name ? '<button class="btn sm red" data-act="block">Blocca</button>' : ""}
      </div>`;
    more.hidden = false;
    if (!ev.read) markRead(id);
  } else if (act === "suggest") openSuggest(ev);
  else if (act === "read") speak(`${ev.name || ev.sender}${ev.subject ? ", " + ev.subject : ""}. ${ev.body || ""}`);
  else if (act === "notspam") { await api("/api/events/" + id, { method: "PATCH", body: { not_spam: true } }); toast("Fatto", "Ora mi fido di questo mittente."); refreshAll(); }
  else if (act === "block") { await api("/api/events/" + id, { method: "PATCH", body: { block: true } }); toast("Bloccato", ev.sender, "red"); refreshAll(); }
});
async function markRead(id) { try { await api("/api/events/" + id, { method: "PATCH", body: { read: true } }); const e = S.events.find(x => x.id === id); if (e) e.read = 1; $(`.row[data-id="${id}"]`)?.classList.remove("unread"); updateBadges(); } catch { } }
$("#inboxTabs").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  S.inboxKind = b.dataset.k; $$("#inboxTabs button").forEach(x => x.classList.toggle("on", x === b)); loadInbox();
});

/* ------------------------------------------------------------------ agenda */
async function loadAgenda() {
  const rows = await api("/api/agenda");
  $("#agendaCount").textContent = rows.length ? `${rows.length} in sospeso` : "";
  const box = $("#agendaList");
  if (!rows.length) { box.innerHTML = '<div class="empty">Agenda libera.<br>Scrivi sopra o dì «ricordami domani alle 15 il dentista».</div>'; return; }
  const soon = Date.now() + 3 * 3600e3;
  box.innerHTML = rows.map(a => `<div class="ag ${a.when_ts && new Date(a.when_ts) < soon ? "soon" : ""}" data-id="${a.id}">
    <button class="chk" data-act="done" aria-label="Fatto"></button>
    <div><div>${esc(a.title)}</div><div class="when">${esc(a.label)}</div></div>
    <button class="icon del" data-act="del" aria-label="Elimina"><svg><use href="#i-x"/></svg></button></div>`).join("");
}
$("#agendaList").addEventListener("click", async e => {
  const id = e.target.closest(".ag")?.dataset.id, act = e.target.closest("[data-act]")?.dataset.act; if (!id) return;
  if (act === "done") await api("/api/agenda/" + id, { method: "PATCH", body: { done: true } });
  if (act === "del") await api("/api/agenda/" + id, { method: "DELETE" });
  loadAgenda();
});
$("#agendaForm").addEventListener("submit", async e => {
  e.preventDefault(); const t = $("#agendaText").value.trim(); if (!t) return;
  await api("/api/agenda", { body: { text: t } }); $("#agendaText").value = ""; loadAgenda();
});

/* ------------------------------------------------------------------ registrazioni */
function pickMime() {
  const c = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
  return c.find(m => window.MediaRecorder && MediaRecorder.isTypeSupported(m)) || "";
}
async function startRecording(title = "") {
  if (S.recording) return;
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { toast("Registrazione non disponibile", "Serve HTTPS o localhost e un browser recente.", "red"); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: true } });
    const mime = pickMime(), mr = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined), chunks = [];
    mr.ondataavailable = e => e.data.size && chunks.push(e.data);
    mr.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      const dur = Math.round((Date.now() - S.recording.t0) / 1000), tt = S.recording.title;
      clearInterval(S.recording.timer); S.recording = null; $("#recTimer").textContent = ""; baseState(); setRecUi(false);
      const blob = new Blob(chunks, { type: mr.mimeType || "audio/webm" });
      const fd = new FormData(); fd.append("audio", blob, "rec"); fd.append("duration", dur); fd.append("title", tt);
      try { const r = await api("/api/recordings", { method: "POST", body: fd }); toast("Registrazione salvata", r.transcribing ? "Sto trascrivendo in locale…" : r.title); loadRecs(); }
      catch (e) { toast("Salvataggio non riuscito", e.message, "red"); }
    };
    mr.start(1000);
    S.recording = { mr, t0: Date.now(), title, timer: setInterval(() => { const s = Math.round((Date.now() - S.recording.t0) / 1000); $("#recTimer").textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; }, 500) };
    setRecUi(true); setState("recording");
  } catch (e) { toast("Microfono non disponibile", e.message, "red"); }
}
function stopRecording() { S.recording?.mr.state !== "inactive" && S.recording?.mr.stop(); }
function setRecUi(on) { $("#btnRec").classList.toggle("on", on); $("#recLabel").textContent = on ? "Registrazione in corso, tocca per fermare" : "Inizia a registrare"; }
$("#btnRec").addEventListener("click", () => S.recording ? stopRecording() : startRecording());
async function loadRecs() {
  const rows = await api("/api/recordings"), box = $("#recList");
  if (!rows.length) { box.innerHTML = '<div class="empty">Nessuna registrazione.<br>Dì «registra» per cominciare.</div>'; return; }
  const mm = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  box.innerHTML = rows.map(r => `<div class="rc" data-id="${r.id}">
    <div class="rc-h"><b>${esc(r.title)}</b><span class="muted">${fmtTime(r.ts)} · ${mm(r.duration)}</span></div>
    <audio controls preload="none" src="/api/recordings/${r.id}/audio${tokQ()}"></audio>
    ${r.transcript ? `<div class="tr">${esc(r.transcript)}</div>` : ""}
    <div class="acts" style="margin-top:6px"><button class="btn sm ghost" data-act="ren">Rinomina</button><button class="btn sm red" data-act="del">Elimina</button></div></div>`).join("");
}
$("#recList").addEventListener("click", async e => {
  const id = e.target.closest(".rc")?.dataset.id, act = e.target.dataset.act; if (!id) return;
  if (act === "del" && confirm("Eliminare questa registrazione?")) { await api("/api/recordings/" + id, { method: "DELETE" }); loadRecs(); }
  if (act === "ren") { const t = prompt("Nuovo titolo:"); if (t) { await api("/api/recordings/" + id, { method: "PATCH", body: { title: t } }); loadRecs(); } }
});

/* ------------------------------------------------------------------ modali */
function openModal(html) {
  $("#sheet").innerHTML = `<button class="icon x" aria-label="Chiudi" data-close><svg><use href="#i-x"/></svg></button>${html}`;
  $("#modal").hidden = false; return $("#sheet");
}
function closeModal() { $("#modal").hidden = true; }
$("#modal").addEventListener("click", e => { if (e.target.id === "modal" || e.target.closest("[data-close]")) closeModal(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") { closeModal(); if (S.speaking) { stopSpeaking(); baseState(); } } });

async function openReport(period = "24h", speakIt = false) {
  const r = await api("/api/report?period=" + period), c = r.counts;
  const sh = openModal(`<h3>Report ${esc(r.label)}</h3>
    <div class="pills" id="repP">${[["oggi", "Oggi"], ["24h", "24 ore"], ["7d", "7 giorni"]].map(([k, l]) => `<button data-p="${k}" class="${k === r.period ? "on" : ""}">${l}</button>`).join("")}</div>
    <div class="kpi"><div><b>${c.calls}</b><span>chiamate</span></div><div class="am"><b>${c.calls_missed}</b><span>perse</span></div>
    <div><b>${c.messages}</b><span>messaggi</span></div><div><b>${c.emails}</b><span>email</span></div>
    <div class="rd"><b>${c.spam_total}</b><span>spam fermato</span></div></div>
    <p style="margin:8px 0">${esc(r.spoken)}</p>
    <pre class="md">${esc(r.markdown)}</pre>
    <div class="acts" style="margin-top:12px"><button class="btn" id="repSay">Leggi ad alta voce</button>
    <button class="btn ghost" id="repSave">Salva in output/</button><button class="btn ghost" id="repDl">Scarica .md</button></div>`);
  $("#repP", sh).onclick = e => { const p = e.target.dataset.p; if (p) openReport(p); };
  $("#repSay", sh).onclick = () => speak(r.spoken);
  $("#repSave", sh).onclick = async () => { const x = await api("/api/report?period=" + r.period + "&save=1"); toast("Report salvato", x.saved_as); };
  $("#repDl", sh).onclick = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([r.markdown], { type: "text/markdown" })); a.download = `report_${r.period}.md`; a.click(); };
  if (speakIt) speak(r.spoken);
}
$("#btnReport").onclick = () => openReport("24h");

async function openSuggest(ev, tone = null) {
  const sh = openModal(`<h3>Come rispondere a ${esc(ev.name || ev.sender)}</h3>
    <div class="msg ai" style="max-width:100%">${esc(ev.subject ? ev.subject + " — " : "")}${esc(ev.body || "")}</div>
    <h4>Stile</h4><div class="pills" id="tonP">${[[null, "Come suggerisce lei"], ["breve", "Breve"], ["cordiale", "Cordiale"], ["formale", "Formale"], ["accetta", "Accetta"], ["declina", "Declina"]].map(([k, l]) => `<button data-t="${k ?? ""}" class="${k === tone ? "on" : ""}">${l}</button>`).join("")}</div>
    <div id="sugOut" class="muted">Sto scrivendo…</div>`);
  $("#tonP", sh).onclick = e => { if (e.target.dataset.t !== undefined && e.target.tagName === "BUTTON") openSuggest(ev, e.target.dataset.t || null); };
  try {
    const r = await api("/api/suggest", { body: { event_id: ev.id, tone } });
    const out = $("#sugOut", sh); out.className = ""; out.innerHTML = "";
    out.append(repliesBox(r.replies, r.source));
  } catch (e) { $("#sugOut", sh).textContent = "Errore: " + e.message; }
}

function openSim() {
  const sh = openModal(`<h3>Simulatore</h3>
    <p class="muted" style="margin-bottom:10px">Invia al motore un evento come farebbe il telefono: vedi subito se lo bloccherebbe.</p>
    <div class="pills" id="presets">
      <button data-p='{"kind":"call","sender":"+39 06 8737 1234"}'>Telemarketing</button>
      <button data-p='{"kind":"call","sender":"+225 07 12 34 56"}'>Squillo da estero</button>
      <button data-p='{"kind":"sms","sender":"Poste","text":"Il tuo conto è bloccato. Verifica i tuoi dati: bit.ly/xk2"}'>SMS phishing</button>
      <button data-p='{"kind":"call","sender":"333 1234567"}'>Chiamata di Marco</button>
      <button data-p='{"kind":"email","sender":"promo@offerte.com","subject":"Offerta esclusiva","text":"Sconto gratis! Cambia gestore luce e gas"}'>Email promo</button>
    </div>
    <div class="f2"><div class="f"><label>Tipo</label><select id="sKind"><option value="call">Chiamata</option><option value="sms">SMS</option><option value="whatsapp">WhatsApp</option><option value="email">Email</option></select></div>
    <div class="f"><label>Numero o indirizzo</label><input id="sSender" placeholder="+39 333 1234567"></div></div>
    <div class="f"><label>Oggetto (email)</label><input id="sSubj"></div>
    <div class="f"><label>Testo del messaggio</label><textarea id="sText" rows="3"></textarea></div>
    <div class="acts"><button class="btn" id="sGo">Invia al motore</button></div><div id="sRes"></div>`);
  $("#presets", sh).onclick = e => {
    const p = e.target.dataset.p; if (!p) return; const d = JSON.parse(p);
    $("#sKind").value = d.kind; $("#sSender").value = d.sender; $("#sSubj").value = d.subject || ""; $("#sText").value = d.text || "";
  };
  $("#sGo", sh).onclick = async () => {
    if (!$("#sSender").value.trim()) { toast("Manca il mittente", "Scrivi un numero o un indirizzo.", "amber"); return; }
    const r = await api("/api/events", { body: { kind: $("#sKind").value, sender: $("#sSender").value, subject: $("#sSubj").value, text: $("#sText").value } });
    const lab = { block: "Verrebbe bloccata", warn: "Verrebbe silenziata e segnalata", allow: "Passa" }[r.action];
    $("#sRes", sh).innerHTML = `<div class="verdict ${r.verdict}"><b>${lab}</b> · rischio ${r.score}/100
      <div class="meter"><i style="width:${r.score}%"></i></div><ul style="padding-left:18px;color:var(--mut)">${r.reasons.map(x => `<li>${esc(x)}</li>`).join("")}</ul></div>`;
    refreshAll();
  };
}
$("#btnSim").onclick = openSim;

/* ------------------------------------------------------------------ impostazioni */
async function openSettings() {
  const [set, bl, ct] = await Promise.all([api("/api/settings"), api("/api/blocklist"), api("/api/contacts")]);
  const st = S.status, tts = st.tts;
  const bv = voices.filter(v => v.lang?.toLowerCase().startsWith("it"));
  const opts = [...tts.voices.map(v => `<option value="server:${esc(v)}">Voce del server: ${esc(v)}${tts.engine === "espeak" ? " (robotica)" : ""}</option>`),
  ...bv.map(v => `<option value="${esc(v.name)}">${esc(v.name)}</option>`)];
  const sh = openModal(`<h3>Impostazioni</h3>
    <div class="f2"><div class="f"><label>Nome della segretaria</label><input id="stA" value="${esc(set.assistente)}"></div>
    <div class="f"><label>Il tuo nome</label><input id="stU" value="${esc(set.utente)}"></div></div>
    <div class="f2"><div class="f"><label>Il tuo numero (per riconoscere lo spoofing)</label><input id="stN" value="${esc(set.own_number)}" placeholder="+39..."></div>
    <div class="f"><label>Preavviso promemoria (minuti)</label><input id="stR" type="number" min="0" max="120" value="${esc(set.remind_before)}"></div></div>
    <h4>Voce</h4>
    <div class="f"><label>Voce della segretaria</label><select id="stV"><option value="">Automatica</option>${opts.join("")}</select></div>
    <div class="f2"><div class="f"><label>Velocità</label><input id="stRate" type="range" min=".7" max="1.4" step=".05" value="${S.prefs.rate}"></div>
    <div class="f"><label>Tono</label><input id="stPitch" type="range" min=".6" max="1.5" step=".05" value="${S.prefs.pitch}"></div></div>
    <label class="sw"><input type="checkbox" id="stSpeak" ${S.prefs.speak ? "checked" : ""}> Rispondi sempre anche a voce</label>
    <label class="sw"><input type="checkbox" id="stCont" ${S.prefs.continuous ? "checked" : ""}> Microfono sempre attivo</label>
    <label class="sw"><input type="checkbox" id="stWake" ${S.prefs.wake !== false ? "checked" : ""}> Rispondi solo se dico «${esc(set.assistente)}» (dopo una risposta puoi continuare 25 secondi senza nome)</label>
    <div class="acts" style="margin:6px 0"><button class="btn sm" id="stTest">Prova la voce</button><button class="btn sm ghost" id="stNotif">Attiva notifiche</button><button class="btn sm ghost" id="stMic">Testa il microfono</button></div>
    <p class="muted" style="font-size:13px">Voce del server: ${esc(tts.engine)}. Per usare una voce tua o diversa metti i file .onnx di Piper nella cartella <code>voci/</code> (vedi README).</p>
    <h4>Cervello</h4><p class="muted">${st.llm.available ? `Modello locale attivo: <b>${esc(st.llm.model)}</b> (${esc(st.llm.backend)})` : "Nessun modello locale: funziono con le regole base. Installa Ollama per le conversazioni libere (vedi README)."}</p>
    <h4>Email</h4><div class="acts"><button class="btn sm" id="stMail" ${st.mail ? "" : "disabled"}>Controlla la posta ora</button></div>
    ${st.mail ? "" : '<p class="muted" style="font-size:13px">Configura IMAP_HOST, IMAP_USER e IMAP_PASS nel file .env.</p>'}
    <h4>Lista nera</h4><div id="blL">${bl.map(b => `<div class="mini"><span>${esc(b.pattern)} <span class="muted">${esc(b.reason || "")}</span></span><button class="btn sm ghost" data-bl="${esc(b.pattern)}">Togli</button></div>`).join("") || '<p class="muted">Vuota.</p>'}</div>
    <div class="inline" style="margin-top:8px"><input id="blN" placeholder="Numero o prefisso con * (es. +3906873*)"><button class="btn" id="blAdd">Blocca</button></div>
    <h4>Rubrica (contatti che non vengono mai bloccati)</h4><div id="ctL">${ct.map(c => `<div class="mini"><span>${esc(c.name)} ${c.vip ? '<span class="tag amber">VIP</span>' : ""} <span class="muted">${esc(c.number)}</span></span><button class="btn sm ghost" data-ct="${esc(c.number)}">Togli</button></div>`).join("") || '<p class="muted">Vuota.</p>'}</div>
    <div class="f2" style="margin-top:8px"><input id="ctNum" placeholder="Numero o email"><input id="ctName" placeholder="Nome"></div>
    <label class="sw"><input type="checkbox" id="ctVip"> VIP: i suoi messaggi sono sempre importanti</label>
    <div class="acts"><button class="btn" id="ctAdd">Aggiungi contatto</button></div>
    <h4>Dati</h4><div class="acts"><button class="btn sm" id="stDemo">Carica dati demo</button><button class="btn sm red" id="stReset">Cancella tutto</button><button class="btn sm ghost" id="stTok">Token di accesso</button></div>
    <h4>Aria</h4><div class="acts"><button class="btn sm red" id="stOff">Spegni Aria in sicurezza</button></div>
    <div class="acts" style="margin-top:16px"><button class="btn" id="stSave">Salva</button></div>`);
  $("#stV", sh).value = S.prefs.voice || "";
  const collect = () => { S.prefs.voice = $("#stV", sh).value; S.prefs.rate = +$("#stRate", sh).value; S.prefs.pitch = +$("#stPitch", sh).value; S.prefs.speak = $("#stSpeak", sh).checked; S.prefs.continuous = $("#stCont", sh).checked; S.prefs.wake = $("#stWake", sh).checked; savePrefs(); };
  $("#stTest", sh).onclick = () => { collect(); speak(`Ciao ${$("#stU", sh).value}, sono ${$("#stA", sh).value}. Questa è la mia voce.`); };
  $("#stMic", sh).onclick = () => openMicTest();
  $("#stNotif", sh).onclick = async () => { const p = await Notification?.requestPermission?.(); toast("Notifiche", p === "granted" ? "Attive." : "Non consentite dal browser."); };
  $("#stMail", sh).onclick = async () => { const r = await api("/api/mail/sync", { method: "POST", body: {} }); toast(r.ok ? "Posta controllata" : "Errore posta", r.ok ? `${r.new} nuove email` : r.error, r.ok ? "" : "red"); refreshAll(); };
  sh.addEventListener("click", async e => {
    const t = e.target;
    if (t.dataset.bl) { await api("/api/blocklist?pattern=" + encodeURIComponent(t.dataset.bl), { method: "DELETE" }); openSettings(); }
    if (t.dataset.ct) { await api("/api/contacts?number=" + encodeURIComponent(t.dataset.ct), { method: "DELETE" }); openSettings(); }
  });
  $("#blAdd", sh).onclick = async () => { const v = $("#blN", sh).value.trim(); if (v) { await api("/api/blocklist", { body: { pattern: v, reason: "aggiunto a mano" } }); openSettings(); } };
  $("#ctAdd", sh).onclick = async () => { const n = $("#ctNum", sh).value.trim(), nm = $("#ctName", sh).value.trim(); if (n && nm) { await api("/api/contacts", { body: { number: n, name: nm, vip: $("#ctVip", sh).checked } }); openSettings(); } else toast("Mancano dati", "Servono numero e nome.", "amber"); };
  $("#stOff", sh).onclick = async () => {
    if (!confirm("Spegnere Aria? Le registrazioni in corso vengono salvate.")) return;
    if (S.recording) { stopRecording(); for (let i = 0; i < 40 && S.recording; i++) await new Promise(r => setTimeout(r, 100)); }
    S.prefs = { ...S.prefs, continuous: false }; stopListening(); stopSpeaking();   // solo in memoria: alla prossima apertura riparte
    try { await api("/api/shutdown", { method: "POST", body: {} }); } catch { }
    closeModal(); document.body.innerHTML = '<div style="display:grid;place-items:center;height:100vh;font:20px system-ui;color:#8fd">Aria è spenta. Puoi chiudere questa scheda.</div>';
  };
  $("#stDemo", sh).onclick = async () => { await loadDemo(); closeModal(); };
  $("#stReset", sh).onclick = async () => { if (confirm("Cancellare chiamate, messaggi, agenda, rubrica e lista nera? Le registrazioni audio restano sul disco.")) { await api("/api/reset", { method: "POST", body: {} }); S.lastEventId = 0; refreshAll(); closeModal(); } };
  $("#stTok", sh).onclick = () => { const t = prompt("Token di accesso (vuoto per rimuoverlo):", store.get("token", "")); if (t !== null) store.set("token", t); };
  $("#stSave", sh).onclick = async () => {
    collect();
    await api("/api/settings", { body: { assistente: $("#stA", sh).value, utente: $("#stU", sh).value, own_number: $("#stN", sh).value, remind_before: $("#stR", sh).value } });
    closeModal(); await loadStatus(); S.prefs.continuous ? startListening() : stopListening(); toast("Impostazioni salvate");
  };
}
$("#btnSettings").onclick = openSettings;

async function loadDemo() { await api("/api/demo", { method: "POST", body: {} }); S.lastEventId = 0; await refreshAll(true); toast("Dati demo caricati", "Prova: «fammi il report» o «come rispondo ad Anna?»"); }

/* ------------------------------------------------------------------ stato, promemoria, notifiche */
function notify(title, body) { try { if (Notification?.permission === "granted") new Notification(title, { body, icon: "/static/icon-192.png" }); } catch { } }
function chip(id, ok, text) { const c = $(id); c.className = "chip " + (ok === true ? "ok" : ok === "mid" ? "mid" : ""); $("em", c).textContent = text; }
async function loadStatus() {
  const st = await api("/api/status"); S.status = st;
  const nm = st.settings.assistente; $("#brandName").textContent = dotted(nm); $("#hudName").textContent = dotted(nm); $("#hud").style.setProperty("--nl", dotted(nm).length); document.title = nm + " · segretaria";
  chip("#chipLlm", st.llm.available ? true : "mid", st.llm.available ? st.llm.model : "regole base");
  chip("#chipVoice", st.tts.engine !== "browser" ? true : "mid", st.tts.engine === "piper" ? "Piper" : st.tts.engine === "espeak" ? "eSpeak" : "del browser");
  const mm = micMode(), sttOk = st.stt?.ready;
  chip("#chipMic", mm === "none" ? "mid" : mm === "server" && !sttOk ? "mid" : true,
    mm === "browser" ? "browser" : mm === "server" ? (sttOk ? "locale" : st.stt?.error ? "locale · errore" : "locale · carico…") : "solo testo · clicca");
  $("#stats").innerHTML = `<span class="stat red"><b>${st.counts.spam_total}</b> spam fermato</span><span class="stat amber"><b>${st.counts.calls_missed}</b> perse</span><span class="stat"><b>${st.counts.unread}</b> da leggere</span>`;
  updateBadges();
}
function updateBadges() {
  const n = S.status?.counts.unread || 0, b = $("#bdgInbox"); b.hidden = !n; b.textContent = n;
}
async function pollDue() {
  try {
    for (const d of await api("/api/due")) {
      const msg = `Promemoria: ${d.title}, ${d.phase}.`;
      toast("Promemoria", `${d.title} — ${d.phase}`, "amber"); notify("Promemoria", `${d.title} — ${d.phase}`);
      addMsg("ai", msg); if (!S.speaking) speak(msg);
      loadAgenda();
    }
  } catch { }
}
async function refreshAll(initial) {
  try { await Promise.all([loadStatus(), loadInbox(initial), loadAgenda(), loadRecs()]); } catch (e) { console.warn(e); }
}

/* ------------------------------------------------------------------ avvio */
(async function boot() {
  setState("idle");
  await refreshAll(true);
  console.log("Aria versione", S.status?.version);
  if (micMode() === "none") addMsg("ai", "Per parlarmi con " + (IS_OPERA ? "Opera" : "questo browser") + " serve la trascrizione locale: nel terminale scrivi  pip install faster-whisper  e riavvia. Intanto puoi scrivermi qui sotto. Clicca «Ascolto» in alto per la guida.");
  addMsg("ai", S.status?.empty ? `Ciao, sono ${S.status.settings.assistente}. Per cominciare scrivimi un comando, oppure carica i dati demo dal pannello a sinistra.` : "Pronta. Dimmi pure.");
  setInterval(() => loadInbox().then(loadStatus).catch(() => { }), 20000);
  setInterval(pollDue, 15000); pollDue();
  setInterval(() => { if (srFails > 0) srFails--; }, 60000);      // dopo un po riprova col browser
  if (S.prefs.continuous) { if (navigator.userActivation?.hasBeenActive) startListening(); else $("#live").textContent = "Tocca la pagina per attivare il microfono…"; }
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => { });
  const kick = () => { if (S.prefs.continuous && !S.listening && !S.speaking && !S.busy && !S.fatalListen) startListening(); };
  ["pointerdown", "keydown"].forEach(ev => document.addEventListener(ev, kick, { once: true }));
  setInterval(() => { if (document.visibilityState === "visible" && S.prefs.continuous && !S.listening && !S.speaking && !S.busy && !S.fatalListen) startListening(); }, 4000);
})();