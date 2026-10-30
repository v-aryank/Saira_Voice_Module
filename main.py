# ============================================================
# Project: Saira Voice Control Engine
# Developer: Aryan Kabir
# Copyright (c) 2026 Aryan Kabir
# All Rights Reserved.
# ============================================================

from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as cfg                                   # noqa: E402
from audio.audio_env import silence_backend_noise      # noqa: E402

silence_backend_noise()      # must run before PortAudio is initialised

from utils.logger import SairaLogger                    # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Saira Voice Control Engine")
    parser.add_argument("--list-mics", action="store_true", help="list input devices and exit")
    parser.add_argument("--test-mic", action="store_true", help="test the microphone and exit")
    parser.add_argument("--mic", help="microphone index or name (overrides config.MIC_DEVICE_INDEX)")
    parser.add_argument("--debug", action="store_true", help="verbose terminal output")
    parser.add_argument("--no-popup", action="store_true", help="do not open the popup terminal")
    parser.add_argument("--mute", action="store_true", help="disable speech output (text only)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mic_choice = args.mic or os.environ.get("SAIRA_MIC")
    if mic_choice:
        cfg.MIC_DEVICE_INDEX = int(mic_choice) if mic_choice.isdigit() else mic_choice
    if args.no_popup:
        cfg.POPUP_ENABLED = False
    if args.mute:
        cfg.TTS_ENABLED = False

    log = SairaLogger(debug=args.debug or cfg.DEBUG, log_file=cfg.LOG_FILE)

    if args.list_mics or args.test_mic:
        from audio.diagnostics import list_microphones, test_microphone
        return list_microphones(log) if args.list_mics else test_microphone(log)

    from core.assistant import Assistant
    assistant = Assistant(log)
    signal.signal(signal.SIGTERM, lambda *_: assistant.request_stop())
    try:
        assistant.run()
    except KeyboardInterrupt:
        pass        # Assistant.run() already shut everything down in its finally block
    return 0


if __name__ == "__main__":
    sys.exit(main())
