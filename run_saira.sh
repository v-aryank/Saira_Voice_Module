#!/usr/bin/env bash
# Start / stop Saira from anywhere.   run_saira.sh [--stop] [main.py options]
SCRIPT="$(readlink -f "${BASH_SOURCE[0]}")"
APP_DIR="$(dirname "$SCRIPT")"
if [ -x "$APP_DIR/.venv/bin/python" ]; then
    PYTHON="$APP_DIR/.venv/bin/python"
elif command -v python3.12 >/dev/null 2>&1; then
    PYTHON="$(command -v python3.12)"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
else
    echo "Python 3.12 or later is required."
    exit 127
fi
if ! "$PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
    echo "Python 3.12 or later is required."
    exit 2
fi
RUN_DIR="${XDG_RUNTIME_DIR:-/tmp}"
PIDFILE="$RUN_DIR/saira.pid"

if [ "${1:-}" = "--stop" ]; then
    pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [ -n "$pid" ] && [ -r "/proc/$pid/cmdline" ] && tr '\0' ' ' < "/proc/$pid/cmdline" | grep -qF "$APP_DIR/main.py"; then
        kill "$pid" && echo "Saira stopped."
    else
        echo "Saira is not running."
    fi
    exit 0
fi

# Only one Saira at a time (two would fight over the microphone).
exec 9>"$RUN_DIR/saira.lock"
flock -n 9 || { echo "Saira is already running."; exit 1; }
echo $$ > "$PIDFILE"

# No terminal (autostart / app menu)? Keep console output in a file instead of losing it.
if [ ! -t 1 ]; then
    mkdir -p "$APP_DIR/logs"
    exec >>"$APP_DIR/logs/stdout.log" 2>&1
fi

cd "$APP_DIR"
exec "$PYTHON" "$APP_DIR/main.py" "$@"
