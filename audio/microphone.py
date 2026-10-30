"""Microphone management for SAIRA.

Design goals (each one addresses a problem of the old implementation):

* ONE PyAudio instance and ONE input stream, opened once and kept alive for the
  whole session. Nothing is opened or closed per command.
* Device selection happens once (and again only after a failure): explicit
  config wins, otherwise PipeWire/PulseAudio-backed devices are preferred and
  every candidate is verified by really opening it.
* We use our own small AudioSource instead of speech_recognition.Microphone.
  Its __enter__ swallows open errors and leaves `stream = None`, which is the
  origin of "NoneType object has no attribute close".
* Failures (OSError from the stream) trigger automatic reopen, then a full
  re-detection, then MicrophoneError for the assistant's recovery loop.
* Recognition outcomes are classified: OK / NO_SPEECH / UNCLEAR / SERVICE_ERROR.
"""
from __future__ import annotations

import enum
import time
from dataclasses import dataclass
from typing import Optional

import speech_recognition as sr

import config as cfg
from audio.audio_env import suppress_c_stderr
from utils.logger import SairaLogger

try:
    import pyaudio
except ImportError:          # reported with an install hint when the mic is first used
    pyaudio = None           # type: ignore[assignment]

_PYAUDIO_HINT = ("PyAudio is not installed. Run: sudo apt install portaudio19-dev python3-dev "
                 "build-essential && pip install PyAudio")


class MicrophoneError(RuntimeError):
    """The microphone cannot be used right now."""


class ListenStatus(enum.Enum):
    OK = "ok"                        # text recognized
    NO_SPEECH = "no_speech"          # nothing was heard
    UNCLEAR = "unclear"              # audio captured, but not understood
    SERVICE_ERROR = "service_error"  # recognition service unreachable


@dataclass(frozen=True)
class ListenResult:
    status: ListenStatus
    text: str = ""


@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str
    channels: int
    default_rate: int
    host_api: str

    @property
    def label(self) -> str:
        return f"{self.name} (index {self.index})"


# ─────────────────────────── audio source ─────────────────────────
class _StreamAdapter:
    """The tiny stream interface speech_recognition expects (`read(size)`)."""

    def __init__(self, pa_stream) -> None:
        self._stream = pa_stream

    def read(self, size: int) -> bytes:
        return self._stream.read(size, exception_on_overflow=False)

    def available(self) -> int:
        return int(self._stream.get_read_available())

    def close(self) -> None:
        try:
            if not self._stream.is_stopped():
                self._stream.stop_stream()
        finally:
            self._stream.close()


class PersistentSource(sr.AudioSource):
    """A microphone AudioSource whose lifetime we control explicitly."""

    SAMPLE_WIDTH = 2   # 16-bit samples

    def __init__(self, pa, device_index: int, sample_rate: int, chunk: int) -> None:  # noqa: D107
        self._pa = pa
        self.device_index = device_index
        self.SAMPLE_RATE = sample_rate
        self.CHUNK = chunk
        self.stream: Optional[_StreamAdapter] = None

    def open(self) -> None:
        with suppress_c_stderr():
            raw = self._pa.open(format=pyaudio.paInt16, channels=1, rate=self.SAMPLE_RATE,
                                input=True, input_device_index=self.device_index,
                                frames_per_buffer=self.CHUNK)
        self.stream = _StreamAdapter(raw)

    def close(self) -> None:
        stream, self.stream = self.stream, None
        if stream is not None:
            stream.close()


# ───────────────────────── device discovery ───────────────────────
def enumerate_input_devices(pa) -> list[InputDevice]:
    """All PortAudio devices that can record."""
    devices: list[InputDevice] = []
    for index in range(pa.get_device_count()):
        try:
            info = pa.get_device_info_by_index(index)
        except OSError:
            continue
        if int(info.get("maxInputChannels", 0)) <= 0:
            continue
        try:
            host_api = str(pa.get_host_api_info_by_index(int(info["hostApi"]))["name"])
        except (OSError, KeyError, ValueError):
            host_api = "?"
        devices.append(InputDevice(index=index, name=str(info["name"]),
                                   channels=int(info["maxInputChannels"]),
                                   default_rate=int(info.get("defaultSampleRate") or 0),
                                   host_api=host_api))
    return devices


