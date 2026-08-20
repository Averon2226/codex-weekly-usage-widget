#!/usr/bin/env python3
"""
Codex Weekly Remaining Widget (Windows 11)
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import time
import tkinter as tk
import urllib.error
import urllib.request


DB_PATH = Path.home() / ".codex" / "logs_2.sqlite"
AUTH_PATH = Path.home() / ".codex" / "auth.json"
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
POLL_MS = 2500
USAGE_POLL_SECONDS = 30
MARGIN_X = 14
MARGIN_Y = 10
WEEKLY_WINDOW_MINUTES = 10080  # 7 days
STALE_AFTER_SECONDS = 15 * 60
TRANSPARENT_KEY = "#010101"

CODEX_RESPONSE_URL_MARKER = "url=https://chatgpt.com/backend-api/codex/responses"
PRIMARY_USED_KEY = '"x-codex-primary-used-percent":'
SECONDARY_USED_KEY = '"x-codex-secondary-used-percent":'
# Codex has used both ``primary`` and ``secondary`` for the weekly bucket.
# Extract the response's header block first so strings in prompts or tool output
# cannot be mistaken for quota data.
HEADER_BLOCK_RE = re.compile(r"headers=\\?(\{.*?\})", re.DOTALL)
HEADER_INT_RE = re.compile(
    r'"x-codex-(primary|secondary)-(used-percent|window-minutes|reset-at)"\s*:'
    r'\s*"?(\d+)"?'
)


@dataclass
class WeeklySnapshot:
    row_id: int
    used_percent: int
    remaining_percent: int
    window_minutes: int | None
    secondary_reset_at: int | None
    limit_name: str
    source_ts: int | None = None


class CodexWeeklyReader:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._last_row_id = 0
        self._snapshot: WeeklySnapshot | None = None
        self._remote_snapshot: WeeklySnapshot | None = None
        self._last_history_scan_ts = 0.0
        self._last_usage_fetch_ts = 0.0

    @staticmethod
    def _parse_usage_payload(
        source_ts: int,
        payload: dict[str, object],
    ) -> WeeklySnapshot | None:
        rate_limit = payload.get("rate_limit")
        if not isinstance(rate_limit, dict):
            return None

        windows = (
            ("primary", rate_limit.get("primary_window")),
            ("secondary", rate_limit.get("secondary_window")),
        )
        for limit_name, window in windows:
            if not isinstance(window, dict):
                continue
            try:
                window_seconds = int(window.get("limit_window_seconds", 0))
                if window_seconds != WEEKLY_WINDOW_MINUTES * 60:
                    continue
                used = int(float(window["used_percent"]))
            except (KeyError, TypeError, ValueError):
                continue

            reset_at_value = window.get("reset_at")
            try:
                reset_at = int(reset_at_value) if reset_at_value else 0
            except (TypeError, ValueError):
                reset_at = 0
            if reset_at <= 0:
                try:
                    reset_after = int(window.get("reset_after_seconds", 0))
                except (TypeError, ValueError):
                    reset_after = 0
                reset_at = source_ts + max(0, reset_after)

            used = max(0, min(100, used))
            return WeeklySnapshot(
                row_id=0,
                used_percent=used,
                remaining_percent=100 - used,
                window_minutes=WEEKLY_WINDOW_MINUTES,
                secondary_reset_at=reset_at,
                limit_name=limit_name,
                source_ts=source_ts,
            )

        return None

    def _fetch_remote_usage(self, now: float) -> WeeklySnapshot | None:
        if now - self._last_usage_fetch_ts < USAGE_POLL_SECONDS:
            return None
        self._last_usage_fetch_ts = now

        try:
            auth = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
            tokens = auth.get("tokens", {})
            if not isinstance(tokens, dict):
                return None
            access_token = tokens.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                return None

            headers = {
                "Accept": "application/json",
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "CodexWeeklyWidget/1.0",
            }
            account_id = tokens.get("account_id")
            if account_id:
                headers["ChatGPT-Account-ID"] = str(account_id)

            request = urllib.request.Request(USAGE_URL, headers=headers, method="GET")
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read(256 * 1024).decode("utf-8"))
            if not isinstance(payload, dict):
                return None
            return self._parse_usage_payload(int(now), payload)
        except (
            OSError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
            urllib.error.URLError,
        ):
            # Keep the last valid local-log snapshot when the network or token
            # is unavailable.  The stale indicator prevents it looking live.
            return None

    @staticmethod
    def _parse_snapshot(row_id: int, body: str) -> WeeklySnapshot | None:
        # A log entry can include the prompt and tool output as well as the HTTP
        # response.  Only inspect actual ``headers={...}`` blocks, and prefer the
        # last one because it belongs to the completed response.
        normalized_body = body.replace(r'\"', '"')
        header_blocks = list(HEADER_BLOCK_RE.finditer(normalized_body))
        for match in reversed(header_blocks):
            fields: dict[str, dict[str, int]] = {"primary": {}, "secondary": {}}
            for limit_name, field_name, value in HEADER_INT_RE.findall(match.group(1)):
                fields[limit_name][field_name] = int(value)

            # New Codex versions expose the week as ``primary``; older versions
            # exposed a 5-hour primary window and the week as ``secondary``.
            for limit_name in ("primary", "secondary"):
                quota = fields[limit_name]
                if quota.get("window-minutes") != WEEKLY_WINDOW_MINUTES:
                    continue
                if "used-percent" not in quota or "reset-at" not in quota:
                    continue

                used = max(0, min(100, quota["used-percent"]))
                return WeeklySnapshot(
                    row_id=row_id,
                    used_percent=used,
                    remaining_percent=100 - used,
                    window_minutes=quota["window-minutes"],
                    secondary_reset_at=quota["reset-at"],
                    limit_name=limit_name,
                )

        return None

    @staticmethod
    def _query_rows(
        conn: sqlite3.Connection,
        last_row_id: int | None,
        limit: int,
    ) -> list[tuple[int, int, str]]:
        base = (
            "SELECT id, ts, feedback_log_body "
            "FROM logs "
            "WHERE feedback_log_body LIKE ? "
            "AND (feedback_log_body LIKE ? OR feedback_log_body LIKE ?) "
        )
        # Do not require the exact status text.  Codex changed it from
        # ``status=200`` to ``status=200 OK``.
        params: list[object] = [
            f"%{CODEX_RESPONSE_URL_MARKER}%",
            f"%{PRIMARY_USED_KEY}%",
            f"%{SECONDARY_USED_KEY}%",
        ]
        if last_row_id is not None:
            base += "AND id > ? "
            params.append(last_row_id)
        base += "ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(base, params).fetchall()
        return [(int(row[0]), int(row[1]), str(row[2])) for row in rows]

    @staticmethod
    def is_stale(snapshot: WeeklySnapshot | None, now_ts: int | None = None) -> bool:
        if snapshot is None or snapshot.source_ts is None:
            return True
        current_ts = int(time.time()) if now_ts is None else int(now_ts)
        return current_ts - snapshot.source_ts > STALE_AFTER_SECONDS

    @staticmethod
    def _rollover_if_expired(snapshot: WeeklySnapshot | None) -> WeeklySnapshot | None:
        if snapshot is None or snapshot.secondary_reset_at is None:
            return snapshot

        now_ts = int(time.time())
        reset_at = snapshot.secondary_reset_at
        if now_ts < reset_at:
            return snapshot

        window_minutes = snapshot.window_minutes or WEEKLY_WINDOW_MINUTES
        window_seconds = max(60, int(window_minutes) * 60)
        periods = ((now_ts - reset_at) // window_seconds) + 1
        next_reset_at = reset_at + periods * window_seconds

        return WeeklySnapshot(
            row_id=snapshot.row_id,
            used_percent=0,
            remaining_percent=100,
            window_minutes=window_minutes,
            secondary_reset_at=next_reset_at,
            limit_name=snapshot.limit_name,
            source_ts=snapshot.source_ts,
        )

    def _current_snapshot(self) -> WeeklySnapshot | None:
        self._snapshot = self._rollover_if_expired(self._snapshot)
        return self._snapshot

    def refresh(self) -> WeeklySnapshot | None:
        if not self.db_path.exists():
            remote_snapshot = self._fetch_remote_usage(time.time())
            if remote_snapshot is not None:
                self._remote_snapshot = remote_snapshot
                self._snapshot = remote_snapshot
            elif self._remote_snapshot is not None:
                self._snapshot = self._remote_snapshot
            return self._current_snapshot()

        uri = f"file:{self.db_path.as_posix()}?mode=ro"
        now = time.time()

        remote_snapshot = self._fetch_remote_usage(now)
        if remote_snapshot is not None:
            self._remote_snapshot = remote_snapshot
            self._snapshot = remote_snapshot
            return self._current_snapshot()
        if self._remote_snapshot is not None:
            # A throttled or failed remote poll must never let an older log
            # record overwrite the last authoritative usage response.
            self._snapshot = self._remote_snapshot
            return self._current_snapshot()

        try:
            with sqlite3.connect(uri, uri=True, timeout=1.0) as conn:
                # 1) Incremental scan: fast path
                new_rows = self._query_rows(conn, self._last_row_id, limit=160)
                for row_id, source_ts, body in new_rows:
                    snapshot = self._parse_snapshot(row_id, body)
                    if snapshot is not None:
                        self._snapshot = replace(snapshot, source_ts=source_ts)
                        self._last_row_id = max(self._last_row_id, row_id)
                        return self._current_snapshot()

                # 2) Periodic history fallback: handles situations where many
                # non-data rows appear above real rows in id ordering.
                if self._snapshot is None or (now - self._last_history_scan_ts) >= 30.0:
                    self._last_history_scan_ts = now
                    history_rows = self._query_rows(conn, None, limit=800)
                    for row_id, source_ts, body in history_rows:
                        snapshot = self._parse_snapshot(row_id, body)
                        if snapshot is not None:
                            self._snapshot = replace(snapshot, source_ts=source_ts)
                            self._last_row_id = max(self._last_row_id, row_id)
                            return self._current_snapshot()
        except sqlite3.Error:
            return self._current_snapshot()

        return self._current_snapshot()


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class APPBARDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT),
        ("rc", RECT),
        ("lParam", ctypes.c_long),
    ]


ABM_GETTASKBARPOS = 0x00000005
ABE_LEFT = 0
ABE_TOP = 1
ABE_RIGHT = 2
ABE_BOTTOM = 3

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040


def get_taskbar_rect() -> tuple[int, int, int, int, int] | None:
    abd = APPBARDATA()
    abd.cbSize = ctypes.sizeof(APPBARDATA)
    ok = ctypes.windll.shell32.SHAppBarMessage(ABM_GETTASKBARPOS, ctypes.byref(abd))
    if not ok:
        return None
    return (abd.rc.left, abd.rc.top, abd.rc.right, abd.rc.bottom, abd.uEdge)


class WeeklyWidget:
    def __init__(self, reader: CodexWeeklyReader) -> None:
        self.reader = reader
        self.root = tk.Tk()
        self.root.title("Codex Weekly")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 1.0)
        self.root.configure(bg=TRANSPARENT_KEY)
        try:
            self.root.attributes("-transparentcolor", TRANSPARENT_KEY)
        except tk.TclError:
            pass
        try:
            self.root.attributes("-toolwindow", True)
        except tk.TclError:
            pass

        self.frame = tk.Frame(
            self.root,
            bg=TRANSPARENT_KEY,
            bd=0,
            highlightthickness=0,
            padx=8,
            pady=6,
        )
        self.frame.pack(fill="both", expand=True)

        self.top_label = tk.Label(
            self.frame,
            text="--%",
            fg="#f3f3f3",
            bg=TRANSPARENT_KEY,
            font=("Segoe UI Semibold", 10),
            anchor="w",
        )
        self.top_label.pack(fill="x")

        self.bottom_label = tk.Label(
            self.frame,
            text="--/-- --:--",
            fg="#66dd66",
            bg=TRANSPARENT_KEY,
            font=("Segoe UI", 9),
            anchor="w",
        )
        self.bottom_label.pack(fill="x", pady=(2, 0))

        self._drag_origin: tuple[int, int] | None = None
        self._manual_position: tuple[int, int] | None = None
        self._hwnd = None

        self._bind_drag(self.root)
        self._bind_drag(self.frame)
        self._bind_drag(self.top_label)
        self._bind_drag(self.bottom_label)

        self.root.bind("<Button-3>", lambda _e: self.root.destroy())
        self.root.bind("<Map>", lambda _e: self._enforce_topmost())
        self.root.bind("<FocusOut>", lambda _e: self._enforce_topmost())

        self.root.update_idletasks()
        self._hwnd = int(self.root.winfo_id())
        self._enforce_topmost()

        self._tick()

    def _bind_drag(self, widget: tk.Misc) -> None:
        widget.bind("<Button-1>", self._start_move)
        widget.bind("<B1-Motion>", self._on_move)

    def _start_move(self, event: tk.Event) -> None:
        self._drag_origin = (event.x_root, event.y_root)
        self._manual_position = (self.root.winfo_x(), self.root.winfo_y())

    def _on_move(self, event: tk.Event) -> None:
        if not self._drag_origin or not self._manual_position:
            return
        dx = event.x_root - self._drag_origin[0]
        dy = event.y_root - self._drag_origin[1]
        x = self._manual_position[0] + dx
        y = self._manual_position[1] + dy
        self.root.geometry(f"+{x}+{y}")
        self._manual_position = (x, y)
        self._drag_origin = (event.x_root, event.y_root)

    def _auto_position(self) -> tuple[int, int]:
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()

        x = MARGIN_X
        y = screen_h - height - MARGIN_Y

        tb = get_taskbar_rect()
        if tb is not None:
            left, top, right, bottom, edge = tb
            tb_width = max(0, right - left)
            tb_height = max(0, bottom - top)
            if edge == ABE_BOTTOM:
                y = top + max(0, (tb_height - height) // 2)
            elif edge == ABE_TOP:
                y = top + max(0, (tb_height - height) // 2)
            elif edge == ABE_LEFT:
                x = left + max(0, (tb_width - width) // 2)
                y = screen_h - height - MARGIN_Y
            elif edge == ABE_RIGHT:
                x = left + max(0, (tb_width - width) // 2)
                y = screen_h - height - MARGIN_Y

        x = max(0, min(screen_w - width, x))
        y = max(0, min(screen_h - height, y))
        return x, y

    def _enforce_topmost(self) -> None:
        self.root.attributes("-topmost", True)
        if self._hwnd:
            ctypes.windll.user32.SetWindowPos(
                self._hwnd,
                HWND_TOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )

    @staticmethod
    def _format_reset_text(snapshot: WeeklySnapshot | None) -> str:
        if snapshot is None or snapshot.secondary_reset_at is None:
            return "--/-- --:--"
        dt = datetime.fromtimestamp(snapshot.secondary_reset_at, tz=timezone.utc).astimezone()
        return f"{dt.month}/{dt.day} {dt:%H:%M}"

    @staticmethod
    def _format_source_time(snapshot: WeeklySnapshot | None) -> str:
        if snapshot is None or snapshot.source_ts is None:
            return "未记录"
        dt = datetime.fromtimestamp(snapshot.source_ts, tz=timezone.utc).astimezone()
        return f"{dt.month}/{dt.day} {dt:%H:%M}"

    def _set_label(self, snapshot: WeeklySnapshot | None) -> None:
        if snapshot is None:
            self.top_label.config(text="--%", fg="#d9d9d9")
            self.bottom_label.config(text="--/-- --:--", fg="#66aa66")
            return

        if self.reader.is_stale(snapshot):
            # The local log is passive: without a fresh Codex response header,
            # the old percentage must not look like a live value.
            self.top_label.config(text="--%", fg="#d9d9d9")
            self.bottom_label.config(
                text=f"数据未刷新 · {self._format_source_time(snapshot)}",
                fg="#ffd166",
            )
            return

        rem = snapshot.remaining_percent
        if rem <= 15:
            color = "#ff6b6b"
        elif rem <= 35:
            color = "#ffd166"
        else:
            color = "#e9f7ef"
        self.top_label.config(text=f"{rem}%", fg=color)
        self.bottom_label.config(
            text=f"Reset {self._format_reset_text(snapshot)}  Update {self._format_source_time(snapshot)}",
            fg="#66dd66",
        )

    def _tick(self) -> None:
        snapshot = self.reader.refresh()
        self._set_label(snapshot)
        self._enforce_topmost()
        if self._manual_position is None:
            x, y = self._auto_position()
            self.root.geometry(f"+{x}+{y}")
        self.root.after(POLL_MS, self._tick)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    reader = CodexWeeklyReader(DB_PATH)
    widget = WeeklyWidget(reader)
    widget.run()


if __name__ == "__main__":
    main()
