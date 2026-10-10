"""VoiceSession: the seam itself — wires an STT backend to a TTS backend.

A `self_report` is the smallest end-to-end proof this seam works: audio in,
text in the middle, audio out. A consuming repo's tool-registry adapter and
HITL wiring sit on top of this, never replacing it.
"""

from dataclasses import dataclass
from pathlib import Path

from vox.stt import SpeechToText
from vox.tts import TextToSpeech


@dataclass
class SelfReport:
    """The result of one audio -> text -> audio round trip."""

    transcript: str
    input_audio_path: str | None
    output_audio_path: str


@dataclass
class RoundTrip:
    """The result of one *closed* loop: audio -> text -> audio -> text.

    `SelfReport` stops at the audio it produced, so the last leg is checked
    by a person listening. This carries the transcript of vox's own output,
    which is the first fact in the chain a test can assert on.
    """

    heard: str
    """Transcript of the input audio."""

    spoken_path: str
    """Where the synthesized reply was written."""

    echoed: str
    """Transcript of that reply, read back through the same engine."""

    input_audio_path: str | None = None

    @property
    def closed(self) -> bool:
        """Whether the text survived the trip out through audio and back."""
        return self.echoed == self.heard


class VoiceSession:
    """Orchestrates one STT backend and one TTS backend. Owns no audio itself."""

    def __init__(self, stt: SpeechToText, tts: TextToSpeech, echo_dir: str | None = None):
        self.stt = stt
        self.tts = tts
        self.echo_dir = echo_dir
        """Directory the STT engine resolves filenames against, for `round_trip`.

        Closing the loop hands vox's own output back to the engine, and
        `EngineContract` has the engine take a *filename it can already see*
        rather than an upload. So the two share a filesystem: true of a local
        development loop, not of an engine on another host. An engine that
        accepts bytes needs a different contract and a different session
        method, and vox has neither.
        """

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

    def self_report_live(self, duration: float = 5.0, pause_ms: int | None = None) -> SelfReport:
        """Record from the mic (via the STT backend), then speak the transcript back.

        `pause_ms` goes to the backend as given; whether and how it reaches
        the engine is the backend's contract to decide.
        """
        transcript, input_path = self.stt.listen(duration=duration, pause_ms=pause_ms)
        output_path = self.tts.speak(transcript)
        return SelfReport(
            transcript=transcript,
            input_audio_path=input_path,
            output_audio_path=output_path,
        )

    def round_trip(self, filename: str, echo_as: str = "echo.wav") -> RoundTrip:
        """Close the loop: transcribe, speak the transcript, transcribe that.

        `echo_as` is the name *the engine* will be asked for, resolved under
        `echo_dir` on the way out. It is a bare filename, with no directory
        in it: an engine may refuse any name containing a separator, as joe
        does, because accepting one lets a path traversal in.

        The loop closing proves the seam carried the text; it does not prove
        the transcription was right. Both legs run through the same engine,
        so a backend that mis-hears consistently closes the loop on the
        wrong words. `heard` is the value to check against what was said.
        """
        if self.echo_dir is None:
            raise ValueError(
                "round_trip needs echo_dir: the directory the STT engine resolves "
                "filenames against, so vox's own output can be handed back to it."
            )

        heard = self.stt.transcribe_file(filename)
        spoken_path = self.tts.speak(heard, out_path=str(Path(self.echo_dir) / echo_as))
        echoed = self.stt.transcribe_file(echo_as)
        return RoundTrip(
            heard=heard,
            spoken_path=spoken_path,
            echoed=echoed,
            input_audio_path=filename,
        )
