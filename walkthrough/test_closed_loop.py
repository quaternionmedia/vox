"""The worked example: one closed loop, run by the ordinary test command.

`uv run pytest` runs this. It starts a real HTTP engine on a real socket,
sends text out through a real WAV file, reads it back through the engine's
HTTP contract, and writes what happened to `walkthrough/closed-loop.md`.

The artifact is **recorded, not compared**: this file rewrites it on every
run, and nothing in the suite asserts it against a golden copy, because a
golden copy is a second place the behaviour is written down. Its content is
deterministic by construction — no port, no clock, no duration — so a run
that changes nothing leaves the file unchanged and a run that changes
something shows it in the diff. CI fails on that diff
(`.github/workflows/ci.yml`) until the rewritten file is committed.
"""

from pathlib import Path

import pytest

from vox.engine import EngineState, encode_wav, serve
from vox.session import VoiceSession
from vox.stt import HttpSTT
from vox.tts import RecordingTTS

SAID = "approve the deploy"
ARTIFACT = Path(__file__).parent / "closed-loop.md"


def _run_loop(audio_dir: Path) -> tuple:
    """One full trip, returning the result and the requests the engine served."""
    encode_wav(SAID, audio_dir / "said.wav")
    state = EngineState(
        audio_dirs=[audio_dir], microphone="deterministic engine", heard=SAID, capture_dir=audio_dir
    )
    with serve(state) as (base_url, state), HttpSTT(base_url) as stt:
        assert stt.reachable(), "the engine did not answer its own health check"
        session = VoiceSession(stt=stt, tts=RecordingTTS(), echo_dir=str(audio_dir))
        result = session.round_trip("said.wav")
    return result, list(state.requests)


@pytest.fixture(scope="module")
def loop(tmp_path_factory):
    return _run_loop(tmp_path_factory.mktemp("loop"))


def test_the_loop_closes(loop):
    """Text out through audio, back in through the engine, unchanged."""
    result, _ = loop
    assert result.heard == SAID
    assert result.echoed == SAID
    assert result.closed is True


def test_it_went_over_the_wire(loop):
    """The loop used HTTP, rather than a mock returning the answer.

    Without this the suite would pass just as well if `round_trip` handed
    the text to itself.

    The echo is a *bare* filename: `vox.engine` accepts a name with a
    directory in it, and a real engine may refuse one, because a name with a
    separator in it lets a path traversal in. This assertion pins the bare
    name.
    """
    _, requests = loop
    assert requests == [
        "/api/health",
        "/api/voice/transcribe?filename=said.wav",
        "/api/voice/transcribe?filename=echo.wav",
    ]


def test_the_audio_is_a_real_file_written_this_run(loop):
    """Assert the intermediate: a leftover file from a previous run closes the loop too.

    `tmp_path_factory` gives each run its own directory, so this can only
    pass if `speak` actually wrote; a stale artifact would let this guard
    report green while doing nothing.
    """
    result, _ = loop
    spoken = Path(result.spoken_path)
    assert spoken.is_file()
    assert spoken.stat().st_size > 44, "44 bytes is a WAV header with no samples"


def test_two_runs_produce_the_same_bytes(tmp_path_factory):
    """The determinism claim, asserted rather than assumed.

    Separate directories, separate engines, separate ephemeral ports. If
    anything in the path reached for a clock, a random seed or a model, the
    two files would differ.
    """
    first, _ = _run_loop(tmp_path_factory.mktemp("a"))
    second, _ = _run_loop(tmp_path_factory.mktemp("b"))

    assert Path(first.spoken_path).read_bytes() == Path(second.spoken_path).read_bytes()
    assert first.heard == second.heard == SAID


def test_it_records_what_happened(loop):
    """Write the artifact. Not an assertion about vox — the last step of the walkthrough."""
    result, requests = loop
    spoken = Path(result.spoken_path)
    served = "\n".join(f"{i + 1}. `{r}`" for i, r in enumerate(requests))

    ARTIFACT.write_text(
        f"""# The closed loop, as it ran

Recorded by `walkthrough/test_closed_loop.py` on every `uv run pytest`, which
overwrites it: do not edit. CI fails if the rewritten page differs from the
committed one, so a change in what happened is a diff to commit with the
change. If this page is wrong, the walkthrough is wrong.

## What went round

| Leg | Value |
|---|---|
| said | `{SAID}` |
| heard back from the engine | `{result.heard}` |
| synthesized to | `{spoken.name}` ({spoken.stat().st_size} bytes) |
| transcribed again as | `{result.echoed}` |
| loop closed | {result.closed} |

## What the engine was asked

{served}

Three requests over HTTP, on a port the OS chose. The first is the identity
check: a 200 proves something is listening, not that it is the right thing,
so the engine names itself on `/api/health` before anything is measured
against it.

## What this does not show

The engine is `vox.engine`, a codec with an `EngineContract` around it, so it
carries the text faithfully. **No claim is made here about transcription
accuracy**: that is a real engine's work, and where it can mis-hear. Run the
same loop against one:

```sh
uv run vox loop               # no --offline; --engine names the adapter
```

That run is not deterministic and is not in this suite.
""",
        encoding="utf-8",
        # Pinned to LF so a Windows run writes the same bytes a Linux run
        # does: text mode would translate to the platform separator, and
        # `git status` would report this artifact modified after every local
        # run though its content had not changed.
        newline="\n",
    )
    assert ARTIFACT.stat().st_size > 0
