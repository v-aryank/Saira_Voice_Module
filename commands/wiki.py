"""Wikipedia lookups: "Who is Nikola Tesla?", "Tell me about Alan Turing".

Handles disambiguation, missing pages, timeouts and network failures without
raising. The `wikipedia` package has no request timeout of its own, so the call
runs in a worker thread and we stop waiting after config.WIKI_TIMEOUT seconds.
"""
from __future__ import annotations

import re
import warnings
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
 
import requests

import config as cfg
from core.command_router import CommandContext, CommandError, CommandRouter
from utils.logger import SyraLogger

warnings.filterwarnings("ignore", message=".*No parser was explicitly specified.*")

try:
    import wikipedia
    from wikipedia.exceptions import DisambiguationError, HTTPTimeoutError, PageError, WikipediaException
except ImportError:
    wikipedia = None  # type: ignore[assignment]

QUERY_PATTERN = (
    r"(?:who (?:is|was|are|were)|what (?:is|was|are|were)|tell me about|search wikipedia for|"
    r"wikipedia|look up|define|explain)\s+"
    r"(?!(?:you|your|my|me|this|that|it|the time|the date|todays)\b)(?P<query>.+)"
)

_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="syra-wiki")
_PARENTHESES = re.compile(r"\([^()]*\)")
_CITATIONS = re.compile(r"\[\d+\]")


@dataclass(frozen=True)
class WikiAnswer:
    title: str
    summary: str
    ambiguous: bool


class NotFound(Exception):
    """No suitable article exists."""


def _clean(text: str) -> str:
    """Drop pronunciations/dates in brackets and citation marks that sound awful when spoken."""
    previous = None
    while previous != text:
        previous = text
        text = _PARENTHESES.sub("", text)
    text = _CITATIONS.sub("", text)
    return re.sub(r"\s{2,}", " ", text).replace(" ,", ",").strip()


def _summary(title: str, sentences: int) -> str:
    return _clean(wikipedia.summary(title, sentences=sentences, auto_suggest=False, redirect=True))


def _fetch(query: str, sentences: int) -> WikiAnswer:
    """Runs in a worker thread; raises NotFound or wikipedia/requests exceptions."""
    titles = wikipedia.search(query, results=5)
    if not titles:
        raise NotFound(query)
    ambiguous = False
    for title in titles[:3]:
        try:
            return WikiAnswer(title, _summary(title, sentences), ambiguous)
        except DisambiguationError as exc:
            ambiguous = True
            for option in exc.options[:3]:
                try:
                    return WikiAnswer(option, _summary(option, sentences), True)
                except (DisambiguationError, PageError):
                    continue
        except PageError:
            continue
    raise NotFound(query)


def register(router: CommandRouter, log: SyraLogger) -> None:
    if wikipedia is None:
        log.warn("The 'wikipedia' package is missing - lookups disabled. Run: pip install wikipedia")

        def unavailable(match: re.Match, ctx: CommandContext) -> str:
            return "Wikipedia lookups are not installed."

        router.register("wikipedia", [QUERY_PATTERN], unavailable)
        return

    wikipedia.set_lang(cfg.WIKI_LANGUAGE)

    def handle_query(match: re.Match, ctx: CommandContext) -> str:
        query = match.group("query").strip()
        future = _POOL.submit(_fetch, query, cfg.WIKI_SENTENCES)
        try:
            answer = future.result(timeout=cfg.WIKI_TIMEOUT)
        except FutureTimeout:
            raise CommandError("Wikipedia is taking too long to respond.") from None
        except NotFound:
            raise CommandError(f"I couldn't find anything about {query}.") from None
        except (requests.RequestException, HTTPTimeoutError) as exc:
            log.debug(f"Wikipedia network error: {exc}")
            raise CommandError("I can't reach Wikipedia right now. Please check the internet connection.") from None
        except WikipediaException as exc:
            log.warn(f"Wikipedia error: {exc}")
            raise CommandError("Wikipedia returned an error for that request.") from None
        if answer.ambiguous:
            return f"There are several topics called {query}. The closest is {answer.title}. {answer.summary}"
        return answer.summary

    router.register("wikipedia", [QUERY_PATTERN], handle_query)
