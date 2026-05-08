"""操作稽核日誌模組 — 所有使用者行為寫入 audit_log.jsonl"""
import json
import os
from datetime import datetime
from pathlib import Path

_DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent))
_DATA_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_FILE = _DATA_DIR / "audit_log.jsonl"


def log(action: str, user: str = "-", ip: str = "-", detail: str = ""):
    """
    寫入一筆稽核記錄（JSONL，每行一個 JSON 物件）。
    action 範例: login_success, login_fail, register, schedule_start,
                 schedule_done, schedule_cancel, upload_excel, preview,
                 admin_approve, admin_reject, admin_delete
    """
    entry = {
        "ts":     datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "user":   user,
        "ip":     ip,
        "action": action,
        "detail": detail,
    }
    with AUDIT_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_logs(limit: int = 500) -> list[dict]:
    """讀取最新 limit 筆（從檔案尾端）"""
    if not AUDIT_FILE.exists():
        return []
    lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines()
    result = []
    for line in lines[-limit:]:
        line = line.strip()
        if line:
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return list(reversed(result))   # 最新在前
