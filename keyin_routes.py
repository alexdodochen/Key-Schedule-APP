"""CV_APP — Phase 2: keyin (Key-In-The-CVSchedule) integration.

APIRouter mounted at /keyin in app.py. Carries the keyin-specific endpoints
(upload Excel, preview, start/continue/cancel, status, websocket, index page).
Login/register/admin routes live in app.py and are shared.

Reuses cv_app's auth.py / audit.py — they are byte-identical to the keyin
copies (verified via diff before vendoring).
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime
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
DRAFTS_DIR = BASE_DIR / "keyin_drafts"
DRAFTS_DIR.mkdir(exist_ok=True)
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


# ── Draft save / load (mid-form-fill resume points, local-only) ─────
# Mirrors the 排班 path's sched_drafts/. The user types VS/CR per day +
# rotation lists into the form, which can take a while; they can stash
# the in-progress state and reopen the app later without redoing it.
# Stored under keyin_drafts/{safe_name}.json (gitignored).
@router.post("/api/save-draft")
async def api_keyin_save_draft(request: Request):
    user = _get_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "未登入"}, status_code=401)
    body = await request.json()
    state_blob = body.get("state") or {}
    year = state_blob.get("year")
    month = state_blob.get("month")
    if not year or not month:
        return JSONResponse({"ok": False, "error": "state 缺 year/month"})
    name = body.get("name") or f"{int(year)}{int(month):02d}"
    safe = "".join(c for c in str(name) if c.isalnum() or c in "_-")
    if not safe:
        return JSONResponse({"ok": False, "error": "草稿名稱不合法"})
    payload = {
        "saved_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "saved_by": user.username,
        "state": state_blob,
    }
    path = DRAFTS_DIR / f"{safe}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    audit.log("keyin_save_draft", user=user.username, ip=_client_ip(request), detail=safe)
    return JSONResponse({"ok": True, "name": safe, "saved_at": payload["saved_at"]})


@router.get("/api/list-drafts")
async def api_keyin_list_drafts(request: Request):
    user = _get_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "未登入"}, status_code=401)
    items = []
    for f in sorted(DRAFTS_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        s = d.get("state") or {}
        items.append({
            "name": f.stem,
            "saved_at": d.get("saved_at", ""),
            "saved_by": d.get("saved_by", ""),
            "year": s.get("year"),
            "month": s.get("month"),
        })
    items.sort(key=lambda x: x["saved_at"], reverse=True)
    return JSONResponse({"ok": True, "drafts": items})


@router.post("/api/load-draft")
async def api_keyin_load_draft(request: Request):
    user = _get_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "未登入"}, status_code=401)
    body = await request.json()
    name = "".join(c for c in str(body.get("name", "")) if c.isalnum() or c in "_-")
    if not name:
        return JSONResponse({"ok": False, "error": "缺 name"})
    path = DRAFTS_DIR / f"{name}.json"
    if not path.exists():
        return JSONResponse({"ok": False, "error": "草稿不存在"})
    audit.log("keyin_load_draft", user=user.username, ip=_client_ip(request), detail=name)
    return JSONResponse({"ok": True, "draft": json.loads(path.read_text(encoding="utf-8"))})


@router.post("/api/delete-draft")
async def api_keyin_delete_draft(request: Request):
    user = _get_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "未登入"}, status_code=401)
    body = await request.json()
    name = "".join(c for c in str(body.get("name", "")) if c.isalnum() or c in "_-")
    if not name:
        return JSONResponse({"ok": False, "error": "缺 name"})
    path = DRAFTS_DIR / f"{name}.json"
    if path.exists():
        path.unlink()
    audit.log("keyin_delete_draft", user=user.username, ip=_client_ip(request), detail=name)
    return JSONResponse({"ok": True})


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
