"""Universal application discovery from freedesktop `.desktop` files.

Reads the standard application directories (system, user, Snap, Flatpak),
builds an in-memory index once, and matches a spoken name against application
names, generic names and keywords. Launching goes through `gio launch`, which
understands Exec field codes, Snap/Flatpak and DBus activation - so SYRA does
not have to parse or trust Exec lines itself.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import threading
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from utils.helpers import launch_detached, normalize

MIN_SCORE = 60
_FIELD_CODES = re.compile(r"%[fFuUdDnNickvm]")


@dataclass(frozen=True)
class DesktopApp:
    desktop_id: str            # e.g. "org.gnome.Calculator.desktop"
    name: str
    generic_name: str
    keywords: tuple[str, ...]
    exec_line: str
    path: Path


def _search_dirs() -> list[Path]:
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    system = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs = [data_home / "applications"]
    dirs += [Path(p) / "applications" for p in system.split(":") if p]
    dirs += [Path("/var/lib/snapd/desktop/applications"),
             Path("/var/lib/flatpak/exports/share/applications"),
             Path.home() / ".local" / "share" / "flatpak" / "exports" / "share" / "applications"]
    seen: set[Path] = set()
    unique: list[Path] = []
    for directory in dirs:
        if directory not in seen:
            seen.add(directory)
            unique.append(directory)
    return unique


def parse_desktop_file(path: Path) -> Optional[DesktopApp]:
    """Parse one .desktop file; return None for hidden, non-application or malformed entries."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fields: dict[str, str] = {}
    in_entry = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            in_entry = line == "[Desktop Entry]"
            continue
        if not in_entry or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if "[" in key:            # localized variant such as Name[bn]
            continue
        fields.setdefault(key, value.strip())
    if fields.get("Type", "Application") != "Application":
        return None
    if fields.get("NoDisplay", "").lower() == "true" or fields.get("Hidden", "").lower() == "true":
        return None
    name, exec_line = fields.get("Name"), fields.get("Exec")
    if not name or not exec_line:
        return None
    keywords = tuple(k for k in fields.get("Keywords", "").split(";") if k)
    return DesktopApp(desktop_id=path.name, name=name, generic_name=fields.get("GenericName", ""),
                      keywords=keywords, exec_line=exec_line, path=path)


def _score(query: str, tokens: list[str], app: DesktopApp) -> int:
    name = normalize(app.name)
    generic = normalize(app.generic_name)
    keywords = normalize(" ".join(app.keywords))
    id_tail = normalize(app.desktop_id.removesuffix(".desktop").split(".")[-1])
    if query == name:
        return 100
    if query == id_tail:
        return 95
    name_words = set(name.split())
    if tokens and all(t in name_words for t in tokens):
        return 90 - min(len(name_words), 8)       # prefer the shortest matching name
    if generic and query == generic:
        return 75
    if tokens and all(t in keywords.split() for t in tokens):
        return 62
    ratio = SequenceMatcher(None, query, name).ratio()
    return int(ratio * 70) if ratio >= 0.8 else 0


class DesktopAppIndex:
    """Lazy, thread-safe index of installed desktop applications."""

    def __init__(self) -> None:
        self._apps: list[DesktopApp] = []
        self._loaded = False
        self._lock = threading.Lock()

    def load(self, force: bool = False) -> int:
        with self._lock:
            if self._loaded and not force:
                return len(self._apps)
            apps: dict[str, DesktopApp] = {}
            for directory in _search_dirs():          # earlier directories take precedence
                if not directory.is_dir():
                    continue
                for path in directory.rglob("*.desktop"):
                    app = parse_desktop_file(path)
                    if app is not None:
                        apps.setdefault(app.desktop_id, app)
            self._apps = list(apps.values())
            self._loaded = True
            return len(self._apps)

    def best_match(self, spoken_name: str) -> Optional[DesktopApp]:
        matches = self.search(spoken_name, limit=1)
        return matches[0][1] if matches else None

    def search(self, spoken_name: str, limit: int = 3) -> list[tuple[int, DesktopApp]]:
        self.load()
        query = normalize(spoken_name)
        tokens = query.split()
        if not tokens:
            return []
        scored = [(_score(query, tokens, app), app) for app in self._apps]
        scored = [item for item in scored if item[0] >= MIN_SCORE]
        scored.sort(key=lambda item: (-item[0], len(item[1].name)))
        return scored[:limit]

    @staticmethod
    def launch(app: DesktopApp) -> tuple[bool, str]:
        gio = shutil.which("gio")
        if gio is not None:
            return launch_detached([gio, "launch", str(app.path)], settle_seconds=0.8)
        gtk_launch = shutil.which("gtk-launch")
        if gtk_launch is not None:
            return launch_detached([gtk_launch, app.desktop_id], settle_seconds=0.8)
        try:
            argv = shlex.split(_FIELD_CODES.sub("", app.exec_line).replace("%%", "%"))
        except ValueError as exc:
            return False, f"unparseable Exec line: {exc}"
        if not argv:
            return False, "empty Exec line"
        return launch_detached(argv, settle_seconds=0.8)
