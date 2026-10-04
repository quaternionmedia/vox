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
# SAPI's speak flags: return at once, and drop whatever is still playing.
SVSF_ASYNC, SVSF_PURGE = 1, 2
# How often a sentence that may be interrupted asks whether it has been, and
# how long a stop may take to settle, in milliseconds. Measured on a virtual
# cable: the audio ended within one 100 ms block of the stop.
UNTIL_POLL_MS = 100
STOP_WAIT_MS = 2000


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


def _asked(until) -> bool:
    """Whether `until()` says to stop. A check that fails never cuts a sentence."""
    try:
        return bool(until())
    except Exception:  # noqa: BLE001 -- a check that fails never cuts a sentence
        return False


def _sapi_speak(text: str, out_path: str, playback: bool, output_device: str | None,
                until=None) -> bool:
    """Write `text` to `out_path` with SAPI, then play that file to the end, or
    until `until()` is true. Returns whether it was cut short."""
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
        return False
    player = comtypes.client.CreateObject("SAPI.SpVoice")
    if output_device:
        player.AudioOutput = _output_token(player, output_device)
    reader = comtypes.client.CreateObject("SAPI.SpFileStream")
    reader.Open(out_path, SSFM_OPEN_FOR_READ)
    try:
        if until is None:
            player.SpeakStream(reader, 0)  # synchronous: returns when the audio ends
            return False
        player.SpeakStream(reader, SVSF_ASYNC)
        while not player.WaitUntilDone(UNTIL_POLL_MS):
            if _asked(until):
                player.Speak("", SVSF_ASYNC | SVSF_PURGE)
                player.WaitUntilDone(STOP_WAIT_MS)
                return True
        return False
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

    **A question can be answered over.** With `until`, a callable, the voice
    stops as soon as it returns true -- asked every `UNTIL_POLL_MS` -- and
    `speak` returns once it has, so a person who answers before the question
    ends is not talked over; `cut` says whether the last sentence was. Only
    on SAPI: pyttsx3's own loop has no stop that was seen to work, and plays
    to the end.
    """

    def __init__(self, out_dir: str = "Data/Voice/out", playback: bool = True,
                 output_device: str | None = None):
        self.out_dir = out_dir
        self.playback = playback
        # Which output the voice is heard on, by a fragment of its name; the
        # platform default when unset. `VOX_OUTPUT_DEVICE` names it for a
        # process that cannot be handed an argument.
        self.output_device = output_device or os.environ.get("VOX_OUTPUT_DEVICE") or None
        self.cut = False

    def speak(self, text: str, out_path: str | None = None, until=None) -> str:
        os.makedirs(self.out_dir, exist_ok=True)
        if out_path is None:
            stamp = datetime.now().strftime("%m-%d-%y_%H-%M-%S")
            out_path = os.path.join(self.out_dir, f"speak_{stamp}.wav")

        self.cut = False
        if _sapi():
            self.cut = _sapi_speak(text, out_path, self.playback, self.output_device, until)
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
