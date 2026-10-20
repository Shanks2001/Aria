#!/usr/bin/env python3
"""Avvio della segretaria AI.

  python run.py                 solo su questo PC        http://127.0.0.1:8000
  python run.py --lan           raggiungibile dal telefono sulla stessa Wi-Fi
  python run.py --lan --https   come sopra ma con HTTPS (necessario per microfono e installazione su telefono)
"""
import argparse
import os
import socket
import subprocess
import sys
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent


def load_env() -> None:
    f = BASE / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def make_cert(ip: str) -> tuple[Path, Path]:
    d = BASE / "archivio" / "cert"
    d.mkdir(parents=True, exist_ok=True)
    crt, key = d / "cert.pem", d / "key.pem"
    if crt.exists() and key.exists() and (d / "ip.txt").read_text() == ip:
        return crt, key
    try:
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825", "-keyout", str(key),
                        "-out", str(crt), "-subj", "/CN=segretaria-ai",
                        "-addext", f"subjectAltName=DNS:localhost,IP:127.0.0.1,IP:{ip}"],
                       check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as e:
        sys.exit(f"Non riesco a creare il certificato HTTPS (serve openssl): {e}")
    (d / "ip.txt").write_text(ip)
    return crt, key


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lan", action="store_true", help="ascolta su tutta la rete locale (telefono)")
    ap.add_argument("--https", action="store_true", help="HTTPS con certificato autofirmato")
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--installa-ascolto", action="store_true",
                    help="installa faster-whisper: ascolto vocale offline (necessario su Opera GX, Brave, Firefox)")
    a = ap.parse_args()
    load_env()

    if a.installa_ascolto:
        print("Installo faster-whisper (circa 200 MB, serve internet)...")
        rc = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "faster-whisper"]).returncode
        if rc == 0:
            print("\nFatto. Avvia con:  python run.py")
            print("Al primo avvio scarica il modello vocale (una volta sola, serve internet).")
        sys.exit(rc)

    try:
        import faster_whisper  # noqa: F401
        print("  Ascolto locale: faster-whisper trovato.")
    except ImportError:
        print("  Se usi Opera GX, Brave o Firefox il browser non sa ascoltare la voce.")
        print("  Installa l'ascolto locale una volta sola:  python run.py --installa-ascolto")

    import uvicorn
    ip = lan_ip()
    kw = {}
    scheme = "http"
    if a.https:
        crt, key = make_cert(ip)
        kw.update(ssl_certfile=str(crt), ssl_keyfile=str(key))
        scheme = "https"
    host = "0.0.0.0" if a.lan else "127.0.0.1"

    from app import VERSION
    print(f"\n  Aria  (versione {VERSION})")
    print(f"  Su questo PC:   {scheme}://localhost:{a.port}")
    if a.lan:
        print(f"  Dal telefono:   {scheme}://{ip}:{a.port}   (stessa Wi-Fi)")
        if not os.getenv("Aria_TOKEN"):
            print("  ATTENZIONE: sei in rete locale senza password. Imposta Aria_TOKEN nel file .env.")
        if a.https:
            print("  Il browser avviserà che il certificato è autofirmato: è normale, scegli 'Procedi comunque'.")
    print("  Ctrl+C per fermare.\n")
    if not a.no_browser and not a.lan:
        webbrowser.open(f"{scheme}://localhost:{a.port}")
    from app import runtime
    config = uvicorn.Config("app.main:app", host=host, port=a.port, log_level="warning", **kw)
    server = uvicorn.Server(config)
    runtime.server = server
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            from app import db
            db.conn().commit()
            db.conn().close()
        except Exception:
            pass
        print("\n  Aria spenta. A presto!")
        sys.stdout.flush()
        os._exit(0)      # chiude subito, senza i messaggi d'errore delle librerie che si spengono dopo di noi


if __name__ == "__main__":
    os.chdir(BASE)
    sys.path.insert(0, str(BASE))
    main()
