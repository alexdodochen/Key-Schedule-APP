"""CV_APP — Phase 2: keyin (Key-In-The-CVSchedule) integration.

APIRouter mounted at /keyin in app.py. Carries the keyin-specific endpoints
(upload Excel, preview, start/continue/cancel, status, websocket, index page).
Login/register/admin routes live in app.py and are shared.

Reuses cv_app's auth.py / audit.py — they are byte-identical to the keyin
copies (verified via diff before vendoring).
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import audit
from auth import TokenData, verify_token
from keyin_excel_parser import parse_schedule_excel
from keyin_scheduler import ConnectionManager, SchedulerSession, build_schedule_from_config

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

router = APIRouter()
manager = ConnectionManager()
session: Optional[SchedulerSession] = None

# Per-user prefill payload pushed by /api/sched/handoff-to-keyin (in app.py).
# {username: {year, month, vs_schedule, cr_schedule, tw_holidays}}
prefill_cache: dict[str, dict[str, Any]] = {}


# Login is bypassed app-wide (see app.py); mirror the same synthetic user here.
_LOCAL_USER = TokenData("local", "admin")


def _get_user(request: Request):
    return _LOCAL_USER


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "-"


# ── index page ──────────────────────────────────────────────────────
@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def keyin_index(request: Request):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/login")
    state = session.state if session else "idle"
    return templates.TemplateResponse(request, "keyin_index.html", {
        "username": user.username,
        "is_admin": user.is_admin,
        "session_state": state,
    })


# ── prefill (handoff from cv_solver) ───────────────────────────────
@router.get("/api/prefill")
async def api_keyin_prefill(request: Request):
    user = _get_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "未登入"}, status_code=401)
    payload = prefill_cache.pop(user.username, None)
    if payload is None:
        return JSONResponse({"ok": False, "error": "no prefill"})
    return JSONResponse({"ok": True, "prefill": payload})


# ── Excel upload ────────────────────────────────────────────────────
@router.post("/api/upload-schedule")
async def api_upload_schedule(request: Request, file: UploadFile = File(...)):
    user = _get_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "未登入"}, status_code=401)
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".xls", ".xlsx"):
        return JSONResponse({"ok": False, "error": "僅支援 .xls 或 .xlsx 檔案"})
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        result = parse_schedule_excel(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    audit.log("keyin_upload_excel", user=user.username, ip=_client_ip(request),
              detail=file.filename)
    return JSONResponse(result)


# ── preview ─────────────────────────────────────────────────────────
@router.post("/api/preview")
async def api_preview(request: Request):
    user = _get_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "未登入"}, status_code=401)
    try:
        data = await request.json()
        schedule, _ = build_schedule_from_config(data)
        preview = [{"day": d, "doctor": doc, "shift": sh} for d, doc, sh in schedule]
        audit.log("keyin_preview", user=user.username, ip=_client_ip(request),
                  detail=f"{data.get('year')}/{data.get('month')} 共{len(preview)}筆")
        return JSONResponse({"ok": True, "preview": preview, "total": len(preview)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


# ── run controls ────────────────────────────────────────────────────
@router.post("/api/start")
async def api_start(request: Request):
    global session
    user = _get_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "未登入"}, status_code=401)
    if session and session.state in ("waiting_login", "running", "starting"):
        return JSONResponse({"ok": False, "error": "排班機器人正在執行中，請先取消"})
    data = await request.json()
    session = SchedulerSession(data, manager)
    asyncio.create_task(session.run())
    audit.log("keyin_start", user=user.username, ip=_client_ip(request),
              detail=f"{data.get('year')}/{data.get('month')}")
    return JSONResponse({"ok": True})


@router.post("/api/continue")
async def api_continue(request: Request):
    user = _get_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "未登入"}, status_code=401)
    if not session or session.state != "waiting_login":
        return JSONResponse({"ok": False, "error": "目前沒有等待登入的工作"})
    session.login_event.set()
    audit.log("keyin_continue", user=user.username, ip=_client_ip(request))
    return JSONResponse({"ok": True})


@router.post("/api/cancel")
async def api_cancel(request: Request):
    user = _get_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "未登入"}, status_code=401)
    if session:
        await session.cancel()
        audit.log("keyin_cancel", user=user.username, ip=_client_ip(request))
    return JSONResponse({"ok": True})


@router.get("/api/status")
async def api_status(request: Request):
    if not _get_user(request):
        return JSONResponse({"ok": False}, status_code=401)
    if not session:
        return JSONResponse({"state": "idle", "logs": []})
    if session.state == "done" and not getattr(session, "_logged_done", False):
        session._logged_done = True
        u = _get_user(request)
        if u:
            audit.log("keyin_done", user=u.username, ip=_client_ip(request))
    return JSONResponse({"state": session.state, "logs": session.logs[-100:]})


# ── websocket ───────────────────────────────────────────────────────
@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    # Login bypassed — accept any websocket from the local app.
    await manager.connect(ws)
    if session:
        await ws.send_json({"type": "status", "state": session.state})
        for line in session.logs[-50:]:
            await ws.send_json({"type": "log", "text": line})
    else:
        await ws.send_json({"type": "status", "state": "idle"})

    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
