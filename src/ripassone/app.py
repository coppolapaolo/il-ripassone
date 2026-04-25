"""FastAPI app — Tappa 1: scheletro funzionante.

3 route HTTP (admin/team/display) che servono template Jinja2,
1 endpoint WebSocket di echo, file statici montati su /static.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ripassone import config
from ripassone.ws import router as ws_router

app = FastAPI(title="Il Ripassone")

app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
templates = Jinja2Templates(directory=config.TEMPLATES_DIR)
app.include_router(ws_router)


@app.get("/", response_class=HTMLResponse)
async def root() -> RedirectResponse:
    return RedirectResponse("/display")


@app.get("/admin", response_class=HTMLResponse)
async def admin(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "admin.html", {"role": "admin"})


@app.get("/team", response_class=HTMLResponse)
async def team(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "team.html", {"role": "team"})


@app.get("/display", response_class=HTMLResponse)
async def display(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "display.html", {"role": "display"})
