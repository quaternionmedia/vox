"""Text-to-speech: the contract, and the backend that needs no hardware.

Synthesis is the half of the seam vox owns, because it is the half with no
engine behind it. What vox states is the protocol; a backend that drives a
real synthesizer is an adapter and lives in `vox.adapters`.
"""

import hashlib
import os
from pathlib import Path
from typing import Protocol


class TextToSpeech(Protocol):
    """What a text-to-speech backend must provide."""

    def speak(self, text: str, out_path: str | None = None) -> str:
        """Synthesize `text` to an audio file. Returns the path written."""
        ...


class RecordingTTS:
    """Deterministic synthesis: writes the WAV, drives no sound card.

    A real synthesizer needs an audio device, takes a variable amount of
    wall-clock time, and produces different bytes on different machines.
    None of that can be asserted on, so a loop built around one can only
    ever be run by hand.

    This backend writes the same file every time for the same text, via
    `vox.engine.encode_wav`, which the deterministic engine's transcribe
    route reads back. That is what closes the loop offline: the text really
    does travel out to a file on disk and come back in over HTTP, rather
    than being carried around the side in a mock's return value.

    It is a codec and not a voice. Nobody can listen to the output and hear
    words — for that, use a real synthesizer adapter against a real engine.
    """

    def __init__(self, out_dir: str = "Data/Voice/out"):
        self.out_dir = out_dir

    def speak(self, text: str, out_path: str | None = None) -> str:
        from vox.engine import encode_wav

        if out_path is None:
            # Named for the content, not the clock: two runs of the same loop
            # write the same path, so a recorded artifact does not churn.
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
            out_path = os.path.join(self.out_dir, f"speak_{digest}.wav")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        return encode_wav(text, out_path)
