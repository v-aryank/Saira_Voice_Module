"""Microphone diagnostics: `python main.py --list-mics` and `python main.py --test-mic`.

--test-mic works in two stages so a problem can be pinned to one side:
  1. RAW LEVELS   - records ambient noise and your voice straight from the stream (no
                    speech detection, no Google) and reports level, peak and clipping.
  2. RECOGNITION  - the real listen -> Google path, 3 attempts, saving what was sent.
Every recording is written to /tmp so you can play it back and hear exactly what Saira hears.
"""
from __future__ import annotations

import math
import sys
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

import config as cfg
from audio.audio_env import detect_audio_system
from audio.microphone import ListenStatus, MicrophoneError, MicrophoneManager, rank_device
from utils.logger import SairaLogger

OUT_DIR = Path("/tmp")


@dataclass(frozen=True)
class Levels:
    rms: float        # total level (what Saira's speech detector sees, DC included)
    ac_rms: float     # level after removing any constant offset (the actual audio)
    dc: float         # constant offset; should be near 0
    peak: int
    clipped: float    # fraction of samples at the limit


def level_stats(raw: bytes) -> Levels:
    """Level statistics of 16-bit mono PCM."""
    samples = array("h")
    samples.frombytes(raw[: len(raw) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    count = len(samples)
    if count == 0:
        return Levels(0.0, 0.0, 0.0, 0, 0.0)
    mean = sum(samples) / count
    power = sum(s * s for s in samples) / count
    rms = math.sqrt(power)
    ac_rms = math.sqrt(max(0.0, power - mean * mean))
    peak = max(abs(s) for s in samples)
    clipped = sum(1 for s in samples if abs(s) >= 32000) / count
    return Levels(rms, ac_rms, mean, peak, clipped)


def save_wav(path: Path, raw: bytes, rate: int) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(raw)


def verdict(ambient: Levels, speech: Levels) -> list[str]:
    """Plain-language reading of the levels (rules of thumb, not hard limits)."""
    notes: list[str] = []
    if speech.peak < 50:
        return ["Signal is essentially silent: the source is muted, or it is the wrong input."]
    if abs(ambient.dc) > 300 or abs(speech.dc) > 300:
        notes.append(f"DC OFFSET: the signal is shifted by about {ambient.dc:.0f}. Saira's speech detector "
                     "sees that as constant loud noise. Try another source (--mic pipewire / default) "
                     "or another input port in GNOME Settings > Sound.")
    if speech.clipped > 0.005:
        notes.append(f"CLIPPING: {speech.clipped:.1%} of samples are at the limit. Input is too hot; "
                     "lower the input volume / mic boost. Distorted audio is often 'not understood'.")
    if speech.peak < 2500:
        notes.append("Voice is very quiet (peak < 2500). Raise the input volume.")
    if ambient.ac_rms > 150:
        notes.append(f"High noise floor (ambient level {ambient.ac_rms:.0f}; a quiet room is typically 20-100). "
                     "Gain may be too high, or something noisy is nearby (fan, fizz, bad input port).")
    if ambient.ac_rms > 0 and speech.ac_rms / ambient.ac_rms < 3:
        notes.append("Your voice is barely louder than the background (ratio < 3).")
    if not notes:
        notes.append("Levels look healthy.")
    return notes


def list_microphones(log: SairaLogger) -> int:
    info = detect_audio_system()
    log.saira(f"Audio system: {info.server}")
    if info.default_source:
        log.saira(f"System default source (GNOME Settings > Sound): {info.default_source}")
    mic = MicrophoneManager(log)
    try:
        devices = mic.ordered_devices()
    except MicrophoneError as exc:
        log.error(str(exc))
        return 1
    finally:
        mic.close()
    if not devices:
        log.error("No input devices found.")
        return 1
    print(f"\n{'INDEX':<6} {'NAME':<52} {'API':<8} {'RATE':<7} PRIORITY")
    for position, dev in enumerate(devices):
        marker = "  <- would be tried first" if position == 0 else ""
        print(f"{dev.index:<6} {dev.name[:50]:<52} {dev.host_api[:6]:<8} {dev.default_rate:<7} "
              f"{rank_device(dev)}{marker}")
    print("\nUse a device:  python main.py --mic <index or name>   (or set MIC_DEVICE_INDEX in config.py)")
    return 0


def _raw_stage(mic: MicrophoneManager, log: SairaLogger) -> None:
    rate = mic.sample_rate
    log.saira("STAGE 1/2 - raw levels. Stay completely quiet for 2 seconds...")
    ambient = mic.record_raw(2.0)
    log.saira("Now speak normally for 4 seconds, e.g. 'open Chrome and tell me the time'...")
    speech = mic.record_raw(4.0)

    a = level_stats(ambient)
    v = level_stats(speech)
    save_wav(OUT_DIR / "saira_mic_ambient.wav", ambient, rate)
    save_wav(OUT_DIR / "saira_mic_speech.wav", speech, rate)

    print(f"\n  Ambient : level {a.ac_rms:7.0f}   DC offset {a.dc:7.0f}   peak {a.peak:6d}   (total RMS {a.rms:.0f})")
    print(f"  Speech  : level {v.ac_rms:7.0f}   DC offset {v.dc:7.0f}   peak {v.peak:6d}   clipped {v.clipped:.2%}")
    for note in verdict(a, v):
        print(f"  -> {note}")
    print(f"  Recordings: {OUT_DIR}/saira_mic_ambient.wav , {OUT_DIR}/saira_mic_speech.wav\n")


def test_microphone(log: SairaLogger) -> int:
    mic = MicrophoneManager(log)
    try:
        mic.initialize()
        _raw_stage(mic, log)

        log.saira("STAGE 2/2 - recognition. Calibrating ambient noise, stay quiet for a second...")
        before = mic.recognizer.energy_threshold
        mic.calibrate(cfg.CALIBRATION_SECONDS_STARTUP)
        log.saira(f"Energy threshold: {before:.0f} -> {mic.recognizer.energy_threshold:.0f}")

        outcomes = {status: 0 for status in ListenStatus}
        for attempt in range(1, 4):
            log.saira(f"Test {attempt}/3 - say something now...")
            result = mic.listen(timeout=6, phrase_time_limit=6)
            outcomes[result.status] += 1
            if result.status is ListenStatus.OK:
                log.saira(f"Heard: {result.text}")
            elif result.status is ListenStatus.NO_SPEECH:
                log.saira("No voice detected.")
            elif result.status is ListenStatus.UNCLEAR:
                log.saira("Could not understand.")
            else:
                log.saira("Recognition service unavailable.")
            audio = mic.last_audio
            if audio is not None and result.status is not ListenStatus.NO_SPEECH:
                raw = audio.get_raw_data()
                lv = level_stats(raw)
                seconds = len(raw) / 2 / audio.sample_rate
                path = OUT_DIR / f"saira_mic_recognition_{attempt}.wav"
                save_wav(path, raw, audio.sample_rate)
                print(f"    sent to recognizer: {seconds:.1f}s, level {lv.ac_rms:.0f}, DC {lv.dc:.0f}, "
                      f"peak {lv.peak}, clipped {lv.clipped:.2%}  ->  {path}")
            mic.last_audio = None

        print(f"\nSummary: {outcomes[ListenStatus.OK]}/3 recognized, "
              f"{outcomes[ListenStatus.NO_SPEECH]} silent, {outcomes[ListenStatus.UNCLEAR]} unclear, "
              f"{outcomes[ListenStatus.SERVICE_ERROR]} service errors.")
        print(f"Play any recording to hear what Saira hears:  aplay {OUT_DIR}/saira_mic_speech.wav")
        return 0 if outcomes[ListenStatus.OK] else 2
    except MicrophoneError as exc:
        log.error(f"Microphone test failed: {exc}")
        return 1
    finally:
        mic.close()
