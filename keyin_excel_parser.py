"""
Excel 排班表解析器
支援 .xlsx / .xls
自動偵測兩種常見格式：
  縱向：每列一天，欄位為日期/VS/CR
  橫向：每行一個班別，欄位為日期數字
"""
import re
from pathlib import Path

# 已知 VS 醫師名單（用於辨識欄位與驗證）
VS_DOCTORS = {
    "廖瑀","陳昭佑","張獻元","陳柏偉","李文煌","劉嚴文",
    "詹世鴻","陳則瑋","林佳淩","鄭朝允","柯呈諭","黃睦翔","黃鼎鈞",
}
# CR 專屬名單（非 VS 的 CR）
CR_ONLY = {"胡展瀚","徐麒翔","李見賢","陳常胤","葉建寬","陳昭佑"}
ALL_DOCTORS = VS_DOCTORS | CR_ONLY

# 易錯名字修正對照
NAME_CORRECTIONS = {
    "胡晟瀚": "胡展瀚",
    "黃勝翔": "黃睦翔",
    "陳則璋": "陳則瑋",
    "張世鴻": "詹世鴻",
}


def _read_sheet(filepath: str) -> list[list[str]]:
    """讀取第一個工作表，回傳 list[list[str]]"""
    p = Path(filepath)
    ext = p.suffix.lower()

    if ext == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(filepath, data_only=True)
        ws = wb.active
        return [
            [str(cell.value).strip() if cell.value is not None else ""
             for cell in row]
            for row in ws.iter_rows()
        ]
    elif ext == ".xls":
        import xlrd
        wb = xlrd.open_workbook(filepath)
        ws = wb.sheets()[0]
        return [
            [str(ws.cell_value(r, c)).strip() if ws.cell_value(r, c) != "" else ""
             for c in range(ws.ncols)]
            for r in range(ws.nrows)
        ]
    else:
        raise ValueError(f"不支援的檔案格式：{ext}（請使用 .xlsx 或 .xls）")


def _to_int_day(s: str):
    """嘗試轉成 1~31 的整數，失敗回傳 None"""
    try:
        n = int(float(s))
        return n if 1 <= n <= 31 else None
    except (ValueError, TypeError):
        return None


def _normalize_name(name: str) -> str:
    """去除空白、修正易錯名字"""
    name = re.sub(r'\s+', '', name)
    return NAME_CORRECTIONS.get(name, name)


def _col_keyword(s: str) -> str | None:
    """判斷欄位標頭是 vs / cr / day / None"""
    s = s.lower().replace(' ', '')
    if re.search(r'vs|值班vs|vs值班|主治', s):
        return 'vs'
    if re.search(r'cr|值班cr|cr值班|住院醫師|總醫師|值班r', s):
        return 'cr'
    if re.search(r'^日$|^日期$|^date$|^日次$', s):
        return 'day'
    return None


# ── 縱向格式解析 ──────────────────────────────────────────────────
def _parse_vertical(rows: list[list[str]]) -> dict | None:
    """
    每列一天。第一欄（或有「日」字的欄）為日期，
    找到 VS 欄和 CR 欄後逐列讀取。
    """
    day_col = vs_col = cr_col = -1
    header_row = -1

    # Step 1：找含有「日」關鍵字的 header row
    for ri, row in enumerate(rows[:15]):
        for ci, cell in enumerate(row):
            kw = _col_keyword(cell)
            if kw == 'day':
                day_col = ci
                header_row = ri
            elif kw == 'vs' and day_col >= 0:
                vs_col = ci
            elif kw == 'cr' and day_col >= 0:
                cr_col = ci
        if day_col >= 0 and (vs_col >= 0 or cr_col >= 0):
            break

    # Step 2：若 header 不明顯，改用數值推測 day_col
    if day_col < 0:
        for ci in range(min(4, len(rows[0]) if rows else 0)):
            hits = sum(1 for row in rows if _to_int_day(row[ci] if ci < len(row) else "") is not None)
            if hits >= 20:
                day_col = ci
                header_row = next(
                    (ri - 1 for ri, row in enumerate(rows)
                     if ci < len(row) and _to_int_day(row[ci]) == 1),
                    -1
                )
                break

    if day_col < 0:
        return None  # 不像縱向格式

    # Step 3：若 VS/CR 欄還沒找到，掃 header row 或用內容猜
    if vs_col < 0 or cr_col < 0:
        for ci, cell in enumerate(rows[header_row] if header_row >= 0 else []):
            kw = _col_keyword(cell)
            if kw == 'vs':
                vs_col = ci
            elif kw == 'cr':
                cr_col = ci

    # 若仍找不到，用醫師名出現頻率推測
    if vs_col < 0 or cr_col < 0:
        col_vs: dict[int, int] = {}
        col_cr: dict[int, int] = {}
        for row in rows:
            for ci, cell in enumerate(row):
                n = _normalize_name(cell)
                if n in VS_DOCTORS:
                    col_vs[ci] = col_vs.get(ci, 0) + 1
                if n in CR_ONLY:
                    col_cr[ci] = col_cr.get(ci, 0) + 1
        if vs_col < 0 and col_vs:
            vs_col = max(col_vs, key=col_vs.get)
        if cr_col < 0 and col_cr:
            cr_col = max(col_cr, key=col_cr.get)

    # Step 4：逐列讀取
    data_start = (header_row + 1) if header_row >= 0 else 0
    vs_sch: dict[int, str] = {}
    cr_sch: dict[int, str] = {}

    for row in rows[data_start:]:
        day = _to_int_day(row[day_col] if day_col < len(row) else "")
        if day is None:
            continue
        if vs_col >= 0 and vs_col < len(row) and row[vs_col]:
            vs_sch[day] = _normalize_name(row[vs_col])
        if cr_col >= 0 and cr_col < len(row) and row[cr_col]:
            cr_sch[day] = _normalize_name(row[cr_col])

    if not vs_sch and not cr_sch:
        return None

    return {"vs": vs_sch, "cr": cr_sch, "fmt": "縱向"}


