
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
_model = None


def stt_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def transcribe(path: str, lang: str = "it") -> str | None:
    global _model
    if not stt_available():
        return None
    try:
        from faster_whisper import WhisperModel
        if _model is None:
            _model = WhisperModel(os.getenv("WHISPER_MODEL", "base"), device="cpu", compute_type="int8")
        segs, _ = _model.transcribe(path, language=lang, vad_filter=True)
        return " ".join(s.text.strip() for s in segs).strip()
    except Exception:
        return None
