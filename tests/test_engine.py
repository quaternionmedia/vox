"""The codec under `vox.engine`: does the text survive a real WAV file?

Everything the deterministic loop claims rests on these. If `encode_wav`
and `decode_wav` are not exact inverses, the loop closes on the wrong words
and reports success; if `decode_wav` accepts audio it did not write, the
engine answers a real recording with confident nonsense.
"""

import wave

import pytest

from vox.engine import MAGIC, SAMPLE_RATE, NotVoxAudioError, decode_wav, encode_wav


def test_text_survives_the_file(tmp_path):
    path = encode_wav("approve the deploy", tmp_path / "said.wav")
    assert decode_wav(path) == "approve the deploy"


@pytest.mark.parametrize(
    "text",
    ["", "a", "approve the deploy", "yes" * 500, "café naïve — ümlaut", "line\nbreak\ttab"],
)
def test_text_survives_whatever_it_is(tmp_path, text):
    """Empty, long, multibyte and whitespace-bearing text all round trip.

    The length header is bytes rather than characters for the multibyte
    case: `len("café")` is 4 and its UTF-8 is 5 bytes, and a decoder that
    trusted the character count would truncate.
    """
    assert decode_wav(encode_wav(text, tmp_path / "x.wav")) == text


def test_the_same_text_writes_the_same_bytes(tmp_path):
    """No clock, no dithering, no engine state — this is the determinism claim."""
    first = tmp_path / "a.wav"
    second = tmp_path / "b.wav"
    encode_wav("approve the deploy", first)
    encode_wav("approve the deploy", second)
    assert first.read_bytes() == second.read_bytes()


def test_different_text_writes_different_bytes(tmp_path):
    """Guards the inverse: a codec that ignored its input would pass the test above."""
    first = tmp_path / "a.wav"
    second = tmp_path / "b.wav"
    encode_wav("approve", first)
    encode_wav("reject", second)
    assert first.read_bytes() != second.read_bytes()


def test_it_writes_a_real_wav(tmp_path):
    """A playable 16-bit mono file at joe's sample rate, not a blob with a header."""
    path = encode_wav("hello", tmp_path / "x.wav")
    with wave.open(path, "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == SAMPLE_RATE
        assert wav.getnframes() == len(MAGIC) + 4 + len("hello")


def test_it_refuses_audio_it_did_not_write(tmp_path):
    """A recording of a human voice is an error, not a transcript.

    Without the marker the engine would read whatever the samples happened
    to spell and answer 200 with it, which is the worst failure available:
    a stand-in that cannot tell it was handed the real thing.
    """
    path = tmp_path / "human.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(b"\x01\x02" * 400)

    with pytest.raises(NotVoxAudioError, match="not written by vox's codec"):
        decode_wav(path)


def test_it_refuses_a_truncated_file(tmp_path):
    """The length header is checked against what is actually there."""
    path = encode_wav("approve the deploy", tmp_path / "x.wav")
    with wave.open(path, "rb") as wav:
        frames = wav.readframes(wav.getnframes())
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(frames[:-10])

    with pytest.raises(NotVoxAudioError, match="truncated"):
        decode_wav(path)


def test_it_refuses_stereo(tmp_path):
    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(b"\x00\x00\x00\x00" * 100)

    with pytest.raises(NotVoxAudioError, match="16-bit mono"):
        decode_wav(path)
