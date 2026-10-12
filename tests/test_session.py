from vox.session import VoiceSession


class FakeSTT:
    def __init__(self, transcript: str = "hello world", audio_path: str = "in.wav"):
        self.transcript = transcript
        self.audio_path = audio_path
        self.transcribe_calls: list[str] = []
        self.listen_calls: list[float] = []
        self.pauses: list[int | None] = []

    def transcribe_file(self, filename: str) -> str:
        self.transcribe_calls.append(filename)
        return self.transcript

    def listen(self, duration: float = 5.0, *, pause_ms: int | None = None) -> tuple[str, str]:
        self.listen_calls.append(duration)
        self.pauses.append(pause_ms)
        return self.transcript, self.audio_path


class FakeTTS:
    def __init__(self):
        self.spoken: list[str] = []

    def speak(self, text: str, out_path: str | None = None) -> str:
        self.spoken.append(text)
        return out_path or "out.wav"


def test_self_report_file_round_trips_audio_to_audio():
    stt = FakeSTT(transcript="testing one two three")
    tts = FakeTTS()
    session = VoiceSession(stt=stt, tts=tts)

    result = session.self_report_file("clip.wav")

    assert result.transcript == "testing one two three"
    assert result.input_audio_path == "clip.wav"
    assert result.output_audio_path == "out.wav"
    assert stt.transcribe_calls == ["clip.wav"]
    assert tts.spoken == ["testing one two three"]


def test_self_report_live_round_trips_audio_to_audio():
    stt = FakeSTT(transcript="live capture", audio_path="Data/Voice/capture_x.wav")
    tts = FakeTTS()
    session = VoiceSession(stt=stt, tts=tts)

    result = session.self_report_live(duration=3.0)

    assert result.transcript == "live capture"
    assert result.input_audio_path == "Data/Voice/capture_x.wav"
    assert result.output_audio_path == "out.wav"
    assert stt.listen_calls == [3.0]
    assert stt.pauses == [None]
    assert tts.spoken == ["live capture"]


def test_self_report_live_hands_the_pause_to_the_backend():
    """The session carries `pause_ms` through unchanged; it is the backend's to spell.

    Seen to fail by having `self_report_live` call `listen(duration=duration)`
    alone: the backend recorded None where 1500 was asked for.
    """
    stt = FakeSTT()
    session = VoiceSession(stt=stt, tts=FakeTTS())

    session.self_report_live(duration=3.0, pause_ms=1500)

    assert stt.pauses == [1500]
