@echo off
chcp 65001 >nul
cd /d "%~dp0"
python main.py
if errorlevel 1 (
  echo.
  echo [錯誤] 啟動失敗。請確認已安裝 requirements.txt 列出的套件:
  echo   pip install -r requirements.txt
  pause
)
