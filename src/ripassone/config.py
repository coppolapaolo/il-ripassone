"""Configurazione runtime di Il Ripassone.

Per Tappa 1 sono solo costanti hardcoded. Quando aggiungeremo l'auth admin
useremo passlib per gestire l'hash bcrypt della password "pippo$4".
"""
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = ROOT_DIR / "templates"
STATIC_DIR = ROOT_DIR / "static"

HOST = "0.0.0.0"
PORT = 8000
DEV_RELOAD = True

ADMIN_PASSWORD_PLAINTEXT = "pippo$4"
