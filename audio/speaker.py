"""Speech output: gTTS for synthesis, mpg123 for playback.

Pipeline (all off the main thread, so the assistant never blocks on the network):

    say(text) -> split into sentence chunks -> [synth thread: gTTS -> mp3]
                                            -> [play thread: mpg123]

While chunk N plays, chunk N+1 is already being synthesized, so long answers
start quickly and flow without gaps. Short phrases ("Yes Sir.", "Opening
Chrome.") are cached on disk, so repeats are instant. Everything else is
written to a private temp directory and deleted right after playback; the
directory itself is removed on shutdown.
"""
from __future__ import annotations

import atexit
import hashlib
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Iterable, Optional

import requests

import config as cfg
from utils.logger import SyraLogger

try:
    from gtts import gTTS
    from gtts.tts import gTTSError
except ImportError:          # reported by Speaker.start()
    gTTS = None              # type: ignore[assignment]
    gTTSError = Exception    # type: ignore[assignment,misc]

_MIN_CHUNK = 30      # merge very short sentences so playback is not choppy
_MAX_CHUNK = 220     # keep each gTTS request small for a fast first word
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def split_for_speech(text: str) -> list[str]:
    """Split text into sentence-sized chunks (short sentences are merged)."""
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if not current:
            current = sentence
        elif len(current) < _MIN_CHUNK and len(current) + 1 + len(sentence) <= _MAX_CHUNK:
            current = f"{current} {sentence}"
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks


