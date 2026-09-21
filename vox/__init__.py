"""vox — the voice-interaction seam.

vox owns no audio DSP itself: speech-to-text is delegated to a running
`joe` engine over HTTP, and text-to-speech is a pluggable local backend.
What vox owns is the seam — session orchestration and the adapter contract
that a consuming repo (e.g. qmcp) vendors and wires into its own tool
registry / approval flow.
"""

from vox.session import SelfReport, VoiceSession
from vox.stt import JoeSTT, SpeechToText
from vox.tts import Pyttsx3TTS, TextToSpeech

__all__ = [
    "VoiceSession",
    "SelfReport",
    "SpeechToText",
    "JoeSTT",
    "TextToSpeech",
    "Pyttsx3TTS",
]
