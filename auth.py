"""認證模組：bcrypt 密碼、JWT Token、使用者管理"""
import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import bcrypt
from fastapi import HTTPException, Request
from jose import JWTError, jwt

import os
BASE_DIR = Path(__file__).parent
_DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR))
_DATA_DIR.mkdir(parents=True, exist_ok=True)
USERS_FILE = _DATA_DIR / "users.json"
SECRET_KEY_FILE = _DATA_DIR / ".secret_key"
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 8


# ── Secret Key ──────────────────────────────────────────────────
def _get_secret_key() -> str:
    if SECRET_KEY_FILE.exists():
        return SECRET_KEY_FILE.read_text().strip()
    key = secrets.token_hex(32)
    SECRET_KEY_FILE.write_text(key)
    return key


SECRET_KEY = _get_secret_key()


# ── TokenData ───────────────────────────────────────────────────
class TokenData:
    def __init__(self, username: str, role: str = "user"):
        self.username = username
        self.role = role

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


# ── Users JSON schema ────────────────────────────────────────────
# {
#   "username": {
#     "password":            "$2b$...",
#     "doctor_name":         "廖瑀",
#     "employee_id":         "A12345",
#     "rank":                "VS主治醫師",
#     "training_start_roc":  108,        # 民國年
#     "role":                "user",     # "user" | "admin"
#     "approved":            true,
#     "created_at":          "2026-03-25T10:00:00"
#   }
# }

def load_users() -> dict:
    if not USERS_FILE.exists():
        return {}
    raw = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    # 相容舊格式：值若為字串（只有 hash）則自動升級
    upgraded = {}
    for u, v in raw.items():
        if isinstance(v, str):
            upgraded[u] = {
                "password":           v,
                "doctor_name":        u,
                "employee_id":        u,
                "rank":               "admin",
                "training_start_roc": None,
                "role":               "admin",
                "approved":           True,
                "created_at":         "2026-01-01T00:00:00",
            }
        else:
            upgraded[u] = v
    return upgraded


def save_users(users: dict):
    USERS_FILE.write_text(
        json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── 登入 ────────────────────────────────────────────────────────
def login_user(username: str, password: str) -> Optional[str]:
    """成功傳回 JWT，否則傳回 None。"""
    users = load_users()
    user = users.get(username)
    if not user:
        return None
    if not bcrypt.checkpw(password.encode(), user["password"].encode()):
        return None
    if not user.get("approved", False):
        return "PENDING"   # 帳號待審核
    expire = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode(
        {"sub": username, "role": user.get("role", "user"), "exp": expire},
        SECRET_KEY, algorithm=ALGORITHM
    )


# ── 驗證 Token ──────────────────────────────────────────────────
def verify_token(token: str) -> TokenData:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        role = payload.get("role", "user")
        if not username:
            raise ValueError
        return TokenData(username, role)
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")


async def require_auth(request: Request) -> TokenData:
    token = request.cookies.get("token")
    if not token:
        raise HTTPException(status_code=401)
    return verify_token(token)


# ── 註冊（待管理者審核） ──────────────────────────────────────────
def register_user(
    username: str,
    password: str,
    doctor_name: str,
    employee_id: str,
    rank: str,
    training_start_roc: Optional[int],
) -> tuple[bool, str]:
    """
    回傳 (success, message)
    """
    if len(password) < 6:
        return False, "密碼至少需要 6 個字元"
    users = load_users()
    if username in users:
        return False, "此帳號已被使用，請換一個帳號名稱"
    # 檢查員工號是否重複
    for u in users.values():
        if u.get("employee_id") == employee_id:
            return False, "此員工號已有帳號，請聯絡管理者"
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    users[username] = {
        "password":           hashed,
        "doctor_name":        doctor_name,
        "employee_id":        employee_id,
        "rank":               rank,
        "training_start_roc": training_start_roc,
        "role":               "user",
        "approved":           False,
        "created_at":         datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
    save_users(users)
    return True, "註冊成功！請等待管理者審核後即可登入"


# ── 管理員操作 ───────────────────────────────────────────────────
def approve_user(username: str) -> bool:
    users = load_users()
    if username not in users:
        return False
    users[username]["approved"] = True
    save_users(users)
    return True


def reject_delete_user(username: str) -> bool:
    users = load_users()
    if username not in users:
        return False
    del users[username]
    save_users(users)
    return True


def get_admin_names() -> list[str]:
    """回傳所有已核准的管理員醫師姓名清單"""
    users = load_users()
    admins = []
    for info in users.values():
        if info.get("role") == "admin" and info.get("approved", False):
            name = info.get("doctor_name", "")
            if name:
                admins.append(name)
    return admins


def get_all_users() -> list[dict]:
    users = load_users()
    result = []
    for uname, info in users.items():
        result.append({
            "username":           uname,
            "doctor_name":        info.get("doctor_name", ""),
            "employee_id":        info.get("employee_id", ""),
            "rank":               info.get("rank", ""),
            "training_start_roc": info.get("training_start_roc"),
            "role":               info.get("role", "user"),
            "approved":           info.get("approved", False),
            "created_at":         info.get("created_at", ""),
        })
    return result
