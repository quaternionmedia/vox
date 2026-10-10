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

    Writes the same file every time for the same text, via
    `vox.engine.encode_wav`, which the deterministic engine's transcribe
    route reads back: the text travels out to a file on disk and back in
    over HTTP. A real synthesizer needs an audio device, takes a variable
    time and writes different bytes on different machines, so a loop built
    on one cannot be asserted on.

    It is a codec and not a voice: nobody listening to the output hears
    words. For that, use a real synthesizer adapter against a real engine.
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
