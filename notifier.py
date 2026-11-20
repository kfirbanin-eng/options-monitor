"""Desktop notifications + alert log.

The monitor decides WHEN to notify (status change or level hit only); this
module just delivers. Tries, in order: plyer (pip install plyer - best on
Windows), macOS osascript, Linux notify-send. Always also prints to the
console and appends to logs/alerts.log.
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


class Notifier:
    def __init__(self, desktop: bool = True, log_path: Path | None = None):
        self.desktop = desktop
        self.log_path = log_path
        self.sent: list[dict] = []

    def notify(self, title: str, message: str, when: datetime | None = None) -> None:
        stamp = (when or datetime.now()).strftime("%Y-%m-%d %H:%M")
        self.sent.append({"time": stamp, "title": title, "message": message})
        print(f"\a\n*** ALERT {stamp}  {title}\n    {message}\n", flush=True)
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as f:
                f.write(f"{stamp}\t{title}\t{message}\n")
        if self.desktop:
            self._desktop(title, message)

    @staticmethod
    def _desktop(title: str, message: str) -> None:
        try:
            from plyer import notification  # type: ignore
            notification.notify(title=title, message=message[:250], app_name="Options Monitor", timeout=15)
            return
        except Exception:
            pass
        try:
            system = platform.system()
            if system == "Darwin":
                script = f"display notification {json.dumps(message)} with title {json.dumps(title)} sound name \"Glass\""
                subprocess.run(["osascript", "-e", script], check=False, timeout=5)
            elif system == "Linux" and shutil.which("notify-send"):
                subprocess.run(["notify-send", "-a", "Options Monitor", "-u", "critical", title, message],
                               check=False, timeout=5)
        except Exception as e:  # never let a notification failure stop monitoring
            print(f"(desktop notification failed: {e})")
