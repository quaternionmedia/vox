"""Does `vox.engine` answer the way joe answers?

A stand-in that drifts from the engine it stands in for keeps the suite
green while the seam stops working against the real one. Every expectation
here was read off joe's `api.py` and `Modules/Voice.py` at `c2d9a01`, except
where a test's docstring names a later commit, at which that one was read;
the commit is named so a reader can diff rather than re-derive.

These drive the engine through `HttpSTT`, unmodified, over a real socket: a
client that needed a change to talk to the stand-in would mean the stand-in
is not standing in.

The media surface's expectations are read off the statement of joe's media
API in the QM wave-3 lane brief (V1), which joe's `feat/video-audio-engine`
implements: the routes under `/api/media`, the keys of each answer, 415 for
a type it does not take, 422 for an upload with no decodable audio, and
`{"detail": ...}` for every refusal.
"""

import io
import wave

import httpx
import pytest

from vox.engine import EngineState, encode_wav, media_transport, serve
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
    """Never a default port: a fixed port can be another program's server
    (`handbook/async-contract.md` §4 in the QM corpus).
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

    Seen to fail with the bounds check removed: every integer case was
    answered 200 and recorded in `pauses`. Seen to fail with the parse
    unguarded: the two non-integer cases raised ValueError inside the handler
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
    """The bound is the stood-in engine's, and it applies whenever a contract
    names a pause parameter, under that contract's own spelling. On the
    default contract, which names no pause parameter, the same key is an
    alien query parameter the engine never reads: answered 200, nothing
    recorded.

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
    """The client that talks to joe talks to this, unmodified."""
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


# --- the media surface ------------------------------------------------------------------


def _wav(seconds: float, rate: int = 16000, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * channels * int(seconds * rate))
    return buffer.getvalue()


SAID = Transcript("Hold.", "en", (Segment(0.0, 0.5, " Hold.", (Word(0.0, 0.5, " Hold.", 0.9),)),), {"model": "x"})
MEASURED = Analysis(tempo=100.0, beats=(0.1,), onsets=(0.0,), loudness=Loudness(-20.0, -6.0, None),
                    silences=(Silence(0.5, 1.0),), peaks=Peaks(2.0, (0.5, 0.0)))


@pytest.fixture
def media_engine():
    """A running engine whose media surface answers a script, and one client
    for every request to it: a client per request costs more than the request."""
    with (
        serve(EngineState(transcript=SAID, analysis=MEASURED)) as (base_url, state),
        httpx.Client(base_url=base_url) as client,
    ):
        yield client, state


def _upload(client: httpx.Client, body: bytes, content_type: str = "audio/wav") -> httpx.Response:
    return client.post("/api/media", content=body, headers={"Content-Type": content_type})


def test_a_media_upload_answers_v1_s_five_keys_with_201(media_engine):
    client, _ = media_engine

    resp = _upload(client, _wav(0.75, rate=8000, channels=2))

    assert resp.status_code == 201
    assert resp.json() == {"media_id": "m1", "duration": 0.75, "sample_rate": 8000, "channels": 2,
                           "has_audio": True}


def test_a_media_upload_reads_its_type_without_parameters_or_case(media_engine):
    client, _ = media_engine

    assert _upload(client, _wav(0.1), "Video/MP4; codecs=avc1").status_code == 201


@pytest.mark.parametrize(("content_type", "body", "status"), [
    ("text/plain", _wav(0.1), 415),
    ("image/png", _wav(0.1), 415),
    ("audio/wav", b"ID3 not a wav", 422),
    ("video/mp4", b"\x00\x00\x00\x18ftypmp42", 422),
    ("audio/wav", _wav(0.0), 422),
], ids=["text", "image", "undecodable", "a-video-it-cannot-decode", "silent"])
def test_a_media_upload_refuses_what_v1_refuses_with_a_detail(media_engine, content_type, body, status):
    """415 for a type it does not take, 422 for an upload with no decodable
    audio. The stand-in decodes a WAV and nothing else, so anything else it
    takes is undecodable to it.

    Seen to fail with the type check removed: the text and the image, WAV
    bodies under the wrong type, were answered 201 and held."""
    client, state = media_engine

    resp = _upload(client, body, content_type)

    assert resp.status_code == status
    assert set(resp.json()) == {"detail"}
    assert state.uploads == {}


def test_a_media_upload_over_its_limit_is_refused_413():
    with serve(EngineState(media_limit=1000)) as (base_url, state), httpx.Client(base_url=base_url) as client:
        resp = _upload(client, _wav(1.0))

    assert resp.status_code == 413
    assert state.uploads == {}


