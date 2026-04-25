"""Configurazione runtime di Il Ripassone.

Hash bcrypt della password admin computato una sola volta a startup.
Token di sessione random rigenerato ad ogni avvio (i cookie vecchi
diventano automaticamente invalidi al riavvio del server, e' una
proprieta voluta per un servizio "single session" da laptop in classe).
"""
from __future__ import annotations

import secrets
from pathlib import Path

import bcrypt

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = ROOT_DIR / "templates"
STATIC_DIR = ROOT_DIR / "static"

HOST = "0.0.0.0"
PORT = 8000
DEV_RELOAD = True

# Password admin (cambiare qui se serve)
ADMIN_PASSWORD_PLAIN = "pippo$4"
ADMIN_PASSWORD_HASH = bcrypt.hashpw(ADMIN_PASSWORD_PLAIN.encode(), bcrypt.gensalt()).decode()

# Token di sessione: rigenerato ad ogni boot.
# Il cookie ripassone_admin contiene questo valore quando autenticati.
ADMIN_SESSION_TOKEN = secrets.token_urlsafe(32)

# Cookie config
COOKIE_NAME = "ripassone_admin"
COOKIE_MAX_AGE = 8 * 3600  # 8 ore
