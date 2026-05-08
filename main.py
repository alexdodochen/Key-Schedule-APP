"""CV_APP — desktop launcher.

Spawns the FastAPI backend on 127.0.0.1:8765 in a background thread, then
opens a PyQt5 QWebEngineView pointed at that port. Closes the process when
the window closes.
"""
from __future__ import annotations

import sys
import threading
import time

import uvicorn
from PyQt5.QtCore import QUrl, QTimer
from PyQt5.QtWebEngineWidgets import QWebEngineProfile, QWebEngineView
from PyQt5.QtWidgets import QApplication

from app import app

PORT = 8765
INACTIVITY_TIMEOUT_MS = 30 * 60 * 1000  # 30 min idle → auto-close


def _start_server() -> None:
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="error")


class MainWindow(QWebEngineView):
    def __init__(self):
        super().__init__()
        self._timer = QTimer(self)
        self._timer.setInterval(INACTIVITY_TIMEOUT_MS)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.close)
        self._timer.start()

    def _reset(self):
        self._timer.start()

    def mouseMoveEvent(self, ev):  self._reset(); super().mouseMoveEvent(ev)
    def mousePressEvent(self, ev): self._reset(); super().mousePressEvent(ev)
    def keyPressEvent(self, ev):   self._reset(); super().keyPressEvent(ev)
    def wheelEvent(self, ev):      self._reset(); super().wheelEvent(ev)


def main() -> int:
    t = threading.Thread(target=_start_server, daemon=True)
    t.start()
    time.sleep(2)

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("CV_APP — 心臟內科排班整合")

    QWebEngineProfile.defaultProfile().cookieStore().deleteAllCookies()

    view = MainWindow()
    view.setWindowTitle("CV_APP — 心臟內科排班整合")
    view.resize(1280, 880)
    view.load(QUrl(f"http://127.0.0.1:{PORT}"))
    view.show()
    return qt_app.exec_()


if __name__ == "__main__":
    sys.exit(main())
