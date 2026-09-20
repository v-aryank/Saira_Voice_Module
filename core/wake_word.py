"""Wake-phrase detection on top of recognized text ("wake up Syra").

Google's recognizer often mis-hears an unusual name ("Sarah", "Sira", "Sid"...)
or drops it entirely ("wake up"). Matching rules, in order:

  1. "wake up"                          -> wake      (name dropped by the recognizer)
  2. "wake up <name>"                   -> wake      (name = exact, known ASR variant,
                                                      or spelled similarly to WAKE_NAME)
  3. anything else, incl. "wake" alone  -> ignored   (protects against accidental wake-ups)

The phrase must start the utterance (at most one stray word before it), and anything
said after the name is returned as `remainder` ("wake up syra open chrome").
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

import config as cfg
from utils.helpers import normalize

# Words the recognizer commonly returns when the user says "Syra" (they are only ever
# matched against; SYRA never says or prints them). WAKE_NAME_ALIASES in config.py is
# checked too, so either list can be extended.
_ASR_NAME_VARIANTS = frozenset({
    "sira", "sid", "sidd", "syd", "cid", "sir", "sera", "seira", "syria", "siria",
    "cyra", "cira", "zira", "zyra", "seera", "syrah", "sarah", "sara", "sierra",
    "siri", "ciara", "zaira",
})


@dataclass(frozen=True)
class WakeMatch:
    """Result of a successful wake-phrase match."""
    remainder: str   # anything said after the wake phrase, e.g. "open chrome"


def _name_rule(word: str) -> Optional[str]:
    """Which rule accepts `word` as the wake name? None if it is not name-like."""
    if word == cfg.WAKE_NAME:
        return "exact name"
    if word in _ASR_NAME_VARIANTS or word in cfg.WAKE_NAME_ALIASES:
        return "known ASR variant"
    if 3 <= len(word) <= 8 and SequenceMatcher(None, word, cfg.WAKE_NAME).ratio() >= cfg.WAKE_FUZZY_CUTOFF:
        return "similar spelling"
    return None


def _evaluate(words: list[str]) -> tuple[Optional[WakeMatch], str]:
    """Apply the wake rules to normalised words. Returns (match or None, reason)."""
    for prefix in cfg.WAKE_PREFIXES:
        prefix_words = prefix.split()
        n = len(prefix_words)
        # The phrase may be preceded by at most one stray word ("hey wake up syra").
        for start in (0, 1):
            if words[start:start + n] != prefix_words:
                continue
            name_index = start + n
            if name_index == len(words):
                return WakeMatch(remainder=""), f"'{prefix}' with no name"
            rule = _name_rule(words[name_index])
            if rule is not None:
                remainder = " ".join(words[name_index + 1:])
                return WakeMatch(remainder=remainder), f"'{prefix}' + '{words[name_index]}' ({rule})"
            return None, f"'{prefix}' followed by '{words[name_index]}', which is not the name"
    return None, "no wake prefix at the start"


def match_wake_phrase(text: str, log=None) -> Optional[WakeMatch]:
    """Return a WakeMatch if `text` is a wake phrase, else None.

    `log` (optional, anything with .debug(str)) receives one line per call showing
    the normalised transcript and whether/why it matched.
    """
    words: list[str] = []
    for word in normalize(text).split():          # lowercase, punctuation and spacing removed
        words.extend(["wake", "up"] if word == "wakeup" else [word])
    match, reason = _evaluate(words)
    if log is not None:
        verdict = "MATCH" if match is not None else "no match"
        log.debug(f"Wake check: {' '.join(words)!r} -> {verdict} ({reason})")
    return match
