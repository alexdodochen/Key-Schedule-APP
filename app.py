#!/usr/bin/env python3
"""CV_APP — FastAPI backend.

Routes:
  /login, /register, /logout    — auth (templates from keyin)
  /                             — home menu (排班 / key班 / 查閱班表)
  /sched                        — 5-step scheduling UI (Phase 1)
  /sheet                        — read-only Google Sheet viewer
  /admin                        — admin console
  /api/sched/init               — load month context (H, W, baseline, holidays)
  /api/sched/compute            — compute VS / 建寬 / category targets given X
  /api/sched/solve              — run backtracking solver (preview only)
  /api/sched/write              — write the solved schedule to Google Sheet
  /api/sheet/list-tabs          — list all worksheet tabs in the master Sheet
  /api/sheet/read-tab           — return one worksheet's grid as 2-D values
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import audit
from auth import (
    TokenData, approve_user, get_admin_names, get_all_users, login_user,
    register_user, reject_delete_user, verify_token,
)
import cv_solver
import gsheet_io
import keyin_routes

BASE_DIR = Path(__file__).parent
HISTORY_DIR = BASE_DIR / "schedule_history"
HISTORY_DIR.mkdir(exist_ok=True)
DRAFTS_DIR = BASE_DIR / "sched_drafts"
DRAFTS_DIR.mkdir(exist_ok=True)
app = FastAPI(title="CV_APP — 心臟內科排班整合")
app.include_router(keyin_routes.router, prefix="/keyin")

static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

_solve_cache: dict[str, Any] = {}


# ── helpers ─────────────────────────────────────────────────────────
# Login is bypassed for the local desktop app — every request is treated as
# the synthetic "local" admin. Restore the cookie/JWT check below to re-enable.
_LOCAL_USER = TokenData("local", "admin")


def _get_user(request: Request):
    return _LOCAL_USER


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


def _build_projection(year: int, month: int, baseline: dict,
                      monthly_map: dict) -> tuple[list[dict], bool]:
    """Projected `值班總數統計` AFTER writing this month.

    projected = baseline − prev_contribution + new_contribution, with all
    three components returned per cell so the UI can render the math
    explicitly. `prev_contribution` is read off the existing
    `{YYYYMM} 班數統計` tab (0 if first write). Shared by /api/sched/solve
    and /api/sched/apply-edits so the projection always reflects whatever
    schedule (solved or hand-edited) currently sits in the cache.
    """
    prev_monthly: dict = {}
    try:
        sheet = gsheet_io.get_sheet()
        prev_monthly = gsheet_io.read_monthly_stats(
            sheet, f"{year}{month:02d} 班數統計")
    except Exception:
        prev_monthly = {}

    KEY_PAIRS = [("平日", "平日班"), ("週五", "週五班"),
                 ("週六", "週六班"), ("週日", "週日班"), ("假日", "假日班")]
    projected_cum = []
    all_names = set(baseline) | set(monthly_map) | set(prev_monthly)
    for name in sorted(all_names):
        b = baseline.get(name, {})
        new = monthly_map.get(name, {})
        prev = prev_monthly.get(name, {})
        row = {"姓名": name}
        prev_contrib: dict[str, int] = {}
        new_contrib: dict[str, int] = {}
        for cum_key, mon_key in KEY_PAIRS:
            base_val = b.get(cum_key, 0)
            prev_val = prev.get(mon_key, 0)
            new_val = new.get(mon_key, 0)
            row[cum_key] = base_val - prev_val + new_val
            prev_contrib[cum_key] = prev_val
            new_contrib[cum_key] = new_val
        row["總班數"] = row["平日"] + row["週五"] + row["假日"]
        row["baseline"] = {k: b.get(k, 0) for k, _ in KEY_PAIRS}
        row["prev_contribution"] = prev_contrib
        row["new_contribution"] = new_contrib
        projected_cum.append(row)
    return projected_cum, bool(prev_monthly)


# ── auth pages ──────────────────────────────────────────────────────
# Login is bypassed — these endpoints redirect straight to the home menu so
# the desktop app opens directly into the app.
@app.get("/login")
async def login_page(request: Request):
    return RedirectResponse(url="/")


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
    # Login is bypassed — nothing to log out from. Bounce back to the home menu.
    return RedirectResponse(url="/")


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


# ── git update (multi-machine sync) ─────────────────────────────────
# This is a desktop app that ships from a single GitHub repo and is run on
# multiple machines (work laptop + clinic PC). When the user pushes a new
# commit from one machine, the others need an easy way to pull. These two
# endpoints drive a button in home.html that checks remote state and runs
# `git pull --rebase origin <branch>`. Python code changes still need a
# manual app restart to take effect; templates auto-reload via Jinja.
def _run_git(*args: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run a git command in the project dir. Returns (returncode, stdout, stderr)."""
    proc = subprocess.run(
        ["git", *args], cwd=str(BASE_DIR),
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


@app.get("/api/update/check")
async def api_update_check(request: Request):
    user = _get_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "未登入"}, status_code=401)
    try:
        # Ensure we're actually in a git checkout
        rc, _, err = _run_git("rev-parse", "--is-inside-work-tree")
        if rc != 0:
            return JSONResponse({"ok": False, "error": "本機不是 git 工作目錄，無法檢查更新"})
        rc, branch, _ = _run_git("rev-parse", "--abbrev-ref", "HEAD")
        if rc != 0 or not branch:
            return JSONResponse({"ok": False, "error": "無法取得目前分支"})
        # Fetch the latest from origin (network call — can be slow / fail)
        rc, _, ferr = _run_git("fetch", "origin", branch, timeout=20)
        if rc != 0:
            return JSONResponse({"ok": False, "error": f"無法連到 GitHub：{ferr[:200]}"})
        rc, cur_sha, _ = _run_git("rev-parse", "HEAD")
        rc2, rem_sha, _ = _run_git("rev-parse", f"origin/{branch}")
        rc3, behind_log, _ = _run_git(
            "log", f"HEAD..origin/{branch}", "--oneline", "--no-decorate",
        )
        rc4, ahead_log, _ = _run_git(
            "log", f"origin/{branch}..HEAD", "--oneline", "--no-decorate",
        )
        rc5, status_short, _ = _run_git("status", "--porcelain")
        behind_commits = [ln for ln in behind_log.splitlines() if ln]
        ahead_commits = [ln for ln in ahead_log.splitlines() if ln]
        return JSONResponse({
            "ok": True,
            "branch": branch,
            "current": cur_sha[:7],
            "remote": rem_sha[:7],
            "behind": len(behind_commits),
            "ahead": len(ahead_commits),
            "behind_commits": behind_commits,
            "ahead_commits": ahead_commits,
            "dirty": bool(status_short),
            "dirty_files": status_short.splitlines() if status_short else [],
        })
    except subprocess.TimeoutExpired:
        return JSONResponse({"ok": False, "error": "git 操作逾時 — 檢查網路連線"})
    except FileNotFoundError:
        return JSONResponse({"ok": False, "error": "本機找不到 git 指令（請安裝 Git for Windows）"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"檢查失敗：{e}"})


