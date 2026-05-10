"""Google Sheets I/O helpers for the scheduling scripts.

Swap-in replacement for the previous openpyxl file I/O. The master workbook
lives in Google Sheets at SHEET_ID; service-account credentials are loaded
from .gsa.json (gitignored) or the GOOGLE_SERVICE_ACCOUNT_JSON env var.
"""
import os
import json
import calendar
from datetime import date, timedelta

import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = '10ilVOmJrr8jjfnMMbtj60tAIIAe1YX3ZRU1RLgn6Elk'
CREDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.gsa.json')

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
]

YELLOW = {'red': 1.0, 'green': 235 / 255, 'blue': 156 / 255}
CUMULATIVE_TAB = '值班總數統計'

# ── 中華民國官方非週末放假日 ──────────────────────────────────────────────────
# 來源：行政院人事行政總處辦公日曆表（含補假）
# 週六、週日本已是假日，此表只記錄平日（週一~週五）放假日。
TAIWAN_HOLIDAYS: set[date] = {
    # 2025
    date(2025, 9, 29),   # 教師節補假 (9/28 日→9/29 一)
    date(2025, 10, 6),   # 中秋節 (10/5 日→10/6 一)
    date(2025, 10, 10),  # 國慶日 (五)
    date(2025, 10, 24),  # 臺灣光復節補假 (10/25 六→10/24 五)
    date(2025, 12, 25),  # 行憲紀念日 (四)
    # 2026
    date(2026, 1, 1),    # 元旦 (四)
    date(2026, 2, 16),   # 農曆連假 (一) 除夕/連假開始
    date(2026, 2, 17),   # 春節初一 (二)
    date(2026, 2, 18),   # 春節初二 (三)
    date(2026, 2, 19),   # 春節初三 (四)
    date(2026, 2, 20),   # 春節連假 (五)
    date(2026, 2, 27),   # 228和平紀念日補假 (2/28 六→2/27 五)
    date(2026, 4, 3),    # 兒童節+清明補假 (五)
    date(2026, 4, 6),    # 清明補假 (一)
    date(2026, 5, 1),    # 勞動節 (五)
    date(2026, 6, 19),   # 端午節 (五)
    # 2026下半年
    date(2026, 10, 9),   # 國慶補假 (10/10六→10/9五)
    date(2026, 10, 26),  # 臺灣光復節補假 (10/25日→10/26一)
    date(2026, 12, 25),  # 行憲紀念日 (五)
}

# 國定假日對應的中文名稱 — 給 UI 顯示用（週末沒有名稱，故僅含非週末假日）
TAIWAN_HOLIDAY_NAMES: dict[date, str] = {
    date(2025, 9, 29):  "教師節補假",
    date(2025, 10, 6):  "中秋節",
    date(2025, 10, 10): "國慶日",
    date(2025, 10, 24): "光復節補假",
    date(2025, 12, 25): "行憲紀念日",
    date(2026, 1, 1):   "元旦",
    date(2026, 2, 16):  "除夕",
    date(2026, 2, 17):  "春節初一",
    date(2026, 2, 18):  "春節初二",
    date(2026, 2, 19):  "春節初三",
    date(2026, 2, 20):  "春節連假",
    date(2026, 2, 27):  "228 補假",
    date(2026, 4, 3):   "兒童節",
    date(2026, 4, 6):   "清明補假",
    date(2026, 5, 1):   "勞動節",
    date(2026, 6, 19):  "端午節",
    date(2026, 10, 9):  "國慶補假",
    date(2026, 10, 26): "光復節補假",
    date(2026, 12, 25): "行憲紀念日",
}


def is_taiwan_holiday(d: date) -> bool:
    """Return True if d is a public holiday (weekend or official non-weekend holiday)."""
    return d.weekday() >= 5 or d in TAIWAN_HOLIDAYS


def taiwan_holiday_name(d: date) -> str:
    """Return the official Chinese name for non-weekend holidays; '' otherwise."""
    return TAIWAN_HOLIDAY_NAMES.get(d, "")


