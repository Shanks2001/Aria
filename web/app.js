
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
  speaking: false, listening: false, recording: null, audio: null, pendingWake: null,
  prefs: Object.assign({ speak: true, continuous: false, voice: "", rate: 1, pitch: 1 }, store.get("prefs", {})),
};
const savePrefs = () => store.set("prefs", S.prefs);

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
function setState(st) { document.body.dataset.state = st; $("#hudState").textContent = ({ idle: "pronta", listening: "ti ascolto", thinking: "elaboro", speaking: "parlo", recording: "registro" })[st] || st; }
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
  const clean = cleanForSpeech(text);
  S.speaking = true; setState("speaking"); pauseListening();
  const done = () => { S.speaking = false; baseState(); resumeListening(); };
  try {
    const wantServer = (S.prefs.voice || "").startsWith("server:") || (!S.prefs.voice && S.status && S.status.tts.engine !== "browser");
    if (wantServer && S.status.tts.engine !== "browser") {
      const blob = await api("/api/tts", { body: { text: clean, voice: (S.prefs.voice || "").replace("server:", "") } });
      if (blob) {
        const a = new Audio(URL.createObjectURL(blob)); S.audio = a;
        a.playbackRate = S.prefs.rate;
        a.onended = a.onerror = done;
        await a.play(); return;
      }
    }
  } catch { /* ripiega sulla voce del browser */ }
  if (!("speechSynthesis" in window)) return done();
  const u = new SpeechSynthesisUtterance(clean);
  u.lang = "it-IT"; u.rate = S.prefs.rate; u.pitch = S.prefs.pitch;
  const v = voices.find(v => v.name === S.prefs.voice) || voices.find(v => v.lang?.toLowerCase().startsWith("it"));
  if (v) u.voice = v;
  u.onend = u.onerror = done;
  speechSynthesis.speak(u);
}
function stopSpeaking() {
  try { speechSynthesis.cancel(); } catch { }
  if (S.audio) { S.audio.onended = null; S.audio.pause(); S.audio = null; }
  S.speaking = false;
}

/* ------------------------------------------------------------------ voce in ingresso */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null, restartT = null;

function makeRec() {
  if (!SR) return null;
  const r = new SR();
  r.lang = "it-IT"; r.interimResults = true; r.continuous = false; r.maxAlternatives = 1;
  r.onstart = () => { S.listening = true; $("#btnMic").classList.add("on"); if (!S.speaking && !S.busy) baseState(); };
  r.onresult = ev => {
    let interim = "", final = "";
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      const t = ev.results[i][0].transcript;
      ev.results[i].isFinal ? final += t : interim += t;
    }
    $("#live").textContent = interim || final;
    if (final.trim()) onUtterance(final.trim());
  };
  r.onerror = e => {
    if (e.error === "not-allowed" || e.error === "service-not-allowed") {
      S.prefs.continuous = false; savePrefs();
      toast("Microfono bloccato", "Consenti l'uso del microfono nel browser (serve HTTPS o localhost).", "red");
    }
  };
  r.onend = () => {
    S.listening = false; $("#btnMic").classList.remove("on"); $("#live").textContent = "";
    if (!S.speaking && !S.busy) baseState();
    if (S.prefs.continuous && !S.speaking) { clearTimeout(restartT); restartT = setTimeout(startListening, 350); }
  };
  return r;
}
function startListening() {
  if (!SR) { toast("Riconoscimento vocale non disponibile", "Usa Chrome, Edge o Safari, oppure scrivi il comando.", "amber"); return; }
  if (S.listening || S.speaking) return;
  stopSpeaking();
  rec = rec || makeRec();
  try { rec.start(); } catch { }
}
function stopListening() { clearTimeout(restartT); try { rec && rec.abort(); } catch { } S.listening = false; $("#btnMic").classList.remove("on"); baseState(); }
function pauseListening() { try { rec && rec.abort(); } catch { } S.listening = false; $("#btnMic").classList.remove("on"); }
function resumeListening() { if (S.prefs.continuous && !S.busy) { clearTimeout(restartT); restartT = setTimeout(startListening, 500); } }