@app.post("/api/update/pull")
async def api_update_pull(request: Request):
    user = _get_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "未登入"}, status_code=401)
    try:
        rc, branch, _ = _run_git("rev-parse", "--abbrev-ref", "HEAD")
        if rc != 0 or not branch:
            return JSONResponse({"ok": False, "error": "無法取得目前分支"})
        # Refuse if local has uncommitted changes — pulling on a dirty tree
        # is the #1 way new users lose work. Surface the file list instead.
        rc, status_short, _ = _run_git("status", "--porcelain")
        if status_short:
            return JSONResponse({
                "ok": False,
                "error": "本機有未提交的變更，請先處理後再更新",
                "dirty_files": status_short.splitlines(),
            })
        rc, before_sha, _ = _run_git("rev-parse", "HEAD")
        rc, out, err = _run_git("pull", "--rebase", "origin", branch, timeout=60)
        if rc != 0:
            return JSONResponse({
                "ok": False,
                "error": "git pull 失敗",
                "detail": (out + "\n" + err).strip()[:500],
            })
        rc, after_sha, _ = _run_git("rev-parse", "HEAD")
        rc, summary, _ = _run_git(
            "log", f"{before_sha}..HEAD", "--oneline", "--no-decorate",
        )
        new_commits = [ln for ln in summary.splitlines() if ln]
        audit.log("update_pull", user=user.username, ip=_client_ip(request),
                  detail=f"{before_sha[:7]}->{after_sha[:7]} ({len(new_commits)})")
        return JSONResponse({
            "ok": True,
            "before": before_sha[:7],
            "after": after_sha[:7],
            "new_commits": new_commits,
            "restart_required": before_sha != after_sha,
        })
    except subprocess.TimeoutExpired:
        return JSONResponse({"ok": False, "error": "git pull 逾時 — 檢查網路連線"})
    except FileNotFoundError:
        return JSONResponse({"ok": False, "error": "本機找不到 git 指令"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"更新失敗：{e}"})


