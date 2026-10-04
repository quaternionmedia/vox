"""pyttsx3, as a `TextToSpeech` backend.

Offline synthesis through whatever the platform provides — SAPI5, NSSpeech,
espeak. No cloud call and no model download, which is why it is the one a
live demo reaches for.

It is an adapter and not a default: nothing in `vox` imports this module, and
the import of `pyttsx3` itself happens inside `speak` so that having this
file on disk costs a caller nothing.
"""

import os
import sys
from datetime import datetime

# SAPI's file-stream modes.
SSFM_OPEN_FOR_READ, SSFM_CREATE_FOR_WRITE = 0, 3


def _sapi() -> bool:
    """Whether to drive SAPI directly rather than through pyttsx3: on Windows.

    **PYTTSX3'S EVENT LOOP DID NOT PLAY.** Measured on the workstation this
    was built on: after `save_to_file`, `say` with `runAndWait` returned in
    0.09 s for a sentence 2.8 s long, and a conversation spoke every line into
    files while the person heard nothing; re-initialising the engine in the
    same process then hung. SAPI's own `Speak` into a file stream, and a
    synchronous `SpeakStream` of that file, played each sentence for exactly
    its length, three in a row, in one process.
    """
    return sys.platform == "win32"


def outputs() -> list[str]:
    """The audio outputs the platform voice can play to, by name. SAPI's on
    Windows; elsewhere the platform default, which this cannot list."""
    if not _sapi():
        return []
    import comtypes.client

    voice = comtypes.client.CreateObject("SAPI.SpVoice")
    return [token.GetDescription() for token in voice.GetAudioOutputs()]


def _output_token(voice, fragment: str):
    """The one output whose name contains `fragment`, ignoring case. A
    fragment matching none or several is refused rather than guessed: one
    machine here lists the same headset under several names."""
    matches = [t for t in voice.GetAudioOutputs() if fragment.lower() in t.GetDescription().lower()]
    if len(matches) != 1:
        names = [t.GetDescription() for t in voice.GetAudioOutputs()]
        raise ValueError(f"output {fragment!r} matches {len(matches)} of: {', '.join(names)}")
    return matches[0]


def _sapi_speak(text: str, out_path: str, playback: bool, output_device: str | None) -> None:
    """Write `text` to `out_path` with SAPI, then play that file to the end."""
    import comtypes.client

    writer = comtypes.client.CreateObject("SAPI.SpVoice")
    stream = comtypes.client.CreateObject("SAPI.SpFileStream")
    stream.Open(out_path, SSFM_CREATE_FOR_WRITE)
    try:
        writer.AudioOutputStream = stream
        writer.Speak(text, 0)
    finally:
        stream.Close()
    if not playback:
        return
    player = comtypes.client.CreateObject("SAPI.SpVoice")
    if output_device:
        player.AudioOutput = _output_token(player, output_device)
    reader = comtypes.client.CreateObject("SAPI.SpFileStream")
    reader.Open(out_path, SSFM_OPEN_FOR_READ)
    try:
        player.SpeakStream(reader, 0)  # synchronous: returns when the audio ends
    finally:
        reader.Close()


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

    def __init__(self, out_dir: str = "Data/Voice/out", playback: bool = True,
                 output_device: str | None = None):
        self.out_dir = out_dir
        self.playback = playback
        # Which output the voice is heard on, by a fragment of its name; the
        # platform default when unset. `VOX_OUTPUT_DEVICE` names it for a
        # process that cannot be handed an argument.
        self.output_device = output_device or os.environ.get("VOX_OUTPUT_DEVICE") or None

    def speak(self, text: str, out_path: str | None = None) -> str:
        os.makedirs(self.out_dir, exist_ok=True)
        if out_path is None:
            stamp = datetime.now().strftime("%m-%d-%y_%H-%M-%S")
            out_path = os.path.join(self.out_dir, f"speak_{stamp}.wav")

        if _sapi():
            _sapi_speak(text, out_path, self.playback, self.output_device)
            return out_path
        import pyttsx3

        engine = pyttsx3.init()
        engine.save_to_file(text, out_path)
        engine.runAndWait()
        if self.playback:
            # After the file is complete, so the returned path is always a
            # finished recording of what was said.
            engine.say(text)
            engine.runAndWait()
        return out_path
