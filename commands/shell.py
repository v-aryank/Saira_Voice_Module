"""Restricted command execution - a deliberate SECURITY BOUNDARY.

Rules of this module:
  * It is OFF unless config.SHELL_ENABLED is True; commands/__init__.py does not
    even register its route otherwise.
  * Spoken words only ever select a NAME from config.SHELL_ALLOWED_COMMANDS.
    They are never split, expanded, or handed to a shell.
  * Every command is a fixed argv list run with shell=False, a timeout, no stdin,
    and truncated output.
  * It has no dependency on the normal command handlers, and they have none on it.

To allow a new command, add a NAME -> argv entry to SHELL_ALLOWED_COMMANDS.
Never add entries that interpolate user input.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Mapping, Sequence

import config as cfg
from core.command_router import CommandContext, CommandError, CommandRouter
from utils.helpers import normalize
from utils.logger import SyraLogger

RUN_PATTERN = r"(?:run|execute)\s+(?:the\s+)?(?:system\s+)?command\s+(?P<name>.+)"


@dataclass(frozen=True)
class ShellResult:
    ok: bool
    output: str


class RestrictedShell:
    """Runs only pre-approved argv lists."""

    def __init__(self, allowed: Mapping[str, Sequence[str]], timeout: float, max_chars: int) -> None:
        self._allowed = {normalize(name): list(argv) for name, argv in allowed.items()}
        self._timeout = timeout
        self._max_chars = max_chars

    def names(self) -> list[str]:
        return sorted(self._allowed)

    def run(self, name: str) -> ShellResult:
        argv = self._allowed.get(normalize(name))
        if argv is None:
            return ShellResult(False, f"'{name}' is not an approved command.")
        try:
            proc = subprocess.run(argv, shell=False, stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, timeout=self._timeout, check=False)
        except subprocess.TimeoutExpired:
            return ShellResult(False, "The command timed out.")
        except OSError as exc:
            return ShellResult(False, f"The command could not start: {exc}")
        text = (proc.stdout or proc.stderr).strip()[: self._max_chars]
        return ShellResult(proc.returncode == 0, text or "The command produced no output.")


def register(router: CommandRouter, log: SyraLogger) -> None:
    shell = RestrictedShell(cfg.SHELL_ALLOWED_COMMANDS, cfg.SHELL_TIMEOUT_SECONDS, cfg.SHELL_MAX_OUTPUT_CHARS)

    def handler(match: re.Match, ctx: CommandContext) -> str:
        name = match.group("name")
        result = shell.run(name)
        log.debug(f"Restricted command {name!r}: ok={result.ok}")
        if not result.ok and "not an approved" in result.output:
            raise CommandError(f"I'm not allowed to run {name}. Approved commands are: {', '.join(shell.names())}.")
        return result.output

    router.register("restricted_shell", [RUN_PATTERN], handler)
