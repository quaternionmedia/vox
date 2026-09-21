"""VoiceSession: the seam itself — wires an STT backend to a TTS backend.

A `self_report` is the smallest end-to-end proof this seam works: audio in,
text in the middle, audio out. A consuming repo's tool-registry adapter and
HITL wiring sit on top of this, never replacing it.
"""

from dataclasses import dataclass

from vox.stt import SpeechToText
from vox.tts import TextToSpeech


@dataclass
class SelfReport:
    """The result of one audio -> text -> audio round trip."""

    transcript: str
    input_audio_path: str | None
    output_audio_path: str


class VoiceSession:
    """Orchestrates one STT backend and one TTS backend. Owns no audio itself."""

    def __init__(self, stt: SpeechToText, tts: TextToSpeech):
        self.stt = stt
        self.tts = tts

    def self_report_file(self, filename: str) -> SelfReport:
        """Transcribe an existing audio file, then speak the transcript back.

        Proves the audio -> audio path without touching live hardware.
        """
        transcript = self.stt.transcribe_file(filename)
        output_path = self.tts.speak(transcript)
        return SelfReport(
            transcript=transcript,
            input_audio_path=filename,
            output_audio_path=output_path,
        )

    def self_report_live(self, duration: float = 5.0) -> SelfReport:
        """Record from the mic (via the STT backend), then speak the transcript back."""
        transcript, input_path = self.stt.listen(duration=duration)
        output_path = self.tts.speak(transcript)
        return SelfReport(
            transcript=transcript,
            input_audio_path=input_path,
            output_audio_path=output_path,
        )