def make_stat_type_fn(is_holiday_fn):
    """Return a get_stat_type(d) function based on holiday position logic.

    Rules (no "假日其他" — all holidays are classified by position):
    - Holiday, next day also holiday  → 週六班 (middle of holiday block)
    - Holiday, next day not holiday   → 週日班 (last day of holiday block)
    - Non-holiday, next day holiday   → 週五班 (day before holiday block)
    - Non-holiday, regular Friday     → 週五班
    - Non-holiday, Mon-Thu            → 平日

    Cross-month note: is_holiday_fn must return True for next-month holidays
    when needed (e.g. April's fn should return True for 5/1 if 5/1 is 勞動節,
    so that 4/30 is correctly classified as 週五班). Pass a merged fn that
    covers the boundary days of adjacent months when required.
    """
    def get_stat_type(d):
        tomorrow = d + timedelta(days=1)
        if is_holiday_fn(d):
            return "週日班" if not is_holiday_fn(tomorrow) else "週六班"
        else:
            if is_holiday_fn(tomorrow):
                return "週五班"
            return "週五班" if d.weekday() == 4 else "平日"
    return get_stat_type


def get_sheet():
    creds_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
    if creds_json:
        creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
    return gspread.authorize(creds).open_by_key(SHEET_ID)


def _ensure_worksheet(sheet, title, rows, cols):
    try:
        ws = sheet.worksheet(title)
        ws.clear()
        ws.resize(rows=rows, cols=cols)
    except gspread.exceptions.WorksheetNotFound:
        ws = sheet.add_worksheet(title=title, rows=rows, cols=cols)
    return ws


def previous_year_month(year: int, month: int) -> tuple[int, int]:
    """Return (year, month) of the calendar month immediately before (year, month)."""
    return (year - 1, 12) if month == 1 else (year, month - 1)


def read_calendar_tail(sheet, year: int, month: int, n: int = 2) -> dict[date, str]:
    """Read the last `n` filled days from the {YYYYMM} calendar tab.

    Returns {date: doctor_name}. Empty dict if the tab doesn't exist (e.g.
    the previous month was never written through this app). Used by the
    solver to enforce cross-month rules:
      - 不連兩天：day 1 of this month must differ from last day of prev month
      - QOD：day 1/2 of this month vs prev's last 2 days (D ↔ D+2 pair)
    """
    sheet_name = f"{year}{month:02d}"
    try:
        ws = sheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        return {}
    all_values = ws.get_all_values()
    if not all_values or len(all_values) < 2:
        return {}

    month_cal = calendar.monthcalendar(year, month)
    result: dict[date, str] = {}
    for r_idx, week in enumerate(month_cal):
        # date_row at 2*r_idx + 1, name_row at 2*r_idx + 2 (header is row 0)
        name_row_idx = r_idx * 2 + 2
        if name_row_idx >= len(all_values):
            break
        name_row = all_values[name_row_idx]
        for c_idx, day in enumerate(week):
            if day == 0 or c_idx >= len(name_row):
                continue
            name = (name_row[c_idx] or '').strip()
            if name:
                result[date(year, month, day)] = name

    sorted_dates = sorted(result.keys(), reverse=True)
    return {d: result[d] for d in sorted_dates[:n]}


def write_calendar_sheet(sheet, sheet_name, year, month, result, is_holiday_fn):
    """Write the Mon-Sun calendar grid for a month, yellow-highlight holidays.

    result: {date: doctor_name}
    """
    month_cal = calendar.monthcalendar(year, month)
    rows = 1 + len(month_cal) * 2
    cols = 7

    header = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    grid = [header]
    holiday_cells = []
    for r_idx, week in enumerate(month_cal):
        date_row = [''] * 7
        name_row = [''] * 7
        for c_idx, day in enumerate(week):
            if day == 0:
                continue
            d_obj = date(year, month, day)
            date_row[c_idx] = day
            name_row[c_idx] = result.get(d_obj, "")
            if is_holiday_fn(d_obj):
                holiday_cells.append((r_idx * 2 + 1, c_idx))
                holiday_cells.append((r_idx * 2 + 2, c_idx))
        grid.append(date_row)
        grid.append(name_row)

    ws = _ensure_worksheet(sheet, sheet_name, rows=rows, cols=cols)
    ws.update(range_name='A1', values=grid, value_input_option='USER_ENTERED')

    sheet_gid = ws.id
    requests = []
    requests.append({
        'repeatCell': {
            'range': {'sheetId': sheet_gid, 'startRowIndex': 0, 'endRowIndex': 1, 'startColumnIndex': 0, 'endColumnIndex': 7},
            'cell': {'userEnteredFormat': {'textFormat': {'bold': True}, 'horizontalAlignment': 'CENTER'}},
            'fields': 'userEnteredFormat.textFormat.bold,userEnteredFormat.horizontalAlignment',
        }
    })
    for (r, c) in holiday_cells:
        requests.append({
            'repeatCell': {
                'range': {'sheetId': sheet_gid, 'startRowIndex': r, 'endRowIndex': r + 1, 'startColumnIndex': c, 'endColumnIndex': c + 1},
                'cell': {'userEnteredFormat': {'backgroundColor': YELLOW}},
                'fields': 'userEnteredFormat.backgroundColor',
            }
        })
    requests.append({
        'updateDimensionProperties': {
            'range': {'sheetId': sheet_gid, 'dimension': 'COLUMNS', 'startIndex': 0, 'endIndex': 7},
            'properties': {'pixelSize': 110},
            'fields': 'pixelSize',
        }
    })
    sheet.batch_update({'requests': requests})


