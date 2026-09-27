"""Speech synthesis with nothing installed: stdlib only, no system voice.

`RecordingTTS` writes a WAV that carries text through the samples and is not
audible as words. `Pyttsx3TTS` produces real speech and needs a platform
voice — SAPI on Windows, espeak elsewhere — which is a system package, is
absent on a plain Linux runner, and has no bytes-identical behaviour across
machines.

This is the third thing: a formant synthesizer in pure Python. It produces
audible, recognisably speech-like audio for any text, on any machine, with
nothing to install and nothing to configure, and it produces the same bytes
every time.

**It is crude, and the docstring is the wrong place to claim otherwise.**
What it can be trusted for is written in `SPEECH_IS_NOT_TRANSCRIBABLE`
below, next to the measurement that establishes it.

The model, which is about as simple as a formant synthesizer gets:

- Text is split into vowel and consonant units by spelling, with a handful
  of digraph rules. English spelling is not phonetic and this makes no
  attempt to fix that.
- A vowel is three resonators at that vowel's formant frequencies, driven
  by a buzz at the pitch.
- A fricative is filtered noise; a stop is a silence and a burst; a nasal
  is a low resonance.
- Pitch falls across an utterance, because a flat pitch sounds like a
  machine even by the standards of this machine.
"""

from __future__ import annotations

import hashlib
import math
import os
import random
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 16000

SPEECH_IS_NOT_TRANSCRIBABLE = True
"""Whisper does not read this synthesizer's output.

Measured 2026-09-27 against a live engine running whisper `base`: the phrase
"approve the deploy" came back as unrelated text, and so did every other
phrase tried. Two-formant-per-vowel synthesis with no coarticulation, no
prosody to speak of and spelling-based phonemes is not close enough to
speech for a model trained on human recordings.

So this backend closes the *audio* leg of a round trip and not the
*transcription* leg. `VoiceSession.round_trip` against a real engine needs
`Pyttsx3TTS` or another real voice. What this is for:

- hearing what a loop said, on a machine with no system voice;
- an audible output in CI, where there is no SAPI and no espeak;
- a deterministic WAV that is also a waveform, when `RecordingTTS`'s
  codec output is too far from audio to be a useful stand-in.

The constant is here so that a caller can branch on it rather than
rediscovering it, and so the day somebody improves this enough to be
transcribed, one edit and one test move together.
"""

# Formant frequencies (F1, F2, F3) in Hz. Textbook values for a male voice;
# the point is that the vowels differ from each other, not that they match
# any particular speaker.
VOWELS: dict[str, tuple[int, int, int]] = {
    "iy": (270, 2290, 3010),   # beat
    "ih": (390, 1990, 2550),   # bit
    "eh": (530, 1840, 2480),   # bet
    "ae": (660, 1720, 2410),   # bat
    "aa": (730, 1090, 2440),   # father
    "ao": (570, 840, 2410),    # bought
    "uh": (440, 1020, 2240),   # book
    "uw": (300, 870, 2240),    # boot
    "er": (490, 1350, 1690),   # bird
    "ax": (500, 1500, 2500),   # about, the reduced one
}

# Spelling to vowel, in the crudest way that still distinguishes them.
VOWEL_SPELLING: dict[str, str] = {
    "a": "ae", "e": "eh", "i": "ih", "o": "aa", "u": "uh", "y": "ih",
    "ee": "iy", "ea": "iy", "oo": "uw", "ou": "uh", "ow": "uh",
    "oa": "ao", "au": "ao", "aw": "ao", "ai": "eh", "ay": "eh",
    "er": "er", "ir": "er", "ur": "er", "ar": "aa", "or": "ao",
}

FRICATIVES = {
    "s": (5000, 0.30), "z": (4500, 0.28), "f": (6000, 0.22), "v": (5500, 0.20),
    "th": (6500, 0.20), "sh": (3000, 0.34), "h": (1500, 0.14), "ch": (3000, 0.26),
    "j": (2500, 0.24),
}
STOPS = {"p", "t", "k", "b", "d", "g", "c", "q", "x"}
NASALS = {"m": 250, "n": 350, "ng": 300}
LIQUIDS = {"l": (400, 1200, 2600), "r": (490, 1350, 1690), "w": (300, 870, 2240)}

DIGRAPHS = ("th", "sh", "ch", "ng", "ee", "ea", "oo", "ou", "ow", "oa",
            "au", "aw", "ai", "ay", "er", "ir", "ur", "ar", "or")


def _units(word: str) -> list[tuple[str, str]]:
    """Split a word into (kind, key) units. Digraphs first, then letters."""
    out: list[tuple[str, str]] = []
    i = 0
    lowered = word.lower()
    while i < len(lowered):
        pair = lowered[i : i + 2]
        token = pair if pair in DIGRAPHS else lowered[i]
        i += len(token)

        if token in VOWEL_SPELLING:
            out.append(("vowel", VOWEL_SPELLING[token]))
        elif token in FRICATIVES:
            out.append(("fricative", token))
        elif token in NASALS:
            out.append(("nasal", token))
        elif token in LIQUIDS:
            out.append(("liquid", token))
        elif token in STOPS:
            out.append(("stop", token))
        elif token.isalpha():
            out.append(("vowel", "ax"))
    return out


