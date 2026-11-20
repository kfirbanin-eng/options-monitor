"""Desktop notifications + alert log.

The monitor decides WHEN to notify (status change or level hit only); this
module just delivers. Tries, in order: plyer (pip install plyer - best on
Windows), macOS osascript, Linux notify-send. Always also prints to the
console and appends to logs/alerts.log.

Phone push: set NTFY_TOPIC (in .env, or as a GitHub secret for the phone
workflow) and install the free ntfy app (Android/iOS), subscribed to the same
topic. The topic name works like a password - make it long and random.
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


TAGS = {"GREEN": "green_circle", "YELLOW": "yellow_circle", "RED": "red_circle"}


class Notifier:
    def __init__(self, desktop: bool = True, log_path: Path | None = None,
                 ntfy_topic: str | None = None, ntfy_server: str = "https://ntfy.sh", http=None):
        self.desktop = desktop
        self.log_path = log_path
        self.sent: list[dict] = []
        self.ntfy_topic = (ntfy_topic or "").strip() or None
        self.ntfy_server = ntfy_server.rstrip("/")
        self._http = http

    def _ntfy(self, title: str, message: str, priority: str = "default", tags: str = "") -> None:
        """POST to ntfy. Never raises - a failed push must not stop monitoring."""
        if not self.ntfy_topic:
            return
        try:
            import requests
            http = self._http or requests
            safe_title = title.encode("latin-1", "replace").decode("latin-1")   # HTTP headers are latin-1
            r = http.post(f"{self.ntfy_server}/{self.ntfy_topic}", data=message.encode("utf-8"), timeout=10,
                          headers={"Title": safe_title, "Priority": priority, "Tags": tags})
            if getattr(r, "status_code", 200) >= 400:
                print(f"(phone push failed: HTTP {r.status_code})")
        except Exception as e:
            print(f"(phone push failed: {e})")

    def push(self, title: str, message: str, priority: str = "default", tags: str = "") -> None:
        """Informational phone message (start, periodic P/L, end of session). Not an alert:
        no desktop popup and not counted in the alert list."""
        print(f"[phone] {title} | {message.splitlines()[0] if message else ''}", flush=True)
        self._ntfy(title, message, priority, tags)

    def notify(self, title: str, message: str, when: datetime | None = None, priority: str = "high",
               tags: str = "") -> None:
        stamp = (when or datetime.now()).strftime("%Y-%m-%d %H:%M")
        self.sent.append({"time": stamp, "title": title, "message": message})
        print(f"\a\n*** ALERT {stamp}  {title}\n    {message}\n", flush=True)
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as f:
                f.write(f"{stamp}\t{title}\t{message}\n")
        if self.desktop:
            self._desktop(title, message)
        self._ntfy(title, message, priority, tags or "bell")

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
