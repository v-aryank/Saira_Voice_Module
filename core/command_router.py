"""Command router: maps what the user said to a handler.

A command is a regular expression (matched against the WHOLE normalised
utterance) plus a handler function:

    def handler(match: re.Match[str], ctx: CommandContext) -> str | CommandResult | None

Return a string to have SYRA say it, a CommandResult for extra control
(sleep / shutdown), or None if the handler already spoke via ctx.say().
Routes are tried in registration order; the first match wins.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Union

from utils.helpers import strip_command_fillers
from utils.logger import SyraLogger


class Action(enum.Enum):
    NONE = "none"
    SLEEP = "sleep"        # return to IDLE after speaking
    SHUTDOWN = "shutdown"  # exit SYRA after speaking


@dataclass
class CommandResult:
    reply: Optional[str] = None
    action: Action = Action.NONE
    handled: bool = True


class CommandError(Exception):
    """A user-facing failure. The message is spoken as SYRA's reply."""


@dataclass
class CommandContext:
    """What a command handler is allowed to use.

    say(text)          queue speech without blocking
    ask(prompt, secs)  speak a question and return the next answer (voice or typed), or None
    """
    say: Callable[[str], None]
    ask: Callable[..., Optional[str]]
    log: SyraLogger


Reply = Union[str, CommandResult, None]
Handler = Callable[[re.Match, CommandContext], Reply]


@dataclass(frozen=True)
class Route:
    name: str
    patterns: tuple[re.Pattern, ...]
    handler: Handler


class CommandRouter:
    """Ordered list of routes with safe dispatch."""

    UNKNOWN_REPLY = "Sorry, I don't know that."

    def __init__(self, log: SyraLogger) -> None:
        self._log = log
        self._routes: list[Route] = []

    def register(self, name: str, patterns: Sequence[str], handler: Handler) -> None:
        compiled = tuple(re.compile(p, re.IGNORECASE) for p in patterns)
        self._routes.append(Route(name=name, patterns=compiled, handler=handler))

    def command(self, name: str, *patterns: str) -> Callable[[Handler], Handler]:
        """Decorator form of register()."""
        def decorator(handler: Handler) -> Handler:
            self.register(name, patterns, handler)
            return handler
        return decorator

    def dispatch(self, raw_text: str, ctx: CommandContext) -> CommandResult:
        text = strip_command_fillers(raw_text)
        if not text:
            return CommandResult(handled=False)
        for route in self._routes:
            for pattern in route.patterns:
                match = pattern.fullmatch(text)
                if match is None:
                    continue
                self._log.debug(f"Route '{route.name}' matched: {text!r}")
                return self._run(route, match, ctx)
        self._log.debug(f"No route for: {text!r}")
        return CommandResult(reply=self.UNKNOWN_REPLY, handled=False)

    def _run(self, route: Route, match: re.Match, ctx: CommandContext) -> CommandResult:
        try:
            reply = route.handler(match, ctx)
        except CommandError as exc:
            return CommandResult(reply=str(exc))
        except Exception:  # noqa: BLE001 - last-resort barrier: one buggy command must not kill SYRA
            self._log.error(f"Command '{route.name}' crashed (details in logs/syra.log).", exc_info=True)
            return CommandResult(reply="Sorry, something went wrong while doing that.")
        if reply is None:
            return CommandResult()
        if isinstance(reply, CommandResult):
            return reply
        return CommandResult(reply=reply)
