"""System-level voice commands for SYRA.

Handles:
- SYRA sleep / idle mode
- SYRA shutdown
- System restart / reboot
- System suspend
- System shutdown / power off
- Time
- Date
- Identity
- Thanks

Important:
    "sleep" by itself means SYRA goes to IDLE.
    "suspend system" means Ubuntu system suspend.
    Restart and system shutdown require confirmation.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from typing import Optional

from core.command_router import (
    Action,
    CommandContext,
    CommandResult,
    CommandRouter,
)


# ============================================================================
# Patterns
# ============================================================================

# ---------------------------------------------------------------------------
# SYRA sleep
# ---------------------------------------------------------------------------
# This does NOT suspend Ubuntu.
# It simply returns SYRA to IDLE.
#
# "quit" and "quite" are included intentionally because speech recognition
# can sometimes produce these words when the user says "quiet".
# ---------------------------------------------------------------------------

SLEEP_PATTERN = (
    r"(?:"
    r"go to sleep|"
    r"sleep(?: syra)?|"
    r"be quiet|"
    r"quiet(?: syra)?|"
    r"quite|"
    r"quit|"
    r"go quiet|"
    r"stop listening|"
    r"stand by|"
    r"terminal closed|"
    r"close (?:the |your )?(?:syra )?(?:console|popup|terminal)"
    r")"
)


# ---------------------------------------------------------------------------
# SYRA shutdown
# ---------------------------------------------------------------------------
# This shuts down SYRA itself, NOT Ubuntu.
# ---------------------------------------------------------------------------

SHUTDOWN_PATTERN = (
    r"(?:"
    r"turn off(?: syra)?|"
    r"turn off assistant|"
    r"stop syra"
    r")"
)


# ---------------------------------------------------------------------------
# System restart / reboot
# ---------------------------------------------------------------------------

RESTART_PATTERN = (
    r"(?:"
    r"restart(?: the)? system|"
    r"reboot(?: the)? system|"
    r"restart|"
    r"reboot"
    r")"
)


# ---------------------------------------------------------------------------
# System suspend
# ---------------------------------------------------------------------------
# Do NOT include plain "sleep" here.
# Plain "sleep" belongs to SYRA's own sleep/IDLE command.
# ---------------------------------------------------------------------------

SUSPEND_PATTERN = (
    r"(?:"
    r"suspend(?: the)? system|"
    r"suspend|"
    r"put(?: the)? system to sleep|"
    r"system suspend"
    r")"
)


# ---------------------------------------------------------------------------
# System shutdown / power off
# ---------------------------------------------------------------------------

SYSTEM_SHUTDOWN_PATTERN = (
    r"(?:"
    r"shutdown(?: the)? system|"
    r"shut down(?: the)? system|"
    r"power off(?: the)? system|"
    r"power down(?: the)? system|"
    r"shutdown|"
    r"shut down"
    r")"
)


# ---------------------------------------------------------------------------
# Confirmation responses
# ---------------------------------------------------------------------------

YES_PATTERN = (
    r"(?:"
    r"yes|"
    r"yeah|"
    r"yep|"
    r"yup|"
    r"yes please|"
    r"do it|"
    r"confirm|"
    r"confirmed|"
    r"go ahead|"
    r"proceed"
    r")"
)


NO_PATTERN = (
    r"(?:"
    r"no|"
    r"nope|"
    r"nah|"
    r"cancel|"
    r"cancel it|"
    r"don't|"
    r"do not|"
    r"never mind|"
    r"nevermind"
    r")"
)


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

TIME_PATTERN = (
    r"(?:"
    r"what(?:s| is) the (?:current )?time(?: now| right now)?|"
    r"what time is it(?: now| right now)?|"
    r"tell me the time|"
    r"current time|"
    r"time now|"
    r"do you have the time"
    r")"
)


# ---------------------------------------------------------------------------
# Date
# ---------------------------------------------------------------------------

DATE_PATTERN = (
    r"(?:"
    r"what(?:s| is) (?:the |todays |the current )?date(?: today)?|"
    r"what day is (?:it|today)|"
    r"todays date|"
    r"current date|"
    r"tell me (?:the|todays) date"
    r")"
)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

IDENTITY_PATTERN = (
    r"(?:"
    r"who are you|"
    r"hu r u|"
    r"what are you|"
    r"what is your name|"
    r"whats your name"
    r")"
)


# ---------------------------------------------------------------------------
# Thanks
# ---------------------------------------------------------------------------

THANKS_PATTERN = r"(?:thanks|thank you)(?: syra)?"


# ============================================================================
# Pending system action
# ============================================================================

# Possible values:
#
#     None
#     "restart"
#     "shutdown"
#
# Only one power action can be waiting for confirmation at a time.

_pending_action: Optional[str] = None


# ============================================================================
# Registration
# ============================================================================

def register(router: CommandRouter) -> None:
    """Register all system-related commands with the SYRA router."""

    # SYRA internal controls
    router.register("shutdown", [SHUTDOWN_PATTERN], _shutdown)
    router.register("sleep", [SLEEP_PATTERN], _sleep)

    # Operating-system controls
    router.register(
        "restart_system",
        [RESTART_PATTERN],
        _restart_system,
    )

    router.register(
        "suspend_system",
        [SUSPEND_PATTERN],
        _suspend_system,
    )

    router.register(
        "shutdown_system",
        [SYSTEM_SHUTDOWN_PATTERN],
        _shutdown_system,
    )

    # Confirmation responses
    router.register(
        "system_confirm_yes",
        [YES_PATTERN],
        _confirm_yes,
    )

    router.register(
        "system_confirm_no",
        [NO_PATTERN],
        _confirm_no,
    )

    # Information
    router.register("time", [TIME_PATTERN], _time)
    router.register("date", [DATE_PATTERN], _date)
    router.register("identity", [IDENTITY_PATTERN], _identity)
    router.register("thanks", [THANKS_PATTERN], _thanks)


# ============================================================================
# SYRA internal controls
# ============================================================================

def _sleep(
    match: re.Match,
    ctx: CommandContext,
) -> CommandResult:
    """Put SYRA into IDLE mode."""

    _clear_pending_action()

    return CommandResult(
        reply="Going to sleep.",
        action=Action.SLEEP,
    )


def _shutdown(
    match: re.Match,
    ctx: CommandContext,
) -> CommandResult:
    """Shut down SYRA itself."""

    _clear_pending_action()

    return CommandResult(
        reply="Turning off. Goodbye.",
        action=Action.SHUTDOWN,
    )


# ============================================================================
# System restart
# ============================================================================

def _restart_system(
    match: re.Match,
    ctx: CommandContext,
) -> CommandResult:
    """Ask for confirmation before restarting Ubuntu."""

    global _pending_action

    _pending_action = "restart"

    return CommandResult(
        reply="Are you sure you want to restart the system?"
    )


def _execute_restart() -> CommandResult:
    """Execute the actual system restart."""

    global _pending_action

    _pending_action = None

    try:
        subprocess.Popen(
            ["systemctl", "reboot"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        return CommandResult(
            reply="Restarting now."
        )

    except Exception:
        return CommandResult(
            reply="I couldn't restart the system."
        )


# ============================================================================
# System suspend
# ============================================================================

def _suspend_system(
    match: re.Match,
    ctx: CommandContext,
) -> CommandResult:
    """Suspend Ubuntu immediately."""

    _clear_pending_action()

    try:
        subprocess.Popen(
            ["systemctl", "suspend"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        return CommandResult(
            reply="Suspending the system."
        )

    except Exception:
        return CommandResult(
            reply="I couldn't suspend the system."
        )


# ============================================================================
# System shutdown / power off
# ============================================================================

def _shutdown_system(
    match: re.Match,
    ctx: CommandContext,
) -> CommandResult:
    """Ask for confirmation before shutting down Ubuntu."""

    global _pending_action

    _pending_action = "shutdown"

    return CommandResult(
        reply="Are you sure you want to shut down the system?"
    )


def _execute_shutdown() -> CommandResult:
    """Execute the actual Ubuntu shutdown."""

    global _pending_action

    _pending_action = None

    try:
        subprocess.Popen(
            ["systemctl", "poweroff"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        return CommandResult(
            reply="Shutting down the system."
        )

    except Exception:
        return CommandResult(
            reply="I couldn't shut down the system."
        )


# ============================================================================
# Confirmation handling
# ============================================================================

def _confirm_yes(
    match: re.Match,
    ctx: CommandContext,
) -> CommandResult:
    """Handle an affirmative response to a pending system action."""

    global _pending_action

    if _pending_action is None:
        return CommandResult(
            reply="There is nothing waiting for confirmation."
        )

    if _pending_action == "restart":
        return _execute_restart()

    if _pending_action == "shutdown":
        return _execute_shutdown()

    _pending_action = None

    return CommandResult(
        reply="There is no pending system action."
    )


def _confirm_no(
    match: re.Match,
    ctx: CommandContext,
) -> CommandResult:
    """Cancel a pending system action."""

    global _pending_action

    if _pending_action is None:
        return CommandResult(
            reply="Okay."
        )

    action = _pending_action

    _pending_action = None

    if action == "restart":
        return CommandResult(
            reply="Restart cancelled."
        )

    if action == "shutdown":
        return CommandResult(
            reply="Shutdown cancelled."
        )

    return CommandResult(
        reply="Cancelled."
    )


# ============================================================================
# Pending-action helpers
# ============================================================================

def _clear_pending_action() -> None:
    """Clear any pending confirmation."""

    global _pending_action

    _pending_action = None


# ============================================================================
# Information commands
# ============================================================================

def _time(
    match: re.Match,
    ctx: CommandContext,
) -> str:
    """Return the current local time."""

    now = datetime.now()

    return f"It's {now.strftime('%I:%M %p').lstrip('0')}."


def _date(
    match: re.Match,
    ctx: CommandContext,
) -> str:
    """Return today's date."""

    now = datetime.now()

    return (
        f"Today is {now:%A}, "
        f"{now:%B} {now.day}, "
        f"{now.year}."
    )


def _identity(
    match: re.Match,
    ctx: CommandContext,
) -> str:
    """Return SYRA's identity."""

    return "I am SYRA, your desktop voice assistant."


def _thanks(
    match: re.Match,
    ctx: CommandContext,
) -> str:
    """Respond to thanks."""

    return "You're welcome."