"""pyttsx3, as a `TextToSpeech` backend.

Offline synthesis through whatever the platform provides — SAPI5, NSSpeech,
espeak. No cloud call and no model download, which is why it is the one a
live demo reaches for.

It is an adapter and not a default: nothing in `vox` imports this module, and
the import of `pyttsx3` itself happens inside `speak` so that having this
file on disk costs a caller nothing.
"""

import os
from datetime import datetime


class Pyttsx3TTS:
    """Synthesis via pyttsx3. Needs an audio stack; produces platform-specific bytes."""

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