DEFAULT_MONTHLY_HEADERS = ['姓名', '平日班', '週五班', '假日班', '週六班', '週日班']


def read_monthly_stats(sheet, sheet_name):
    """Read a `{YYYYMM} 班數統計` tab back into a {name: {col: int}} dict.

    Used by the writer to recover the previous monthly contribution when
    rewriting the same month — so the cumulative tab can be adjusted
    (subtract prev, add new) instead of double-counting. Returns {} if the
    tab does not exist (i.e. the month was never written).
    """
    try:
        ws = sheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        return {}
    all_values = ws.get_all_values()
    if not all_values:
        return {}
    header = all_values[0]

    def find_col(name):
        for i, h in enumerate(header):
            if h == name:
                return i
        return None

    cols = {key: find_col(key) for key in
            ('姓名', '平日班', '週五班', '週六班', '週日班', '假日班')}
    if cols['姓名'] is None:
        return {}

    def as_int(row, idx):
        if idx is None or idx >= len(row):
            return 0
        v = row[idx].strip() if isinstance(row[idx], str) else row[idx]
        try:
            return int(v) if v not in (None, '') else 0
        except (ValueError, TypeError):
            return 0

    result = {}
    for row in all_values[1:]:
        if not row or cols['姓名'] >= len(row) or not row[cols['姓名']]:
            continue
        name = row[cols['姓名']]
        result[name] = {
            '平日班': as_int(row, cols['平日班']),
            '週五班': as_int(row, cols['週五班']),
            '週六班': as_int(row, cols['週六班']),
            '週日班': as_int(row, cols['週日班']),
            '假日班': as_int(row, cols['假日班']),
        }
    return result


def write_monthly_stats(sheet, sheet_name, stats_rows, headers=None):
    """Write the per-month 班數統計 tab.

    stats_rows: list of dicts. Each row must contain every key in ``headers``.
    headers: optional column order; defaults to DEFAULT_MONTHLY_HEADERS. Pass
    DEFAULT_MONTHLY_HEADERS + ['QOD次數'] (or equivalent) to include quality
    metrics — SKILL.md requires QOD次數 for 2026-05 onward.
    """
    if headers is None:
        headers = DEFAULT_MONTHLY_HEADERS
    grid = [list(headers)]
    for r in stats_rows:
        grid.append([r[h] for h in headers])
    ws = _ensure_worksheet(sheet, sheet_name, rows=len(grid), cols=len(headers))
    ws.update(range_name='A1', values=grid, value_input_option='USER_ENTERED')


def _find_cum_cols(header):
    """Locate the standard columns in a 值班總數統計-style header.

    Accepts both 平日班 / 平日班(一至四) and 週/周 variants so the sheet can
    be relabeled without breaking readers. '總班數' is optional.
    """
    def find(*predicates, required=True):
        for pred in predicates:
            for i, h in enumerate(header):
                if pred(h):
                    return i
        if required:
            raise RuntimeError(f'Unexpected {CUMULATIVE_TAB} header: {header}')
        return None

    return {
        'name': find(lambda h: h == '姓名'),
        'weekday': find(lambda h: h.startswith('平日班')),
        'fri': find(lambda h: h in ('週五班', '周五班')),
        'sat': find(lambda h: h in ('週六班', '周六班')),
        'sun': find(lambda h: h in ('週日班', '周日班')),
        'holiday': find(lambda h: h.startswith('假日班')),
        'total': find(lambda h: h == '總班數', required=False),
    }


