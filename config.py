"""
SYRA v2.0 - central configuration.

Everything you are likely to tweak lives in this one file.
Other modules read it with:  import config as cfg
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
HOME = Path.home()

# ─────────────────────────── Identity ────────────────────────────
ASSISTANT_NAME = "SYRA"
GREETING = "Hello Sir."
WAKE_REPLY = "Yes Sir."
GREET_ON_START = True

# ─────────────────────────── Wake word ───────────────────────────
# "<prefix> <name>" wakes SYRA. Speech recognizers often mis-spell an unusual
# name, so a word also counts as the name if it is in WAKE_NAME_ALIASES or is
# spelled similarly enough to WAKE_NAME (difflib ratio >= WAKE_FUZZY_CUTOFF).
WAKE_PREFIXES = ("wake up",)
WAKE_NAME = "syra"
WAKE_NAME_ALIASES = ("cyra", "sara", "saira", "syra",
                     "seera", "sayra", "cira", "ciara", "zyra")
WAKE_FUZZY_CUTOFF = 0.6

# ────────────────────────── Microphone ───────────────────────────
# None / "auto"  -> detect the best input device automatically (recommended)
# 7              -> use PortAudio device index 7 (see:  python main.py --list-mics)
# "pulse"        -> use the first input device whose name contains "pulse"
# Indexes can change between boots, so a name or "auto" is more robust.
# Override without editing this file:  python main.py --mic 7   or   SYRA_MIC=pulse
MIC_DEVICE_INDEX: int | str | None = None

# Auto-detect order (exact device names, case-insensitive). All three go through
# PipeWire's PulseAudio/ALSA compatibility layer, so GNOME sees SYRA as a normal
# audio client (and shows its microphone indicator). Direct "hw:" devices come last.
MIC_PREFERRED_NAMES = ("pulse", "pipewire", "default")

SAMPLE_RATE = 16000            # 16 kHz mono is what speech recognizers want
CHUNK_SIZE = 1024              # 64 ms per read at 16 kHz - low CPU, fine granularity
MIC_MAX_REOPEN_ATTEMPTS = 3    # stream reopen attempts before escalating to recovery
MIC_REOPEN_DELAY = 1.0         # seconds; multiplied by the attempt number
MIC_RECOVERY_INTERVAL = 5.0    # seconds between recovery attempts after a hard failure

# ───────────────────────── Speech recognition ────────────────────
RECOGNITION_LANGUAGE = "en-US"      # try "en-IN" / "en-GB" if your accent is misheard
RECOGNITION_TIMEOUT = 8.0           # network timeout for Google (library default is "forever")

ENERGY_THRESHOLD_INITIAL = 300      # start value; calibration refines it
ENERGY_THRESHOLD_MIN = 150          # never let the threshold drift below this
ENERGY_THRESHOLD_MAX = 3000         # ...or above this (protects against calibrating on speech)
DYNAMIC_ENERGY_THRESHOLD = True     # follow slow changes in room noise
DYNAMIC_ENERGY_RATIO = 1.5          # threshold = ambient * 1.5
DYNAMIC_ENERGY_DAMPING = 0.15       # how fast the threshold follows the noise

PAUSE_THRESHOLD = 0.7               # silence that ends a phrase (library default 0.8)
PHRASE_THRESHOLD = 0.25             # ignore "speech" shorter than this: clicks, coughs
NON_SPEAKING_DURATION = 0.4         # silence kept around a phrase; must be <= PAUSE_THRESHOLD

LISTEN_SLICE_ACTIVE = 1.0           # ACTIVE: listen in 1 s slices so typed commands stay responsive
LISTEN_SLICE_IDLE = 3.0             # IDLE: nothing else to poll, so longer slices
WAKE_PHRASE_TIME_LIMIT = 4.0        # "wake up syra" is short
COMMAND_PHRASE_TIME_LIMIT = 10.0    # longest spoken command

CALIBRATION_SECONDS_STARTUP = 1.0   # once at start (needed for wake-word detection)
CALIBRATION_SECONDS_ACTIVE = 0.6    # once each time SYRA wakes up
POST_SPEECH_DELAY = 0.3             # let the room echo die before listening again
SPEECH_MAX_WAIT_SECONDS = 60.0      # safety: never wait for TTS longer than this

# ───────────────────── Session behaviour ─────────────────────────
ACTIVE_TIMEOUT_SECONDS = 120        # back to IDLE after this long without a command (0 = never)
NO_SPEECH_LOG_SECONDS = 10.0        # log "No voice detected." after this much silence
UNCLEAR_HINT_AFTER = 3              # after N failed recognitions in a row, say ONE gentle hint (0 = never)

# ─────────────────────────── Speech output ───────────────────────
TTS_ENABLED = True
TTS_LANG = "en"
TTS_TLD = "com"                     # accent: "com" US, "co.uk" UK, "com.au", "co.in" ...
TTS_SLOW = False
TTS_CACHE_ENABLED = True            # cache SHORT phrases so "Yes Sir." is instant
TTS_CACHE_DIR = HOME / ".cache" / "syra" / "tts"
TTS_CACHE_MAX_FILES = 300
TTS_CACHE_MAX_CHARS = 90            # only phrases up to this length are cached
# If gTTS mispronounces a word, map it to a spelling it reads better, e.g.
# {"SYRA": "Seera"}. Terminal text is never changed - only what is spoken.
TTS_PRONUNCIATIONS: dict[str, str] = {}
MPG123_OUTPUT = "auto"              # "auto" (prefers pulse/PipeWire), "pulse", "alsa", or None

# ───────────────────────── Popup terminal ────────────────────────
POPUP_ENABLED = True
POPUP_TITLE = "SYRA"
POPUP_GEOMETRY = "100x30"
CONSOLE_SOCKET = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "syra_console.sock"

# ─────────────────── Application registry (voice: "open X") ─────────
# key      -> spoken canonical name (also an alias)
# display  -> what SYRA says/prints
# commands -> executables tried in order; the first one found on PATH is used
# aliases  -> other things you might say
# Anything NOT listed here is still found through the .desktop-file discovery.
APPLICATIONS: dict[str, dict] = {
    "visual studio code": {
        "display": "Visual Studio Code",
        "commands": ["code"],
        "aliases": ["vs code", "vscode", "v s code", "code editor", "visual studio", "code"],
    },
    "chrome": {
        "display": "Chrome",
        "commands": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"],
        "aliases": ["google chrome", "chrome browser"],
    },
    "firefox": {
        "display": "Firefox",
        "commands": ["firefox"],
        "aliases": ["fire fox", "mozilla firefox"],
    },
    "terminal": {
        "display": "Terminal",
        "commands": ["gnome-terminal", "x-terminal-emulator"],
        "aliases": ["gnome terminal", "command line", "console", "shell"],
    },
    "files": {
        "display": "Files",
        "commands": ["nautilus"],
        "aliases": ["file manager", "file explorer", "nautilus", "file", "files"],
    },
    "settings": {
        "display": "Settings",
        "commands": ["gnome-control-center"],
        "aliases": ["system settings", "control center", "gnome settings"],
    },
    "calculator": {
        "display": "Calculator",
        "commands": ["gnome-calculator"],
        "aliases": ["calc"],
    },
    "text editor": {
        "display": "Text Editor",
        "commands": ["gnome-text-editor", "gedit"],
        "aliases": ["notepad", "gedit"],
    },
    "system monitor": {
        "display": "System Monitor",
        "commands": ["gnome-system-monitor"],
        "aliases": ["task manager", "resource monitor"],
    },
}

# ───────────────────── Application closing ───────────────────────
# Voice commands:
#   "close VS Code"
#   "quit Firefox"
#   "exit Chrome"
#
# SYRA only uses these fixed process names.
# Spoken text is NEVER passed directly to a shell.

CLOSE_TARGETS: dict[str, dict] = {
    "visual studio code": {
        "display": "Visual Studio Code",
        "process_names": ["code"],
        "aliases": [
            "vs code",
            "vscode",
            "v s code",
            "code editor",
            "visual studio",
            "code",
        ],
    },

    "chrome": {
        "display": "Chrome",
        "process_names": [
            "chrome",
            "google-chrome",
            "google-chrome-stable",
        ],
        "aliases": [
            "google chrome",
            "chrome browser",
        ],
    },

    "firefox": {
        "display": "Firefox",
        "process_names": [
            "firefox",
        ],
        "aliases": [
            "fire fox",
            "mozilla firefox",
        ],
    },

    "terminal": {
        "display": "Terminal",
        "process_names": [
            "gnome-terminal-server",
        ],
        "aliases": [
            "gnome terminal",
            "command line",
            "console",
            "shell",
        ],
    },

    "files": {
        "display": "Files",
        "process_names": [
            "nautilus",
        ],
        "aliases": [
            "file manager",
            "file explorer",
            "nautilus",
            "file",
        ],
    },

    "settings": {
        "display": "Settings",
        "process_names": [
            "gnome-control-center",
        ],
        "aliases": [
            "system settings",
            "control center",
            "gnome settings",
        ],
    },

    "calculator": {
        "display": "Calculator",
        "process_names": [
            "gnome-calculator",
        ],
        "aliases": [
            "calc",
        ],
    },

    "text editor": {
        "display": "Text Editor",
        "process_names": [
            "gnome-text-editor",
            "gedit",
        ],
        "aliases": [
            "notepad",
            "gedit",
        ],
    },

    "system monitor": {
        "display": "System Monitor",
        "process_names": [
            "gnome-system-monitor",
        ],
        "aliases": [
            "task manager",
            "resource monitor",
        ],
    },
}

# ───────────────────── Web shortcuts (voice: "open X") ───────────
# Opened in the system default browser via a plain URL - SYRA does not start
# a browser process of its own.
WEB_SITES: dict[str, dict] = {
    "youtube": {"display": "YouTube", "url": "https://www.youtube.com", "aliases": ["you tube"]},
    "facebook": {"display": "Facebook", "url": "https://www.facebook.com", "aliases": ["face book"]},
    "linkedin": {"display": "LinkedIn", "url": "https://www.linkedin.com", "aliases": ["linked in"]},
    "whatsapp": {"display": "WhatsApp", "url": "https://web.whatsapp.com",
                 "aliases": ["whats app", "whatsapp web"]},
    "google": {"display": "Google", "url": "https://www.google.com", "aliases": []},
    "gmail": {"display": "Gmail", "url": "https://mail.google.com", "aliases": ["g mail", "email", "mail"]},
    "github": {"display": "GitHub", "url": "https://github.com", "aliases": ["git hub"]},
}


# ───────────────────────── Information ───────────────────────────
WIKI_LANGUAGE = "en"
WIKI_SENTENCES = 2                  # spoken summary length
WIKI_TIMEOUT = 10.0                 # the wikipedia package has no timeout of its own

# ───────────────────────── Development ───────────────────────────
PROJECTS_DIR = HOME / "Documents" / "vscode" / "Project"
WEBPAGE_TEMPLATE_DIR = BASE_DIR / "templates" / "webpage_architecture"
NEW_PROJECT_TEMPLATE_DIR = BASE_DIR / "templates" / "new_project"

# ──────────────── Restricted command execution (OFF) ─────────────
# SECURITY BOUNDARY: SYRA never runs free-form shell text. Even when enabled,
# only the fixed argv lists below can run - spoken words select a NAME, they
# are never passed to a shell. Keep disabled until you need it.
SHELL_ENABLED = False
SHELL_ALLOWED_COMMANDS: dict[str, list[str]] = {
    "disk usage": ["df", "-h", "/"],
    "memory usage": ["free", "-h"],
    "uptime": ["uptime", "-p"],
}
SHELL_TIMEOUT_SECONDS = 10
SHELL_MAX_OUTPUT_CHARS = 400

# ─────────────────────────── Logging ─────────────────────────────
DEBUG = False
LOG_FILE = BASE_DIR / "logs" / "syra.log"