# ── Google Sheet read-only viewer ───────────────────────────────────
# Lets the user browse any worksheet (calendar tabs, 班數統計 tabs,
# 值班總數統計, 主治醫師抽籤表 …) inside the app without opening the
# Sheet in a browser. Read-only by design — uses the same service-account
# credential as the solver/write paths.
@app.get("/sheet", response_class=HTMLResponse)
async def sheet_viewer_page(request: Request):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse(request, "sheet_viewer.html", {
        "username": user.username,
        "is_admin": user.is_admin,
        "sheet_url": f"https://docs.google.com/spreadsheets/d/{gsheet_io.SHEET_ID}/edit",
    })


@app.get("/api/sheet/list-tabs")
async def api_sheet_list_tabs(request: Request):
    user = _get_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "未登入"}, status_code=401)
    try:
        sheet = gsheet_io.get_sheet()
        tabs = gsheet_io.list_worksheets(sheet)
        return JSONResponse({
            "ok": True,
            "tabs": tabs,
            "sheet_url": f"https://docs.google.com/spreadsheets/d/{gsheet_io.SHEET_ID}/edit",
        })
    except FileNotFoundError:
        return JSONResponse({
            "ok": False,
            "error": "找不到 .gsa.json — 請從父專案複製 service-account 憑證",
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"無法連 Google Sheet：{e}"})


@app.get("/api/sheet/read-tab")
async def api_sheet_read_tab(request: Request, name: str):
    user = _get_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "未登入"}, status_code=401)
    if not name or not name.strip():
        return JSONResponse({"ok": False, "error": "缺少工作表名稱"}, status_code=400)
    try:
        sheet = gsheet_io.get_sheet()
        data = gsheet_io.read_worksheet_grid(sheet, name)
        return JSONResponse({"ok": True, **data})
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"讀取「{name}」失敗：{e}"})


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
            "holiday_name": gsheet_io.taiwan_holiday_name(d),
            "stat_type": get_stat_type(d),
        })
    prev_year, prev_month = gsheet_io.previous_year_month(year, month)
    prev_tail: dict = {}
    try:
        sheet = gsheet_io.get_sheet()
        baseline = gsheet_io.load_cumulative_stats(sheet)
        prev_tail = gsheet_io.read_calendar_tail(sheet, prev_year, prev_month, n=2)
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
        "prev_tail": {
            d.strftime("%Y-%m-%d"): n for d, n in prev_tail.items()
        },
        "prev_year": prev_year,
        "prev_month": prev_month,
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
    vs_holiday_exempt = body.get("vs_holiday_exempt") or []
    targets = cv_solver.compute_initial_targets(
        year, month, X, baseline,
        vs_holiday_exempt=vs_holiday_exempt,
    )
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
    prev_tail_in = body.get("prev_tail") or {}
    vs_holiday_exempt = body.get("vs_holiday_exempt") or []

    fixed = {_parse_iso_date(k): v for k, v in fixed_in.items() if v}
    avoid = {n: [_parse_iso_date(d) for d in dates]
             for n, dates in avoid_in.items() if dates}
    prev_tail = {_parse_iso_date(k): v for k, v in prev_tail_in.items() if v}

    result = cv_solver.solve_month(
        year, month, X, fixed, avoid, baseline,
        jk_target=int(jk_target) if jk_target is not None else None,
        prev_tail=prev_tail,
        vs_holiday_exempt=vs_holiday_exempt,
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
        "targets": result["targets"],
    }

    # Projection of the cumulative tab AFTER writing this month — so the user
    # can sanity-check 累計值班總數 before clicking 寫入. If this same month was
    # previously written, the prev contribution is read off the sheet and
    # subtracted (mirrors update_cumulative_stats with previous_monthly=).
    projected_cum, had_prev_monthly = _build_projection(
        year, month, baseline, result["monthly_stats_map"])

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
            "cr_holiday_target": result["targets"].get("cr_holiday_target", {}),
        },
        "projected_cumulative": projected_cum,
        "had_prev_monthly": had_prev_monthly,
    })


