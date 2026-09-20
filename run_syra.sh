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