def test_a_media_transcript_answers_v1_s_keys(media_engine):
    client, _ = media_engine
    _upload(client, _wav(1.0))

    body = client.post("/api/media/m1/transcribe", params={"words": "true"}).json()

    assert set(body) == {"text", "language", "segments", "info"}
    assert set(body["segments"][0]) == {"start", "end", "text", "words"}
    assert set(body["segments"][0]["words"][0]) == {"start", "end", "word", "probability"}


def test_a_media_transcript_without_words_answers_segments_without_them(media_engine):
    client, _ = media_engine
    _upload(client, _wav(1.0))

    body = client.post("/api/media/m1/transcribe", params={"words": "false"}).json()
    refused = client.post("/api/media/m1/transcribe", params={"words": "maybe"})

    assert set(body["segments"][0]) == {"start", "end", "text"}
    assert refused.status_code == 422


def test_a_media_analysis_answers_its_duration_and_each_feature_asked_for(media_engine):
    """`silence` asked for, `silences` answered, as V1 states it."""
    client, _ = media_engine
    _upload(client, _wav(1.0))

    some = client.post("/api/media/m1/analyze", params={"features": "tempo,silence"}).json()
    every = client.post("/api/media/m1/analyze").json()

    assert some == {"duration": 1.0, "tempo": 100.0, "silences": [{"start": 0.5, "end": 1.0}]}
    assert set(every) == {"duration", "tempo", "beats", "onsets", "loudness", "silences", "peaks"}
    assert every["loudness"] == {"rms_db": -20.0, "peak_db": -6.0, "lufs": None}
    assert every["peaks"] == {"rate": 2.0, "values": [0.5, 0.0]}


def test_a_media_analysis_refuses_a_feature_it_does_not_measure(media_engine):
    client, _ = media_engine
    _upload(client, _wav(1.0))

    resp = client.post("/api/media/m1/analyze", params={"features": "tempo,silences"})

    assert resp.status_code == 400
    assert "silences" in resp.json()["detail"]


def test_media_capabilities_answer_v1_s_keys(media_engine):
    client, _ = media_engine

    body = client.get("/api/media/capabilities").json()

    assert set(body) == {"formats", "features", "speech_model", "word_timestamps"}
    assert body["features"] == ["tempo", "beats", "onsets", "loudness", "silence", "peaks"]


def test_a_media_delete_discards_and_an_unknown_upload_is_404_on_every_route(media_engine):
    client, state = media_engine
    _upload(client, _wav(1.0))

    deleted = client.delete("/api/media/m1")
    after = [
        client.post("/api/media/m1/transcribe"),
        client.post("/api/media/m1/analyze"),
        client.delete("/api/media/m1"),
    ]

    assert deleted.status_code == 200 and state.uploads == {}
    assert [r.status_code for r in after] == [404, 404, 404]
    assert after[0].json() == {"detail": "No such media: m1"}


def test_a_media_id_is_never_reused(media_engine):
    """An id handed out once names that upload alone, deleted or not."""
    client, _ = media_engine
    first = _upload(client, _wav(0.1))
    client.delete("/api/media/m1")
    second = _upload(client, _wav(0.1))

    assert (first.json()["media_id"], second.json()["media_id"]) == ("m1", "m2")


def test_the_media_surface_answers_the_same_over_a_socket_and_in_process(tmp_path):
    """`media_transport` is the same engine with no socket: the same answers
    to the same requests, recorded the same way."""
    path = tmp_path / "mix.wav"
    path.write_bytes(_wav(1.25))

    def run(media: MediaEngine) -> list:
        upload = media.upload(path, "audio/wav")
        results = [upload, media.transcribe(upload, language="de"), media.analyze(upload, ["beats", "peaks"]),
                   media.capabilities()]
        try:
            media.upload(b"plain", "text/plain")
        except Exception as exc:  # noqa: BLE001 -- the refusal is one of the answers compared
            results.append((type(exc).__name__, exc.status, exc.detail))
        return results

    over_a_socket = EngineState(transcript=SAID, analysis=MEASURED)
    with serve(over_a_socket) as (base_url, _), MediaEngine(over_a_socket.contract, base_url) as media:
        sent = run(media)

    in_process = EngineState(transcript=SAID, analysis=MEASURED)
    with httpx.Client(transport=media_transport(in_process)) as client, \
            MediaEngine(in_process.contract, "http://vox.engine", client=client) as media:
        answered = run(media)

    assert sent == answered
    assert over_a_socket.requests == in_process.requests
    assert over_a_socket.requests[-1] == "/api/media/m1"
