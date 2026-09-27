"""Text-to-speech: the other half of the seam, owned locally by vox (not joe —
joe is analysis only, synthesis is a different concern).
"""

import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Protocol


class TextToSpeech(Protocol):
    """What a text-to-speech backend must provide."""

    def speak(self, text: str, out_path: str | None = None) -> str:
        """Synthesize `text` to an audio file. Returns the path written."""
        ...


class Pyttsx3TTS:
    """Offline TTS via pyttsx3 (SAPI5 / NSSpeechSynthesizer / espeak, depending on platform).

    The default backend so vox works out of the box with no cloud dependency
    and no model download — swap in another `TextToSpeech` implementation
    (piper, coqui, a cloud API) without touching the seam.
    """

    def __init__(self, out_dir: str = "Data/Voice/out"):
        self.out_dir = out_dir

    def speak(self, text: str, out_path: str | None = None) -> str:
        import pyttsx3

        os.makedirs(self.out_dir, exist_ok=True)
        if out_path is None:
            stamp = datetime.now().strftime("%m-%d-%y_%H-%M-%S")
            out_path = os.path.join(self.out_dir, f"speak_{stamp}.wav")

        engine = pyttsx3.init()
        engine.save_to_file(text, out_path)
        engine.runAndWait()
        return out_path


class RecordingTTS:
    """Deterministic synthesis: writes the WAV, drives no sound card.

    `Pyttsx3TTS` hands the text to SAPI5/NSSpeech/espeak, which needs an
    audio device, takes a variable amount of wall-clock time, and produces
    different bytes on different machines. None of that can be asserted on,
    so the loop it sits in can only ever be run by hand.

    This backend writes the same file every time for the same text, via
    `vox.engine.encode_wav`, which `vox.engine`'s transcribe route reads
    back. That is what closes the loop offline: the text really does travel
    out to a file on disk and come back in through the engine's HTTP
    contract, rather than being carried around the side in a mock's return
    value.

    It is a codec and not a voice. Nobody can listen to the output and hear
    words — for that, use `Pyttsx3TTS` against a real engine.
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
