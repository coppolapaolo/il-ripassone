"""FastAPI app — route HTTP, mount static, dispatch WebSocket.

Le route admin (/admin GET, /admin/upload POST, e gli eventi admin/* su WS)
sono gating per cookie di sessione (vedi auth.py).
"""
from __future__ import annotations

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ripassone import auth, config, excel, state
from ripassone.ws import broadcast_state, router as ws_router

app = FastAPI(title="Il Ripassone")

app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
templates = Jinja2Templates(directory=config.TEMPLATES_DIR)
app.include_router(ws_router)


# ============================================================
# Pagine pubbliche
# ============================================================
@app.get("/", response_class=HTMLResponse)
async def root() -> RedirectResponse:
    return RedirectResponse("/display")


@app.get("/team", response_class=HTMLResponse)
async def team(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "team.html", {"role": "team"})


@app.get("/display", response_class=HTMLResponse)
async def display(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "display.html", {"role": "display"})


# ============================================================
# Auth admin
# ============================================================
@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, error: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"role": "anon", "error": error})


@app.post("/login")
async def login_submit(password: str = Form(...)) -> Response:
    if not auth.check_password(password):
        return RedirectResponse("/login?error=1", status_code=303)
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(
        key=config.COOKIE_NAME,
        value=config.ADMIN_SESSION_TOKEN,
        max_age=config.COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return resp


@app.post("/logout")
async def logout() -> Response:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(config.COOKIE_NAME)
    return resp


# ============================================================
# Admin (gating cookie)
# ============================================================
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request) -> Response:
    if not auth.is_admin_request(request):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "admin.html", {"role": "admin"})


@app.post("/admin/upload")
async def admin_upload(request: Request, file: UploadFile) -> JSONResponse:
    if not auth.is_admin_request(request):
        raise HTTPException(401, "non autenticato")
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "atteso un file .xlsx")

    content = await file.read()
    next_id = (max(state.STATE.questions_pool.keys()) + 1) if state.STATE.questions_pool else 1
    result = excel.parse_workbook(content, filename=file.filename, id_offset=next_id - 1)
    if result.questions:
        try:
            await state.admin_add_questions(result.questions)
        except state.StateError as e:
            raise HTTPException(409, str(e))
        # broadcast aggiornato a tutti i client WS
        await broadcast_state()
    return JSONResponse(result.to_dict())
