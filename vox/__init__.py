"""vox — the voice-interaction seam.

vox owns no audio DSP and names no engine. What it owns is the seam:

- **`EngineContract`** — what an HTTP speech engine must answer, as a value.
- **`HttpSTT`** — a client that drives any engine matching one.
- **`TextToSpeech`** — the synthesis protocol, with `RecordingTTS` as the
  deterministic backend that needs no hardware.
- **`VoiceSession`** — the orchestration, and `round_trip`, which closes the
  loop by handing vox's own output back to the engine.
- **`vox.engine`** — a real HTTP server answering any contract, so the whole
  loop runs in a test with no engine, no model and no microphone.

A particular engine or synthesizer is named in `vox.adapters` and nowhere
else. A consuming repository vendors vox to get voice interaction, registering
`VoiceSession` against its own flow rather than reimplementing any of this.
"""

from vox.contract import EngineContract
from vox.engine import EngineState, NotVoxAudioError, decode_wav, encode_wav, serve
from vox.session import RoundTrip, SelfReport, VoiceSession
from vox.stt import HttpSTT, SpeechToText
from vox.tts import RecordingTTS, TextToSpeech

__all__ = [
    "EngineContract",
    "VoiceSession",
    "SelfReport",
    "RoundTrip",
    "SpeechToText",
    "HttpSTT",
    "TextToSpeech",
    "RecordingTTS",
    "EngineState",
    "serve",
    "encode_wav",
    "decode_wav",
    "NotVoxAudioError",
]
