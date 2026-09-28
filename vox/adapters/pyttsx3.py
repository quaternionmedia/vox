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
    """Synthesis via pyttsx3. Needs an audio stack; produces platform-specific bytes.

    Speaking means being heard: by default `speak` also plays the utterance
    through the platform voice, and returns only when the audio has finished.
    That ordering is the polite-speaker contract — a caller that opens a
    microphone right after `speak` returns cannot record over its own prompt,
    because the prompt is over. `save_to_file` alone writes a WAV and plays
    nothing, which once left a live loop recording a human who had heard
    only silence; `playback=False` keeps that file-only behaviour for
    artifact generation, where sound is noise.
    """

    def __init__(self, out_dir: str = "Data/Voice/out", playback: bool = True):
        self.out_dir = out_dir
        self.playback = playback

    def speak(self, text: str, out_path: str | None = None) -> str:
        import pyttsx3

        os.makedirs(self.out_dir, exist_ok=True)
        if out_path is None:
            stamp = datetime.now().strftime("%m-%d-%y_%H-%M-%S")
            out_path = os.path.join(self.out_dir, f"speak_{stamp}.wav")

        engine = pyttsx3.init()
        engine.save_to_file(text, out_path)
        engine.runAndWait()
        if self.playback:
            # After the file is complete, so the returned path is always a
            # finished recording of what was said.
            engine.say(text)
            engine.runAndWait()
        return out_path
