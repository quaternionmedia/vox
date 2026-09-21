"""Text-to-speech: the other half of the seam, owned locally by vox (not joe —
joe is analysis only, synthesis is a different concern).
"""

import os
from datetime import datetime
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
