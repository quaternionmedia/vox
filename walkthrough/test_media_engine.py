"""The worked example of the media surface, run by the ordinary test command.

`uv run pytest` runs this. It writes a line of narration with `FormantTTS`,
starts a real HTTP engine on a real socket, uploads the narration through
`MediaEngine`, reads back its transcript with times and what its sound
measures, lets the upload be discarded as the block ends, and writes what
happened to `walkthrough/media-engine.md`.

The artifact is **recorded, not compared**, as `closed-loop.md` is: this
file rewrites it on every run, and its content is deterministic by
construction -- no port, no clock, the same narration bytes every run.
"""

from pathlib import Path

import pytest

from vox.contract import EngineContract
from vox.engine import EngineState, serve
from vox.media import (
    Analysis,
    Loudness,
    MediaEngine,
    Peaks,
    Segment,
    Silence,
    Transcript,
    Word,
)
from vox.synth import SAMPLE_RATE, FormantTTS

NARRATION = "Approve the deploy. Hold the release."
ARTIFACT = Path(__file__).parent / "media-engine.md"

HEARD = Transcript(
    text="Approve the deploy. Hold the release.",
    language="en",
    segments=(
        Segment(0.0, 1.4, "Approve the deploy.", (
            Word(0.0, 0.62, " Approve", 0.97), Word(0.62, 0.84, " the", 0.99), Word(0.84, 1.4, " deploy.", 0.94),
        )),
        Segment(1.6, 3.0, "Hold the release.", (
            Word(1.6, 2.0, " Hold", 0.96), Word(2.0, 2.2, " the", 0.99), Word(2.2, 3.0, " release.", 0.92),
        )),
    ),
    info={"model": "a script"},
)
"""What the engine answers it heard: a script, since `vox.engine` hears nothing."""

MEASURED = Analysis(
    tempo=120.0,
    beats=(0.5, 1.0, 1.5, 2.0, 2.5, 3.0),
    onsets=(0.02, 0.62, 0.84, 1.6, 2.0, 2.2),
    loudness=Loudness(rms_db=-21.4, peak_db=-1.8, lufs=None),
    silences=(Silence(1.4, 1.6),),
    peaks=Peaks(rate=2.0, values=(0.62, 0.81, 0.4, 0.77, 0.69, 0.35, 0.05)),
)
"""What the engine answers its sound measures: a script too."""


def _run(workdir: Path) -> dict:
    """One narration up and its answers back. Returns what each step gave."""
    narration = Path(FormantTTS().to_wav(NARRATION, workdir / "narration.wav"))
    state = EngineState(transcript=HEARD, analysis=MEASURED)
    with serve(state) as (base_url, state), MediaEngine(EngineContract(), base_url) as engine:
        capabilities = engine.capabilities()
        upload = engine.upload(narration, "audio/wav")
        transcript = engine.transcribe(upload)
        analysis = engine.analyze(upload, ["beats", "silences", "peaks"])
        held_inside = set(state.uploads)
    return {
        "narration": narration,
        "capabilities": capabilities,
        "upload": upload,
        "transcript": transcript,
        "analysis": analysis,
        "held_inside": held_inside,
        "held_after": set(state.uploads),
        "requests": list(state.requests),
    }


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    return _run(tmp_path_factory.mktemp("media"))


def test_the_narration_goes_up_with_its_real_length(run):
    """The length is read from the WAV the narration was written to, not
    from the script: 50000 samples at 16 kHz."""
    assert run["upload"].duration == 50000 / SAMPLE_RATE == 3.125
    assert (run["upload"].sample_rate, run["upload"].channels) == (SAMPLE_RATE, 1)


def test_the_answers_come_back_as_dataclasses(run):
    assert run["transcript"] == HEARD
    assert run["analysis"] == Analysis(duration=3.125, beats=MEASURED.beats, silences=MEASURED.silences,
                                       peaks=MEASURED.peaks)