def load_cumulative_stats(sheet):
    """Read 值班總數統計 into a baseline dict usable by the schedulers.

    Returns: {name: {'平日': n, '週五': n, '週六': n, '週日': n, '假日': n}}

    Represents cumulative totals as currently written on the sheet — treat
    as the "pre-this-month" baseline when running a brand-new month.
    """
    ws = sheet.worksheet(CUMULATIVE_TAB)
    all_values = ws.get_all_values()
    if not all_values:
        return {}
    header = all_values[0]
    cols = _find_cum_cols(header)

    def as_int(row, idx):
        if idx >= len(row):
            return 0
        v = row[idx].strip() if isinstance(row[idx], str) else row[idx]
        return int(v) if v not in (None, '') else 0

    result = {}
    for row in all_values[1:]:
        if not row or not row[cols['name']]:
            continue
        result[row[cols['name']]] = {
            '平日': as_int(row, cols['weekday']),
            '週五': as_int(row, cols['fri']),
            '週六': as_int(row, cols['sat']),
            '週日': as_int(row, cols['sun']),
            '假日': as_int(row, cols['holiday']),
        }
    return result


def update_cumulative_stats(sheet, baseline, monthly_stats, previous_monthly=None):
    """Overwrite 值班總數統計 with baseline + monthly_stats.

    baseline: {name: {'平日': n, '週五': n, '週六': n, '週日': n, '假日': n}}
    monthly_stats: {name: {'平日班': n, '週五班': n, '週六班': n, '週日班': n, '假日班': n}}
    previous_monthly: optional same shape as monthly_stats — if supplied, the
        baseline is treated as already including this previous month's
        contribution and the prev values are subtracted before adding the new
        ones. Lets the caller safely re-write a month that was written before
        without double-counting in the cumulative tab.

    平日 / 平日班 uses the Mon-Thu (non-holiday) definition. 週五 is tracked
    separately. 總班數 = 平日班 + 週五班 + 假日班 is written to column G.
    """
    if previous_monthly:
        zero = {'平日班': 0, '週五班': 0, '週六班': 0, '週日班': 0, '假日班': 0}
        adjusted = {}
        for name, base in baseline.items():
            prev = previous_monthly.get(name, zero)
            adjusted[name] = {
                '平日': base['平日'] - prev['平日班'],
                '週五': base['週五'] - prev['週五班'],
                '週六': base['週六'] - prev['週六班'],
                '週日': base['週日'] - prev['週日班'],
                '假日': base['假日'] - prev['假日班'],
            }
        baseline = adjusted
    ws = sheet.worksheet(CUMULATIVE_TAB)
    all_values = ws.get_all_values()
    if not all_values:
        return
    cols = _find_cum_cols(all_values[0])

    # Ensure header has 總班數 in column G (index 6)
    header = list(all_values[0])
    if cols['total'] is None:
        header.append('總班數')
        ws.update(range_name='A1', values=[header], value_input_option='USER_ENTERED')
        cols['total'] = len(header) - 1

    header_len = len(header)

    updated_rows = []
    for row in all_values[1:]:
        name = row[cols['name']]
        base = baseline.get(name)
        month = monthly_stats.get(name, {'平日班': 0, '週五班': 0, '週六班': 0, '週日班': 0, '假日班': 0})
        if base is None:
            updated_rows.append(row)
            continue
        new_row = list(row) + [''] * (header_len - len(row))
        new_weekday = base['平日'] + month['平日班']
        new_fri = base['週五'] + month['週五班']
        new_sat = base['週六'] + month['週六班']
        new_sun = base['週日'] + month['週日班']
        new_hol = base['假日'] + month['假日班']
        new_row[cols['weekday']] = new_weekday
        new_row[cols['fri']] = new_fri
        new_row[cols['sat']] = new_sat
        new_row[cols['sun']] = new_sun
        new_row[cols['holiday']] = new_hol
        new_row[cols['total']] = new_weekday + new_fri + new_hol
        updated_rows.append(new_row)

    ws.update(range_name='A2', values=updated_rows, value_input_option='USER_ENTERED')
