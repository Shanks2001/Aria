from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
VOCI = BASE / "voci"
CACHE = BASE / "archivio" / "tts_cache"
VOCI.mkdir(exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)


def _piper_cmd() -> list[str] | None:
    exe = shutil.which("piper")
    if exe:
        return [exe]
    try:
        import piper  # noqa: F401
        return [os.sys.executable, "-m", "piper"]
    except Exception:
        return None


def voices() -> list[str]:
    return sorted(p.stem for p in VOCI.glob("*.onnx"))


def tts_engine() -> dict:
    if _piper_cmd() and voices():
        return {"engine": "piper", "voices": voices()}
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        return {"engine": "espeak", "voices": ["it"]}
    return {"engine": "browser", "voices": []}


def synth(text: str, voice: str | None = None) -> bytes | None:
    """Ritorna un WAV, oppure None se il server non ha un motore (allora parla il browser)."""
    text = text.strip()[:1200]
    eng = tts_engine()
    if not text or eng["engine"] == "browser":
        return None
    voice = voice if voice in eng["voices"] else eng["voices"][0]
    key = CACHE / (hashlib.sha1(f"{eng['engine']}|{voice}|{text}".encode()).hexdigest() + ".wav")
    if key.exists():
        return key.read_bytes()
    try:
        if eng["engine"] == "piper":
            subprocess.run(_piper_cmd() + ["--model", str(VOCI / f"{voice}.onnx"), "--output_file", str(key)],
                           input=text.encode(), check=True, capture_output=True, timeout=60)
        else:
            exe = shutil.which("espeak-ng") or shutil.which("espeak")
            subprocess.run([exe, "-v", "it", "-s", "165", "-w", str(key), text], check=True, capture_output=True, timeout=30)
        return key.read_bytes()
    except Exception:
        return None


# ------------------------------------------------------------------------ STT
import re
import threading

_model = None
_lock = threading.Lock()
_state = {"ready": False, "loading": False, "error": ""}
MODEL_NAME = lambda: os.getenv("WHISPER_MODEL", "small")  # noqa: E731
_PROMPT = ("Comandi per la segretaria: ricordami domani alle 15 il dentista. Cosa ho oggi? Fammi il report. "
           "Come rispondo a Marco? Registra. Ferma la registrazione. Blocca il numero. Aria, che ore sono?")
_HALLU = re.compile(r"sottotitoli|amara\.org|grazie per la visione|iscriviti al canale|\bwww\.", re.I)


def stt_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def stt_state() -> dict:
    return {"available": stt_available(), "model": MODEL_NAME(), **_state}


def _compat_av() -> None:
    """Con un PyAV vecchio faster-whisper va in errore («metadata_errors»): togliamo quell'argomento e funziona lo stesso."""
    try:
        import av
        if getattr(av, "_aria_patched", False):
            return
        orig = av.open

        def _open(*a, **k):
            try:
                return orig(*a, **k)
            except TypeError:
                k.pop("metadata_errors", None)
                return orig(*a, **k)
        av.open = _open
        av._aria_patched = True
    except Exception:
        pass


def _load():
    global _model
    with _lock:
        if _model is not None:
            return _model
        _state.update(loading=True, error="")
        try:
            _compat_av()
            from faster_whisper import WhisperModel
            _model = WhisperModel(MODEL_NAME(), device="cpu", compute_type="int8")
            _state.update(ready=True)
        except Exception as e:  # download non riuscito (niente internet), memoria, ecc.
            _state.update(error=f"{type(e).__name__}: {e}")
            raise
        finally:
            _state.update(loading=False)
        return _model


def preload() -> None:
    """Carica (e se serve scarica) il modello in background all'avvio, così il primo comando non aspetta."""
    if os.getenv("WHISPER_PRELOAD", "1") != "0" and stt_available() and not _state["ready"] and not _state["loading"]:
        def work():
            try:
                _load()
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()


def transcribe(path: str, lang: str = "it") -> str:
    """Testo trascritto ('' se silenzio). Solleva eccezione se il modello non è disponibile."""
    model = _load()
    try:
        segs, _ = model.transcribe(path, language=lang, vad_filter=True, beam_size=1,
                                   initial_prompt=_PROMPT, condition_on_previous_text=False)
        text = " ".join(x.text.strip() for x in segs).strip()
    except TypeError as e:
        if "metadata_errors" in str(e):
            raise RuntimeError("PyAV (av) è troppo vecchio per faster-whisper: nel terminale scrivi  python -m pip install -U av faster-whisper") from e
        raise
    return "" if _HALLU.search(text) else text
