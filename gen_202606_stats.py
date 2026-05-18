"""Read existing 202606 calendar tab → generate 202606 班數統計 →
rebuild 值班總數統計 = 202508-202603 統計 + every {YYYYMM} 班數統計.

Dry run: python _gen_202606.py        Apply: python _gen_202606.py --apply
"""
import sys
import json
from datetime import date, datetime

from gsheet_io import get_sheet, make_stat_type_fn, is_taiwan_holiday
from cv_solver import _compute_stats, ALL_DOCTORS

YEAR, MONTH = 2026, 6
CAL_TAB = "202606"
STATS_TAB = "202606 班數統計"
# schema mirrors 202605 班數統計
STATS_HEADER = ["姓名", "平日班", "週五班", "假日班", "週六班", "週日班",
                "QOD次數", "總班數"]


def parse_calendar(values):
    """Week-block grid: header row, then (date-row, name-row) pairs."""
    sched = {}
    i = 1
    while i + 1 < len(values):
        drow, nrow = values[i], values[i + 1]
        for c in range(min(7, len(drow))):
            ds = (drow[c] or "").strip()
            if ds.isdigit():
                nm = (nrow[c] or "").strip() if c < len(nrow) else ""
                if nm:
                    sched[date(YEAR, MONTH, int(ds))] = nm
        i += 2
    return sched


def main(apply):
    sheet = get_sheet()
    cal = sheet.worksheet(CAL_TAB).get_all_values()
    sched = parse_calendar(cal)
    print(f"202606 解析 {len(sched)} 天（{min(sched)} → {max(sched)}）")

    stf = make_stat_type_fn(is_taiwan_holiday)
    rows, by_name = _compute_stats(sched, stf)

    print(f"\n{'姓名':<5}{'平日':>6}{'週五':>6}{'假日':>6}{'週六':>6}"
          f"{'週日':>6}{'QOD':>5}{'總':>5}  六+日")
    bad = False
    out_rows = [STATS_HEADER]
    for r in rows:
        n = r["姓名"]
        tot = r["平日班"] + r["週五班"] + r["假日班"]
        chk = r["週六班"] + r["週日班"]
        if r["假日班"] != chk:
            bad = True
        if any(r[k] for k in ("平日班", "週五班", "假日班")) or r["QOD次數"]:
            print(f"{n:<5}{r['平日班']:>6}{r['週五班']:>6}{r['假日班']:>6}"
                  f"{r['週六班']:>6}{r['週日班']:>6}{r['QOD次數']:>5}{tot:>5}"
                  f"  {chk}" + ("  <-- 假日≠六+日!" if r['假日班'] != chk else ""))
        out_rows.append([n, r["平日班"], r["週五班"], r["假日班"],
                         r["週六班"], r["週日班"], r["QOD次數"], tot])

    if bad:
        print("\n❌ 假日≠六+日，中止。")
        return
    print("\n✅ 202606 每人 假日 == 週六+週日")

    # rebuild cumulative = snapshot + ALL monthly 班數統計 (incl. new 202606)
    def read_tab(t):
        v = sheet.worksheet(t).get_all_values()
        return v[0], v[1:]

    snap_h, snap_r = read_tab("202508-202603 統計")
    snap = {}
    for r in snap_r:
        if r and r[0].strip():
            snap[r[0].strip()] = {
                "平日": int(r[1] or 0), "週五": int(r[2] or 0),
                "週六": int(r[3] or 0), "週日": int(r[4] or 0),
                "假日": int(r[5] or 0)}

    titles = sorted(w.title for w in sheet.worksheets()
                    if w.title.endswith(" 班數統計") and w.title >= "202604")

    def read_monthly(rs, h):
        idx = {nm: i for i, nm in enumerate(h)}
        out = {}
        for r in rs:
            if not r or not r[0].strip():
                continue
            g = lambda k: (int(r[idx[k]] or 0)
                           if k in idx and idx[k] < len(r) else 0)
            out[r[0].strip()] = {"平日": g("平日班"), "週五": g("週五班"),
                                 "週六": g("週六班"), "週日": g("週日班"),
                                 "假日": g("假日班")}
        return out

    months = {}
    for t in titles:
        if t == STATS_TAB:                       # use freshly computed
            months[t] = {n: {"平日": by_name[n]["平日班"],
                             "週五": by_name[n]["週五班"],
                             "週六": by_name[n]["週六班"],
                             "週日": by_name[n]["週日班"],
                             "假日": by_name[n]["假日班"]} for n in ALL_DOCTORS}
        else:
            h, rs = read_tab(t)
            months[t] = read_monthly(rs, h)
    if STATS_TAB not in months:                  # tab not created yet
        months[STATS_TAB] = {n: {"平日": by_name[n]["平日班"],
                                 "週五": by_name[n]["週五班"],
                                 "週六": by_name[n]["週六班"],
                                 "週日": by_name[n]["週日班"],
                                 "假日": by_name[n]["假日班"]}
                             for n in ALL_DOCTORS}
    print("累計來源:", "202508-202603 統計 +", " + ".join(sorted(months)))

    cum_rows = [["姓名", "平日班(一至四)", "週五班", "週六班", "週日班",
                 "假日班(含六日及國定假日)", "總班數"]]
    print(f"\n值班總數統計 (預覽):\n{'姓名':<5}{'平日':>6}{'週五':>6}"
          f"{'週六':>6}{'週日':>6}{'假日':>6}{'總':>6}")
    for n in ALL_DOCTORS:
        acc = dict(snap.get(n, {"平日": 0, "週五": 0, "週六": 0,
                                "週日": 0, "假日": 0}))
        for m in months.values():
            d = m.get(n, {})
            for k in acc:
                acc[k] += d.get(k, 0)
        tot = acc["平日"] + acc["週五"] + acc["假日"]
        print(f"{n:<5}{acc['平日']:>6}{acc['週五']:>6}{acc['週六']:>6}"
              f"{acc['週日']:>6}{acc['假日']:>6}{tot:>6}"
              + ("  <-- 假日≠六+日!" if acc['假日'] != acc['週六']+acc['週日'] else ""))
        cum_rows.append([n, acc["平日"], acc["週五"], acc["週六"],
                         acc["週日"], acc["假日"], tot])

    if not apply:
        print("\n[DRY RUN] 加 --apply 才會寫入。")
        return

    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    bk = f"cumulative_backup_{ts}.json"
    cum_h, cum_r = read_tab("值班總數統計")
    with open(bk, "w", encoding="utf-8") as f:
        json.dump({"值班總數統計": [cum_h] + cum_r}, f,
                  ensure_ascii=False, indent=2)
    print(f"\n備份 → {bk}")

    try:
        ws = sheet.worksheet(STATS_TAB)
        ws.clear()
    except Exception:
        ws = sheet.add_worksheet(title=STATS_TAB, rows=20, cols=10)
    ws.update(range_name="A1", values=out_rows,
              value_input_option="USER_ENTERED")
    print(f"已寫入 {STATS_TAB}")

    wc = sheet.worksheet("值班總數統計")
    wc.clear()
    wc.update(range_name="A1", values=cum_rows,
              value_input_option="USER_ENTERED")
    print("已重建 值班總數統計（含 202606）")


if __name__ == "__main__":
    main("--apply" in sys.argv)
