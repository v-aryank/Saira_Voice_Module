"""The assistant: state machine + listen/dispatch/speak loop.

Threads:
    main thread    - the loop below (listen -> route -> speak); the only place that touches the mic
    speaker        - synthesis + playback workers (audio/speaker.py)
    console        - popup terminal socket threads (ui/console_server.py); they only enqueue
                     typed text and set the "sleep requested" flag
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Optional

import config as cfg
from audio.audio_env import detect_audio_system
from audio.microphone import ListenResult, ListenStatus, MicrophoneError, MicrophoneManager
from audio.speaker import Speaker
from commands import register_all
from core.command_router import Action, CommandContext, CommandRouter
from core.state import State, StateMachine
from core.wake_word import match_wake_phrase
from ui.console_server import ConsoleServer
from utils.logger import SyraLogger


@dataclass(frozen=True)
class UserInput:
    text: str
    source: str   # "voice" or "text"


class Assistant:
    def __init__(self, log: SyraLogger, *, mic: Optional[MicrophoneManager] = None,
                 speaker: Optional[Speaker] = None, console: Optional[ConsoleServer] = None) -> None:
        self.log = log
        self.state = StateMachine(log)
        self.mic = mic or MicrophoneManager(log)
        self.speaker = speaker or Speaker(log)
        self.console = console or ConsoleServer(log, on_text=self.submit_text,
                                                on_disconnect=self._on_console_closed)
        self.router = CommandRouter(log)
        register_all(self.router, log)
        self._ctx = CommandContext(say=self.say, ask=self.ask, log=log)
        log.add_sink(self.console.broadcast)

        self._inputs: "queue.Queue[UserInput]" = queue.Queue()
        self._sleep_requested = threading.Event()
        self._stop = threading.Event()
        self._last_activity = time.monotonic()
        self._silence_seconds = 0.0
        self._unclear_streak = 0
        self._service_down = False
        self._last_service_log = 0.0

    # ═════════════════════ public / thread-safe entry points ═════════════════════
    def submit_text(self, text: str) -> None:
        """A command typed in the popup terminal."""
        self._inputs.put(UserInput(text, "text"))

    def request_stop(self) -> None:
        self._stop.set()

    def _on_console_closed(self) -> None:
        """The user closed the popup terminal: cut speech and go back to sleep."""
        if self.state.is_(State.IDLE):
            return
        self.log.syra("Terminal closed.")
        self._sleep_requested.set()
        self.speaker.stop()

    # ═══════════════════════════════ output ══════════════════════════════════════
    def say(self, text: str) -> None:
        """Print and queue speech. Does not block."""
        if not text:
            return
        self.log.syra(text)
        self.speaker.say(text)

    def _finish_speech(self, resume: State) -> None:
        """Wait (without listening) until SYRA finished talking, then return to `resume`."""
        if self.speaker.is_speaking:
            self.state.transition(State.SPEAKING)
            if not self.speaker.wait(cfg.SPEECH_MAX_WAIT_SECONDS):
                self.log.warn("Speech took too long; stopping it.")
                self.speaker.stop()
            time.sleep(cfg.POST_SPEECH_DELAY)
            self.mic.flush()          # drop SYRA's own voice from the input buffer
        self.state.transition(resume)

    # ═══════════════════════════════ life cycle ══════════════════════════════════
    def run(self) -> None:
        try:
            self._startup()
            while not self._stop.is_set():
                self._tick()
        finally:
            self.shutdown()

    def _startup(self) -> None:
        self.log.syra("Initializing...")
        info = detect_audio_system()
        self.log.syra(f"Audio system detected: {info.server}")
        if info.default_source:
            self.log.debug(f"System default microphone: {info.default_source}")
        self.speaker.start()
        self.speaker.prewarm([cfg.WAKE_REPLY, cfg.GREETING])
        self.console.start()
        self._initialize_microphone()
        if cfg.GREET_ON_START and not self._stop.is_set():
            self.say(cfg.GREETING)
            self._finish_speech(State.IDLE)
        self.log.syra("Waiting for wake word...")

    def _initialize_microphone(self) -> None:
        """Open and calibrate the mic; on failure keep retrying instead of crashing."""
        announced = False
        while not self._stop.is_set():
            try:
                self.mic.initialize()
                self.mic.calibrate(cfg.CALIBRATION_SECONDS_STARTUP)
                if self.state.is_(State.ERROR):
                    self.state.transition(State.IDLE)
                return
            except MicrophoneError as exc:
                self.state.transition(State.ERROR)
                self.log.error(f"Microphone problem: {exc}")
                if not announced:
                    announced = True
                    self.say("I'm having trouble with the microphone. I will keep trying.")
                    self.speaker.wait(15)
                self._stop.wait(cfg.MIC_RECOVERY_INTERVAL)

    def shutdown(self) -> None:
        self.log.syra("Shutting down.")
        self.speaker.shutdown()
        self.console.stop()
        self.mic.close()

    # ═══════════════════════════════ main loop ═══════════════════════════════════
    def _tick(self) -> None:
        """One iteration of the loop. Never raises: problems are logged and recovered from."""
        try:
            if self.state.is_(State.IDLE):
                self._idle_step()
            else:
                self._active_step()
        except MicrophoneError as exc:
            self.log.error(f"Microphone failure: {exc}")
            self._go_idle()
            self.state.transition(State.ERROR)
            self._initialize_microphone()
        except Exception:  # noqa: BLE001 - supervisor barrier: log the traceback, keep running
            self.log.error("Unexpected error (traceback saved to logs/syra.log).", exc_info=True)
            self._go_idle()
            self._stop.wait(1.0)

    # ── IDLE ─────────────────────────────────────────────────────
    def _idle_step(self) -> None:
        result = self.mic.listen(timeout=cfg.LISTEN_SLICE_IDLE, phrase_time_limit=cfg.WAKE_PHRASE_TIME_LIMIT)
        if result.status is ListenStatus.OK:
            self._service_recovered()
            wake = match_wake_phrase(result.text, self.log)    # logs the debug line itself
            if wake is not None:
                self._wake(wake.remainder)
        elif result.status is ListenStatus.SERVICE_ERROR:
            self._note_service_error(speak=False)
            self._stop.wait(2.0)      # do not hammer an unreachable service
        elif result.status is ListenStatus.UNCLEAR:
            self.log.debug("Sound detected but not understood.")   # visible with --debug only
        # NO_SPEECH in IDLE is silence; UNCLEAR is usually background noise: stay quiet.

    def _wake(self, remainder: str) -> None:
        self.console.begin_session()
        self._clear_inputs()
        self._sleep_requested.clear()
        self.log.syra("Wake word detected.")
        self.state.transition(State.ACTIVE)
        self.console.open_window()
        self.say(cfg.WAKE_REPLY)
        self._finish_speech(State.ACTIVE)
        self.mic.calibrate(cfg.CALIBRATION_SECONDS_ACTIVE)      # once per activation
        self._reset_counters()
        self.log.syra("Listening...")
        if remainder:
            self._inputs.put(UserInput(remainder, "voice"))

    # ── ACTIVE ───────────────────────────────────────────────────
    def _active_step(self) -> None:
        if self._sleep_requested.is_set():
            self._go_idle()
            return
        inp = self._poll_input(cfg.LISTEN_SLICE_ACTIVE)
        if inp is not None:
            self._handle_input(inp)
        elif (cfg.ACTIVE_TIMEOUT_SECONDS
              and time.monotonic() - self._last_activity > cfg.ACTIVE_TIMEOUT_SECONDS):
            self.log.syra("No activity - going back to sleep.")
            self._go_idle()

    def _poll_input(self, slice_seconds: float) -> Optional[UserInput]:
        """Next typed command if any, otherwise listen for up to `slice_seconds`."""
        try:
            return self._inputs.get_nowait()
        except queue.Empty:
            pass
        result = self.mic.listen(timeout=slice_seconds, phrase_time_limit=cfg.COMMAND_PHRASE_TIME_LIMIT)
        return self._interpret(result, slice_seconds)

    def _interpret(self, result: ListenResult, slice_seconds: float) -> Optional[UserInput]:
        """Turn a listen result into input, with calm, non-spammy feedback."""
        if result.status is ListenStatus.OK:
            self._service_recovered()
            self._silence_seconds = 0.0
            self._unclear_streak = 0
            return UserInput(result.text, "voice")

        if result.status is ListenStatus.NO_SPEECH:
            self._silence_seconds += slice_seconds
            if self._silence_seconds >= cfg.NO_SPEECH_LOG_SECONDS:
                self.log.syra("No voice detected.")
                self._silence_seconds = 0.0
        elif result.status is ListenStatus.UNCLEAR:
            self._silence_seconds = 0.0
            self._unclear_streak += 1
            self.log.syra("Could not understand.")
            if cfg.UNCLEAR_HINT_AFTER and self._unclear_streak >= cfg.UNCLEAR_HINT_AFTER:
                self._unclear_streak = 0
                self.say("I'm having trouble understanding. You can also type your command.")
                self._finish_speech(State.ACTIVE)
        elif result.status is ListenStatus.SERVICE_ERROR:
            self._note_service_error(speak=True)
        return None

    def _handle_input(self, inp: UserInput) -> None:
        self.state.transition(State.PROCESSING)
        self._last_activity = time.monotonic()
        self.log.you(inp.text, broadcast=(inp.source == "voice"))
        result = self.router.dispatch(inp.text, self._ctx)
        if result.reply:
            self.say(result.reply)
        self._finish_speech(State.ACTIVE)
        if result.action is Action.SLEEP:
            self._go_idle()
        elif result.action is Action.SHUTDOWN:
            self._stop.set()
        self._last_activity = time.monotonic()

    def ask(self, prompt: str, timeout: float = 20.0) -> Optional[str]:
        """Ask a question and return the answer (spoken or typed), or None on timeout/sleep."""
        self.say(prompt)
        self._finish_speech(State.ACTIVE)
        self.log.syra("Listening...")
        deadline = time.monotonic() + timeout
        answer: Optional[str] = None
        while (time.monotonic() < deadline and not self._stop.is_set()
               and not self._sleep_requested.is_set()):
            inp = self._poll_input(cfg.LISTEN_SLICE_ACTIVE)
            if inp is not None:
                self.log.you(inp.text, broadcast=(inp.source == "voice"))
                answer = inp.text
                break
        self.state.transition(State.PROCESSING)
        return answer

    # ── transitions / bookkeeping ────────────────────────────────
    def _go_idle(self) -> None:
        self._sleep_requested.clear()
        self._clear_inputs()
        self.console.close_window()
        self.state.transition(State.IDLE)
        self.mic.flush()
        self.log.syra("Waiting for wake word...")

    def _clear_inputs(self) -> None:
        while True:
            try:
                self._inputs.get_nowait()
            except queue.Empty:
                return

    def _reset_counters(self) -> None:
        self._last_activity = time.monotonic()
        self._silence_seconds = 0.0
        self._unclear_streak = 0

    def _note_service_error(self, speak: bool) -> None:
        now = time.monotonic()
        if now - self._last_service_log > 30 or not self._service_down:
            self.log.syra("Recognition service unavailable.")
            self._last_service_log = now
        if speak and not self._service_down:
            self.say("The speech recognition service is unavailable. You can type commands instead.")
            self._finish_speech(State.ACTIVE)
        self._service_down = True

    def _service_recovered(self) -> None:
        if self._service_down:
            self._service_down = False
            self.log.syra("Recognition service is back.")
