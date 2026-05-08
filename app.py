#!/usr/bin/env python3
"""CV_APP — FastAPI backend.

Routes:
  /login, /register, /logout    — auth (templates from keyin)
  /                             — home menu (排班 / key班)
  /sched                        — 5-step scheduling UI (Phase 1)
  /admin                        — admin console
  /api/sched/init               — load month context (H, W, baseline, holidays)
  /api/sched/compute            — compute VS / 建寬 / category targets given X
  /api/sched/solve              — run backtracking solver (preview only)
  /api/sched/write              — write the solved schedule to Google Sheet
"""
from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import audit
from auth import (
    approve_user, get_admin_names, get_all_users, login_user, register_user,
    reject_delete_user, verify_token,
)
import cv_solver
import gsheet_io

BASE_DIR = Path(__file__).parent
app = FastAPI(title="CV_APP — 心臟內科排班整合")

static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

_solve_cache: dict[str, Any] = {}


# ── helpers ─────────────────────────────────────────────────────────
def _get_user(request: Request):
    token = request.cookies.get("token")
    if not token:
        return None
    try:
        return verify_token(token)
    except Exception:
        return None


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "-"


def _parse_iso_date(s: str) -> date:
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


def _serialize_schedule(schedule: dict[date, str]) -> list[dict]:
    out = []
    for d in sorted(schedule.keys()):
        out.append({
            "date": d.strftime("%Y-%m-%d"),
            "day": d.day,
            "weekday": d.weekday(),
            "is_holiday": gsheet_io.is_taiwan_holiday(d),
            "doctor": schedule[d],
        })
    return out


# ── auth pages ──────────────────────────────────────────────────────
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": ""})


@app.post("/login")
async def do_login(request: Request):
    form = await request.form()
    username = str(form.get("username", ""))
    password = str(form.get("password", ""))
    ip = _client_ip(request)

    token = login_user(username, password)
    if token is None:
        audit.log("login_fail", user=username, ip=ip, detail="帳號或密碼錯誤")
        return templates.TemplateResponse(request, "login.html", {"error": "帳號或密碼錯誤"})
    if token == "PENDING":
        audit.log("login_fail", user=username, ip=ip, detail="帳號待審核")
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "您的帳號尚待管理者審核，審核通過後方可登入"},
        )

    audit.log("login_success", user=username, ip=ip)
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie("token", token, httponly=True, samesite="lax", max_age=3600 * 8)
    return resp


@app.get("/logout")
async def logout(request: Request):
    user = _get_user(request)
    if user:
        audit.log("logout", user=user.username, ip=_client_ip(request))
    resp = RedirectResponse(url="/login")
    resp.delete_cookie("token")
    return resp


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html", {"error": "", "success": ""})


@app.post("/register")
async def do_register(request: Request):
    form = await request.form()
    username    = str(form.get("username", "")).strip()
    password    = str(form.get("password", ""))
    password2   = str(form.get("password2", ""))
    doctor_name = str(form.get("doctor_name", "")).strip()
    employee_id = str(form.get("employee_id", "")).strip()
    rank        = str(form.get("rank", "")).strip()
    roc_str     = str(form.get("training_start_roc", "")).strip()

    def _err(msg):
        return templates.TemplateResponse(
            request, "register.html",
            {"error": msg, "success": "", "form": dict(form)},
        )

    if not all([username, password, doctor_name, employee_id, rank]):
        return _err("所有欄位皆為必填")
    if password != password2:
        return _err("兩次輸入的密碼不一致")
    training_start_roc = int(roc_str) if roc_str.isdigit() else None

    ok, msg = register_user(
        username=username, password=password, doctor_name=doctor_name,
        employee_id=employee_id, rank=rank, training_start_roc=training_start_roc,
    )
    ip = _client_ip(request)
    if ok:
        admins = get_admin_names()
        admin_str = "、".join(admins) if admins else "管理者"
        msg = (f"註冊成功！帳號待審核中。<br>"
               f"<b>請聯絡：{admin_str} 醫師</b> 審核您的申請。")
        audit.log("register", user=username, ip=ip,
                  detail=f"{doctor_name} / {rank} / 員工號:{employee_id}")
        return templates.TemplateResponse(
            request, "register.html",
            {"error": "", "success": msg, "form": {}},
        )
    return _err(msg)


