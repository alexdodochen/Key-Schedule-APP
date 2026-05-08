"""Pure scheduling logic for the cardiology monthly call schedule.

No I/O. Caller supplies year, month, X (展瀚 weekday count), fixed dict,
avoid dict, and baseline cumulative stats; receives schedule + per-doctor
stats. UI / Sheet I/O lives in app.py + gsheet_io.py.

Rules (mirror CLAUDE.md in the 排班 project):
- CR pool: 麒翔, 見賢, 常胤
- VS pool: 廖瑀, 昭佑, 朝允, 則瑋
- Mid pool: 展瀚 (weekday only), 建寬 (≤ 3 weekday)
- Caps: CR total ≤ 7/month; per-category 週五/週六/週日 hard cap from
  balanced targets; VS ≤ 2/month with ≤ 1 holiday; 建寬 ≤ 3 weekday.
- No back-to-back days for anyone except 展瀚.
- No QOD (D and D±2) for anyone except 展瀚 — hard. If solver fails with
  strict QOD, fall back to relaxed and surface violations.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Optional

from gsheet_io import is_taiwan_holiday, make_stat_type_fn

CRS: list[str] = ["麒翔", "見賢", "常胤"]
VS_LIST: list[str] = ["廖瑀", "昭佑", "朝允", "則瑋"]
INTER_MID: list[str] = ["展瀚", "建寬"]
ALL_DOCTORS: list[str] = CRS + VS_LIST + INTER_MID

CR_TOTAL_CAP = 7
JK_WEEKDAY_CAP = 3
VS_TOTAL_CAP = 2
VS_HOLIDAY_CAP = 1


# ── Public helpers ──────────────────────────────────────────────────
def month_days(year: int, month: int) -> list[date]:
    n = calendar.monthrange(year, month)[1]
    return [date(year, month, d) for d in range(1, n + 1)]


def month_h_w(year: int, month: int) -> tuple[int, int]:
    days = month_days(year, month)
    H = sum(1 for d in days if is_taiwan_holiday(d))
    return H, len(days) - H


def previous_month_last_doctor(baseline_by_date: dict | None) -> Optional[str]:
    """Stub for future cross-month boundary lookup. Caller resolves via
    Google Sheet (read previous month's calendar tab, last filled cell).
    Returns None when unknown."""
    return None


# ── Step 2/3: compute counts before preferences are collected ───────
def compute_initial_targets(year: int, month: int, X: int, baseline: dict) -> dict:
    """Headline counts the UI shows after the user supplies X.

    No `fixed` yet; assumes default allocation (4 VS each take 1 holiday +
    1 weekday up to vs_weekday_total). Real per-category CR targets are
    recomputed by the solver once preferences arrive.

    Returns:
      {
        H, W,
        vs_holiday_total, vs_weekday_total,
        vs_per_doctor: {name: {"holiday": n, "weekday": n}},
        jk_count: int,
        warnings: [str, ...],
        cr_fri_total, cr_sat_total, cr_sun_total: total category days
      }
    """
    days = month_days(year, month)
    H, W = month_h_w(year, month)
    get_stat_type = make_stat_type_fn(is_taiwan_holiday)

    jk = max(0, min(JK_WEEKDAY_CAP, W - 15 - X))
    vs_h_total = max(0, H - 6)
    vs_w_total = max(0, W - 15 - X - jk)

    warnings: list[str] = []
    if vs_w_total > VS_TOTAL_CAP * len(VS_LIST):
        warnings.append("VS 平日缺額已超過 4 人 × 1 班的容量；展瀚 X 可能太低")
    if vs_h_total > VS_HOLIDAY_CAP * len(VS_LIST):
        warnings.append(
            f"假日缺 {vs_h_total} 班，超過 4 位 VS × 1 = 4 班；CR 假日 ≤ 2 軟上限可能放寬到 3"
        )

    vs_per_doctor: dict[str, dict[str, int]] = {n: {"holiday": 0, "weekday": 0} for n in VS_LIST}

    holiday_order = sorted(
        VS_LIST,
        key=lambda n: (
            baseline.get(n, {}).get("假日", 0),
            -(baseline.get(n, {}).get("假日", 0)
              + baseline.get(n, {}).get("平日", 0)
              + baseline.get(n, {}).get("週五", 0)),
        ),
    )
    h_remaining = vs_h_total
    idx = 0
    while h_remaining > 0:
        vs = holiday_order[idx % len(VS_LIST)]
        if vs_per_doctor[vs]["holiday"] < VS_HOLIDAY_CAP:
            vs_per_doctor[vs]["holiday"] += 1
            h_remaining -= 1
        idx += 1
        if idx > len(VS_LIST) * 3:
            break

    weekday_order = sorted(
        VS_LIST,
        key=lambda n: baseline.get(n, {}).get("平日", 0) + baseline.get(n, {}).get("週五", 0),
    )
    w_remaining = vs_w_total
    idx = 0
    while w_remaining > 0:
        vs = weekday_order[idx % len(VS_LIST)]
        total = vs_per_doctor[vs]["holiday"] + vs_per_doctor[vs]["weekday"]
        if total < VS_TOTAL_CAP:
            vs_per_doctor[vs]["weekday"] += 1
            w_remaining -= 1
        idx += 1
        if idx > len(VS_LIST) * 3:
            break

    cr_fri_total = sum(1 for d in days if get_stat_type(d) == "週五班")
    cr_sat_total = sum(1 for d in days if get_stat_type(d) == "週六班")
    cr_sun_total = sum(1 for d in days if get_stat_type(d) == "週日班")

    return {
        "H": H,
        "W": W,
        "vs_holiday_total": vs_h_total,
        "vs_weekday_total": vs_w_total,
        "vs_per_doctor": vs_per_doctor,
        "jk_count": jk,
        "cr_fri_total": cr_fri_total,
        "cr_sat_total": cr_sat_total,
        "cr_sun_total": cr_sun_total,
        "warnings": warnings,
    }


# ── Step 5: solve ───────────────────────────────────────────────────
def solve_month(
    year: int,
    month: int,
    X: int,
    fixed: dict[date, str],
    avoid: dict[str, list[date]],
    baseline: dict,
    jk_target: Optional[int] = None,
) -> Optional[dict]:
    """Run the backtracking solver. Returns None when no feasible schedule
    exists even after relaxing QOD; otherwise returns:

      {
        schedule: {date: name},
        stats_rows: [{姓名, 平日班, 假日班, 週五班, 週六班, 週日班, QOD次數}, ...],
        monthly_stats_map: {name: row},
        qod_violations: [(date, name), ...],
        qod_relaxed: bool,
      }
    """
    days = month_days(year, month)
    get_stat_type = make_stat_type_fn(is_taiwan_holiday)

    if jk_target is None:
        H, W = month_h_w(year, month)
        jk_target = max(0, min(JK_WEEKDAY_CAP, W - 15 - X))

    # Per-category CR targets, accounting for fixed assignments
    cr_fri_target = _category_target(days, fixed, baseline, get_stat_type, "週五班", "週五")
    cr_sat_target = _category_target(days, fixed, baseline, get_stat_type, "週六班", "週六")
    cr_sun_target = _category_target(days, fixed, baseline, get_stat_type, "週日班", "週日")

    targets = {
        "cr_fri_target": cr_fri_target,
        "cr_sat_target": cr_sat_target,
        "cr_sun_target": cr_sun_target,
    }

    for strict in (True, False):
        result = _backtrack_run(
            days, fixed, avoid, baseline, jk_target,
            get_stat_type, targets, strict_qod=strict,
        )
        if result is not None:
            schedule = result
            stats_rows, monthly_map = _compute_stats(schedule, get_stat_type)
            qod_violations = _scan_qod(schedule)
            return {
                "schedule": schedule,
                "stats_rows": stats_rows,
                "monthly_stats_map": monthly_map,
                "qod_violations": qod_violations,
                "qod_relaxed": not strict,
                "targets": targets,
            }
    return None


# ── Internal: per-category CR target ────────────────────────────────
def _category_target(
    days: list[date],
    fixed: dict[date, str],
    baseline: dict,
    get_stat_type,
    stat_label: str,
    cum_key: str,
) -> dict[str, int]:
    cr_eligible = [
        d for d in days
        if get_stat_type(d) == stat_label
        and (d not in fixed or fixed[d] in CRS)
    ]
    fixed_in_cat = {n: 0 for n in CRS}
    for d in cr_eligible:
        if d in fixed:
            fixed_in_cat[fixed[d]] += 1

    n_total = len(cr_eligible)
    base = n_total // len(CRS)
    surplus = n_total % len(CRS)
    order = sorted(CRS, key=lambda n: baseline.get(n, {}).get(cum_key, 0) + fixed_in_cat[n])
    target = {n: base for n in CRS}
    for i in range(surplus):
        target[order[i]] += 1
    for n in CRS:
        if target[n] < fixed_in_cat[n]:
            target[n] = fixed_in_cat[n]
    return target


# ── Internal: backtracking ──────────────────────────────────────────
def _backtrack_run(
    days, fixed, avoid, baseline, jk_target,
    get_stat_type, targets, strict_qod: bool,
) -> Optional[dict[date, str]]:
    num_days = len(days)
    schedule: dict[date, str] = dict(fixed)
    cr_w = {n: 0 for n in CRS}
    cr_h = {n: 0 for n in CRS}
    cr_fri = {n: 0 for n in CRS}
    cr_sat = {n: 0 for n in CRS}
    cr_sun = {n: 0 for n in CRS}
    jk_count = 0

    for d, name in fixed.items():
        if name in CRS:
            if is_taiwan_holiday(d):
                cr_h[name] += 1
            else:
                cr_w[name] += 1
            stat = get_stat_type(d)
            if stat == "週五班":
                cr_fri[name] += 1
            elif stat == "週六班":
                cr_sat[name] += 1
            elif stat == "週日班":
                cr_sun[name] += 1
        if name == "建寬":
            jk_count += 1

    for n in CRS:
        if cr_w[n] + cr_h[n] > CR_TOTAL_CAP:
            return None
    if jk_count > jk_target:
        return None

    open_days = [d for d in days if d not in fixed]
    cr_fri_target = targets["cr_fri_target"]
    cr_sat_target = targets["cr_sat_target"]
    cr_sun_target = targets["cr_sun_target"]

    def qod_score(name: str, d_idx: int) -> int:
        if name == "展瀚":
            return 0
        s = 0
        for off in (-2, 2):
            j = d_idx + off
            if 0 <= j < num_days and schedule.get(days[j]) == name:
                s += 1
        return s

    def is_qod_conflict(name: str, d_idx: int) -> bool:
        if name == "展瀚":
            return False
        for off in (-2, 2):
            j = d_idx + off
            if 0 <= j < num_days and schedule.get(days[j]) == name:
                return True
        return False

    def backtrack(i: int) -> bool:
        nonlocal jk_count
        if i == len(open_days):
            return True
        d = open_days[i]
        d_idx = (d - days[0]).days
        is_h = is_taiwan_holiday(d)
        stat = get_stat_type(d)

        if is_h:
            candidates = list(CRS)
        else:
            candidates = list(CRS) + (["建寬"] if jk_count < jk_target else [])

        def sort_key(name: str) -> tuple:
            qp = qod_score(name, d_idx)
            if name == "建寬":
                return (qp, 99, 99)
            cum_key = {"週五班": "週五", "週六班": "週六", "週日班": "週日"}.get(stat, "平日")
            count_dict = {"週五班": cr_fri, "週六班": cr_sat, "週日班": cr_sun}.get(stat, cr_w)
            return (
                qp,
                baseline.get(name, {}).get(cum_key, 0) + count_dict[name],
                cr_w[name] + cr_h[name],
            )

        candidates.sort(key=sort_key)

        for name in candidates:
            if name != "展瀚":
                if d_idx > 0 and schedule.get(days[d_idx - 1]) == name:
                    continue
                if d_idx < num_days - 1 and schedule.get(days[d_idx + 1]) == name:
                    continue
            if strict_qod and is_qod_conflict(name, d_idx):
                continue
            if name in avoid and d in avoid[name]:
                continue

            if name in CRS:
                if cr_w[name] + cr_h[name] >= CR_TOTAL_CAP:
                    continue
                if stat == "週五班" and cr_fri[name] >= cr_fri_target.get(name, 99):
                    continue
                if stat == "週六班" and cr_sat[name] >= cr_sat_target.get(name, 99):
                    continue
                if stat == "週日班" and cr_sun[name] >= cr_sun_target.get(name, 99):
                    continue

            if name == "建寬" and jk_count >= jk_target:
                continue

            schedule[d] = name
            if name in CRS:
                if is_h:
                    cr_h[name] += 1
                else:
                    cr_w[name] += 1
                if stat == "週五班":
                    cr_fri[name] += 1
                elif stat == "週六班":
                    cr_sat[name] += 1
                elif stat == "週日班":
                    cr_sun[name] += 1
            if name == "建寬":
                jk_count += 1

            if backtrack(i + 1):
                return True

            if name in CRS:
                if is_h:
                    cr_h[name] -= 1
                else:
                    cr_w[name] -= 1
                if stat == "週五班":
                    cr_fri[name] -= 1
                elif stat == "週六班":
                    cr_sat[name] -= 1
                elif stat == "週日班":
                    cr_sun[name] -= 1
            if name == "建寬":
                jk_count -= 1
            del schedule[d]

        return False

    if backtrack(0):
        return schedule
    return None


# ── Internal: stats ─────────────────────────────────────────────────
def _compute_stats(schedule: dict[date, str], get_stat_type) -> tuple[list[dict], dict]:
    stats_rows: list[dict] = []
    by_name: dict[str, dict] = {}
    for name in ALL_DOCTORS:
        personal = [d for d, n in schedule.items() if n == name]
        personal_set = set(personal)
        row = {
            "姓名": name,
            "平日班": sum(1 for d in personal if get_stat_type(d) == "平日"),
            "假日班": sum(1 for d in personal if is_taiwan_holiday(d)),
            "週五班": sum(1 for d in personal if get_stat_type(d) == "週五班"),
            "週六班": sum(1 for d in personal if get_stat_type(d) == "週六班"),
            "週日班": sum(1 for d in personal if get_stat_type(d) == "週日班"),
            "QOD次數": _qod_count(personal_set),
        }
        stats_rows.append(row)
        by_name[name] = row
    return stats_rows, by_name


def _qod_count(dates_set: set[date]) -> int:
    return sum(1 for d in dates_set if (d + timedelta(days=2)) in dates_set)


def _scan_qod(schedule: dict[date, str]) -> list[tuple[date, str]]:
    by_doctor: dict[str, set[date]] = {}
    for d, n in schedule.items():
        by_doctor.setdefault(n, set()).add(d)
    violations: list[tuple[date, str]] = []
    for n, ds in by_doctor.items():
        if n == "展瀚":
            continue
        for d in sorted(ds):
            if (d + timedelta(days=2)) in ds:
                violations.append((d, n))
    return violations
