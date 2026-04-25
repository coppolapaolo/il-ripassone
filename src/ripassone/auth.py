"""Auth admin: verifica password (bcrypt) e cookie di sessione.

Pattern semplice "single token per server lifetime":
- al boot si genera ADMIN_SESSION_TOKEN random
- POST /login verifica la password con bcrypt e setta il cookie con quel token
- ogni request/WS controlla che il cookie corrisponda al token corrente

Limitazioni note (accettate per il caso d'uso "laptop in classe"):
- al riavvio del server tutti i cookie vecchi diventano invalidi
- non c'e revoca per-utente (c'e un solo admin)
- il token e in chiaro nel cookie (HttpOnly mitiga XSS, non un'intercettazione
  di rete; va dietro HTTPS in produzione, e ngrok la fornisce automaticamente)
"""
from __future__ import annotations

import bcrypt
from fastapi import Request, WebSocket

from ripassone import config


def check_password(plaintext: str) -> bool:
    """Verifica la password contro l'hash bcrypt configurato."""
    if not plaintext:
        return False
    return bcrypt.checkpw(plaintext.encode(), config.ADMIN_PASSWORD_HASH.encode())


def is_admin_request(request: Request) -> bool:
    return request.cookies.get(config.COOKIE_NAME) == config.ADMIN_SESSION_TOKEN


def is_admin_ws(ws: WebSocket) -> bool:
    return ws.cookies.get(config.COOKIE_NAME) == config.ADMIN_SESSION_TOKEN
