"""Terminal logger for SYRA.

Terminal output stays clean and human-readable ([SYRA] / [YOU] / [WARN] ...),
while a rotating file log keeps full detail (including tracebacks) for diagnosis.
Output can also be mirrored to "sinks" - the popup console registers as one.
"""
from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Optional

Sink = Callable[[str, str], None]   # (kind, message)

_RESET = "\033[0m"
_COLORS = {"syra": "\033[96m", "you": "\033[92m", "warn": "\033[93m",
           "error": "\033[91m", "debug": "\033[90m"}
_TAGS = {"syra": "[SYRA]", "you": "[YOU]", "warn": "[WARN]",
         "error": "[ERROR]", "debug": "[DEBUG]"}
_LEVELS = {"syra": logging.INFO, "you": logging.INFO, "warn": logging.WARNING,
           "error": logging.ERROR, "debug": logging.DEBUG}


class SyraLogger:
    """Thread-safe logger with a terminal view, a file log and optional sinks."""

    def __init__(self, debug: bool = False, log_file: Optional[Path] = None) -> None:
        self.debug_enabled = debug
        self._lock = threading.Lock()
        self._sinks: list[Sink] = []
        self._use_color = sys.stdout.isatty()
        self._file_logger = self._build_file_logger(log_file)

    @staticmethod
    def _build_file_logger(path: Optional[Path]) -> logging.Logger:
        logger = logging.getLogger("syra")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        if logger.handlers:
            return logger
        handler: logging.Handler = logging.NullHandler()
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                handler = RotatingFileHandler(path, maxBytes=512_000, backupCount=3, encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
            except OSError:
                handler = logging.NullHandler()   # file logging is optional
        logger.addHandler(handler)
        return logger

    # ── sinks ────────────────────────────────────────────────────
    def add_sink(self, sink: Sink) -> None:
        self._sinks.append(sink)

    # ── public API ───────────────────────────────────────────────
    def syra(self, message: str) -> None:
        self._emit("syra", message)

    def you(self, message: str, broadcast: bool = True) -> None:
        """What the user said/typed. Typed text is not re-broadcast: the popup already shows it."""
        self._emit("you", message, broadcast=broadcast)

    def warn(self, message: str) -> None:
        self._emit("warn", message)

    def error(self, message: str, exc_info: bool = False) -> None:
        self._emit("error", message, exc_info=exc_info)

    def debug(self, message: str) -> None:
        self._emit("debug", message)

    # ── internals ────────────────────────────────────────────────
    def _emit(self, kind: str, message: str, *, broadcast: bool = True, exc_info: bool = False) -> None:
        tag = _TAGS[kind]
        self._file_logger.log(_LEVELS[kind], "%s %s", tag, message, exc_info=exc_info)
        if kind == "debug" and not self.debug_enabled:
            return
        if self._use_color:
            line = f"{_COLORS[kind]}{tag}{_RESET} {message}"
        else:
            line = f"{tag} {message}"
        with self._lock:
            print(line, flush=True)
        if broadcast and kind != "debug":
            for sink in list(self._sinks):
                sink(kind, message)
