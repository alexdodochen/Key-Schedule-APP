"""排班自動化核心：根據網頁表單資料執行 Playwright"""
import asyncio
import calendar
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Optional

TARGET_URL = "https://web.hosp.ncku.edu.tw/edr/login"
DEPARTMENT = "04-心臟血管科"


def build_schedule_from_config(cfg: dict):
    """
    cfg 欄位:
      year, month, vs_schedule{"1":"廖瑀",...}, cr_schedule,
      tw_holidays["2026-05-01",...],
      waizhao_vs_list, waizhao_r_list,
      neizhao_vs_list, neizhao_r_list,
      icu_vs_list, icu_r_list,
      jiuzhen_r_list,
      *_start (index), test_from, test_to
    """
    year  = int(cfg["year"])
    month = int(cfg["month"])
    vs_sch = {int(k): v for k, v in cfg.get("vs_schedule", {}).items()}
    cr_sch = {int(k): v for k, v in cfg.get("cr_schedule", {}).items()}

    holiday_set: set[date] = set()
    for s in cfg.get("tw_holidays", []):
        holiday_set.add(datetime.strptime(s, "%Y-%m-%d").date())

    def is_holiday(d: date) -> bool:
        return d.weekday() >= 5 or d in holiday_set

    # 輪值名單
    wai_vs   = cfg.get("waizhao_vs_list", [])
    wai_r    = cfg.get("waizhao_r_list", [])
    nei_vs   = cfg.get("neizhao_vs_list", [])
    nei_r    = cfg.get("neizhao_r_list", [])
    icu_vs   = cfg.get("icu_vs_list", [])
    icu_r    = cfg.get("icu_r_list", [])
    jz_r     = cfg.get("jiuzhen_r_list", [])

    wi_vs = int(cfg.get("waizhao_vs_start", 0))
    wi_r  = int(cfg.get("waizhao_r_start",  0))
    ni_vs = int(cfg.get("neizhao_vs_start", 0))
    ni_r  = int(cfg.get("neizhao_r_start",  0))
    ii_vs = int(cfg.get("icu_vs_start",     0))
    ii_r  = int(cfg.get("icu_r_start",      0))
    ji    = int(cfg.get("jiuzhen_r_start",  0))

    days_in_month = calendar.monthrange(year, month)[1]
    test_from = cfg.get("test_from")
    test_to   = cfg.get("test_to")
    day_start = int(test_from) if test_from else 1
    day_end   = (int(test_to) if test_to else days_in_month) + 1

    result = []
    for day in range(day_start, day_end):
        d       = date(year, month, day)
        holiday = is_holiday(d)
        vs_doc  = vs_sch.get(day, "")
        cr_doc  = cr_sch.get(day, "")

        if vs_doc:
            for sh in ["1值班VS", "9急診白天照會VS", "11晚班照會VS", "15二級動員召回值班VS"]:
                result.append((day, vs_doc, sh))
        if cr_doc:
            for sh in ["2值班CR", "12晚班照會R", "14三級動員召回值班CR"]:
                result.append((day, cr_doc, sh))

        if holiday:
            if vs_doc:
                for sh in ["3科外白天照會VS", "5科內白天照會VS", "7ICU白天照會VS"]:
                    result.append((day, vs_doc, sh))
            if cr_doc:
                # 假日 13CCU白天控床CR 由當天值班 CR 兼任
                for sh in ["4科外白天照會R", "6科內白班照會R", "8ICU白班照會R", "10急診白天照會R", "13CCU白天控床CR"]:
                    result.append((day, cr_doc, sh))

        else:
            if wai_vs:
                result.append((day, wai_vs[wi_vs % len(wai_vs)], "3科外白天照會VS"))
                wi_vs += 1
            if wai_r:
                result.append((day, wai_r[wi_r % len(wai_r)], "4科外白天照會R"))
                wi_r += 1
            if nei_vs:
                result.append((day, nei_vs[ni_vs % len(nei_vs)], "5科內白天照會VS"))
                ni_vs += 1
            if nei_r:
                result.append((day, nei_r[ni_r % len(nei_r)], "6科內白班照會R"))
                ni_r += 1
            if icu_vs:
                result.append((day, icu_vs[ii_vs % len(icu_vs)], "7ICU白天照會VS"))
                ii_vs += 1
            if icu_r:
                result.append((day, icu_r[ii_r % len(icu_r)], "8ICU白班照會R"))
                ii_r += 1
            if jz_r:
                doc = jz_r[ji % len(jz_r)]
                result.append((day, doc, "10急診白天照會R"))
                result.append((day, doc, "13CCU白天控床CR"))
                ji += 1

    return result, is_holiday