def test_it_went_over_the_wire(run):
    """In the default contract's spelling, silences asked for under the
    engine's own name for them, and the upload discarded as the block ended."""
    assert run["requests"] == [
        "/api/media/capabilities",
        "/api/media",
        "/api/media/m1/transcribe?words=true",
        "/api/media/m1/analyze?features=beats%2Csilence%2Cpeaks",
        "/api/media/m1",
    ]
    assert run["held_inside"] == {"m1"} and run["held_after"] == set()


def test_two_runs_write_the_same_narration(tmp_path_factory):
    """The determinism the artifact below rests on: the same text, the same bytes."""
    first = _run(tmp_path_factory.mktemp("a"))["narration"]
    second = _run(tmp_path_factory.mktemp("b"))["narration"]

    assert first.read_bytes() == second.read_bytes()


def _seconds(value: float) -> str:
    return f"{value:.2f}"


def test_it_records_what_happened(run):
    """Write the artifact. Not an assertion about vox -- the last step of the walkthrough."""
    upload, transcript, analysis = run["upload"], run["transcript"], run["analysis"]
    capabilities = run["capabilities"]
    timed = "\n".join(
        f"| {_seconds(s.start)} | {_seconds(s.end)} | {s.text} | "
        + " · ".join(f"{w.word.strip()} {_seconds(w.start)}–{_seconds(w.end)}" for w in s.words)
        + " |"
        for s in transcript.segments
    )
    served = "\n".join(f"{i + 1}. `{r}`" for i, r in enumerate(run["requests"]))

    ARTIFACT.write_text(
        f"""# The media surface, as it ran

Recorded by `walkthrough/test_media_engine.py` on every `uv run pytest`, which
overwrites it: do not edit. CI fails if the rewritten page differs from the
committed one, so a change in what happened is a diff to commit with the
change. If this page is wrong, the walkthrough is wrong.

## What went up

| | |
|---|---|
| narration | `{NARRATION}`, written by `FormantTTS().to_wav` |
| file | `{run["narration"].name}`, {run["narration"].stat().st_size} bytes |
| the engine's handle | `{upload.media_id}`: {upload.duration} s at {upload.sample_rate} Hz, {upload.channels} channel |

## What came back

The transcript, timed in seconds of the upload:

| start | end | segment | words |
|---|---|---|---|
{timed}

What the sound measures, asked for as `beats`, `silences` and `peaks`:

| feature | answer |
|---|---|
| beats | {", ".join(_seconds(t) for t in analysis.beats)} |
| silences | {", ".join(f"{_seconds(s.start)}–{_seconds(s.end)}" for s in analysis.silences)} |
| peaks | {analysis.peaks.rate:g} a second: {", ".join(f"{v:g}" for v in analysis.peaks.values)} |
| tempo, onsets, loudness | not asked for, so `None` |

What the engine said it offers, asked first:

- features: {", ".join(f"`{f}`" for f in capabilities.features)}
- word timestamps: {"yes" if capabilities.word_timestamps else "no"}
- uploads: {len(capabilities.formats)} content types, up to {capabilities.max_bytes} bytes

## What the engine was asked

{served}

Five requests over HTTP, on a port the OS chose, every path and parameter in
the default contract's spelling. `silences` went out as `silence`, the
engine's name for it. The last is the upload discarded as the `with` block
ended; the walkthrough did not ask for it.

## What this does not show

The engine is `vox.engine`, and its transcript and analysis are the script it
was handed. The narration's length is real, read from the WAV, but **no claim
is made here about what the narration says or how it sounds**. A real engine
answers that, and whisper does not read `FormantTTS`
(`vox.synth.SPEECH_IS_NOT_TRANSCRIBABLE`), so a live check uploads a real
recording:

```python
from vox import MediaEngine
from vox.adapters import JOE

with MediaEngine(JOE, "http://127.0.0.1:1059") as engine:   # joe: uv run uvicorn api:app --port 1059
    print(engine.transcribe(engine.upload("speech.wav", "audio/wav")))
```

That run is not deterministic and is not in this suite.
""",
        encoding="utf-8",
        # LF on every platform, as `closed-loop.md` is, so a Windows run
        # writes the bytes a Linux run does.
        newline="\n",
    )
    assert ARTIFACT.stat().st_size > 0
