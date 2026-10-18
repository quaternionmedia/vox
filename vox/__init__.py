"""vox — the voice-interaction seam.

vox owns no audio DSP and names no engine. What it owns is the seam:

- **`EngineContract`** — what an HTTP speech engine must answer, as a value.
- **`HttpSTT`** — a client that drives any engine matching one.
- **`MediaEngine`** — a client for the contract's media surface: it uploads
  audio or a video's sound and reads back a transcript with times and what
  the sound measures, as dataclasses, and every failure is an `EngineError`
  naming the engine.
- **`TextToSpeech`** — the synthesis protocol. `RecordingTTS` is the
  deterministic codec backend; `FormantTTS` is audible and needs nothing
  installed, and `vox.synth.SPEECH_IS_NOT_TRANSCRIBABLE` says what it
  cannot do.
- **`VoiceSession`** — the orchestration, and `round_trip`, which closes the
  loop by handing vox's own output back to the engine.
- **`vox.engine`** — a real HTTP server answering any contract, so the whole
  loop runs in a test with no engine, no model and no microphone; its media
  surface also answers in-process, through `media_transport`.

A particular engine or synthesizer is named in `vox.adapters` and nowhere
else. A consuming repository vendors vox to get voice interaction, registering
`VoiceSession` against its own flow rather than reimplementing any of this.
"""

from vox.contract import EngineContract
from vox.engine import (
    EngineState,
    NotVoxAudioError,
    decode_wav,
    encode_wav,
    media_transport,
    serve,
)
from vox.media import (
    Analysis,
    Capabilities,
    EngineAnswerMalformed,
    EngineDoesNotOffer,
    EngineError,
    EngineRefused,
    EngineUnavailable,
    Loudness,
    MediaEngine,
    MediaHandle,
    Peaks,
    Segment,
    Silence,
    Transcript,
    UploadTooLarge,
    Word,
)
from vox.session import RoundTrip, SelfReport, VoiceSession
from vox.stt import HttpSTT, SpeechToText
from vox.synth import FormantTTS
from vox.tts import RecordingTTS, TextToSpeech

__all__ = [
    "EngineContract",
    "VoiceSession",
    "SelfReport",
    "RoundTrip",
    "SpeechToText",
    "HttpSTT",
    "MediaEngine",
    "MediaHandle",
    "Transcript",
    "Segment",
    "Word",
    "Analysis",
    "Loudness",
    "Silence",
    "Peaks",
    "Capabilities",
    "EngineError",
    "EngineUnavailable",
    "EngineRefused",
    "EngineAnswerMalformed",
    "EngineDoesNotOffer",
    "UploadTooLarge",
    "TextToSpeech",
    "RecordingTTS",
    "FormantTTS",
    "EngineState",
    "serve",
    "media_transport",
    "encode_wav",
    "decode_wav",
    "NotVoxAudioError",
]