class ConnectionManager:
    def __init__(self):
        self.active = []

    async def connect(self, ws):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.remove(ws)


class SchedulerSession:
    def __init__(self, cfg: dict, manager: ConnectionManager):
        self.cfg      = cfg
        self.manager  = manager
        self.state    = "starting"
        self.logs: list[str] = []
        self.login_event = asyncio.Event()
        self._cancelled  = False
        self._browser    = None

    async def _log(self, text: str):
        self.logs.append(text)
        await self.manager.broadcast({"type": "log", "text": text})

    async def _set_state(self, state: str):
        self.state = state
        await self.manager.broadcast({"type": "status", "state": state})

    async def cancel(self):
        self._cancelled = True
        self.login_event.set()
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        self._browser = None
        await self._set_state("cancelled")

    async def run(self):
        try:
            await self._run()
        except Exception as e:
            await self._log(f"❌ 執行錯誤: {e}")
            await self._set_state("error")

    # ── 主流程 ──────────────────────────────────────────────────
    async def _run(self):
        from playwright.async_api import async_playwright

        year  = int(self.cfg["year"])
        month = int(self.cfg["month"])
        schedule_month = self.cfg.get("schedule_month") or f"{year}-{month:02d}"

        schedule, is_holiday = build_schedule_from_config(self.cfg)
        if not schedule:
            await self._log("排班資料為空，請確認設定")
            await self._set_state("error")
            return

        by_doctor = defaultdict(list)
        for day, doc, shift in schedule:
            by_doctor[doc].append((day, shift))

        await self._log(f"總計 {len(schedule)} 筆排班，{len(by_doctor)} 位醫師")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False, slow_mo=150)
            self._browser = browser
            page = await (await browser.new_context()).new_page()

            await page.goto(TARGET_URL)
            await self._log("已開啟成大排班系統瀏覽器，請在彈出的視窗中手動登入。")
            await self._log("登入完成後，請切回本視窗並按「▶ 繼續排班」。")
            await self._set_state("waiting_login")
            await self.login_event.wait()
            if self._cancelled:
                return

            await self._log("確認登入完成，開始自動排班…")
            await self._set_state("running")
            await page.wait_for_timeout(1000)

            await self._setup_filters(page, schedule_month)

            done = 0
            for doctor, shifts in by_doctor.items():
                if not doctor or self._cancelled:
                    continue
                await self._log(f"[醫師] {doctor}（{len(shifts)} 班）")
                await self._select_doctor(page, doctor)
                for day, shift_name in sorted(shifts):
                    if self._cancelled:
                        break
                    d   = date(year, month, day)
                    tag = "假" if is_holiday(d) else "平"
                    await self._log(f"  [{tag}] {month}/{day:02d} → {shift_name}")
                    await self._click_shift_cell(page, day, shift_name, month)
                    done += 1

            if self._cancelled:
                await self._log("已中途取消")
                await self._set_state("cancelled")
            else:
                await self._log(f"✅ 排班完成！共排 {done} 筆")
                await self._set_state("done")

            self._browser = None

    # ── Playwright helpers ───────────────────────────────────────
    async def _auto_login(self, page, account: str, password: str):
        """自動填入成大排班系統帳號密碼並登入"""
        await page.wait_for_load_state('domcontentloaded')
        await page.wait_for_timeout(1000)

        # 填帳號（嘗試常見欄位 selector）
        for sel in ['input[name="username"]', 'input[name="account"]',
                    'input[type="text"]', 'input[placeholder*="帳號"]',
                    'input[placeholder*="Account"]']:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.fill(account)
                break

        await page.wait_for_timeout(300)

        # 填密碼
        pwd = page.locator('input[type="password"]').first
        await pwd.fill(password)
        await page.wait_for_timeout(300)

        # 點登入按鈕
        for sel in ['button[type="submit"]', 'input[type="submit"]',
                    'button:has-text("登入")', 'button:has-text("Login")']:
            btn = page.locator(sel).first
            if await btn.count() > 0:
                await btn.click()
                break

        await page.wait_for_timeout(2500)

    async def _setup_filters(self, page, schedule_month: str):
        await self._log(f"設定科別={DEPARTMENT}  月份={schedule_month}")
        await page.locator('div.prglist-title:has-text("程式清單")').click()
        await page.wait_for_timeout(600)
        await page.locator('li.ant-menu-item:has-text("排班作業")').click()
        await page.wait_for_selector('input[placeholder="yyyy-MM"]', timeout=10000)
        await page.wait_for_timeout(500)
        await page.locator('.ant-select.dept-selector .ant-select-selector').click()
        await page.wait_for_timeout(500)
        await page.locator(f'.ant-select-item-option-content:has-text("{DEPARTMENT}")').click()
        await page.wait_for_timeout(800)
        mi = page.locator('input[placeholder="yyyy-MM"]')
        await mi.click()
        await mi.fill("")
        await mi.type(schedule_month)
        await mi.press("Enter")
        await page.wait_for_timeout(1200)
        await self._log("篩選設定完成")

    async def _select_doctor(self, page, name: str):
        fil = page.locator('input[placeholder="過濾本科醫師清單"]')
        await fil.click()
        await fil.fill("")
        await fil.type(name)
        await page.wait_for_timeout(600)
        div = page.locator(f'div:has(> span.psn-code):has-text("{name}")').first
        if await div.count() == 0:
            div = page.locator(f'text="{name}"').first
        await div.click()
        await page.wait_for_timeout(1000)

    async def _click_shift_cell(self, page, day: int, shift_name: str, month: int):
        m = re.match(r'^(\d+)', shift_name)
        shift_num = int(m.group(1)) if m else -1
        if shift_num < 0:
            await self._log(f"  ⚠️ 無法解析班別序號: {shift_name}")
            return

        # 透過 JS 尋找元素並回傳診斷資訊
        result = await page.evaluate("""
            ([day, shiftNum]) => {
                const tables = document.querySelectorAll('#shift-div .table');
                let diag = { found: false, reason: "找不到表格容器 #shift-div .table" };
                
                let candidates = [];

                for (let tIdx = 0; tIdx < tables.length; tIdx++) {
                    const table = tables[tIdx];
                    const hdrCells = Array.from(
                        table.querySelectorAll('.table-header-group .table-cell')
                    ).filter(c => !c.classList.contains('th'));
                    
                    let matchedColIndices = [];
                    hdrCells.forEach((cell, i) => {
                        const text = cell.innerText.trim();
                        const m = text.match(/^(\\d+)/);
                        if (m && parseInt(m[1]) === day) {
                            if (day > 20 && parseInt(m[1]) < 10) return; 
                            if (day < 10 && parseInt(m[1]) > 20) return;
                            matchedColIndices.push(i);
                        }
                    });
                    
                    if (matchedColIndices.length === 0) continue;
                    
                    const rows = table.querySelectorAll('.table-row-group .table-row');
                    for (const row of rows) {
                        const seq = row.querySelector('.list-seq');
                        if (!seq || parseInt(seq.innerText.trim()) !== shiftNum) continue;
                        
                        for (const colIdx of matchedColIndices) {
                            const docs = row.querySelectorAll('.doc-block');
                            if (colIdx < docs.length) {
                                const docBlock = docs[colIdx];
                                const target = docBlock.querySelector('.hand') || docBlock;
                                if (target) {
                                    const isNothing = docBlock.classList.contains('nothing');
                                    const hasContent = target.innerText.trim().length > 0;
                                    const rect = target.getBoundingClientRect();
                                    const isVisible = rect.width > 0 && rect.height > 0;
                                    
                                    // 優先權排序：
                                    // 4: 有內容 + 非 nothing + 可見 (最準確)
                                    // 3: 有內容 + 非 nothing
                                    // 2: 非 nothing
                                    // 1: 其他 (可能是 nothing)
                                    let priority = 1;
                                    if (!isNothing && hasContent && isVisible) priority = 4;
                                    else if (!isNothing && hasContent) priority = 3;
                                    else if (!isNothing) priority = 2;

                                    candidates.push({
                                        target: target,
                                        tIdx: tIdx,
                                        colIdx: colIdx,
                                        priority: priority
                                    });
                                }
                            }
                        }
                    }
                }

                if (candidates.length > 0) {
                    // 取優先權最高且索引最靠後的候選者
                    candidates.sort((a, b) => (a.priority - b.priority) || (candidates.indexOf(a) - candidates.indexOf(b)));
                    const best = candidates[candidates.length - 1];
                    
                    best.target.scrollIntoView({ behavior: 'instant', block: 'center' });
                    return { found: true, priority: best.priority };
                }

                diag.reason = `找不到有效的日期 ${day} 與班別 ${shiftNum} 組合 (已過濾 nothing 格子)`;
                return diag;
            }
        """, [day, shift_num])

        if result.get("found"):
            # 再次獲取 handle 進行點擊
            handle = await page.evaluate_handle("""
                ([day, shiftNum]) => {
                    const tables = document.querySelectorAll('#shift-div .table');
                    let cands = [];
                    for (const table of tables) {
                        const hdrCells = Array.from(table.querySelectorAll('.table-header-group .table-cell')).filter(c => !c.classList.contains('th'));
                        let cols = [];
                        hdrCells.forEach((cell, i) => {
                            const m = cell.innerText.trim().match(/^(\\d+)/);
                            if (m && parseInt(m[1]) === day) {
                                if (day > 20 && parseInt(m[1]) < 10) return;
                                if (day < 10 && parseInt(m[1]) > 20) return;
                                cols.push(i);
                            }
                        });
                        if (cols.length === 0) continue;
                        const rows = table.querySelectorAll('.table-row-group .table-row');
                        for (const row of rows) {
                            const seq = row.querySelector('.list-seq');
                            if (seq && parseInt(seq.innerText.trim()) === shiftNum) {
                                for (const colIdx of cols) {
                                    const docBlock = row.querySelectorAll('.doc-block')[colIdx];
                                    if (!docBlock) continue;
                                    const target = docBlock.querySelector('.hand') || docBlock;
                                    const isNothing = docBlock.classList.contains('nothing');
                                    const hasContent = target.innerText.trim().length > 0;
                                    let prio = 1;
                                    if (!isNothing && hasContent) prio = 3;
                                    else if (!isNothing) prio = 2;
                                    cands.push({ target, prio });
                                }
                            }
                        }
                    }
                    if (cands.length === 0) return null;
                    cands.sort((a, b) => a.prio - b.prio);
                    return cands[cands.length - 1].target;
                }
            """, [day, shift_num])
            
            if handle and await handle.evaluate('el => el !== null'):
                try:
                    await handle.dblclick(force=True, timeout=5000)
                    await page.wait_for_timeout(300)
                    await self._log(f"    ✓ {month}/{day} seq={shift_num} (prio={result.get('priority')})")
                except Exception as e:
                    await self._log(f"    ❌ 點擊失敗: {e}")
            else:
                await self._log(f"    ⚠️ 無法獲取 Handle (原因同上)")
        else:
            await self._log(f"  ⚠️ 失敗: {result.get('reason')} ({month}/{day} {shift_name})")