function wakeName() { return (S.status?.settings.assistente || "aria").toLowerCase(); }
function onUtterance(text) {
  if (!S.prefs.continuous) return send(text);          // tocca-e-parla: tutto ciò che dici è un comando
  const low = text.toLowerCase(), w = wakeName();
  if (S.pendingWake && Date.now() < S.pendingWake) { S.pendingWake = null; return send(text); }
  const i = low.indexOf(w);
  if (i < 0) return;                                   // ascolto continuo: ignora se non mi chiami
  const rest = text.slice(i + w.length).replace(/^[\s,.:!]+/, "");
  if (rest.length > 1) send(rest);
  else { S.pendingWake = Date.now() + 9000; speak("Dimmi."); }
}
$("#btnMic").addEventListener("click", () => { S.listening ? stopListening() : startListening(); });

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
async function send(text) {
  text = text.trim(); if (!text || S.busy) return;
  addMsg("me", text); $("#txt").value = ""; S.busy = true; setState("thinking");
  try {
    const out = await api("/api/chat", { body: { text, history: S.history.slice(-8) } });
    S.history.push({ role: "user", content: text }, { role: "assistant", content: out.reply });
    const rs = out.data?.replies;
    addMsg("ai", out.reply, rs ? repliesBox(rs, out.data.source) : null);
    S.busy = false; baseState();
    handleAction(out);
    if (S.prefs.speak) await speak(out.reply); else resumeListening();
  } catch (e) {
    S.busy = false; baseState(); addMsg("ai", "Non riesco a contattare il server: " + e.message); resumeListening();
  }
}
function handleAction(out) {
  const a = out.action || "";
  if (a === "start_recording") startRecording(out.data?.title || "");
  else if (a === "stop_recording") stopRecording();
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
document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });

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
    <label class="sw"><input type="checkbox" id="stCont" ${S.prefs.continuous ? "checked" : ""}> Ascolto continuo: attivami dicendo «${esc(set.assistente)}…»</label>
    <div class="acts" style="margin:6px 0"><button class="btn sm" id="stTest">Prova la voce</button><button class="btn sm ghost" id="stNotif">Attiva notifiche</button></div>
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
    <div class="acts" style="margin-top:16px"><button class="btn" id="stSave">Salva</button></div>`);
  $("#stV", sh).value = S.prefs.voice || "";
  const collect = () => { S.prefs.voice = $("#stV", sh).value; S.prefs.rate = +$("#stRate", sh).value; S.prefs.pitch = +$("#stPitch", sh).value; S.prefs.speak = $("#stSpeak", sh).checked; S.prefs.continuous = $("#stCont", sh).checked; savePrefs(); };
  $("#stTest", sh).onclick = () => { collect(); speak(`Ciao ${$("#stU", sh).value}, sono ${$("#stA", sh).value}. Questa è la mia voce.`); };
  $("#stNotif", sh).onclick = async () => { const p = await Notification?.requestPermission?.(); toast("Notifiche", p === "granted" ? "Attive." : "Non consentite dal browser."); };
  $("#stMail", sh).onclick = async () => { const r = await api("/api/mail/sync", { method: "POST", body: {} }); toast(r.ok ? "Posta controllata" : "Errore posta", r.ok ? `${r.new} nuove email` : r.error, r.ok ? "" : "red"); refreshAll(); };
  sh.addEventListener("click", async e => {
    const t = e.target;
    if (t.dataset.bl) { await api("/api/blocklist?pattern=" + encodeURIComponent(t.dataset.bl), { method: "DELETE" }); openSettings(); }
    if (t.dataset.ct) { await api("/api/contacts?number=" + encodeURIComponent(t.dataset.ct), { method: "DELETE" }); openSettings(); }
  });
  $("#blAdd", sh).onclick = async () => { const v = $("#blN", sh).value.trim(); if (v) { await api("/api/blocklist", { body: { pattern: v, reason: "aggiunto a mano" } }); openSettings(); } };
  $("#ctAdd", sh).onclick = async () => { const n = $("#ctNum", sh).value.trim(), nm = $("#ctName", sh).value.trim(); if (n && nm) { await api("/api/contacts", { body: { number: n, name: nm, vip: $("#ctVip", sh).checked } }); openSettings(); } else toast("Mancano dati", "Servono numero e nome.", "amber"); };
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
  chip("#chipMic", SR ? true : "mid", SR ? "pronto" : "solo testo");
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
  addMsg("ai", S.status?.empty ? `Ciao, sono ${S.status.settings.assistente}. Per cominciare scrivimi un comando, oppure carica i dati demo dal pannello a sinistra.` : "Pronta. Dimmi pure.");
  setInterval(() => loadInbox().then(loadStatus).catch(() => { }), 20000);
  setInterval(pollDue, 15000); pollDue();
  if (S.prefs.continuous) startListening();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => { });
  document.addEventListener("click", () => { }, { once: true });
})();
