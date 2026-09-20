# SYRA v2.0 — Desktop Voice Assistant

Ubuntu 24.04 · GNOME · Wayland · PipeWire · Python 3.12

---

## 1. Project structure

```
SYRA_4.0/
├── main.py                  entry point, CLI flags (--list-mics, --test-mic, --mic, --debug)
├── config.py                ALL settings: mic, timings, apps, websites, paths, voice
├── requirements.txt
├── core/
│   ├── assistant.py         state machine + listen → route → speak loop, recovery
│   ├── command_router.py    regex → handler routing, safe dispatch
│   ├── state.py             IDLE / ACTIVE / PROCESSING / SPEAKING / ERROR
│   └── wake_word.py         "wake up Syra" matching (tolerant to mis-heard names)
├── audio/
│   ├── microphone.py        device selection, persistent stream, calibration, recognition
│   ├── speaker.py           gTTS + mpg123 pipeline, phrase cache, temp-file cleanup
│   ├── audio_env.py         ALSA/JACK noise suppression, PipeWire detection
│   └── diagnostics.py       --list-mics / --test-mic
├── commands/
│   ├── system.py            time, date, sleep, shutdown
│   ├── applications.py      "open X": registry → websites → .desktop discovery
│   ├── app_discovery.py     universal app launcher (reads .desktop files)
│   ├── web.py               open URLs in the default browser
│   ├── wiki.py              Wikipedia (named wiki.py so it never shadows the `wikipedia` package)
│   ├── development.py       "create a webpage architecture"
│   └── shell.py             restricted command execution (DISABLED by default)
├── ui/
│   ├── console_server.py    popup-terminal server (inside SYRA)
│   └── console_client.py    the popup terminal window itself (stdlib only)
├── utils/  logger.py, helpers.py
├── templates/webpage_architecture/   ← put YOUR template files here
└── tests/smoke_test.py      offline tests (no mic/internet needed)
```

## 2. Install

```bash
# System packages
sudo apt update
sudo apt install -y python3-venv python3-dev build-essential portaudio19-dev \
    libasound2-dev libasound2-plugins pipewire-alsa pulseaudio-utils \
    mpg123 flac gnome-terminal libglib2.0-bin

# Python packages into your existing venv
cd ~/#Applications/SYRA
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install -r SYRA_4.0/requirements.txt
```

Optional: `code` (VS Code) must be on PATH for "Open VS Code" (`code --version` should work).

## 3. Run

Run from a terminal **inside your GNOME session** (it needs the desktop environment variables to open windows).

```bash
cd ~/Documents/vscode/Assistant/SYRA_4.0
../venv/bin/python main.py
```

Useful flags: `--debug` (verbose), `--no-popup`, `--mute`, `--mic 7`.

**Microphone test**

```bash
../venv/bin/python main.py --list-mics    # shows devices and the order SYRA will try them
../venv/bin/python main.py --test-mic     # calibrates, then 3 real recognition tests
```

Offline self-test: `../venv/bin/python -m unittest tests.smoke_test -v`

## 4. Changing the microphone

Default is **auto-detect**: SYRA tries `pulse` → `pipewire` → `default` → other devices → raw `hw:` devices, and *really opens* each one before accepting it.

| Way | How |
|---|---|
| Permanent | `config.py`: `MIC_DEVICE_INDEX = 7` (index) or `"pulse"` (name) or `None` (auto) |
| One run | `python main.py --mic 7` |
| Environment | `SYRA_MIC=pulse python main.py` |

Indexes can change between boots — a **name** or **auto** is safer than a number.

Which *physical* microphone is used when SYRA opens `pulse`/`pipewire`/`default` is decided by GNOME: **Settings → Sound → Input** (or `pactl set-default-source <name>`; list with `pactl list short sources`).

## 5. Adding a voice command 

Create a handler and register a regex. The regex must match the **whole** normalised sentence (lowercase, no punctuation, apostrophes removed: "what's" → "whats"; fillers like "please" are already stripped).

```python
# commands/volume.py
import re, subprocess
from core.command_router import CommandContext, CommandRouter

def register(router: CommandRouter, log) -> None:
    def volume_up(match: re.Match, ctx: CommandContext) -> str:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%"], check=False)
        return "Volume increased."
    router.register("volume_up", [r"(?:volume up|increase (?:the )?volume|louder)"], volume_up)
```

Then in `commands/__init__.py` add `from commands import volume` and `volume.register(router, log)` (order matters: first match wins).

