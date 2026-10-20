"""`to_wav`: a synthesizer writes a WAV and plays nothing.

Narration laid under a video, or a clip uploaded to an engine, is audio to
be used rather than heard. The two backends that need nothing installed are
checked here; the platform voice, which can be heard, is checked in
`tests/test_adapters.py` with its stand-ins.
"""

import inspect
import wave
from pathlib import Path

import pytest

from vox.engine import decode_wav
from vox.synth import SAMPLE_RATE, FormantTTS
from vox.tts import RecordingTTS, TextToSpeech


def test_the_protocol_states_it():
    assert "to_wav" in TextToSpeech.__dict__
    assert list(inspect.signature(TextToSpeech.to_wav).parameters) == ["self", "text", "path"]


@pytest.mark.parametrize("backend", [RecordingTTS, FormantTTS], ids=["recording", "formant"])
def test_it_writes_the_wav_where_it_is_told_and_says_where(backend, tmp_path):
    """At the path given, its directories made, and nothing in the backend's
    own output directory: narration goes where the caller lays it."""
    out_dir = tmp_path / "out"
    path = tmp_path / "narration" / "take one.wav"

    written = backend(out_dir=str(out_dir)).to_wav("approve the deploy", path)

    assert written == str(path)
    with wave.open(written, "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, SAMPLE_RATE)
        assert wav.getnframes() > 0
    assert not out_dir.exists()


@pytest.mark.parametrize("backend", [RecordingTTS, FormantTTS], ids=["recording", "formant"])
def test_it_writes_the_same_bytes_speak_does(backend, tmp_path):
    """One synthesis, two ways to ask for it: a WAV made for a timeline is the
    one `speak` would have written to that path."""
    tts = backend(out_dir=str(tmp_path))

    spoken = tts.speak("hold the release", out_path=str(tmp_path / "spoken.wav"))
    written = tts.to_wav("hold the release", tmp_path / "written.wav")

    assert Path(spoken).read_bytes() == Path(written).read_bytes()


def test_the_codec_s_wav_reads_back_as_its_text(tmp_path):
    assert decode_wav(RecordingTTS().to_wav("approve the deploy", tmp_path / "said.wav")) == "approve the deploy"
