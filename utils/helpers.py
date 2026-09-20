"""Small shared helpers: text normalisation, safe process launching."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading

# ── text ─────────────────────────────────────────────────────────
_APOSTROPHES = re.compile(r"[\u2019'`]")
_NON_WORD = re.compile(r"[^a-z0-9\s]")
_SPACES = re.compile(r"\s+")

_LEADING_FILLER = re.compile(
    r"^(?:hey syra|ok syra|okay syra|syra|please|can you|could you|would you|will you|"
    r"i want you to|i want to|i would like to|i need you to)\s+"
)
_TRAILING_FILLER = re.compile(r"\s+(?:please|for me|now|thanks|thank you)$")


def normalize(text: str) -> str:
    """Lowercase, drop apostrophes/punctuation and collapse spaces: "What's up?" -> "whats up"."""
    text = _APOSTROPHES.sub("", text.lower().strip())
    text = _NON_WORD.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def strip_command_fillers(text: str) -> str:
    """Normalise and remove politeness/addressing words: "Syra, could you open Chrome please" -> "open chrome"."""
    text = normalize(text)
    previous = None
    while previous != text:
        previous = text
        text = _LEADING_FILLER.sub("", text).strip()
        text = _TRAILING_FILLER.sub("", text).strip()
    return text


def slugify(text: str, separator: str = "-") -> str:
    """"My Portfolio Site!" -> "my-portfolio-site" (safe for folder names)."""
    return normalize(text).replace(" ", separator)


# ── processes ────────────────────────────────────────────────────
def clean_subprocess_env() -> dict[str, str]:
    """Environment for launching desktop apps from inside SYRA's virtualenv.

    Without this, apps inherit VIRTUAL_ENV / the venv's bin on PATH, which can
    break Python-based desktop tools that expect the system interpreter.
    """
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    venv_dirs = {os.path.normpath(os.path.join(p, "bin"))
                 for p in (env.pop("VIRTUAL_ENV", None),
                           sys.prefix if sys.prefix != sys.base_prefix else None) if p}
    if venv_dirs:
        parts = env.get("PATH", "").split(os.pathsep)
        env["PATH"] = os.pathsep.join(p for p in parts if p and os.path.normpath(p) not in venv_dirs)
    return env


def launch_detached(argv: list[str], settle_seconds: float = 0.5) -> tuple[bool, str]:
    """Start a GUI program fully detached from SYRA.

    Returns (ok, detail). A program that is still running after `settle_seconds`
    is considered launched; one that already exited with 0 (a launcher handing
    over to a running instance) is also fine; a non-zero exit is a failure.
    """
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            env=clean_subprocess_env(),
        )
    except FileNotFoundError:
        return False, f"program not found: {argv[0]}"
    except OSError as exc:
        return False, f"could not start {argv[0]}: {exc}"

    try:
        code = proc.wait(timeout=settle_seconds)
    except subprocess.TimeoutExpired:
        # Still running: reap it in the background later so it never becomes a zombie.
        threading.Thread(target=proc.wait, daemon=True).start()
        return True, ""
    if code == 0:
        return True, ""
    return False, f"{argv[0]} exited with code {code}"