@app.post("/api/sched/apply-edits")
async def api_sched_apply_edits(request: Request):
    """Apply the user's manual tweaks to the solved schedule.

    Step 5 lets the user hand-edit the calendar (swap who's on which day)
    after the solver runs. This endpoint takes the FINAL edited
    `{iso_date: name}` map, recomputes stats / QOD / projection from it
    (cv_solver.recompute_from_schedule — same classification the solver
    uses), and overwrites the cached schedule so /api/sched/write and
    /api/sched/handoff-to-keyin emit the edited result, not the original
    solve. Requires a prior /api/sched/solve (the cache holds baseline +
    targets, which a bare edit doesn't carry).
    """
    user = _get_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "未登入"}, status_code=401)
    body = await request.json()
    year  = int(body["year"])
    month = int(body["month"])
    sched_in = body.get("schedule") or {}
    cache_key = f"{user.username}:{year}{month:02d}"
    cached = _solve_cache.get(cache_key)
    if cached is None:
        return JSONResponse({"ok": False,
                             "error": "請先按 solve 產生班表，再做手動微調"})

    # Empty cells (user cleared a day) are simply dropped — that day stays
    # unassigned and is excluded from every stat, mirroring a blank cell.
    schedule = {_parse_iso_date(k): v.strip()
                for k, v in sched_in.items() if v and v.strip()}

    result = cv_solver.recompute_from_schedule(year, month, schedule)

    cached.update({
        "schedule": result["schedule"],
        "stats_rows": result["stats_rows"],
        "monthly_stats_map": result["monthly_stats_map"],
    })

    baseline = cached.get("baseline") or {}
    projected_cum, had_prev_monthly = _build_projection(
        year, month, baseline, result["monthly_stats_map"])
    targets = cached.get("targets") or {}

    audit.log("sched_apply_edits", user=user.username, ip=_client_ip(request),
              detail=(f"{year}/{month:02d} days={len(schedule)} "
                      f"qod={len(result['qod_violations'])}"))
    return JSONResponse({
        "ok": True,
        "edited": True,
        "schedule": _serialize_schedule(result["schedule"]),
        "stats_rows": result["stats_rows"],
        "qod_violations": [
            {"date": d.strftime("%Y-%m-%d"), "name": n}
            for d, n in result["qod_violations"]
        ],
        "qod_relaxed": result["qod_relaxed"],
        "targets": {
            "cr_fri_target": targets.get("cr_fri_target", {}),
            "cr_sat_target": targets.get("cr_sat_target", {}),
            "cr_sun_target": targets.get("cr_sun_target", {}),
            "cr_holiday_target": targets.get("cr_holiday_target", {}),
        },
        "projected_cumulative": projected_cum,
        "had_prev_monthly": had_prev_monthly,
    })