# ── 橫向格式解析 ──────────────────────────────────────────────────
def _parse_horizontal(rows: list[list[str]]) -> dict | None:
    """
    一行是「日期數字列」，另幾行是 VS/CR 醫師。
    """
    day_row_idx = -1
    day_cols: dict[int, int] = {}  # col_idx → day_num

    for ri, row in enumerate(rows[:20]):
        nums = {ci: _to_int_day(cell) for ci, cell in enumerate(row)
                if _to_int_day(cell) is not None}
        if len(nums) >= 20:
            day_row_idx = ri
            day_cols = {ci: n for ci, n in nums.items()}
            break

    if day_row_idx < 0:
        return None

    vs_row = cr_row = -1
    search_range = range(max(0, day_row_idx - 5), min(len(rows), day_row_idx + 20))
    for ri in search_range:
        if ri == day_row_idx or not rows[ri]:
            continue
        kw = _col_keyword(rows[ri][0])
        if kw == 'vs':
            vs_row = ri
        elif kw == 'cr':
            cr_row = ri

    vs_sch: dict[int, str] = {}
    cr_sch: dict[int, str] = {}

    if vs_row >= 0:
        for ci, day in day_cols.items():
            v = rows[vs_row][ci] if ci < len(rows[vs_row]) else ""
            if v:
                vs_sch[day] = _normalize_name(v)
    if cr_row >= 0:
        for ci, day in day_cols.items():
            v = rows[cr_row][ci] if ci < len(rows[cr_row]) else ""
            if v:
                cr_sch[day] = _normalize_name(v)

    if not vs_sch and not cr_sch:
        return None

    return {"vs": vs_sch, "cr": cr_sch, "fmt": "橫向"}


# ── 主入口 ────────────────────────────────────────────────────────
def parse_schedule_excel(filepath: str) -> dict:
    """
    回傳:
    {
      "ok": True,
      "vs_schedule": {"1": "廖瑀", ...},
      "cr_schedule": {"1": "胡展瀚", ...},
      "warnings": [...],
      "format": "縱向"/"橫向",
      "raw_preview": [["A","B",...], ...]   # 前 8 列預覽
    }
    或
    {
      "ok": False,
      "error": "...",
      "raw_preview": [...]
    }
    """
    try:
        rows = _read_sheet(filepath)
    except Exception as e:
        return {"ok": False, "error": f"無法讀取檔案：{e}", "raw_preview": []}

    if not rows:
        return {"ok": False, "error": "檔案內容為空", "raw_preview": []}

    raw_preview = [row[:12] for row in rows[:8]]

    # 試縱向，再試橫向
    result = _parse_vertical(rows) or _parse_horizontal(rows)

    if not result:
        return {
            "ok": False,
            "error": "無法自動辨識表格格式，請確認 Excel 包含「日期」欄及「值班VS」「值班CR」欄位標頭",
            "raw_preview": raw_preview,
        }

    # 驗證醫師姓名
    warnings = []
    for day, name in {**result["vs"], **result["cr"]}.items():
        if name and name not in ALL_DOCTORS:
            warnings.append(f"{day}日：「{name}」不在已知名單中，請確認")

    return {
        "ok": True,
        "vs_schedule": {str(k): v for k, v in result["vs"].items()},
        "cr_schedule": {str(k): v for k, v in result["cr"].items()},
        "warnings": warnings,
        "format": result["fmt"],
        "raw_preview": raw_preview,
    }