# ── home / scheduling pages ─────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse(request, "home.html", {
        "username": user.username,
        "is_admin": user.is_admin,
    })


@app.get("/sched", response_class=HTMLResponse)
async def sched_page(request: Request):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse(request, "schedule_gen.html", {
        "username": user.username,
        "is_admin": user.is_admin,
        "doctors_cr": cv_solver.CRS,
        "doctors_vs": cv_solver.VS_LIST,
        "doctors_mid": cv_solver.INTER_MID,
    })


# ── admin pages (reused from keyin) ─────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/login")
    if not user.is_admin:
        return HTMLResponse("<h2>403 — 無管理者權限</h2>", status_code=403)
    return templates.TemplateResponse(request, "admin.html", {"username": user.username})


@app.get("/api/admin/users")
async def api_admin_users(request: Request):
    user = _get_user(request)
    if not user or not user.is_admin:
        return JSONResponse({"ok": False, "error": "無權限"}, status_code=403)
    return JSONResponse({"ok": True, "users": get_all_users()})


@app.post("/api/admin/approve/{username}")
async def api_approve(username: str, request: Request):
    user = _get_user(request)
    if not user or not user.is_admin:
        return JSONResponse({"ok": False, "error": "無權限"}, status_code=403)
    ok = approve_user(username)
    if ok:
        audit.log("admin_approve", user=user.username, ip=_client_ip(request),
                  detail=f"核准帳號: {username}")
    return JSONResponse({"ok": ok})


@app.post("/api/admin/delete/{username}")
async def api_delete(username: str, request: Request):
    user = _get_user(request)
    if not user or not user.is_admin:
        return JSONResponse({"ok": False, "error": "無權限"}, status_code=403)
    if username == user.username:
        return JSONResponse({"ok": False, "error": "不能刪除自己的帳號"})
    ok = reject_delete_user(username)
    if ok:
        audit.log("admin_delete", user=user.username, ip=_client_ip(request),
                  detail=f"刪除帳號: {username}")
    return JSONResponse({"ok": ok})


@app.get("/api/admin/logs")
async def api_admin_logs(request: Request):
    user = _get_user(request)
    if not user or not user.is_admin:
        return JSONResponse({"ok": False, "error": "無權限"}, status_code=403)
    return JSONResponse({"ok": True, "logs": audit.read_logs(limit=500)})


# ── scheduling APIs ─────────────────────────────────────────────────
@app.post("/api/sched/init")
async def api_sched_init(request: Request):
    user = _get_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "未登入"}, status_code=401)
    body = await request.json()
    year  = int(body["year"])
    month = int(body["month"])
    days = cv_solver.month_days(year, month)
    H, W = cv_solver.month_h_w(year, month)
    get_stat_type = gsheet_io.make_stat_type_fn(gsheet_io.is_taiwan_holiday)
    calendar_info = []
    for d in days:
        calendar_info.append({
            "date": d.strftime("%Y-%m-%d"),
            "day": d.day,
            "weekday": d.weekday(),
            "is_holiday": gsheet_io.is_taiwan_holiday(d),
            "stat_type": get_stat_type(d),
        })
    try:
        sheet = gsheet_io.get_sheet()
        baseline = gsheet_io.load_cumulative_stats(sheet)
        sheet_ok = True
        sheet_err = ""
    except Exception as e:
        baseline = {n: {"平日": 0, "週五": 0, "週六": 0, "週日": 0, "假日": 0}
                    for n in cv_solver.ALL_DOCTORS}
        sheet_ok = False
        sheet_err = str(e)
    audit.log("sched_init", user=user.username, ip=_client_ip(request),
              detail=f"{year}/{month:02d}")
    return JSONResponse({
        "ok": True,
        "year": year,
        "month": month,
        "H": H,
        "W": W,
        "calendar": calendar_info,
        "baseline": baseline,
        "doctors": {
            "cr": cv_solver.CRS,
            "vs": cv_solver.VS_LIST,
            "mid": cv_solver.INTER_MID,
        },
        "sheet_ok": sheet_ok,
        "sheet_err": sheet_err,
    })