Handler return values: a string (SYRA says it) · `None` (you already called `ctx.say`) · `CommandResult(reply=..., action=Action.SLEEP)`. Raise `CommandError("message")` for a friendly failure. Use `ctx.ask("question?")` to ask a follow-up (works by voice or typing).

To add an **application or website** you do not need code — add an entry to `APPLICATIONS` / `WEB_SITES` in `config.py`.

## 6. How the wake-word state machine works / স্টেট মেশিন

```
IDLE ──"wake up Syra"──▶ ACTIVE ──speech or typed text──▶ PROCESSING ──▶ SPEAKING ──▶ ACTIVE
  ▲                                                                                   │
  └──── "go to sleep" / "quiet" / "sleep Syra" / "stop listening" / popup closed ─────┘
```

* **IDLE** – only listens for `wake up <name>` (name matched loosely: "Sarah", "Sierra"… count). Silent on noise.
* Wake → popup terminal opens → "Yes Sir." → ambient noise calibrated **once** → **ACTIVE**.
* **ACTIVE** – listens in 1-second slices (so typed commands are picked up quickly), or takes text typed in the popup.
* **PROCESSING** – the router runs the command. **SPEAKING** – the mic is **not read** while SYRA talks; afterwards the input buffer is flushed so SYRA never hears itself.
* **ERROR** – microphone could not be recovered: SYRA logs it, says so once, and retries every 5 s. It never crashes.
* Auto-sleep after 120 s of no commands (`ACTIVE_TIMEOUT_SECONDS`).
* "quit" and "quite" mean *quiet* (recognizers confuse them) — they only sleep. Shutting down needs the name: **"exit Syra"**.


## 7. Recognition settings and why

| Setting | Value | Why |
|---|---|---|
| `SAMPLE_RATE` | 16000 Hz mono | What speech recognizers use; low CPU |
| `CHUNK_SIZE` | 1024 (64 ms) | Fine enough to detect speech start, few wake-ups |
| `PAUSE_THRESHOLD` | 0.7 s | Ends a phrase quickly but does not cut "open … visual studio code" mid-thought (library default 0.8) |
| `PHRASE_THRESHOLD` | 0.25 s | Ignores clicks/coughs shorter than this |
| `NON_SPEAKING_DURATION` | 0.4 s | Keeps a little silence around the phrase so word edges are not clipped (must be ≤ pause) |
| `DYNAMIC_ENERGY_THRESHOLD` | on, ratio 1.5, clamped 150–3000 | Follows room noise, but can never drift to "hears everything" or "hears nothing" |
| listen slice | 3 s idle / 1 s active | Long idle slices = fewer restarts; short active slices = typed commands answered fast |
| `WAKE_PHRASE_TIME_LIMIT` | 4 s | "Wake up Syra" is short; bounds what is sent to Google |
| `COMMAND_PHRASE_TIME_LIMIT` | 10 s | Longest sensible spoken command |
| `RECOGNITION_TIMEOUT` | 8 s | The library default is "wait forever" — a dead network must not freeze SYRA |
| Calibration | 1 s at start, 0.6 s per wake | Once per activation, not per command; result is rejected if it is absurdly high (you were talking) |

Log messages are distinct: `No voice detected.` · `Could not understand.` · `Recognition service unavailable.` None of them is spoken each time. After 3 failures in a row SYRA says **one** hint; a service outage is announced **once** until it recovers.

## 8. The GNOME microphone indicator

GNOME's top-bar indicator appears when an application holds an **active recording stream on PipeWire**. SYRA opens the ALSA `pulse`/`pipewire` device, which makes it an ordinary PipeWire client, so the indicator appears — no faking. Direct `hw:1,0` access bypasses PipeWire, does **not** show it, and is what caused your ALSA conflicts, so it is only a last resort.

Because the stream stays open (that is what makes it reliable), the indicator stays on **while SYRA is running, including IDLE** — IDLE must listen for the wake phrase. That is honest: the microphone really is open. To stop it, stop SYRA (or say "exit Syra").

Privacy note: in IDLE and ACTIVE, speech is sent to Google Speech Recognition. An offline wake-word engine (see below) would remove this for IDLE.

## 9. Future integration points

