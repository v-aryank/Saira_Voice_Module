"""SAIRA's state machine.

    IDLE ──wake word──▶ ACTIVE ──speech/text──▶ PROCESSING ──▶ SPEAKING ──▶ ACTIVE
      ▲                                                                     │
      └──────────────────── "go to sleep" / console closed ─────────────────┘

ERROR is entered when the microphone cannot be recovered; SAIRA then retries
until it works and drops back to IDLE. SPEAKING can interrupt any state and
returns to the state that was active before.
"""
from __future__ import annotations

import enum
import threading

from utils.logger import SairaLogger


class State(enum.Enum):
    IDLE = "IDLE"
    ACTIVE = "ACTIVE"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"
    ERROR = "ERROR"


_ALLOWED: dict[State, frozenset[State]] = {
    State.IDLE: frozenset({State.ACTIVE, State.SPEAKING, State.ERROR}),
    State.ACTIVE: frozenset({State.PROCESSING, State.SPEAKING, State.IDLE, State.ERROR}),
    State.PROCESSING: frozenset({State.ACTIVE, State.SPEAKING, State.IDLE, State.ERROR}),
    State.SPEAKING: frozenset({State.IDLE, State.ACTIVE, State.PROCESSING, State.ERROR}),
    State.ERROR: frozenset({State.IDLE}),
}


class StateMachine:
    """Thread-safe holder of the current state with validated transitions."""

    def __init__(self, log: SairaLogger, initial: State = State.IDLE) -> None:
        self._log = log
        self._state = initial
        self._lock = threading.Lock()

    @property
    def current(self) -> State:
        return self._state

    def is_(self, *states: State) -> bool:
        return self._state in states

    def transition(self, new: State) -> bool:
        """Move to `new`. Illegal moves are refused and logged (they indicate a bug)."""
        with self._lock:
            old = self._state
            if new is old:
                return True
            if new not in _ALLOWED[old]:
                self._log.error(f"Illegal state transition {old.value} -> {new.value} ignored.")
                return False
            self._state = new
        self._log.debug(f"State: {old.value} -> {new.value}")
        return True
