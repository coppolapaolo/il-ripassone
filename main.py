"""Entry point: lancia Il Ripassone con uvicorn (+ ngrok opzionale).

Uso:
    uv run main.py            # solo locale (http://localhost:8000)
    uv run main.py --public   # locale + ngrok (URL pubblico per gli studenti)
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

# rendere importabile src/ripassone senza installare il pacchetto
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import qrcode
import uvicorn

from ripassone import config


# ============================================================
# ngrok helpers
# ============================================================
NGROK_API = "http://localhost:4040/api/tunnels"


def start_ngrok() -> subprocess.Popen | None:
    """Lancia ngrok come subprocess. Ritorna il process handle o None su errore."""
    try:
        return subprocess.Popen(
            ["ngrok", "http", str(config.PORT), "--log=stdout"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        print("\n  ⚠  ngrok non trovato nel PATH. Installa con: brew install ngrok\n")
        return None


def fetch_public_url(timeout_sec: float = 15.0) -> str | None:
    """Polla l'API ngrok finche un tunnel pubblico e disponibile."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with urlopen(NGROK_API, timeout=1.0) as resp:
                data = json.loads(resp.read())
            tunnels = data.get("tunnels", [])
            for t in tunnels:
                url = t.get("public_url", "")
                if url.startswith("https://"):
                    return url
            for t in tunnels:
                url = t.get("public_url", "")
                if url.startswith("http://"):
                    return url
        except (URLError, ConnectionResetError, OSError):
            pass
        time.sleep(0.4)
    return None


def print_qr(text: str) -> None:
    """Stampa il QR code in ASCII sul terminale."""
    qr = qrcode.QRCode(border=1)
    qr.add_data(text)
    qr.make(fit=True)
    qr.print_ascii(invert=True)


def print_banner(public_url: str | None) -> None:
    print()
    print("  " + "═" * 60)
    print(f"  ▸ IL RIPASSONE  ·  http://{config.HOST}:{config.PORT}")
    print("  " + "═" * 60)
    print()
    if public_url:
        print(f"  🌐 PUBBLICO  {public_url}")
        print()
        print(f"     Admin    {public_url}/admin   (password: {config.ADMIN_PASSWORD_PLAIN})")
        print(f"     Squadra  {public_url}/team")
        print(f"     Display  {public_url}/display")
        print(f"     Info+QR  {public_url}/info     ← proietta questa")
        print()
        print("  📱 QR per /team (per gli studenti):")
        print()
        print_qr(f"{public_url}/team")
    else:
        print("  ⚠  ngrok non attivo. Server raggiungibile solo da localhost.")
        print()
        print(f"     Admin    http://localhost:{config.PORT}/admin   (password: {config.ADMIN_PASSWORD_PLAIN})")
        print(f"     Squadra  http://localhost:{config.PORT}/team")
        print(f"     Display  http://localhost:{config.PORT}/display")
        print(f"     Info+QR  http://localhost:{config.PORT}/info")
    print()


# ============================================================
# Entry point
# ============================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="Il Ripassone — sfida interattiva a squadre")
    parser.add_argument(
        "--public", action="store_true",
        help="Espone il server via ngrok (URL pubblico raggiungibile da telefoni e altre reti)",
    )
    parser.add_argument(
        "--no-reload", action="store_true",
        help="Disabilita il reload automatico (utile in produzione)",
    )
    parser.add_argument(
        "--serious", action="store_true",
        help="Grafica accademico-istituzionale (default: cartoon-pop)",
    )
    args = parser.parse_args()

    if args.serious:
        os.environ["RIPASSONE_SERIOUS"] = "1"

    ngrok_proc: subprocess.Popen | None = None
    public_url: str | None = None

    if args.public:
        ngrok_proc = start_ngrok()
        if ngrok_proc is not None:
            print("  ⏳ avvio ngrok...")
            public_url = fetch_public_url()
            if public_url is None:
                print("  ⚠  ngrok non risponde sul management API (porta 4040).")

    print_banner(public_url)

    # propaga URL pubblico nell'ambiente per /info
    if public_url:
        os.environ["RIPASSONE_PUBLIC_URL"] = public_url

    # cleanup graceful
    def cleanup(*_a) -> None:
        if ngrok_proc and ngrok_proc.poll() is None:
            ngrok_proc.terminate()
        sys.exit(0)
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        uvicorn.run(
            "ripassone.app:app",
            host=config.HOST,
            port=config.PORT,
            reload=(config.DEV_RELOAD and not args.no_reload),
            reload_dirs=["src", "templates", "static"],
        )
    finally:
        if ngrok_proc and ngrok_proc.poll() is None:
            ngrok_proc.terminate()


if __name__ == "__main__":
    main()