def _resonate(source: list[float], freq: float, bandwidth: float, rate: int) -> list[float]:
    """One two-pole resonator. The whole of the vocal tract, per formant."""
    r = math.exp(-math.pi * bandwidth / rate)
    theta = 2 * math.pi * freq / rate
    a1 = 2 * r * math.cos(theta)
    a2 = -(r * r)
    gain = (1 - r) * math.sqrt(1 - 2 * r * math.cos(2 * theta) + r * r)

    out = [0.0] * len(source)
    y1 = y2 = 0.0
    for i, x in enumerate(source):
        y = gain * x + a1 * y1 + a2 * y2
        out[i] = y
        y2, y1 = y1, y
    return out


def _buzz(samples: int, pitch: float, rate: int) -> list[float]:
    """A glottal pulse train: one impulse per period, which is enough."""
    out = [0.0] * samples
    period = max(1, int(rate / pitch))
    for i in range(0, samples, period):
        out[i] = 1.0
    return out


def _vowel(key: str, seconds: float, pitch: float, rate: int) -> list[float]:
    f1, f2, f3 = VOWELS.get(key, VOWELS["ax"])
    n = int(seconds * rate)
    source = _buzz(n, pitch, rate)
    out = _resonate(source, f1, 60, rate)
    second = _resonate(source, f2, 90, rate)
    third = _resonate(source, f3, 150, rate)
    return [a + 0.6 * b + 0.3 * c for a, b, c in zip(out, second, third)]


def _noise(seconds: float, rate: int, rng: random.Random) -> list[float]:
    return [rng.uniform(-1.0, 1.0) for _ in range(int(seconds * rate))]


def _envelope(samples: list[float], rate: int, fade: float = 0.008) -> list[float]:
    """Fade both ends, so units do not click into each other."""
    n = min(int(fade * rate), len(samples) // 2)
    if n <= 0:
        return samples
    for i in range(n):
        k = i / n
        samples[i] *= k
        samples[-1 - i] *= k
    return samples


def synthesize(text: str, rate: int = SAMPLE_RATE) -> list[float]:
    """Turn text into mono samples in [-1, 1]. Deterministic for the same text."""
    # Seeded from the text, so the noise in a fricative is the same noise
    # every run: this backend is used where a deterministic artifact is the
    # point, and an unseeded rng would put a clock in the output.
    rng = random.Random(hashlib.sha256(text.encode("utf-8")).digest())
    words = [w for w in text.split() if w.strip()]
    out: list[float] = []

    for w_index, word in enumerate(words):
        units = _units(word) or [("vowel", "ax")]
        for u_index, (kind, key) in enumerate(units):
            # Falling pitch across the utterance, and a little across each
            # word. Flat pitch is the single thing that makes synthesis
            # sound least like speech.
            progress = (w_index + u_index / max(1, len(units))) / max(1, len(words))
            pitch = 120.0 - 25.0 * progress

            if kind == "vowel":
                out += _envelope(_vowel(key, 0.14, pitch, rate), rate)
            elif kind == "fricative":
                centre, level = FRICATIVES[key]
                noise = _noise(0.09, rate, rng)
                shaped = _resonate(noise, centre, 1400, rate)
                out += _envelope([s * level * 12 for s in shaped], rate)
            elif kind == "nasal":
                buzz = _buzz(int(0.07 * rate), pitch, rate)
                out += _envelope(_resonate(buzz, NASALS[key], 120, rate), rate)
            elif kind == "liquid":
                f1, f2, f3 = LIQUIDS[key]
                buzz = _buzz(int(0.08 * rate), pitch, rate)
                a = _resonate(buzz, f1, 80, rate)
                b = _resonate(buzz, f2, 120, rate)
                c = _resonate(buzz, f3, 160, rate)
                out += _envelope([x + 0.6 * y + 0.3 * z for x, y, z in zip(a, b, c)], rate)
            elif kind == "stop":
                out += [0.0] * int(0.035 * rate)
                burst = _noise(0.012, rate, rng)
                out += _envelope([s * 0.35 for s in _resonate(burst, 2000, 2000, rate)], rate, 0.003)

        out += [0.0] * int(0.06 * rate)      # between words

    if not out:
        return []

    peak = max(abs(s) for s in out) or 1.0
    return [0.82 * s / peak for s in out]


def write_wav(samples: list[float], out_path: str | os.PathLike[str],
              rate: int = SAMPLE_RATE) -> str:
    """Write mono 16-bit PCM. Returns the path."""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = b"".join(
        struct.pack("<h", max(-32768, min(32767, int(s * 32767)))) for s in samples
    )
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(frames)
    return str(path)


class FormantTTS:
    """Audible offline synthesis with nothing installed.

    Read `SPEECH_IS_NOT_TRANSCRIBABLE` before using this to close a loop
    through a real speech engine: whisper does not read this output, and
    that is measured rather than assumed.
    """

    transcribable = not SPEECH_IS_NOT_TRANSCRIBABLE
    """False. Present so a caller can ask a backend rather than know about it."""

    def __init__(self, out_dir: str = "Data/Voice/out", rate: int = SAMPLE_RATE):
        self.out_dir = out_dir
        self.rate = rate

    def speak(self, text: str, out_path: str | None = None) -> str:
        if out_path is None:
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
            out_path = os.path.join(self.out_dir, f"say_{digest}.wav")
        return write_wav(synthesize(text, self.rate), out_path, self.rate)