@app.post("/api/sched/compute")
async def api_sched_compute(request: Request):
    user = _get_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "未登入"}, status_code=401)
    body = await request.json()
    year     = int(body["year"])
    month    = int(body["month"])
    X        = int(body["X"])
    baseline = body.get("baseline") or {}
    targets = cv_solver.compute_initial_targets(year, month, X, baseline)
    return JSONResponse({"ok": True, "targets": targets})


@app.post("/api/sched/solve")
async def api_sched_solve(request: Request):
    user = _get_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "未登入"}, status_code=401)
    body = await request.json()
    year     = int(body["year"])
    month    = int(body["month"])
    X        = int(body["X"])
    fixed_in = body.get("fixed", {})
    avoid_in = body.get("avoid", {})
    baseline = body.get("baseline") or {}
    jk_target = body.get("jk_target")

    fixed = {_parse_iso_date(k): v for k, v in fixed_in.items() if v}
    avoid = {n: [_parse_iso_date(d) for d in dates]
             for n, dates in avoid_in.items() if dates}

    result = cv_solver.solve_month(
        year, month, X, fixed, avoid, baseline,
        jk_target=int(jk_target) if jk_target is not None else None,
    )
    if result is None:
        audit.log("sched_solve_fail", user=user.username, ip=_client_ip(request),
                  detail=f"{year}/{month:02d} X={X}")
        return JSONResponse({"ok": False, "error": "找不到可行排班，請放寬偏好或檢查 X / 預先指定的日期"})

    cache_key = f"{user.username}:{year}{month:02d}"
    _solve_cache[cache_key] = {
        "year": year, "month": month, "X": X,
        "schedule": result["schedule"],
        "stats_rows": result["stats_rows"],
        "monthly_stats_map": result["monthly_stats_map"],
        "baseline": baseline,
    }
    audit.log("sched_solve", user=user.username, ip=_client_ip(request),
              detail=(f"{year}/{month:02d} qod_relaxed={result['qod_relaxed']} "
                      f"violations={len(result['qod_violations'])}"))
    return JSONResponse({
        "ok": True,
        "schedule": _serialize_schedule(result["schedule"]),
        "stats_rows": result["stats_rows"],
        "qod_violations": [
            {"date": d.strftime("%Y-%m-%d"), "name": n}
            for d, n in result["qod_violations"]
        ],
        "qod_relaxed": result["qod_relaxed"],
        "targets": {
            "cr_fri_target": result["targets"]["cr_fri_target"],
            "cr_sat_target": result["targets"]["cr_sat_target"],
            "cr_sun_target": result["targets"]["cr_sun_target"],
        },
    })


@app.post("/api/sched/write")
async def api_sched_write(request: Request):
    user = _get_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "未登入"}, status_code=401)
    body = await request.json()
    year  = int(body["year"])
    month = int(body["month"])
    cache_key = f"{user.username}:{year}{month:02d}"
    cached = _solve_cache.get(cache_key)
    if cached is None:
        return JSONResponse({"ok": False, "error": "請先按「solve」產生班表，再寫入"})

    schedule = cached["schedule"]
    stats_rows = cached["stats_rows"]
    monthly_stats_map = cached["monthly_stats_map"]
    baseline = cached["baseline"]
    sheet_name = f"{year}{month:02d}"

    try:
        sheet = gsheet_io.get_sheet()
        gsheet_io.write_calendar_sheet(
            sheet, sheet_name, year, month, schedule,
            gsheet_io.is_taiwan_holiday,
        )
        gsheet_io.write_monthly_stats(
            sheet, f"{sheet_name} 班數統計", stats_rows,
            headers=gsheet_io.DEFAULT_MONTHLY_HEADERS + ["QOD次數"],
        )
        gsheet_io.update_cumulative_stats(sheet, baseline, monthly_stats_map)
    except Exception as e:
        audit.log("sched_write_fail", user=user.username, ip=_client_ip(request),
                  detail=f"{sheet_name}: {e}")
        return JSONResponse({"ok": False, "error": f"寫入失敗：{e}"})

    audit.log("sched_write", user=user.username, ip=_client_ip(request),
              detail=sheet_name)
    return JSONResponse({"ok": True, "sheet_name": sheet_name})
