#!/usr/bin/env python3
"""SAIRA popup console (client side). Standard library only.

Shows SAIRA's conversation live and lets you type commands. Closing this window
(or pressing Ctrl+D / Ctrl+C) puts SAIRA back to sleep.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time

try:
    import readline  # noqa: F401  (line editing + lets us redraw the prompt)
except ImportError:
    readline = None  # type: ignore[assignment]

RESET = "\033[0m"
COLORS = {"saira": "\033[96m", "you": "\033[92m", "warn": "\033[93m", "error": "\033[91m"}
TAGS = {"saira": "[SAIRA]", "you": "[YOU]", "warn": "[WARN]", "error": "[ERROR]"}
PROMPT = "[YOU] > "

_out_lock = threading.Lock()


def emit(kind: str, text: str) -> None:
    """Print a line above the input prompt without destroying what the user is typing."""
    tag = TAGS.get(kind, "[SAIRA]")
    color = COLORS.get(kind, "")
    typed = readline.get_line_buffer() if readline else ""
    with _out_lock:
        sys.stdout.write(f"\r\033[K{color}{tag}{RESET} {text}\n{PROMPT}{typed}")
        sys.stdout.flush()


def receive_loop(conn: socket.socket) -> None:
    try:
        with conn.makefile("r", encoding="utf-8", errors="replace", newline="\n") as reader:
            for line in reader:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if message.get("type") == "line":
                    emit(str(message.get("kind", "saira")), str(message.get("text", "")))
                elif message.get("type") == "quit":
                    break
    except OSError:
        pass
    os._exit(0)     # assistant went to sleep or exited: close this window


def connect(path: str) -> socket.socket:
    deadline = time.monotonic() + 8
    while True:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            conn.connect(path)
            return conn
        except OSError:
            conn.close()
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)


def main() -> int:
    parser = argparse.ArgumentParser(description="SAIRA popup console")
    parser.add_argument("--socket", required=True)
    parser.add_argument("--title", default="SAIRA")
    args = parser.parse_args()

    sys.stdout.write(f"\033]0;{args.title}\007")
    try:
        conn = connect(args.socket)
    except OSError as exc:
        print(f"Could not reach SAIRA ({exc}). Is the assistant running?")
        time.sleep(4)
        return 1

    print("Saira console - type a command, or just speak. Ctrl+D closes this window and puts Saira to sleep.\n")
    threading.Thread(target=receive_loop, args=(conn,), daemon=True).start()

    while True:
        try:
            line = input(PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        try:
            conn.sendall((json.dumps({"type": "text", "text": line}) + "\n").encode("utf-8"))
        except OSError:
            break
    try:
        conn.close()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