class Speaker:
    """Non-blocking text-to-speech with a clean shutdown and no overlapping audio."""

    def __init__(self, log: SyraLogger) -> None:
        self.log = log
        self.available = False
        self._mpg123: Optional[str] = None
        self._output_args: list[str] = []
        self._tmpdir: Optional[tempfile.TemporaryDirectory] = None
        self._cache_dir: Path = Path(cfg.TTS_CACHE_DIR)
        self._synth_q: queue.Queue = queue.Queue()
        self._play_q: queue.Queue = queue.Queue()
        self._cond = threading.Condition()
        self._outstanding = 0
        self._generation = 0
        self._proc: Optional[subprocess.Popen] = None
        self._proc_lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._last_synth_warning = 0.0

    # ── setup / teardown ─────────────────────────────────────────
    def start(self) -> None:
        if not cfg.TTS_ENABLED:
            self.log.warn("Speech output is disabled (TTS_ENABLED = False).")
            return
        if gTTS is None:
            self.log.error("gTTS is not installed - speech disabled. Run: pip install gTTS")
            return
        self._mpg123 = shutil.which("mpg123")
        if self._mpg123 is None:
            self.log.error("mpg123 is not installed - speech disabled. Run: sudo apt install mpg123")
            return
        try:
            self._tmpdir = tempfile.TemporaryDirectory(prefix="syra_tts_")
            if cfg.TTS_CACHE_ENABLED:
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                self._prune_cache()
        except OSError as exc:
            self.log.warn(f"TTS cache unavailable ({exc}); continuing without it.")
        if self._tmpdir is None:
            self.log.error("Could not create a temporary directory - speech disabled.")
            return
        self._output_args = self._detect_output_args()
        atexit.register(self.shutdown)
        for target, name in ((self._synth_loop, "syra-tts-synth"), (self._play_loop, "syra-tts-play")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)
        self.available = True

    def shutdown(self) -> None:
        if not self._threads and self._tmpdir is None:
            return
        self.stop()
        self._synth_q.put(None)
        self._play_q.put(None)
        for thread in self._threads:
            thread.join(timeout=2)
        self._threads.clear()
        self.available = False
        if self._tmpdir is not None:
            self._tmpdir.cleanup()
            self._tmpdir = None

    def _detect_output_args(self) -> list[str]:
        choice = cfg.MPG123_OUTPUT
        if choice in (None, "", "default"):
            return []
        if choice != "auto":
            return ["-o", str(choice)]
        try:
            out = subprocess.run([self._mpg123, "--list-modules"], capture_output=True, text=True,
                                 timeout=3, check=False, env={**os.environ, "LC_ALL": "C"}).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        modules = {line.split()[0] for line in out.splitlines() if line.strip()}
        return ["-o", "pulse"] if "pulse" in modules else []

    # ── public API ───────────────────────────────────────────────
    @property
    def is_speaking(self) -> bool:
        return self._outstanding > 0

    def say(self, text: str) -> None:
        """Queue `text` for speech and return immediately."""
        if not self.available or not text.strip():
            return
        for chunk in split_for_speech(self._pronounce(text)):
            with self._cond:
                self._outstanding += 1
            self._synth_q.put((self._generation, chunk))

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until everything queued has been spoken. Returns False on timeout."""
        with self._cond:
            return self._cond.wait_for(lambda: self._outstanding == 0, timeout)

    def stop(self) -> None:
        """Cancel queued speech and cut off the phrase currently playing."""
        self._generation += 1
        self._drain(self._synth_q, has_file=False)
        self._drain(self._play_q, has_file=True)
        with self._proc_lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()

    def prewarm(self, phrases: Iterable[str]) -> None:
        """Synthesize fixed phrases in the background so their first playback is instant."""
        if not self.available or not cfg.TTS_CACHE_ENABLED:
            return

        def work() -> None:
            for phrase in phrases:
                for chunk in split_for_speech(self._pronounce(phrase)):
                    try:
                        self._synthesize(chunk)
                    except (gTTSError, AssertionError, ValueError, requests.RequestException, OSError) as exc:
                        self.log.debug(f"Prewarm failed for {chunk!r}: {exc}")
                        return

        threading.Thread(target=work, name="syra-tts-prewarm", daemon=True).start()

    # ── internals ────────────────────────────────────────────────
    @staticmethod
    def _pronounce(text: str) -> str:
        for written, spoken in cfg.TTS_PRONUNCIATIONS.items():
            text = re.sub(rf"\b{re.escape(written)}\b", spoken, text)
        return text

    def _finish_one(self) -> None:
        with self._cond:
            self._outstanding = max(0, self._outstanding - 1)
            self._cond.notify_all()

    def _drain(self, q: queue.Queue, has_file: bool) -> None:
        while True:
            try:
                item = q.get_nowait()
            except queue.Empty:
                return
            if item is None:          # shutdown sentinel: put it back for the worker
                q.put(None)
                return
            if has_file:
                _, path, cached = item
                if not cached:
                    self._unlink(path)
            self._finish_one()

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass    # the temp dir is removed at shutdown anyway

    def _cache_key(self, text: str) -> str:
        raw = f"{cfg.TTS_LANG}|{cfg.TTS_TLD}|{cfg.TTS_SLOW}|{text}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]

    def _synthesize(self, text: str) -> tuple[Path, bool]:
        """Return (mp3 path, is_cached). Raises gTTS/network/OS errors for the caller to handle."""
        cacheable = cfg.TTS_CACHE_ENABLED and len(text) <= cfg.TTS_CACHE_MAX_CHARS
        if cacheable:
            cached = self._cache_dir / f"{self._cache_key(text)}.mp3"
            if cached.exists():
                try:
                    os.utime(cached)      # mark as recently used for pruning
                except OSError:
                    pass
                return cached, True
        assert self._tmpdir is not None
        fd, tmp_name = tempfile.mkstemp(suffix=".mp3", dir=self._tmpdir.name)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fp:
                gTTS(text=text, lang=cfg.TTS_LANG, tld=cfg.TTS_TLD, slow=cfg.TTS_SLOW).write_to_fp(fp)
        except BaseException:
            self._unlink(tmp)
            raise
        if cacheable:
            target = self._cache_dir / f"{self._cache_key(text)}.mp3"
            try:
                os.replace(tmp, target)   # atomic: never a half-written cache file
                return target, True
            except OSError:
                pass                      # cache dir not writable: play from the temp file
        return tmp, False

    def _prune_cache(self) -> None:
        files = sorted(self._cache_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
        for old in files[:max(0, len(files) - cfg.TTS_CACHE_MAX_FILES)]:
            self._unlink(old)

    def _warn_synth(self, exc: Exception) -> None:
        now = time.monotonic()
        if now - self._last_synth_warning > 15:
            self._last_synth_warning = now
            self.log.warn(f"Speech synthesis failed (internet connection?): {exc}")

    def _synth_loop(self) -> None:
        while True:
            item = self._synth_q.get()
            if item is None:
                return
            generation, text = item
            if generation != self._generation:
                self._finish_one()
                continue
            try:
                path, cached = self._synthesize(text)
            except (gTTSError, AssertionError, ValueError, requests.RequestException, OSError) as exc:
                self._warn_synth(exc)
                self._finish_one()
                continue
            self._play_q.put((generation, path, cached))

    def _play_loop(self) -> None:
        while True:
            item = self._play_q.get()
            if item is None:
                return
            generation, path, cached = item
            try:
                if generation == self._generation:
                    self._play(path)
            finally:
                if not cached:
                    self._unlink(path)
                self._finish_one()

    def _play(self, path: Path) -> None:
        argv = [self._mpg123, "-q", *self._output_args, str(path)]
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, text=True)
        except OSError as exc:
            self.log.error(f"Could not start mpg123: {exc}")
            return
        with self._proc_lock:
            self._proc = proc
        _, err = proc.communicate()
        with self._proc_lock:
            self._proc = None
        if proc.returncode not in (0, -15, -9):           # -15/-9: we stopped it on purpose
            self.log.warn(f"mpg123 failed ({proc.returncode}): {(err or '').strip()[:200]}")
