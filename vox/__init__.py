"""vox — the voice-interaction seam.

vox owns no audio DSP itself: speech-to-text is delegated to a running
`joe` engine over HTTP, and text-to-speech is a pluggable local backend.
What vox owns is the seam — session orchestration and the adapter contract
that a consuming repo (e.g. qmcp) vendors and wires into its own tool
registry / approval flow.
"""

from vox.engine import EngineState, NotVoxAudioError, decode_wav, encode_wav, serve
from vox.session import RoundTrip, SelfReport, VoiceSession
from vox.stt import JoeSTT, SpeechToText
from vox.tts import Pyttsx3TTS, RecordingTTS, TextToSpeech

__all__ = [
    "VoiceSession",
    "SelfReport",
    "RoundTrip",
    "SpeechToText",
    "JoeSTT",
    "TextToSpeech",
    "Pyttsx3TTS",
    "RecordingTTS",
    "EngineState",
    "serve",
    "encode_wav",
    "decode_wav",
    "NotVoxAudioError",
]
