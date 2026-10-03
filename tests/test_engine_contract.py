"""Does `vox.engine` answer the way joe answers?

A stand-in that drifts from the thing it stands in for is worse than no
stand-in: the suite stays green while the seam stops working against the
real engine, and nobody finds out until a demo. Every expectation here was
read off joe's `api.py` and `Modules/Voice.py` at `c2d9a01`, except where a
test's docstring names a later commit, at which that one was read — the
commit is named so the next reader can diff rather than re-derive.

These drive the engine through `HttpSTT`, unmodified, over a real socket.
That is the point: if the client needs a change to talk to the stand-in, the
stand-in is not standing in.
"""

import httpx
import pytest

from vox.engine import EngineState, encode_wav, serve
from vox.stt import HttpSTT


@pytest.fixture
def engine(tmp_path):
    """A running engine with one audio directory and a microphone."""
    state = EngineState(
        audio_dirs=[tmp_path],
        microphone="Deterministic Input (vox)",
        heard="approve the deploy",
        capture_dir=tmp_path,
    )
    with serve(state) as (base_url, state):
        yield base_url, state, tmp_path


def test_it_binds_an_ephemeral_port(engine):
    """Never a default port: two sessions share this workstation.

    `handbook/async-contract.md` §4 — an afternoon was spent measuring
    another project's server on 8000.
    """
    base_url, _, _ = engine
    port = int(base_url.rsplit(":", 1)[1])
    assert port != 8000
    assert port > 1024


def test_it_says_what_it_is(engine):
    """A 200 proves something is listening, not that it is the right something."""
    base_url, _, _ = engine
    body = httpx.get(f"{base_url}/api/health").json()
    assert body["status"] == "ok", "reachable() reads the status; an engine answers {'status': 'ok'}"
    assert body["engine"] == "vox.engine"


def test_devices_matches_joes_shape(engine):
    base_url, _, _ = engine
    body = httpx.get(f"{base_url}/api/voice/devices").json()
    assert body["microphone_available"] is True
    assert body["devices"] == [
        {"index": 0, "name": "Deterministic Input (vox)", "channels": 1, "default": True}
    ]


def test_devices_reports_no_microphone_as_joe_does(tmp_path):
    """Empty list plus False, rather than an error — joe treats it as a fact, not a crash."""
    with serve(EngineState(audio_dirs=[tmp_path], microphone=None)) as (base_url, _):
        body = httpx.get(f"{base_url}/api/voice/devices").json()
    assert body == {"devices": [], "microphone_available": False}


def test_transcribe_returns_joes_three_keys(engine):
    base_url, _, audio_dir = engine
    encode_wav("approve the deploy", audio_dir / "said.wav")

    body = httpx.post(f"{base_url}/api/voice/transcribe", params={"filename": "said.wav"}).json()

    assert body["text"] == "approve the deploy"
    assert set(body) == {"text", "segments", "language"}, "joe's Voice.transcribe returns exactly these"


def test_transcribe_404s_on_a_missing_file(engine):
    base_url, _, _ = engine
    resp = httpx.post(f"{base_url}/api/voice/transcribe", params={"filename": "nope.wav"})
    assert resp.status_code == 404
    assert "No such file under" in resp.json()["detail"]


@pytest.mark.parametrize("escape", ["../outside.wav", "..\\outside.wav", "sub/../../outside.wav"])
def test_transcribe_refuses_to_leave_its_directory(engine, escape):
    """joe's `_resolve_audio_path` containment check, held to here too.

    The stand-in has to refuse what joe refuses, or a path-traversal defect
    in a caller passes the offline suite and fails against the real engine.
    """
    base_url, _, audio_dir = engine
    encode_wav("secret", audio_dir.parent / "outside.wav")

    resp = httpx.post(f"{base_url}/api/voice/transcribe", params={"filename": escape})

    assert resp.status_code == 404


def test_listen_returns_joes_four_keys_and_a_real_file(engine):
    """joe records *then* transcribes, so `audio_path` names a file that exists."""
    base_url, _, _ = engine
    body = httpx.post(f"{base_url}/api/voice/listen", params={"duration": 2}).json()

    assert body["text"] == "approve the deploy"
    assert set(body) == {"text", "segments", "language", "audio_path"}

    from pathlib import Path

    from vox.engine import decode_wav

    assert Path(body["audio_path"]).is_file()
    assert decode_wav(body["audio_path"]) == "approve the deploy"


def test_listen_takes_joes_silence_ms_and_reports_it(tmp_path):
    """joe's listen route ends a take `silence_ms` after the speaker stops
    (`api.py` at `2ba994d`). The stand-in has no speaker to wait for, so it
    accepts the parameter, ignores it, and writes what it saw to `pauses`,
    which is how a test sees the value arrived on the wire.

    Seen to fail by having `_listen` append a fixed None rather than the
    parsed value: `pauses` read `[None]` where 1200 had been sent.
    """
    from vox.adapters import JOE

    state = EngineState(
        audio_dirs=[tmp_path], microphone="Deterministic Input (vox)", heard="go", contract=JOE
    )
    with serve(state) as (base_url, state), HttpSTT(base_url, contract=JOE) as stt:
        stt.listen(duration=2, pause_ms=1200)
        stt.listen(duration=2)

    assert state.pauses == [1200, None]
    assert state.requests[0].endswith("?duration=2&silence_ms=1200")
    assert state.requests[1].endswith("?duration=2")


