#!/usr/bin/env python3
"""
使用者管理 CLI（後台設定帳號密碼）

用法:
  python manage_users.py add <username>     新增使用者（會提示輸入密碼）
  python manage_users.py remove <username>  刪除使用者
  python manage_users.py list               列出所有使用者
  python manage_users.py change <username>  修改密碼
"""
import getpass
import json
import sys
from pathlib import Path

import bcrypt

USERS_FILE = Path(__file__).parent / "users.json"


def load() -> dict:
    if not USERS_FILE.exists():
        return {}
    return json.loads(USERS_FILE.read_text(encoding="utf-8"))


def save(users: dict):
    USERS_FILE.write_text(
        json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _ask_password(username: str) -> str:
    while True:
        pw  = getpass.getpass(f"請輸入 [{username}] 的密碼（至少 6 碼）: ")
        if len(pw) < 6:
            print("⚠️  密碼至少需要 6 個字元，請重試")
            continue
        pw2 = getpass.getpass("再次確認密碼: ")
        if pw == pw2:
            return pw
        print("⚠️  兩次輸入不一致，請重試")


def cmd_add(username: str):
    users = load()
    if username in users:
        print(f"⚠️  使用者 '{username}' 已存在")
        if input("是否覆蓋密碼？(y/N): ").strip().lower() != "y":
            return
    pw = _ask_password(username)
    users[username] = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
    save(users)
    print(f"✅ 使用者 '{username}' 已新增")


def cmd_remove(username: str):
    users = load()
    if username not in users:
        print(f"⚠️  使用者 '{username}' 不存在")
        return
    del users[username]
    save(users)
    print(f"✅ 使用者 '{username}' 已刪除")


def cmd_list():
    users = load()
    if not users:
        print("（目前無任何使用者）")
        return
    print("目前使用者：")
    for u in users:
        print(f"  ✔ {u}")


def cmd_change(username: str):
    users = load()
    if username not in users:
        print(f"⚠️  使用者 '{username}' 不存在，請先 add")
        return
    pw = _ask_password(username)
    users[username] = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
    save(users)
    print(f"✅ 使用者 '{username}' 密碼已更新")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd in ("add", "remove", "change"):
        if len(sys.argv) < 3:
            print(f"請提供使用者名稱：python manage_users.py {cmd} <username>")
            sys.exit(1)
        {"add": cmd_add, "remove": cmd_remove, "change": cmd_change}[cmd](sys.argv[2])
    elif cmd == "list":
        cmd_list()
    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
