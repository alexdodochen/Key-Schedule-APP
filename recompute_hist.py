"""Recompute 202508-202603 historical snapshot from CV班表.xlsx using the
CURRENT stat logic (make_stat_type_fn folds 國定假日 into 週六/週日, so
假日 == 週六 + 週日 by construction).

Dry run:  python _recompute_hist.py
Apply  :  python _recompute_hist.py --apply
  --apply  backs up 202508-202603 統計 + 值班總數統計 to a gitignored JSON,
           overwrites 202508-202603 統計, then rebuilds 值班總數統計 as
           new_snapshot + 202604 班數統計 + 202605 班數統計.
"""
import sys
import json
from datetime import date, datetime
import openpyxl

from gsheet_io import get_sheet, make_stat_type_fn, TAIWAN_HOLIDAYS

XLSX = "CV班表.xlsx"
# (year, month) -> xlsx sheet name. 202603 lives in sheet '2026032'.
SHEET_NAMES = {
    (2025, 8): "202508", (2025, 9): "202509", (2025, 10): "202510",
    (2025, 11): "202511", (2025, 12): "202512",
    (2026, 1): "202601", (2026, 2): "202602", (2026, 3): "2026032",
}
WINDOW_LO = date(2025, 8, 1)
WINDOW_HI = date(2026, 3, 31)

ALL_DOCTORS = ["麒翔", "見賢", "常胤", "廖瑀", "昭佑", "朝允", "則瑋", "展瀚", "建寬"]
NAME_MAP = {"昭祐": "昭佑"}
WEEKDAY_LABELS = {"一", "二", "三", "四", "五", "六", "日"}


def normalize(name):
    if name is None:
        return None
    name = str(name).strip()
    return NAME_MAP.get(name, name) if name else None


def is_holiday(d: date) -> bool:
    return d.weekday() >= 5 or d in TAIWAN_HOLIDAYS


def parse_sheet(ws):
    """Return {date: name} for one xlsx month sheet (3-row week blocks:
    date row, 一二三四五六日 label row, assignment row)."""
    rows = list(ws.iter_rows(values_only=True))
    sched = {}
    i = 0
    while i < len(rows):
        row = rows[i]
        date_vals = {}
        for c in range(7):
            v = row[c] if c < len(row) else None
            if isinstance(v, datetime):
                date_vals[c] = v.date()
        if date_vals:
            asgn = rows[i + 2] if i + 2 < len(rows) else ()
            for c, d in date_vals.items():
                raw = asgn[c] if c < len(asgn) else None
                nm = normalize(raw)
                if nm and nm not in WEEKDAY_LABELS:
                    sched[d] = nm
            i += 3
        else:
            i += 1
    return sched


def build_schedule():
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    merged, conflicts, fixes = {}, [], []
    for (ey, em), sn in SHEET_NAMES.items():
        for d, nm in parse_sheet(wb[sn]).items():
            # Year-typo guard: a date whose month matches the sheet's
            # expected month but whose year is wrong (e.g. 2026/02 sheet
            # has cells typed 2025-02-09..15) is a data-entry slip — snap
            # the year. Edge-spill dates (different month, e.g. 202601
            # sheet's 2025-12-29) keep their real prior-month date.
            if d.month == em and d.year != ey:
                fixes.append((sn, d, d.replace(year=ey), nm))
                d = d.replace(year=ey)
            if not (WINDOW_LO <= d <= WINDOW_HI):
                continue
            if d in merged and merged[d] != nm:
                conflicts.append((d, merged[d], nm, sn))
            else:
                merged[d] = nm
    return merged, conflicts, fixes


def aggregate(sched):
    stf = make_stat_type_fn(is_holiday)
    totals = {n: {"平日": 0, "週五": 0, "週六": 0, "週日": 0, "假日": 0}
              for n in ALL_DOCTORS}
    for d, nm in sched.items():
        if nm not in totals:
            totals[nm] = {"平日": 0, "週五": 0, "週六": 0, "週日": 0, "假日": 0}
        t = totals[nm]
        st = stf(d)
        if st == "平日":
            t["平日"] += 1
        elif st == "週五班":
            t["週五"] += 1
        elif st == "週六班":
            t["週六"] += 1
        elif st == "週日班":
            t["週日"] += 1
        if is_holiday(d):
            t["假日"] += 1
    return totals


def read_tab(sheet, title):
    vals = sheet.worksheet(title).get_all_values()
    return vals[0], vals[1:]


