"""Command registration. Route ORDER matters: the first matching route wins.

To add a new command module, create commands/mything.py with a
`register(router, log)` function and add one line below.
"""
from __future__ import annotations

import config as cfg
from commands import applications, development, shell, system, wiki
from core.command_router import CommandRouter
from utils.logger import SairaLogger


def register_all(router: CommandRouter, log: SairaLogger) -> None:
    system.register(router)                 # sleep, shutdown, time, date, identity
    if cfg.SHELL_ENABLED:                   # security boundary: only wired in when enabled
        shell.register(router, log)
    development.register(router, log)       # "create a webpage architecture"
    applications.register(router, log)      # "open X" (apps, websites, discovered apps)
    wiki.register(router, log)              # "who is X", "tell me about X"
