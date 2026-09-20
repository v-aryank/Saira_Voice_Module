"""Audio-environment helpers: silence backend chatter, detect the sound server.

ALSA and JACK print warnings straight from C code to file descriptor 2, which
Python's logging cannot intercept. We install their official error callbacks
(so the noise never appears) and, for the brief moments PortAudio probes
devices, also point fd 2 at /dev/null. Python exceptions and tracebacks are
never hidden - only those two libraries' diagnostics.
"""
from __future__ import annotations

import contextlib
import ctypes
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterator

_keepalive: list[object] = []   # ctypes callbacks must outlive the C library's use of them
_silenced = False


def silence_backend_noise() -> None:
    """Install no-op ALSA/JACK message handlers. Safe to call more than once."""
    global _silenced
    if _silenced:
        return
    _silenced = True
    os.environ.setdefault("JACK_NO_START_SERVER", "1")   # never auto-start a JACK server

    try:
        asound = ctypes.cdll.LoadLibrary("libasound.so.2")
        alsa_handler_t = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int,
                                          ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p)
        alsa_handler = alsa_handler_t(lambda filename, line, function, err, fmt: None)
        _keepalive.append(alsa_handler)
        asound.snd_lib_error_set_handler(alsa_handler)
    except (OSError, AttributeError):
        pass   # libasound not present: nothing to silence

    try:
        jack = ctypes.cdll.LoadLibrary("libjack.so.0")
        jack_handler_t = ctypes.CFUNCTYPE(None, ctypes.c_char_p)
        jack_handler = jack_handler_t(lambda message: None)
        _keepalive.append(jack_handler)
        jack.jack_set_error_function(jack_handler)
        jack.jack_set_info_function(jack_handler)
    except (OSError, AttributeError):
        pass   # libjack not present: nothing to silence


@contextlib.contextmanager
def suppress_c_stderr() -> Iterator[None]:
    """Temporarily send file descriptor 2 to /dev/null (for PortAudio device probing)."""
    try:
        sys.stderr.flush()
    except (OSError, ValueError, AttributeError):
        pass
    try:
        saved = os.dup(2)
        devnull = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        yield
        return
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)
        os.close(devnull)


@dataclass(frozen=True)
class AudioSystemInfo:
    server: str            # e.g. "PulseAudio (on PipeWire 1.0.5)"
    default_source: str    # the system's default microphone, as chosen in GNOME Settings > Sound
    is_pipewire: bool


def detect_audio_system() -> AudioSystemInfo:
    """Ask the sound server what it is (one cheap `pactl info` call at startup)."""
    pactl = shutil.which("pactl")
    if pactl is None:
        return AudioSystemInfo("unknown (pactl not installed)", "", False)
    env = {**os.environ, "LC_ALL": "C"}
    try:
        out = subprocess.run([pactl, "info"], capture_output=True, text=True,
                             timeout=3, check=False, env=env).stdout
    except (OSError, subprocess.SubprocessError):
        return AudioSystemInfo("unknown (pactl failed)", "", False)
    server = source = ""
    for line in out.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "Server Name":
            server = value.strip()
        elif key.strip() == "Default Source":
            source = value.strip()
    if not server:
        return AudioSystemInfo("no PulseAudio/PipeWire server reachable", "", False)
    return AudioSystemInfo(server, source, "pipewire" in server.lower())