def main(apply):
    sched, conflicts, fixes = build_schedule()
    if fixes:
        print("年份打字修正（month 對、year 錯 → 校正）：")
        for sn, old_d, new_d, nm in fixes:
            print(f"  [{sn}] {old_d} → {new_d}  ({nm})")
        print()
    if conflicts:
        print("⚠ 跨表日期衝突（同一天兩個 sheet 給不同人）：")
        for d, a, b, sn in conflicts:
            print(f"  {d} : {a} vs {b} (in {sn})")
        print("→ 先解決衝突再 apply。中止。")
        return
    print(f"解析 {len(sched)} 天（{min(sched)} → {max(sched)}）")

    new = aggregate(sched)

    sheet = get_sheet()
    hdr, old_rows = read_tab(sheet, "202508-202603 統計")
    old = {}
    for r in old_rows:
        if r and r[0].strip():
            old[r[0].strip()] = {
                "平日": int(r[1] or 0), "週五": int(r[2] or 0),
                "週六": int(r[3] or 0), "週日": int(r[4] or 0),
                "假日": int(r[5] or 0),
            }

    print(f"\n{'姓名':<5}{'平日':>10}{'週五':>8}{'週六':>8}{'週日':>8}"
          f"{'假日':>8}{'六+日':>8}{'差':>6}")
    bad = False
    for n in ALL_DOCTORS:
        nw, od = new[n], old.get(n, {})
        chk = nw["週六"] + nw["週日"]
        gap = nw["假日"] - chk
        if gap:
            bad = True
        def cell(k):
            o = od.get(k)
            return f"{nw[k]}({nw[k]-o:+d})" if o is not None and o != nw[k] else str(nw[k])
        print(f"{n:<5}{cell('平日'):>10}{cell('週五'):>8}{cell('週六'):>8}"
              f"{cell('週日'):>8}{cell('假日'):>8}{chk:>8}{gap:>6}"
              + ("  <-- 假日≠六+日!" if gap else ""))

    print("\n舊 frozen tab (對照): 假日 含國定，故 假日 > 六+日")
    for n in ALL_DOCTORS:
        od = old.get(n)
        if od:
            print(f"  {n}: 假日{od['假日']} vs 六{od['週六']}+日{od['週日']}"
                  f"={od['週六']+od['週日']} (舊差 {od['假日']-od['週六']-od['週日']})")

    if bad:
        print("\n❌ 新算仍有 假日≠六+日，邏輯有問題，中止。")
        return
    print("\n✅ 新算每人 假日 == 週六+週日")

    if not apply:
        print("\n[DRY RUN] 加 --apply 才會寫入。")
        return

    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    bk = f"cumulative_backup_{ts}.json"
    snap_h, snap_r = read_tab(sheet, "202508-202603 統計")
    cum_h, cum_r = read_tab(sheet, "值班總數統計")
    with open(bk, "w", encoding="utf-8") as f:
        json.dump({"202508-202603 統計": [snap_h] + snap_r,
                   "值班總數統計": [cum_h] + cum_r}, f, ensure_ascii=False, indent=2)
    print(f"\n備份 → {bk}")

    # --- write 202508-202603 統計 (keep its 6-col + 總計 layout) ---
    snap_rows = [["姓名", "平日班(一至四)", "週五班", "週六班", "週日班",
                  "假日班(含六日及國定假日)", "總計"]]
    for n in ALL_DOCTORS:
        t = new[n]
        snap_rows.append([n, t["平日"], t["週五"], t["週六"], t["週日"],
                          t["假日"], t["平日"] + t["週五"] + t["假日"]])
    ws_snap = sheet.worksheet("202508-202603 統計")
    ws_snap.clear()
    ws_snap.update(range_name="A1", values=snap_rows,
                   value_input_option="USER_ENTERED")
    print("已覆寫 202508-202603 統計")

    # --- rebuild 值班總數統計 = new snapshot + 202604 + 202605 班數統計 ---
    def read_monthly(title):
        h, rs = read_tab(sheet, title)
        idx = {name: i for i, name in enumerate(h)}
        out = {}
        for r in rs:
            if not r or not r[0].strip():
                continue
            g = lambda k: int(r[idx[k]] or 0) if k in idx and idx[k] < len(r) else 0
            out[r[0].strip()] = {"平日": g("平日班"), "週五": g("週五班"),
                                 "週六": g("週六班"), "週日": g("週日班"),
                                 "假日": g("假日班")}
        return out

    m4 = read_monthly("202604 班數統計")
    m5 = read_monthly("202605 班數統計")
    cum_rows = [["姓名", "平日班(一至四)", "週五班", "週六班", "週日班",
                 "假日班(含六日及國定假日)", "總班數"]]
    for n in ALL_DOCTORS:
        acc = dict(new[n])
        for m in (m4.get(n, {}), m5.get(n, {})):
            for k in acc:
                acc[k] += m.get(k, 0)
        cum_rows.append([n, acc["平日"], acc["週五"], acc["週六"], acc["週日"],
                         acc["假日"], acc["平日"] + acc["週五"] + acc["假日"]])
    ws_cum = sheet.worksheet("值班總數統計")
    ws_cum.clear()
    ws_cum.update(range_name="A1", values=cum_rows,
                  value_input_option="USER_ENTERED")
    print("已重建 值班總數統計 = 新快照 + 202604 + 202605 班數統計")
    print("（值班總數統計_至2026/06 為另一份舊變體，未動）")


if __name__ == "__main__":
    main("--apply" in sys.argv)