def rank_device(device: InputDevice) -> int:
    """Lower is better. PipeWire/Pulse-backed devices first, raw hardware last."""
    name = device.name.lower()
    for position, preferred in enumerate(cfg.MIC_PREFERRED_NAMES):
        if name == preferred.lower():
            return position
    if "hdmi" in name:
        return 300
    if "hw:" in name:
        return 200
    return 100


def _is_auto(setting: object) -> bool:
    return setting is None or str(setting).strip().lower() in ("", "auto")


# ─────────────────────────── the manager ──────────────────────────
class MicrophoneManager:
    """Owns the recognizer and the microphone stream for the whole session."""

    def __init__(self, log: SairaLogger) -> None:
        self.log = log
        self.recognizer = self._build_recognizer()
        self.device: Optional[InputDevice] = None
        self._rate = cfg.SAMPLE_RATE
        self._pa = None
        self._source: Optional[PersistentSource] = None
        self.last_audio: Optional[sr.AudioData] = None   # last phrase sent to the recognizer (diagnostics)

    @property
    def sample_rate(self) -> int:
        return self._rate

    # ── setup ────────────────────────────────────────────────────
    @staticmethod
    def _build_recognizer() -> sr.Recognizer:
        r = sr.Recognizer()
        r.energy_threshold = cfg.ENERGY_THRESHOLD_INITIAL
        r.dynamic_energy_threshold = cfg.DYNAMIC_ENERGY_THRESHOLD
        r.dynamic_energy_ratio = cfg.DYNAMIC_ENERGY_RATIO
        r.dynamic_energy_adjustment_damping = cfg.DYNAMIC_ENERGY_DAMPING
        r.pause_threshold = cfg.PAUSE_THRESHOLD
        r.phrase_threshold = cfg.PHRASE_THRESHOLD
        r.non_speaking_duration = min(cfg.NON_SPEAKING_DURATION, cfg.PAUSE_THRESHOLD)
        r.operation_timeout = cfg.RECOGNITION_TIMEOUT
        return r

    def _ensure_pyaudio(self):
        if pyaudio is None:
            raise MicrophoneError(_PYAUDIO_HINT)
        if self._pa is None:
            try:
                with suppress_c_stderr():
                    self._pa = pyaudio.PyAudio()
            except OSError as exc:
                raise MicrophoneError(f"PortAudio could not initialise: {exc}") from exc
        return self._pa

    def _reset_pyaudio(self) -> None:
        """Drop the PortAudio instance so the next use re-scans devices (after hot-plug/PipeWire restart)."""
        pa, self._pa = self._pa, None
        if pa is not None:
            try:
                with suppress_c_stderr():
                    pa.terminate()
            except OSError as exc:
                self.log.debug(f"PortAudio terminate: {exc}")

    def initialize(self) -> None:
        """Select a working device, open the persistent stream. Raises MicrophoneError on failure."""
        device, rate = self._select_device()
        self._open(device, rate)
        self.log.saira(f"Microphone: {device.name} (index {device.index}, {rate} Hz)")
        self.log.saira("Microphone ready.")

    # ── device selection ─────────────────────────────────────────
    def ordered_devices(self) -> list[InputDevice]:
        """Input devices in the order they will be tried (config choice first)."""
        pa = self._ensure_pyaudio()
        with suppress_c_stderr():
            devices = enumerate_input_devices(pa)
        ordered = sorted(devices, key=lambda d: (rank_device(d), d.index))
        setting = cfg.MIC_DEVICE_INDEX
        if _is_auto(setting):
            return ordered
        chosen = self._match_setting(devices, setting)
        if chosen is None:
            self.log.warn(f"MIC_DEVICE_INDEX={setting!r} is not an input device; using auto-detection.")
            return ordered
        return [chosen] + [d for d in ordered if d.index != chosen.index]

    @staticmethod
    def _match_setting(devices: list[InputDevice], setting: object) -> Optional[InputDevice]:
        text = str(setting).strip()
        if text.isdigit():
            return next((d for d in devices if d.index == int(text)), None)
        needle = text.lower()
        return next((d for d in sorted(devices, key=lambda d: d.index) if needle in d.name.lower()), None)

    def _probe(self, device: InputDevice) -> Optional[int]:
        """Really open `device` for a moment. Returns a working sample rate, or None."""
        pa = self._ensure_pyaudio()
        rates = [cfg.SAMPLE_RATE]
        if device.default_rate and device.default_rate != cfg.SAMPLE_RATE:
            rates.append(device.default_rate)
        for rate in rates:
            stream = None
            try:
                with suppress_c_stderr():
                    stream = pa.open(format=pyaudio.paInt16, channels=1, rate=rate, input=True,
                                     input_device_index=device.index, frames_per_buffer=cfg.CHUNK_SIZE)
                    stream.read(cfg.CHUNK_SIZE, exception_on_overflow=False)
                return rate
            except OSError as exc:
                self.log.debug(f"Probe failed for {device.label} at {rate} Hz: {exc}")
            finally:
                if stream is not None:
                    try:
                        with suppress_c_stderr():
                            stream.close()
                    except OSError:
                        pass
        return None

    def _select_device(self) -> tuple[InputDevice, int]:
        candidates = self.ordered_devices()
        if not candidates:
            raise MicrophoneError("No input devices found. Is a microphone connected and enabled in GNOME Settings > Sound?")
        for device in candidates:
            rate = self._probe(device)
            if rate is not None:
                return device, rate
            self.log.warn(f"Input device unusable: {device.label}")
        raise MicrophoneError("No working input device found (all candidates failed to open).")

    def _open(self, device: InputDevice, rate: int) -> None:
        self.close_stream()
        source = PersistentSource(self._ensure_pyaudio(), device.index, rate, cfg.CHUNK_SIZE)
        try:
            source.open()
        except OSError as exc:
            raise MicrophoneError(f"Could not open {device.label}: {exc}") from exc
        self._source, self.device, self._rate = source, device, rate

    # ── lifecycle ────────────────────────────────────────────────
    @property
    def is_open(self) -> bool:
        return self._source is not None and self._source.stream is not None

    def close_stream(self) -> None:
        source, self._source = self._source, None
        if source is not None:
            try:
                with suppress_c_stderr():
                    source.close()
            except OSError as exc:
                self.log.debug(f"Closing stream: {exc}")

    def close(self) -> None:
        """Release everything (shutdown)."""
        self.close_stream()
        self._reset_pyaudio()

    def _reopen(self, attempt: int) -> None:
        """Recover from a stream failure: same device first, then full re-detection."""
        self.close_stream()
        time.sleep(cfg.MIC_REOPEN_DELAY * (attempt + 1))
        if self.device is not None:
            try:
                self._open(self.device, self._rate)
                self.log.saira("Microphone recovered.")
                return
            except MicrophoneError as exc:
                self.log.warn(f"Reopening failed: {exc}. Re-detecting devices...")
        self._reset_pyaudio()
        device, rate = self._select_device()
        self._open(device, rate)
        self.log.saira(f"Microphone: {device.label}")

    def _ensure_open(self) -> PersistentSource:
        if not self.is_open:
            self._reopen(0)
        assert self._source is not None
        return self._source

    # ── calibration / buffer control ─────────────────────────────
    def calibrate(self, duration: float) -> None:
        """Measure ambient noise once and set the energy threshold from it."""
        previous = self.recognizer.energy_threshold
        for attempt in range(cfg.MIC_MAX_REOPEN_ATTEMPTS + 1):
            source = self._ensure_open()
            try:
                self.recognizer.adjust_for_ambient_noise(source, duration=duration)
                break
            except OSError as exc:
                self.log.warn(f"Microphone error during calibration: {exc}")
                if attempt == cfg.MIC_MAX_REOPEN_ATTEMPTS:
                    raise MicrophoneError("Microphone kept failing during calibration.") from exc
                self._reopen(attempt)
        threshold = self.recognizer.energy_threshold
        if threshold > cfg.ENERGY_THRESHOLD_MAX:
            # Someone was talking during calibration, OR the input signal is abnormally hot
            # (clipping / DC offset / noise). Keep the previous threshold, but say so.
            ambient = threshold / max(cfg.DYNAMIC_ENERGY_RATIO, 1.0)
            self.log.warn(f"Ambient level is abnormally high (about {ambient:.0f}; a quiet room is 20-100). "
                          f"Keeping threshold {previous:.0f}. Check input volume/port, then run: python main.py --test-mic")
            self.recognizer.energy_threshold = previous
        else:
            self._clamp_threshold()
        self.log.debug(f"Energy threshold: {self.recognizer.energy_threshold:.0f}")

    def _clamp_threshold(self) -> None:
        r = self.recognizer
        r.energy_threshold = min(max(r.energy_threshold, cfg.ENERGY_THRESHOLD_MIN), cfg.ENERGY_THRESHOLD_MAX)

    def flush(self) -> None:
        """Discard audio buffered while we were not listening (e.g. SAIRA's own voice)."""
        if not self.is_open:
            return
        stream = self._source.stream  # type: ignore[union-attr]
        try:
            for _ in range(64):
                available = stream.available()
                if available <= 0:
                    break
                stream.read(min(available, 4096))
        except OSError as exc:
            self.log.debug(f"Flush skipped: {exc}")

    def record_raw(self, seconds: float) -> bytes:
        """Record `seconds` of raw 16-bit mono audio from the open stream (no VAD, no recognition)."""
        self.flush()
        source = self._ensure_open()
        chunks = int(seconds * source.SAMPLE_RATE / source.CHUNK) + 1
        frames: list[bytes] = []
        try:
            for _ in range(chunks):
                frames.append(source.stream.read(source.CHUNK))    # type: ignore[union-attr]
        except OSError as exc:
            raise MicrophoneError(f"Microphone read failed during recording: {exc}") from exc
        return b"".join(frames)

    # ── listening ────────────────────────────────────────────────
    def listen(self, timeout: float, phrase_time_limit: Optional[float]) -> ListenResult:
        """Wait up to `timeout` s for speech, capture one phrase, recognize it."""
        audio = self._capture(timeout, phrase_time_limit)
        if audio is None:
            return ListenResult(ListenStatus.NO_SPEECH)
        return self._recognize(audio)

    def _capture(self, timeout: float, phrase_time_limit: Optional[float]) -> Optional[sr.AudioData]:
        for attempt in range(cfg.MIC_MAX_REOPEN_ATTEMPTS + 1):
            source = self._ensure_open()
            try:
                audio = self.recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
                self._clamp_threshold()
                self.last_audio = audio
                return audio
            except sr.WaitTimeoutError:
                self._clamp_threshold()
                return None
            except OSError as exc:
                self.log.warn(f"Microphone stream error: {exc}. Reinitializing...")
                if attempt == cfg.MIC_MAX_REOPEN_ATTEMPTS:
                    break
                self._reopen(attempt)
        raise MicrophoneError("Microphone kept failing after several reopen attempts.")

    def _recognize(self, audio: sr.AudioData) -> ListenResult:
        try:
            text = self.recognizer.recognize_google(audio, language=cfg.RECOGNITION_LANGUAGE)
        except sr.UnknownValueError:
            return ListenResult(ListenStatus.UNCLEAR)
        except (sr.RequestError, OSError) as exc:      # network down, timeout, HTTP error
            self.log.debug(f"Recognition service error: {exc}")
            return ListenResult(ListenStatus.SERVICE_ERROR)
        text = str(text).strip()
        if not text:
            return ListenResult(ListenStatus.UNCLEAR)
        return ListenResult(ListenStatus.OK, text)
