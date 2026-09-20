"""Development voice commands.

Supported commands:

1. Webpage architecture
   - "make a webpage"
   - "create a webpage"
   - "build a website"

   Workflow:
     1. Open VS Code
     2. Ask for project name
     3. Create project folder
     4. Copy templates/webpage_architecture/
     5. Open the project in VS Code

2. New project
   - "make new project"
   - "make a new project"
   - "create a new project"
   - "build a new project"

   Workflow:
     1. Ask for project name
     2. Create project folder
     3. Copy templates/new_project/
     4. Open the project in VS Code

The templates are user-owned and are NOT invented here.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Optional

import config as cfg

from core.command_router import (
    CommandContext,
    CommandError,
    CommandRouter,
    Reply,
)

from utils.helpers import (
    launch_detached,
    normalize,
    slugify,
)

from utils.logger import SyraLogger


# ============================================================
# VOICE COMMAND PATTERNS
# ============================================================

# Webpage / website creation
#
# Accepted examples:
#   make a web
#   make web
#   make webpage
#   make web page
#   make a webpage
#   make a web page
#   make website
#   make a website
#   make site
#   make a site
#
#   create web
#   create webpage
#   create web page
#   create website
#   create site
#
#   build web
#   build webpage
#   build web page
#   build website
#   build site

WEBPAGE_PATTERN = (
    r"^(?:(?:make|create|build)\s+)?"
    r"(?:a\s+)?"
    r"(?:web|website|site|landing)"
    r"(?:\s+page)?$"
)


# New project creation
#
# Accepted examples:
#   make new project
#   make a new project
#   make project
#   make a project
#   create new project
#   create a new project
#   create project
#   create a project
#   build new project
#   build a new project
#   build project
#   build a project

NEW_PROJECT_PATTERN = (
    r"^(?:make|create|build)\s+"
    r"(?:a\s+)?"
    r"(?:new\s+)?"
    r"project$"
)

MAKE_PATTERN = r"^make$"

OPEN_PROJECT_PATTERN = (
    r"^(?:open|launch)\s+"
    r"(?:the\s+)?"
    r"project\s+"
    r"(.+)$"
)

# ============================================================
# GENERAL SETTINGS
# ============================================================

_CANCEL_WORDS = {
    "cancel",
    "never mind",
    "nevermind",
    "stop",
    "forget it",
    "abort",
}

_PLACEHOLDER_GLOB = "_PLACEHOLDER*"


# ============================================================
# VS CODE
# ============================================================

def _find_vscode() -> Optional[str]:
    """Find the VS Code executable."""
    return shutil.which("code")


# Finding files/folders

def find_project(name: str) -> Optional[Path]:
    """
    Find a project folder by name inside PROJECTS_DIR.

    Examples:
        "raj"       -> finds PROJECTS_DIR/raj
        "RC Nagar"  -> finds PROJECTS_DIR/rc-nagar

    The search is case-insensitive and also supports
    slugified names.
    """

    projects_dir = Path(cfg.PROJECTS_DIR)

    if not projects_dir.is_dir():
        return None

    search_name = normalize(name)
    search_slug = slugify(name)

    # Exact folder-name match first
    for item in projects_dir.iterdir():

        if not item.is_dir():
            continue

        if normalize(item.name) == search_name:
            return item

        if slugify(item.name) == search_slug:
            return item

    return None

# ============================================================
# TEMPLATE COPY
# ============================================================

def copy_template(source: Path, destination: Path) -> bool:
    """
    Copy a template directory into the destination.

    Ignored:
        - _PLACEHOLDER*
        - __pycache__
        - .git
        - node_modules

    Returns:
        True  -> at least one real file was copied
        False -> template is missing or contains no real files
    """

    ignore = shutil.ignore_patterns(
        _PLACEHOLDER_GLOB,
        "__pycache__",
        ".git",
        "node_modules",
    )

    if not source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        return False

    shutil.copytree(
        source,
        destination,
        ignore=ignore,
    )

    return any(
        path.is_file()
        for path in destination.rglob("*")
    )


# ============================================================
# PROJECT NAME
# ============================================================

def _get_project_name(
    ctx: CommandContext,
) -> Optional[str]:
    """
    Ask for a project name and keep asking until:

    - a valid unused name is provided
    - the user says cancel
    - the listening timeout expires
    """

    while True:

        answer = ctx.ask(
            "What should I name the project?",
            20.0,
        )

        if answer is None:
            return None

        normalized = normalize(answer)

        if normalized in _CANCEL_WORDS:
            return None

        folder_name = slugify(answer)[:60].strip("-")

        if not folder_name:
            ctx.say(
                "That is not a valid project name. "
                "Please try another name."
            )
            continue

        target = (
            Path(cfg.PROJECTS_DIR)
            / folder_name
        )

        if target.exists():

            ctx.say(
                f"A project called {folder_name} "
                "already exists. Please choose another name."
            )

            continue

        return folder_name

# ============================================================
# WEBPAGE ARCHITECTURE
# ============================================================

def create_webpage_architecture(
    ctx: CommandContext,
    log: SyraLogger,
) -> Reply:
    """
    Create a new webpage project from the user's
    webpage architecture template.
    """

    code = _find_vscode()

    if code is None:
        raise CommandError(
            "Visual Studio Code is not installed, "
            "so I can't start a project."
        )

    # --------------------------------------------------------
    # Open VS Code
    # --------------------------------------------------------

    ctx.say("Opening Visual Studio Code.")

    ok, detail = launch_detached(
        [code],
    )

    if not ok:
        log.warn(
            f"VS Code did not start: {detail}"
        )

    # --------------------------------------------------------
    # Ask for project name
    # --------------------------------------------------------

    folder_name = _get_project_name(ctx)

    if folder_name is None:
        return (
            "I didn't hear a project name, "
            "so I cancelled."
        )

    # --------------------------------------------------------
    # Project path
    # --------------------------------------------------------

    target = (
        Path(cfg.PROJECTS_DIR)
        / folder_name
    )

    # --------------------------------------------------------
    # Existing project check
    # --------------------------------------------------------

    if target.exists():
        raise CommandError(
            f"A project called {folder_name} "
            "already exists. Please choose another name."
        )

    # --------------------------------------------------------
    # Create project
    # --------------------------------------------------------

    try:
        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        has_template = copy_template(
            Path(cfg.WEBPAGE_TEMPLATE_DIR),
            target,
        )

    except (OSError, shutil.Error) as exc:

        log.error(
            f"Webpage project creation failed: {exc}",
            exc_info=True,
        )

        raise CommandError(
            "I couldn't create the webpage project folder."
        ) from None

    # --------------------------------------------------------
    # Log
    # --------------------------------------------------------

    log.syra(
        f"Webpage project folder: {target}"
    )

    # --------------------------------------------------------
    # Open project in VS Code
    # --------------------------------------------------------

    ok, detail = launch_detached(
        [
            code,
            "-r",
            str(target),
        ],
        settle_seconds=0.8,
    )

    if not ok:
        log.warn(
            f"VS Code did not open the project: {detail}"
        )

    # --------------------------------------------------------
    # Final response
    # --------------------------------------------------------

    if not has_template:
        return (
            f"Project {folder_name} is created, "
            "but your webpage template folder is empty."
        )

    return (
        f"Web project {folder_name} is ready."
    )


# ============================================================
# NEW PROJECT
# ============================================================

def create_new_project(
    ctx: CommandContext,
    log: SyraLogger,
) -> Reply:
    """
    Create a new project from the user's
    new_project template.
    """

    code = _find_vscode()

    if code is None:
        raise CommandError(
            "Visual Studio Code is not installed, "
            "so I can't start a project."
        )

    # --------------------------------------------------------
    # Ask for project name
    # --------------------------------------------------------

    folder_name = _get_project_name(ctx)

    if folder_name is None:
        return (
            "I didn't hear a project name, "
            "so I cancelled."
        )

    # --------------------------------------------------------
    # Project path
    # --------------------------------------------------------

    target = (
        Path(cfg.PROJECTS_DIR)
        / folder_name
    )

    # --------------------------------------------------------
    # Existing project check
    # --------------------------------------------------------

    if target.exists():
        raise CommandError(
            f"A project called {folder_name} "
            "already exists. Please choose another name."
        )

    # --------------------------------------------------------
    # Template path
    # --------------------------------------------------------

    template_dir = Path(
        cfg.NEW_PROJECT_TEMPLATE_DIR
    )

    # --------------------------------------------------------
    # Create project
    # --------------------------------------------------------

    try:

        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        if template_dir.is_dir():

            has_template = copy_template(
                template_dir,
                target,
            )

        else:

            target.mkdir(
                parents=True,
                exist_ok=True,
            )

            has_template = False

            log.warn(
                f"New project template not found: "
                f"{template_dir}"
            )

    except (OSError, shutil.Error) as exc:

        log.error(
            f"New project creation failed: {exc}",
            exc_info=True,
        )

        raise CommandError(
            "I couldn't create the new project folder."
        ) from None

    # --------------------------------------------------------
    # Log
    # --------------------------------------------------------

    log.syra(
        f"New project folder: {target}"
    )

    # --------------------------------------------------------
    # Open project in VS Code
    # --------------------------------------------------------

    ok, detail = launch_detached(
        [
            code,
            "-r",
            str(target),
        ],
        settle_seconds=0.8,
    )

    if not ok:
        log.warn(
            f"VS Code did not open the project: {detail}"
        )

    # --------------------------------------------------------
    # Final response
    # --------------------------------------------------------

    if not has_template:
        return (
            f"Project {folder_name} is created, "
            "but your new project template is "
            "missing or empty."
        )

    return (
        f"Project {folder_name} is ready."
    )


# --------------------------------------------------------
# Open Project
# --------------------------------------------------------

def open_project(
    ctx: CommandContext,
    log: SyraLogger,
    project_name: str,
) -> Reply:

    code = _find_vscode()

    if code is None:
        raise CommandError(
            "Visual Studio Code is not installed."
        )

    project = find_project(project_name)

    if project is None:
        return (
            f"I couldn't find a project called "
            f"{project_name}."
        )

    log.syra(
        f"Opening project: {project}"
    )

    ok, detail = launch_detached(
        [
            code,
            "-r",
            str(project),
        ],
        settle_seconds=0.8,
    )

    if not ok:
        log.warn(
            f"VS Code did not open the project: {detail}"
        )
        return (
            f"I found {project.name}, "
            "but I couldn't open it in Visual Studio Code."
        )

    return f"Opening project {project.name}."


# ============================================================
# REGISTER COMMANDS
# ============================================================

def register(
    router: CommandRouter,
    log: SyraLogger,
) -> None:

    # --------------------------------------------------------
    # Webpage command handler
    # --------------------------------------------------------

    def webpage_handler(
        match: re.Match,
        ctx: CommandContext,
    ) -> Reply:

        return create_webpage_architecture(
            ctx,
            log,
        )

    # --------------------------------------------------------
    # New project command handler
    # --------------------------------------------------------

    def new_project_handler(
        match: re.Match,
        ctx: CommandContext,
    ) -> Reply:

        return create_new_project(
            ctx,
            log,
        )

    
    # --------------------------------------------------------
    # Incomplete "make" command
    # --------------------------------------------------------

    def make_handler(
        match: re.Match,
        ctx: CommandContext,
    ) -> Reply:

        return ctx.ask(
            "What should I make?",
            20.0,
        )

    # --------------------------------------------------------
    # Open projects handler
    # --------------------------------------------------------

    def open_project_handler(
        match: re.Match,
        ctx: CommandContext,
    ) -> Reply:

        project_name = match.group(1).strip()

        return open_project(
            ctx,
            log,
            project_name,
        )
    

    # --------------------------------------------------------
    # Register all commands
    # --------------------------------------------------------

    router.register(
        "webpage_architecture",
        [WEBPAGE_PATTERN],
        webpage_handler,
    )

    router.register(
        "new_project",
        [NEW_PROJECT_PATTERN],
        new_project_handler,
    )

    router.register(
        "make",
        [MAKE_PATTERN],
        make_handler,
    )

    router.register(
        "open_project",
        [OPEN_PROJECT_PATTERN],
        open_project_handler,
    )