@app.post("/api/sched/handoff-to-keyin")
async def api_sched_handoff(request: Request):
    """Bridge: take the cached cv_solver schedule and stage it as a keyin prefill.

    Splits the {date: name} schedule into vs_schedule (VS_LIST) and cr_schedule
    (CRS + INTER_MID), keyed by day-of-month. Records tw_holidays for the month
    so the keyin UI can pre-check holiday rows. The prefill is one-shot — it is
    consumed (popped) by the next /keyin/api/prefill call.
    """
    user = _get_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "未登入"}, status_code=401)
    body = await request.json()
    year  = int(body["year"])
    month = int(body["month"])
    cache_key = f"{user.username}:{year}{month:02d}"
    cached = _solve_cache.get(cache_key)
    if cached is None:
        return JSONResponse({"ok": False, "error": "請先按「solve」產生班表，再進入 key 班"})

    schedule: dict[date, str] = cached["schedule"]
    vs_schedule: dict[int, str] = {}
    cr_schedule: dict[int, str] = {}
    for d, name in schedule.items():
        if name in cv_solver.VS_LIST:
            vs_schedule[d.day] = name
        elif name in cv_solver.CRS or name in cv_solver.INTER_MID:
            cr_schedule[d.day] = name

    tw_holidays = [
        d.strftime("%Y-%m-%d")
        for d in cv_solver.month_days(year, month)
        if gsheet_io.is_taiwan_holiday(d)
    ]

    keyin_routes.prefill_cache[user.username] = {
        "year": year,
        "month": month,
        "vs_schedule": vs_schedule,
        "cr_schedule": cr_schedule,
        "tw_holidays": tw_holidays,
    }
    audit.log("sched_handoff_keyin", user=user.username, ip=_client_ip(request),
              detail=f"{year}/{month:02d} VS={len(vs_schedule)} CR={len(cr_schedule)}")
    return JSONResponse({"ok": True, "redirect": "/keyin"})


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
    history_path = HISTORY_DIR / f"{sheet_name}.json"

    # Recover the previous monthly contribution so re-running the same month
    # doesn't double-count in the cumulative tab. The sheet's own
    # `{YYYYMM} 班數統計` tab is the source of truth here — it's written in
    # the same /api/sched/write call as the cumulative tab, so the two are
    # always consistent regardless of what's on disk locally.
    try:
        sheet = gsheet_io.get_sheet()
        previous_monthly = gsheet_io.read_monthly_stats(
            sheet, f"{sheet_name} 班數統計",
        )

        gsheet_io.write_calendar_sheet(
            sheet, sheet_name, year, month, schedule,
            gsheet_io.is_taiwan_holiday,
        )
        gsheet_io.write_monthly_stats(
            sheet, f"{sheet_name} 班數統計", stats_rows,
            headers=gsheet_io.DEFAULT_MONTHLY_HEADERS + ["QOD次數"],
        )
        gsheet_io.update_cumulative_stats(
            sheet, baseline, monthly_stats_map,
            previous_monthly=previous_monthly,
        )
    except Exception as e:
        audit.log("sched_write_fail", user=user.username, ip=_client_ip(request),
                  detail=f"{sheet_name}: {e}")
        return JSONResponse({"ok": False, "error": f"寫入失敗：{e}"})

    # Snapshot the just-written state for human-readable history. This file
    # is for traceability only — the subtract-on-rewrite logic above reads
    # from the sheet's own monthly tab, so this snapshot is not required for
    # cumulative correctness. Commit it to git when you want to share the
    # detailed schedule with collaborators.
    history_path.write_text(
        json.dumps({
            "year": year,
            "month": month,
            "X": cached.get("X"),
            "written_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "written_by": user.username,
            "schedule": {d.strftime("%Y-%m-%d"): n for d, n in schedule.items()},
            "stats_rows": stats_rows,
            "monthly_stats_map": monthly_stats_map,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    audit.log("sched_write", user=user.username, ip=_client_ip(request),
              detail=sheet_name)
    return JSONResponse({
        "ok": True,
        "sheet_name": sheet_name,
        "history_file": str(history_path.relative_to(BASE_DIR)).replace("\\", "/"),
        "rewrite": bool(previous_monthly),
    })


# ── Draft save / load (mid-workflow resume points, local-only) ──────
@app.post("/api/sched/save-draft")
async def api_sched_save_draft(request: Request):
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
        "step": int(body.get("step", 1)),
        "state": state_blob,
    }
    path = DRAFTS_DIR / f"{safe}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    audit.log("sched_save_draft", user=user.username, ip=_client_ip(request), detail=safe)
    return JSONResponse({"ok": True, "name": safe, "saved_at": payload["saved_at"]})


@app.get("/api/sched/list-drafts")
async def api_sched_list_drafts(request: Request):
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
            "step": d.get("step"),
            "year": s.get("year"),
            "month": s.get("month"),
        })
    items.sort(key=lambda x: x["saved_at"], reverse=True)
    return JSONResponse({"ok": True, "drafts": items})


@app.post("/api/sched/load-draft")
async def api_sched_load_draft(request: Request):
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
    audit.log("sched_load_draft", user=user.username, ip=_client_ip(request), detail=name)
    return JSONResponse({"ok": True, "draft": json.loads(path.read_text(encoding="utf-8"))})


@app.post("/api/sched/delete-draft")
async def api_sched_delete_draft(request: Request):
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
    audit.log("sched_delete_draft", user=user.username, ip=_client_ip(request), detail=name)
    return JSONResponse({"ok": True})
