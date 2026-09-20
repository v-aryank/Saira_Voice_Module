"""SYRA's popup terminal: server side (runs inside the assistant process).

The popup is a separate terminal window running ui/console_client.py, connected
to the assistant over a private Unix socket (mode 0600, in $XDG_RUNTIME_DIR).
    assistant -> popup : every [SYRA]/[YOU]/[WARN] line, as JSON lines
    popup -> assistant : commands the user types
If the user closes the popup, the socket drops and SYRA is told, so it can go
back to IDLE ("terminal closed").
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

import config as cfg
from utils.helpers import launch_detached
from utils.logger import SyraLogger

_CLIENT_SCRIPT = Path(__file__).with_name("console_client.py")


class ConsoleServer:
    def __init__(self, log: SyraLogger, on_text: Callable[[str], None],
                 on_disconnect: Callable[[], None]) -> None:
        self._log = log
        self._on_text = on_text
        self._on_disconnect = on_disconnect
        self._path = Path(cfg.CONSOLE_SOCKET)
        self._server: Optional[socket.socket] = None
        self._client: Optional[socket.socket] = None
        self._lock = threading.Lock()          # guards _client / _closing
        self._send_lock = threading.Lock()     # serialises writes to the popup
        self._backlog: deque[dict] = deque(maxlen=200)
        self._closing = False          # True while WE are closing the popup (not a user action)
        self._stopped = False
        self._spawned_at = 0.0

    # ── lifecycle ────────────────────────────────────────────────
    def start(self) -> bool:
        if not cfg.POPUP_ENABLED:
            self._log.debug("Popup terminal disabled in config.")
            return False
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if self._path.exists() or self._path.is_socket():
                self._path.unlink()
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(self._path))
            os.chmod(self._path, 0o600)
            server.listen(1)
        except OSError as exc:
            self._log.warn(f"Popup terminal unavailable: {exc}")
            return False
        self._server = server
        threading.Thread(target=self._accept_loop, name="syra-console-accept", daemon=True).start()
        return True

    def stop(self) -> None:
        self._stopped = True
        self.close_window()
        server, self._server = self._server, None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        try:
            self._path.unlink()
        except OSError:
            pass

    @property
    def connected(self) -> bool:
        return self._client is not None

    # ── session control ──────────────────────────────────────────
    def begin_session(self) -> None:
        """Forget the previous session's lines so a new popup starts clean."""
        self._backlog.clear()

    def open_window(self) -> bool:
        """Open the popup terminal if it is not already open. Never blocks on the window."""
        if self._server is None:
            return False
        if self._client is not None or time.monotonic() - self._spawned_at < 3.0:
            return True
        self._spawned_at = time.monotonic()
        inner = [sys.executable, str(_CLIENT_SCRIPT), "--socket", str(self._path), "--title", cfg.POPUP_TITLE]
        launchers = [
            ("gnome-terminal", ["--title", cfg.POPUP_TITLE, "--geometry", cfg.POPUP_GEOMETRY, "--"]),
            ("x-terminal-emulator", ["-T", cfg.POPUP_TITLE, "-e"]),
            ("xterm", ["-T", cfg.POPUP_TITLE, "-geometry", cfg.POPUP_GEOMETRY, "-e"]),
        ]
        for name, args in launchers:
            exe = shutil.which(name)
            if exe is None:
                continue
            ok, detail = launch_detached([exe, *args, *inner], settle_seconds=0.4)
            if ok:
                return True
            self._log.warn(f"Popup terminal via {name} failed: {detail}")
        self._log.warn("No terminal emulator could be opened; continuing with voice only.")
        return False

    def close_window(self) -> None:
        """Ask the popup to exit (used when SYRA goes to sleep)."""
        with self._lock:
            client = self._client
            if client is not None:
                self._closing = True
        if client is None:
            return
        self._send(client, {"type": "quit"})
        try:
            client.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    # ── output ───────────────────────────────────────────────────
    def broadcast(self, kind: str, text: str) -> None:
        """Logger sink: mirror a line to the popup (and remember it for a popup opening a moment later)."""
        message = {"type": "line", "kind": kind, "text": text}
        self._backlog.append(message)
        client = self._client
        if client is not None:
            self._send(client, message)

    def _send(self, conn: socket.socket, message: dict) -> None:
        data = (json.dumps(message) + "\n").encode("utf-8")
        with self._send_lock:
            try:
                conn.sendall(data)
            except OSError:
                pass    # the read loop notices the broken connection and cleans up

    # ── connection handling ──────────────────────────────────────
    def _accept_loop(self) -> None:
        while not self._stopped and self._server is not None:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            with self._lock:
                old, self._client = self._client, conn
                self._closing = False
            if old is not None:
                try:
                    old.close()
                except OSError:
                    pass
            for message in list(self._backlog):
                self._send(conn, message)
            threading.Thread(target=self._read_loop, args=(conn,), name="syra-console-read",
                             daemon=True).start()

    def _read_loop(self, conn: socket.socket) -> None:
        try:
            with conn.makefile("r", encoding="utf-8", errors="replace", newline="\n") as reader:
                for line in reader:
                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if message.get("type") == "text":
                        text = str(message.get("text", "")).strip()
                        if text:
                            self._on_text(text)
        except OSError:
            pass
        finally:
            self._detach(conn)

    def _detach(self, conn: socket.socket) -> None:
        with self._lock:
            was_current = self._client is conn
            intentional = self._closing
            if was_current:
                self._client = None
        try:
            conn.close()
        except OSError:
            pass
        if was_current and not intentional and not self._stopped:
            self._log.debug("Popup terminal disconnected.")
            self._on_disconnect()
