"""
Application launching and closing.

OPEN:
    "open VS Code"
    "launch Firefox"
    "open YouTube"
    "open WhatsApp"

CLOSE:
    "close VS Code"
    "quit Firefox"
    "exit Chrome"
    "close YouTube"
    "close WhatsApp"
    "close Facebook"
    "close LinkedIn"

Desktop applications:
    - Resolved through config.APPLICATIONS
    - Closed through fixed process names from config.CLOSE_TARGETS

Web applications:
    - Opened through config.WEB_SITES
    - Closed through Chrome DevTools Protocol (CDP)
    - Chrome must expose port 9222

Security:
    Spoken text is NEVER passed directly to a shell.
    All process names and application commands are fixed/configured.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

import config as cfg
from commands import web
from commands.app_discovery import DesktopAppIndex
from core.command_router import CommandContext, CommandRouter, Reply
from utils.helpers import launch_detached, normalize
from utils.logger import SyraLogger
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# Patterns
# ═══════════════════════════════════════════════════════════════

OPEN_PATTERN = (
    r"(?:open|launch|start|fire up|bring up)\s+"
    r"(?P<target>.+)"
)

CLOSE_PATTERN = (
    r"(?:close|quit|exit|shut down|shutdown)\s+"
    r"(?P<target>.+)"
)

REFRESH_PATTERN = (
    r"(?:refresh|reload|update) "
    r"(?:the )?(?:application|app) "
    r"(?:list|index|cache)"
)


# ═══════════════════════════════════════════════════════════════
# Target cleanup
# ═══════════════════════════════════════════════════════════════

_LEADING_NOISE = re.compile(
    r"^(?:up\s+)?(?:(?:the|my)\s+)?"
)

_TRAILING_NOISE = re.compile(
    r"\s+(?:app|application|program|website|site|page)$"
)


def clean_target(raw: str) -> str:
    """
    Normalize a spoken target.

    Examples:
        "the Visual Studio Code app"
            -> "visual studio code"

        "my Chrome browser"
            -> "chrome browser"
    """

    text = _LEADING_NOISE.sub("", normalize(raw))

    text = _TRAILING_NOISE.sub(
        "",
        text,
    ).strip()

    return text


# ═══════════════════════════════════════════════════════════════
# Open registry
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class AppEntry:
    key: str
    display: str
    commands: tuple[str, ...]


class ApplicationRegistry:
    """
    Explicit application registry built from
    config.APPLICATIONS.
    """

    def __init__(
        self,
        definitions: dict[str, dict],
    ) -> None:

        self._index: dict[str, AppEntry] = {}

        for key, spec in definitions.items():

            entry = AppEntry(
                key=key,
                display=spec.get(
                    "display",
                    key.title(),
                ),
                commands=tuple(
                    spec.get(
                        "commands",
                        (),
                    )
                ),
            )

            for alias in (
                key,
                *spec.get(
                    "aliases",
                    (),
                ),
            ):

                self._index[
                    normalize(alias)
                ] = entry

    def resolve(
        self,
        name: str,
    ) -> Optional[AppEntry]:

        return self._index.get(
            normalize(name)
        )

    @staticmethod
    def installed_command(
        entry: AppEntry,
    ) -> Optional[list[str]]:
        """
        Return the first configured command
        that exists on PATH.
        """

        for command in entry.commands:

            argv = shlex.split(command)

            if argv and shutil.which(
                argv[0]
            ):
                return argv

        return None


# ═══════════════════════════════════════════════════════════════
# Close registry
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CloseEntry:
    key: str
    display: str
    process_names: tuple[str, ...]


class CloseRegistry:
    """
    Registry of applications SYRA is allowed to close.

    This is deliberately separate from arbitrary shell execution.
    """

    def __init__(
        self,
        definitions: dict[str, dict],
    ) -> None:

        self._index: dict[str, CloseEntry] = {}

        for key, spec in definitions.items():

            entry = CloseEntry(
                key=key,
                display=spec.get(
                    "display",
                    key.title(),
                ),
                process_names=tuple(
                    spec.get(
                        "process_names",
                        (),
                    )
                ),
            )

            for alias in (
                key,
                *spec.get(
                    "aliases",
                    (),
                ),
            ):

                self._index[
                    normalize(alias)
                ] = entry

    def resolve(
        self,
        name: str,
    ) -> Optional[CloseEntry]:

        return self._index.get(
            normalize(name)
        )


# ═══════════════════════════════════════════════════════════════
# Web tab registry
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class WebTabEntry:
    key: str
    display: str
    domains: tuple[str, ...]


WEB_TAB_TARGETS: dict[str, dict] = {
    "youtube": {
        "display": "YouTube",
        "domains": (
            "youtube.com",
            "www.youtube.com",
            "music.youtube.com",
        ),
        "aliases": (
            "you tube",
            "youtube music",
        ),
    },

    "whatsapp": {
        "display": "WhatsApp",
        "domains": (
            "web.whatsapp.com",
            "whatsapp.com",
        ),
        "aliases": (
            "what's app",
            "what app",
            "whats app",
        ),
    },

    "facebook": {
        "display": "Facebook",
        "domains": (
            "facebook.com",
            "www.facebook.com",
        ),
        "aliases": (
            "face book",
        ),
    },

    "linkedin": {
        "display": "LinkedIn",
        "domains": (
            "linkedin.com",
            "www.linkedin.com",
        ),
        "aliases": (
            "linked in",
        ),
    },

    "instagram": {
        "display": "Instagram",
        "domains": (
            "instagram.com",
            "www.instagram.com",
        ),
        "aliases": (
            "insta",
            "insta gram",
        ),
    },

    "github": {
        "display": "GitHub",
        "domains": (
            "github.com",
            "www.github.com",
        ),
        "aliases": (
            "git hub",
        ),
    },
}


class WebTabRegistry:
    """
    Registry for websites that SYRA can close
    through Chrome DevTools Protocol.
    """

    def __init__(
        self,
        definitions: dict[str, dict],
    ) -> None:

        self._index: dict[str, WebTabEntry] = {}

        for key, spec in definitions.items():

            entry = WebTabEntry(
                key=key,
                display=spec.get(
                    "display",
                    key.title(),
                ),
                domains=tuple(
                    spec.get(
                        "domains",
                        (),
                    )
                ),
            )

            for alias in (
                key,
                *spec.get(
                    "aliases",
                    (),
                ),
            ):

                self._index[
                    normalize(alias)
                ] = entry

    def resolve(
        self,
        name: str,
    ) -> Optional[WebTabEntry]:

        return self._index.get(
            normalize(name)
        )


# ═══════════════════════════════════════════════════════════════
# Process functions
# ═══════════════════════════════════════════════════════════════

def process_is_running(
    process_name: str,
) -> bool:
    """
    Check whether an exact process name is running.

    Uses pgrep with a fixed argv list.
    No shell is involved.
    """

    try:

        result = subprocess.run(
            [
                "pgrep",
                "-x",
                process_name,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

        return result.returncode == 0

    except OSError:

        return False


def close_process(
    process_name: str,
) -> bool:
    """
    Gracefully terminate all processes
    with this exact name.
    """

    if not process_is_running(
        process_name
    ):
        return False

    try:

        result = subprocess.run(
            [
                "pkill",
                "-TERM",
                "-x",
                process_name,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

        return result.returncode == 0

    except OSError:

        return False


def close_application(
    entry: CloseEntry,
    log: SyraLogger,
) -> bool:
    """
    Try every configured process name.

    Returns True if at least one
    matching process was found.
    """

    found = False

    for process_name in entry.process_names:

        if process_is_running(
            process_name
        ):

            log.debug(
                f"Closing {entry.display}: "
                f"process={process_name}"
            )

            if close_process(
                process_name
            ):
                found = True

    return found


# ═══════════════════════════════════════════════════════════════
# Chrome DevTools Protocol
# ═══════════════════════════════════════════════════════════════

CHROME_DEBUG_HOST = "127.0.0.1"
CHROME_DEBUG_PORT = 9222

CHROME_DEBUG_URL = (
    f"http://{CHROME_DEBUG_HOST}:"
    f"{CHROME_DEBUG_PORT}"
)


def get_chrome_tabs(
    log: Optional[SyraLogger] = None,
) -> list[dict]:
    """
    Return Chrome tabs exposed through CDP.

    Chrome must be running with:

        --remote-debugging-port=9222

    No shell is used.
    """

    url = (
        f"{CHROME_DEBUG_URL}/json/list"
    )

    try:

        with urllib.request.urlopen(
            url,
            timeout=1.5,
        ) as response:

            raw = response.read().decode(
                "utf-8"
            )

            data = json.loads(raw)

            if not isinstance(
                data,
                list,
            ):
                return []

            return data

    except (
        OSError,
        urllib.error.URLError,
        json.JSONDecodeError,
    ) as exc:

        if log is not None:

            log.debug(
                f"Chrome CDP unavailable: "
                f"{exc}"
            )

        return []


def close_chrome_tab(
    tab: dict,
    log: Optional[SyraLogger] = None,
) -> bool:
    """
    Close one Chrome tab using its
    DevTools target ID.
    """

    target_id = tab.get("id")

    if not target_id:
        return False

    url = (
        f"{CHROME_DEBUG_URL}/json/close/"
        f"{target_id}"
    )

    try:

        with urllib.request.urlopen(
            url,
            timeout=1.5,
        ):

            return True

    except (
        OSError,
        urllib.error.URLError,
    ) as exc:

        if log is not None:

            log.warn(
                f"Could not close Chrome tab: "
                f"{exc}"
            )

        return False


def url_matches_domains(
    url: str,
    domains: tuple[str, ...],
) -> bool:
    """
    Match a URL against configured domains.

    Example:
        https://www.youtube.com/watch?v=...
    """

    normalized_url = url.lower().strip()

    for domain in domains:

        normalized_domain = (
            domain.lower()
            .strip()
        )

        if (
            f"://{normalized_domain}" in
            normalized_url
        ):
            return True

        if (
            f"://www.{normalized_domain}"
            in normalized_url
        ):
            return True

        if (
            f".{normalized_domain}/"
            in normalized_url
        ):
            return True

    return False


def close_web_tab(
    target: str,
    registry: WebTabRegistry,
    log: SyraLogger,
) -> Optional[str]:
    """
    Find and close a matching Chrome tab.

    Returns:
        display name if closed
        None if no matching tab exists
    """

    entry = registry.resolve(
        target
    )

    if entry is None:
        return None

    tabs = get_chrome_tabs(log)

    if not tabs:

        log.debug(
            "No Chrome tabs available "
            "through DevTools."
        )

        return None

    for tab in tabs:

        # Only browser pages.
        if tab.get("type") != "page":
            continue

        tab_url = str(
            tab.get("url", "")
        ).strip()

        if not tab_url:
            continue

        if not url_matches_domains(
            tab_url,
            entry.domains,
        ):
            continue

        log.debug(
            f"Closing web tab: "
            f"{entry.display} -> "
            f"{tab_url}"
        )

        if close_chrome_tab(
            tab,
            log,
        ):

            return entry.display

    return None


# ═══════════════════════════════════════════════════════════════
# Chrome launcher
# ═══════════════════════════════════════════════════════════════

def launch_chrome_with_debug(
    argv: list[str],
    log: SyraLogger,
) -> tuple[bool, str]:
    """
    Launch Chromium with Chrome DevTools Protocol enabled.

    This system uses Chromium installed through Snap.
    """

    chromium = shutil.which("chromium")

    if chromium is None:
        chromium = "/snap/bin/chromium"

    if not shutil.which(chromium) and not Path(chromium).exists():
        return (
            False,
            "Chromium executable was not found.",
        )

    final_argv = [
        chromium,
        "--remote-debugging-port=9222",
        *argv[1:],
    ]

    log.debug(
        "Launching Chromium with CDP: "
        f"{final_argv}"
    )

    return launch_detached(
        final_argv
    )

# ═══════════════════════════════════════════════════════════════
# Registration
# ═══════════════════════════════════════════════════════════════

def register(
    router: CommandRouter,
    log: SyraLogger,
) -> None:

    registry = ApplicationRegistry(
        cfg.APPLICATIONS
    )

    close_registry = CloseRegistry(
        cfg.CLOSE_TARGETS
    )

    web_tab_registry = WebTabRegistry(
        WEB_TAB_TARGETS
    )

    index = DesktopAppIndex()

    # Build the .desktop index in background.
    threading.Thread(
        target=index.load,
        name="syra-app-index",
        daemon=True,
    ).start()

    # ═══════════════════════════════════════════════════════════
    # OPEN
    # ═══════════════════════════════════════════════════════════

    def handle_open(
        match: re.Match,
        ctx: CommandContext,
    ) -> Reply:

        target = clean_target(
            match.group("target")
        )

        if not target:

            return (
                "What would you like me "
                "to open?"
            )

        # ─────────────────────────────────────────────────────
        # Explicit application registry
        # ─────────────────────────────────────────────────────

        entry = registry.resolve(
            target
        )

        if entry is not None:

            argv = registry.installed_command(
                entry
            )

            if argv is not None:

                ctx.say(
                    f"Opening "
                    f"{entry.display}."
                )

                # Special handling for Chrome.
                if entry.key in {"chrome", "chromium"}:
                    ok, detail = (
                        launch_chrome_with_debug(
                            argv,
                            log,
                        )
                    )

                else:

                    ok, detail = (
                        launch_detached(
                            argv
                        )
                    )

                if ok:
                    return None

                log.warn(
                    f"Could not launch "
                    f"{entry.display}: "
                    f"{detail}"
                )

                return (
                    f"I couldn't open "
                    f"{entry.display}."
                )

            log.debug(
                f"{entry.display}: none of "
                f"{entry.commands} found on "
                f"PATH; trying discovery."
            )

            target = normalize(
                entry.display
            )

        # ─────────────────────────────────────────────────────
        # Website registry
        # ─────────────────────────────────────────────────────

        else:

            site = web.resolve_site(
                target
            )

            if site is not None:

                ctx.say(
                    f"Opening "
                    f"{site.display}."
                )

                if web.open_url(
                    site.url
                ):
                    return None

                return (
                    f"I couldn't open "
                    f"{site.display} "
                    f"in your browser."
                )

        # ─────────────────────────────────────────────────────
        # .desktop discovery
        # ─────────────────────────────────────────────────────

        app = index.best_match(
            target
        )

        if app is None:

            if entry is not None:

                return (
                    f"{entry.display} "
                    f"doesn't seem to be "
                    f"installed."
                )

            return (
                f"I couldn't find an "
                f"application called "
                f"{target}."
            )

        ctx.say(
            f"Opening {app.name}."
        )

        ok, detail = index.launch(
            app
        )

        if ok:
            return None

        log.warn(
            f"Could not launch "
            f"{app.name}: {detail}"
        )

        return (
            f"I couldn't open "
            f"{app.name}."
        )

    # ═══════════════════════════════════════════════════════════
    # CLOSE
    # ═══════════════════════════════════════════════════════════

    def handle_close(
        match: re.Match,
        ctx: CommandContext,
    ) -> Reply:

        target = clean_target(
            match.group("target")
        )

        if not target:

            return (
                "What would you like "
                "me to close?"
            )

        # ─────────────────────────────────────────────────────
        # FIRST: Web tab
        # ─────────────────────────────────────────────────────

        web_tab = close_web_tab(
            target,
            web_tab_registry,
            log,
        )

        if web_tab is not None:

            ctx.say(
                f"Closing {web_tab}."
            )

            return None

        # ─────────────────────────────────────────────────────
        # SECOND: Desktop application
        # ─────────────────────────────────────────────────────

        entry = close_registry.resolve(
            target
        )

        if entry is None:

            return (
                f"I don't have a close "
                f"command for {target}."
            )

        log.debug(
            f"Close requested for "
            f"{entry.display} with "
            f"processes="
            f"{entry.process_names}"
        )

        # Check first.
        running_processes = [
            process_name
            for process_name
            in entry.process_names
            if process_is_running(
                process_name
            )
        ]

        if not running_processes:

            return (
                f"{entry.display} "
                f"is not running."
            )

        ctx.say(
            f"Closing {entry.display}."
        )

        closed = False

        for process_name in (
            running_processes
        ):

            if close_process(
                process_name
            ):
                closed = True

        if not closed:

            return (
                f"I couldn't close "
                f"{entry.display}."
            )

        # Give the application a moment.
        time.sleep(0.3)

        # Verify.
        still_running = [
            process_name
            for process_name
            in entry.process_names
            if process_is_running(
                process_name
            )
        ]

        if still_running:

            log.warn(
                f"{entry.display} is "
                f"still running: "
                f"{still_running}"
            )

            return (
                f"I asked "
                f"{entry.display} to close, "
                f"but it is still running."
            )

        return None

    # ═══════════════════════════════════════════════════════════
    # REFRESH
    # ═══════════════════════════════════════════════════════════

    def handle_refresh(
        match: re.Match,
        ctx: CommandContext,
    ) -> str:

        count = index.load(
            force=True
        )

        return (
            f"Application list refreshed. "
            f"I found {count} applications."
        )

    # ═══════════════════════════════════════════════════════════
    # ROUTES
    # ═══════════════════════════════════════════════════════════

    router.register(
        "refresh_apps",
        [REFRESH_PATTERN],
        handle_refresh,
    )

    # CLOSE must be registered before OPEN.
    router.register(
        "close",
        [CLOSE_PATTERN],
        handle_close,
    )

    router.register(
        "open",
        [OPEN_PATTERN],
        handle_open,
    )