@pytest.mark.parametrize("duration", [0, -1, 61, 1000])
def test_listen_400s_outside_joes_duration_bounds(engine, duration):
    """joe: `if not 0 < duration <= 60`."""
    base_url, _, _ = engine
    resp = httpx.post(f"{base_url}/api/voice/listen", params={"duration": duration})
    assert resp.status_code == 400


@pytest.mark.parametrize(
    ("pause", "status"),
    [(0, 400), (99, 400), (-1, 400), (5001, 400), (99999, 400), ("abc", 422), ("1500.5", 422)],
)
def test_listen_refuses_a_silence_ms_joe_refuses(tmp_path, pause, status):
    """joe: `if not 100 <= silence_ms <= 5000` answers 400 (`api.py` at
    `2ba994d`), and its framework answers 422 to a value that is not an
    integer before the route runs. The stand-in holds to both, or a caller
    sending a pause the real engine refuses passes the offline suite and
    fails at a demo.

    Seen to fail before the bounds check existed: every integer case was
    answered 200 and recorded in `pauses`. Seen to fail before the parse was
    guarded: the two non-integer cases raised ValueError inside the handler
    and the client saw a dropped connection (httpx.RemoteProtocolError)
    rather than any status.
    """
    from vox.adapters import JOE

    state = EngineState(
        audio_dirs=[tmp_path], microphone="Deterministic Input (vox)", heard="go", contract=JOE
    )
    with serve(state) as (base_url, state):
        resp = httpx.post(f"{base_url}/api/voice/listen", params={"duration": 2, "silence_ms": pause})

    assert resp.status_code == status
    assert state.pauses == [], "a refused pause never arrived at a recording"


def test_listen_accepts_the_pauses_at_the_edges_of_joes_bound(tmp_path):
    """joe's bound is inclusive, `100 <= silence_ms <= 5000` (`api.py` at
    `2ba994d`), so both edges are accepted and recorded. The refusal cases
    alone leave the edges unpinned: a stand-in with an exclusive bound
    refuses two values the real engine accepts and passes every refusal case.

    Seen to fail by narrowing the engine's check to `100 < pause < 5000`:
    the first edge was answered 400 and the client raised on it
    (httpx.HTTPStatusError) before the second was sent.
    """
    from vox.adapters import JOE

    state = EngineState(
        audio_dirs=[tmp_path], microphone="Deterministic Input (vox)", heard="go", contract=JOE
    )
    with serve(state) as (base_url, state), HttpSTT(base_url, contract=JOE) as stt:
        stt.listen(duration=2, pause_ms=100)
        stt.listen(duration=2, pause_ms=5000)

    assert state.pauses == [100, 5000]


def test_listen_bounds_only_the_pause_its_contract_names(engine):
    """The bound is the stood-in engine's, so it applies only under that
    engine's spelling. On the default contract, which names no pause
    parameter, the same key is an alien query parameter the engine never
    reads: answered 200, nothing recorded, exactly as before the bound existed.

    Seen to fail by reading the bound off a fixed `silence_ms` key rather
    than `contract.pause_param`: the default engine answered 400.
    """
    base_url, state, _ = engine
    resp = httpx.post(f"{base_url}/api/voice/listen", params={"duration": 2, "silence_ms": 0})
    assert resp.status_code == 200
    assert state.pauses == [None]


def test_listen_503s_without_a_microphone(tmp_path):
    """joe raises NoMicrophoneError and `api.py` maps it to 503."""
    with serve(EngineState(audio_dirs=[tmp_path], microphone=None)) as (base_url, _):
        resp = httpx.post(f"{base_url}/api/voice/listen", params={"duration": 2})
    assert resp.status_code == 503
    assert "No microphone found" in resp.json()["detail"]


def test_joestt_needs_no_changes_to_drive_it(engine):
    """The whole point: the client that talks to joe talks to this, unmodified."""
    base_url, _, audio_dir = engine
    encode_wav("approve the deploy", audio_dir / "said.wav")
    stt = HttpSTT(base_url)

    assert stt.reachable() is True
    assert stt.devices()["microphone_available"] is True
    assert stt.transcribe_file("said.wav") == "approve the deploy"

    text, path = stt.listen(duration=2)
    assert text == "approve the deploy"
    assert path.endswith("capture.wav")


def test_joestt_reports_an_unreachable_engine_rather_than_raising(tmp_path):
    """`devices()` swallows the connection error; `reachable()` answers False.

    Checked against a port nothing is bound to, obtained by binding one and
    letting it go — a hard-coded number might be somebody else's server.
    """
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        dead_port = sock.getsockname()[1]

    # A short timeout on purpose: nothing is listening, so the only thing
    # being waited on is the wait itself.
    stt = HttpSTT(f"http://127.0.0.1:{dead_port}", timeout=0.25)
    assert stt.reachable() is False
    assert stt.devices() == {"devices": [], "microphone_available": False}