| Goal | Where to plug in |
|---|---|
| **Universal app launching** | Already there: `commands/app_discovery.py` (any installed app by name). Improve matching there; "refresh application list" re-scans. |
| **Webpage generation** | `commands/development.py` → `create_webpage_architecture()`. Drop your template into `templates/webpage_architecture/`. To ask for a custom location, add another `ctx.ask(...)` before `target = ...`. |
| **VS Code control** | New `commands/vscode.py`; use the `code` CLI (`code -r <folder>`, `code --goto file:line`, `code --install-extension`). |
| **ESP32 / robotics** | New `commands/robotics.py` using `pyserial` or MQTT/HTTP. Keep hardware behind a small class with an explicit allow-list of actions, like `commands/shell.py`. |
| **AI / LLM** | Add a fallback at the end of `CommandRouter.dispatch`: when no route matches (`handled=False`), call your LLM. Keep it out of the mic/state code. |
| **Better voice** | `Speaker._synthesize()` is the only place that knows about gTTS. Swap it for `edge-tts` (neural voices) or Piper (offline) without touching anything else. |
| **Offline wake word** | Replace `mic.listen` + `match_wake_phrase` in `Assistant._idle_step` with openWakeWord/Porcupine/Vosk. |
| **Restricted shell** | `SHELL_ENABLED = True` + entries in `SHELL_ALLOWED_COMMANDS`. Spoken words only choose a *name*; never a shell string. |

## 10. Troubleshooting

* No speech, `mpg123` errors → `sudo apt install mpg123`; test `mpg123 -q /path/any.mp3`.
* "Microphone problem: PyAudio…" → re-run the apt + pip commands in section 2.
* Wrong microphone → section 4, or GNOME Settings → Sound → Input.
* Name misheard → `WAKE_NAME_ALIASES` in `config.py`; check what was heard with `python main.py --debug`.
* Name mispronounced by TTS → `TTS_PRONUNCIATIONS = {"SYRA": "<spelling that sounds right>"}` (terminal text stays "SYRA").
* Detailed errors → `logs/syra.log` (tracebacks are written there, not to the terminal).

## 11. Executing application:

bash <<'INSTALL'
set -euo pipefail
APP_DIR="$HOME/#Applications/SYRA/SYRA_v2.0"

# 1) launcher script (works from anywhere, single instance, logs when there is no terminal)
cat > "$APP_DIR/run_syra.sh" <<'EOF'
#!/usr/bin/env bash
# Start / stop SYRA from anywhere.   run_syra.sh [--stop] [main.py options]
SCRIPT="$(readlink -f "${BASH_SOURCE[0]}")"
APP_DIR="$(dirname "$SCRIPT")"
PYTHON="$APP_DIR/../venv/bin/python"
RUN_DIR="${XDG_RUNTIME_DIR:-/tmp}"
PIDFILE="$RUN_DIR/syra.pid"

if [ "${1:-}" = "--stop" ]; then
    pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [ -n "$pid" ] && [ -r "/proc/$pid/cmdline" ] && tr '\0' ' ' < "/proc/$pid/cmdline" | grep -qF "$APP_DIR/main.py"; then
        kill "$pid" && echo "SYRA stopped."
    else
        echo "SYRA is not running."
    fi
    exit 0
fi

# Only one SYRA at a time (two would fight over the microphone).
exec 9>"$RUN_DIR/syra.lock"
flock -n 9 || { echo "SYRA is already running."; exit 1; }
echo $$ > "$PIDFILE"

# No terminal (autostart / app menu)? Keep console output in a file instead of losing it.
if [ ! -t 1 ]; then
    mkdir -p "$APP_DIR/logs"
    exec >>"$APP_DIR/logs/stdout.log" 2>&1
fi

cd "$APP_DIR"
exec "$PYTHON" "$APP_DIR/main.py" "$@"
EOF
chmod +x "$APP_DIR/run_syra.sh"

# 2) short command without '#' in the path:  syra
mkdir -p "$HOME/.local/bin"
ln -sf "$APP_DIR/run_syra.sh" "$HOME/.local/bin/syra"

# 3) start automatically at login (8 s delay so PipeWire is ready)
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/syra.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=SYRA
Comment=SYRA 2.0 voice assistant
Exec=$HOME/.local/bin/syra
Terminal=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=8
EOF

# 4) app-menu entry (search "SYRA" in Activities)
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/syra.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=SYRA
Comment=SYRA 2.0 voice assistant
Exec=$HOME/.local/bin/syra
Icon=audio-input-microphone
Terminal=false
Categories=Utility;
EOF

echo "Done. Start now with:  syra     Stop with:  syra --stop     (or say: exit Syra)"
INSTALL
