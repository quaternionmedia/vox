"""Offline speech synthesis with nothing installed.

These do not assert that the output sounds like anything: no test can, and
the one claim worth making about audibility is measured against a real
engine and written down in `SPEECH_IS_NOT_TRANSCRIBABLE` rather than
asserted here. What is checked is that it produces a valid, deterministic,
in-range waveform that differs with the text — the properties a caller
depends on.
"""

from __future__ import annotations

import wave

import pytest

from vox.synth import (
    SAMPLE_RATE,
    SPEECH_IS_NOT_TRANSCRIBABLE,
    VOWELS,
    FormantTTS,
    synthesize,
    write_wav,
)


def _read(path: str) -> tuple[list[int], int]:
    with wave.open(path, "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        frames = wav.readframes(wav.getnframes())
        rate = wav.getframerate()
    samples = [
        int.from_bytes(frames[i : i + 2], "little", signed=True)
        for i in range(0, len(frames), 2)
    ]
    return samples, rate


# ─── what it produces ─────────────────────────────────────────────────────────

def test_it_writes_a_real_wav_at_the_engine_s_rate(tmp_path):
    path = FormantTTS(out_dir=str(tmp_path)).speak("approve the deploy")

    samples, rate = _read(path)

    assert rate == SAMPLE_RATE
    assert len(samples) > SAMPLE_RATE // 2, "less than half a second for three words"


def test_the_samples_stay_in_range(tmp_path):
    """Normalised to 0.82, so nothing clips and nothing overflows the pack."""
    path = FormantTTS(out_dir=str(tmp_path)).speak("approve the deploy")

    samples, _ = _read(path)

    assert max(abs(s) for s in samples) <= 32767
    assert max(abs(s) for s in samples) > 1000, "it should not be near-silent"


def test_the_same_text_gives_the_same_bytes(tmp_path):
    """The noise in a fricative is seeded from the text, not from a clock."""
    a = FormantTTS(out_dir=str(tmp_path / "a")).speak("approve the deploy")
    b = FormantTTS(out_dir=str(tmp_path / "b")).speak("approve the deploy")

    assert open(a, "rb").read() == open(b, "rb").read()


def test_different_text_gives_different_bytes(tmp_path):
    """A synthesizer ignoring its input would pass the test above."""
    a = FormantTTS(out_dir=str(tmp_path / "a")).speak("approve the deploy")
    b = FormantTTS(out_dir=str(tmp_path / "b")).speak("reject the deploy")

    assert open(a, "rb").read() != open(b, "rb").read()


def test_longer_text_gives_longer_audio(tmp_path):
    short, _ = _read(FormantTTS(out_dir=str(tmp_path / "s")).speak("yes"))
    long, _ = _read(FormantTTS(out_dir=str(tmp_path / "l")).speak("yes it is approved"))

    assert len(long) > len(short) * 2


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_text_produces_no_samples(text):
    assert synthesize(text) == []


def test_empty_text_still_writes_a_readable_file(tmp_path):
    """A zero-length WAV is still a WAV; a caller should not get a broken file."""
    path = write_wav([], tmp_path / "silence.wav")

    samples, rate = _read(path)

    assert samples == []
    assert rate == SAMPLE_RATE


def test_punctuation_and_digits_do_not_crash_it(tmp_path):
    path = FormantTTS(out_dir=str(tmp_path)).speak("Deploy v2.1 -- now?")

    samples, _ = _read(path)

    assert samples, "something should come out"


def test_vowels_are_actually_distinct():
    """Three formants per vowel, and no two vowels the same triple.

    If they collided the synthesizer would produce one sound for every
    vowel, which is the failure mode that looks like working code.
    """
    assert len(set(VOWELS.values())) == len(VOWELS)


def test_two_different_vowels_produce_different_audio():
    assert synthesize("bee") != synthesize("bah")


# ─── the claim that is measured, not asserted ────────────────────────────────

def test_the_backend_says_it_is_not_transcribable():
    """Measured against a live engine: whisper returns empty for this output.

    The control matters as much as the measurement — real synthesis through
    the identical path came back as "approve the deploy." — so the empty
    result is a property of this synthesizer and not of the pipeline.

    A caller branches on this rather than rediscovering it. If somebody
    improves the synthesizer, this test and the constant move together.
    """
    assert SPEECH_IS_NOT_TRANSCRIBABLE is True
    assert FormantTTS.transcribable is False


def test_it_is_reachable_by_name_from_the_cli():
    """`--voice formant` resolves without touching vox.adapters."""
    import vox.cli as cli

    assert isinstance(cli._synthesizer("formant"), FormantTTS)


def test_it_needs_nothing_outside_the_standard_library():
    """The whole point: no system voice, no wheel, no model.

    Checked by reading the imports rather than by installing nothing,
    because the test process already has vox's own dependencies.
    """
    import ast
    import pathlib
    import sys

    source = pathlib.Path(cli_path := __import__("vox.synth", fromlist=["x"]).__file__).read_text(
        encoding="utf-8"
    )
    assert cli_path.endswith("synth.py")

    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])

    outside = imported - set(sys.stdlib_module_names) - {"vox"}
    assert not outside, f"vox.synth imports {outside}, which is not the standard library"
