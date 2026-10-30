"""Web shortcuts: "open YouTube" -> the URL opens in the system default browser.

We use the standard URL-opening mechanism (`webbrowser` -> xdg-open), so no
extra browser process is started by SAIRA and the user's default browser and
profile are respected. Add sites in config.WEB_SITES.
"""
from __future__ import annotations

import shutil
import webbrowser
from dataclasses import dataclass
from typing import Optional

import config as cfg
from utils.helpers import launch_detached, normalize


@dataclass(frozen=True)
class Site:
    key: str
    display: str
    url: str


def _build_index() -> dict[str, Site]:
    index: dict[str, Site] = {}
    for key, spec in cfg.WEB_SITES.items():
        site = Site(key=key, display=spec.get("display", key.title()), url=spec["url"])
        for alias in (key, *spec.get("aliases", ())):
            index[normalize(alias)] = site
    return index


_INDEX = _build_index()


def resolve_site(name: str) -> Optional[Site]:
    """Map a spoken name ("linked in") to a configured site, or None."""
    return _INDEX.get(normalize(name))


def open_url(url: str) -> bool:
    """Open `url` in the default browser. Returns True if a handler accepted it."""
    try:
        if webbrowser.open(url, new=2):
            return True
    except webbrowser.Error:
        pass
    xdg_open = shutil.which("xdg-open")
    if xdg_open is not None:
        ok, _ = launch_detached([xdg_open, url], settle_seconds=0.3)
        return ok
    return False
