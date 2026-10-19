"""Does `vox.engine` answer the way joe answers?

A stand-in that drifts from the engine it stands in for keeps the suite
green while the seam stops working against the real one. Every expectation
here can be read off joe's `api.py` and `Modules/Voice.py` at `ed81c4f`, on
joe's `main`, except where a test's docstring names a later commit, at which
that one was read; the commit is named so a reader can diff rather than
re-derive.

These drive the engine through `HttpSTT`, unmodified, over a real socket: a
client that needed a change to talk to the stand-in would mean the stand-in
is not standing in.

The media surface's expectations were read off `api.py`, `Modules/Media.py`
and `Modules/Analysis.py` on joe's `feat/video-audio-engine` at `4ec1424`, and
`vox.media` was run against that engine live: the routes under `/api/media`,
the keys of each answer, 415 for a type it does not take, 422 for an upload
with no decodable audio, an unknown feature or a tuning out of bounds, 204
for a delete, and `{"detail": str}` for every refusal. The order each
request is judged in, and each status and sentence, were read again on
joe's `docs/media-engine` at `d2dd135`, whose media code is `4ec1424`'s, and
checked against that engine in-process, its FastAPI 0.135.1 and pydantic
2.12.5 reading the parameters, whisper `20250625` knowing the languages,
and ffmpeg 7.1 decoding the uploads. "As checked against it" in a
docstring below means that run.
"""

import io
import json
import socket
import ssl
import struct
import wave

import httpx
import pytest

from vox.engine import (
    LANGUAGES,
    MEDIA_FORMATS,
    MEDIA_SECONDS,
    EngineState,
    encode_wav,
    media_transport,
    serve,
)
from vox.media import (
    Analysis,
    Loudness,
    MediaEngine,
    MediaHandle,
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

    The stand-in refuses the escapes joe refuses, or a path-traversal defect
    in a caller passes the offline suite and fails against the real engine.
    It does not refuse every name joe does: since `d1dc980` joe answers 404
    to any name that is not bare, and the stand-in answers one with a
    separator in it that stays inside its directory.
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


def _wav(seconds: float, rate: int = 16000, channels: int = 1, width: int = 2, frames: int | None = None) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(b"\x00" * width * channels * (int(seconds * rate) if frames is None else frames))
    return buffer.getvalue()


def _float_wav(seconds: float, rate: int = 16000, extensible: bool = False) -> bytes:
    """A mono WAV of 32-bit float samples, which the standard library's `wave`
    cannot write, plainly or as WAVE_FORMAT_EXTENSIBLE with a float subformat."""
    data = b"\x00\x00\x00\x00" * int(seconds * rate)
    if extensible:
        fmt = struct.pack("<HHIIHHHHI16s", 0xFFFE, 1, rate, rate * 4, 4, 32, 22, 32, 0,
                          b"\x03\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71")
    else:
        fmt = struct.pack("<HHIIHH", 3, 1, rate, rate * 4, 4, 32)
    chunks = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(chunks)) + chunks


_CONTEXT = ssl.create_default_context()
"""One TLS context for every client below. A client built without one loads
its own CA bundle, which on a busy machine costs more than all the requests a
test here makes, and nothing here speaks TLS."""


def _client(base_url: str) -> httpx.Client:
    return httpx.Client(base_url=base_url, verify=_CONTEXT)


SAID = Transcript("Hold.", "en", (Segment(0.0, 0.5, "Hold.", (Word(0.0, 0.5, " Hold.", 0.9),)),), {"model": "x"})
TEMPO_TO_PEAKS = ("tempo", "beats", "onsets", "loudness", "silences", "peaks")
MEASURED = Analysis(tempo=100.0, beats=(0.1,), onsets=(0.0,), loudness=Loudness(-20.0, -6.0, None),
                    silences=(Silence(0.5, 1.0),), peaks=Peaks(2.0, (0.5, 0.0)))

NOT_HELD = "no such media: it was never uploaded here, has expired, or was deleted"
UNDECODABLE = ("this media has no audio ffmpeg can decode: "
               "Error opening input files: Invalid data found when processing input")
CANNOT_READ = "vox.engine cannot read this media's sound, and EngineState.sound does not say what it holds"
EVERY_FORMAT = ", ".join(sorted(MEDIA_FORMATS))


@pytest.fixture
def media_engine():
    """A running engine whose media surface answers a script, and one client
    for every request to it: a client per request costs more than the request."""
    with (
        serve(EngineState(transcript=SAID, analysis=MEASURED)) as (base_url, state),
        _client(base_url) as client,
    ):
        yield client, state


def _upload(client: httpx.Client, body: bytes, content_type: str | None = "audio/wav") -> httpx.Response:
    headers = {} if content_type is None else {"Content-Type": content_type}
    return client.post("/api/media", content=body, headers=headers)


def test_a_media_upload_answers_v1_s_five_keys_with_201(media_engine):
    client, _ = media_engine

    resp = _upload(client, _wav(0.75, rate=8000, channels=2))

    assert resp.status_code == 201
    assert resp.json() == {"media_id": "m1", "duration": 0.75, "sample_rate": 8000, "channels": 2,
                           "has_audio": True}


def test_a_media_upload_reads_its_type_without_parameters_or_case(media_engine):
    client, _ = media_engine

    assert _upload(client, _wav(0.1), "Video/MP4; codecs=avc1").status_code == 201


@pytest.mark.parametrize(("content_type", "body", "status", "detail"), [
    ("text/plain", _wav(0.1), 415, f"unsupported media type 'text/plain'; vox.engine takes {EVERY_FORMAT}"),
    ("image/png", _wav(0.1), 415, f"unsupported media type 'image/png'; vox.engine takes {EVERY_FORMAT}"),
    (None, _wav(0.1), 415, f"unsupported media type 'none'; vox.engine takes {EVERY_FORMAT}"),
    ("audio/wav", b"plain words", 422, UNDECODABLE),
    ("audio/wav", b"", 422, UNDECODABLE),
    ("audio/wav", b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 10, 422, UNDECODABLE),
    ("audio/wav", _wav(0.0), 422, "this media's audio stream holds no samples"),
    ("video/mp4", b"\x00\x00\x00\x18ftypmp42", 422, CANNOT_READ),
    ("audio/mpeg", b"ID3 not a wav", 422, CANNOT_READ),
], ids=["text", "image", "no-type", "undecodable", "empty", "a-truncated-wav", "silent", "an-mp4", "an-mp3"])
def test_a_media_upload_refuses_what_v1_refuses_with_a_detail(media_engine, content_type, body, status, detail):
    """415 for a type joe does not take, or none, listing the types it takes
    in order; 422 for a body no decoder opens, or one holding no samples, in
    joe's words. A body beginning as a format joe takes and the stand-in
    cannot read is refused in the stand-in's own words, unless the script
    says what it holds.

    Seen to fail with the type check removed: the text and the image, WAV
    bodies under the wrong type, were answered 201 and held."""
    client, state = media_engine

    resp = _upload(client, body, content_type)

    assert (resp.status_code, resp.json()) == (status, {"detail": detail})
    assert state.uploads == {}


def test_a_media_upload_over_its_limit_is_refused_413():
    with serve(EngineState(media_limit=1000)) as (base_url, state), _client(base_url) as client:
        resp = _upload(client, _wav(1.0))

    assert (resp.status_code, resp.json()) == (413, {"detail": "media larger than 1000 bytes (EngineState.media_limit)"})
    assert state.uploads == {}


@pytest.mark.parametrize(("content_type", "body", "decoder", "status"), [
    ("text/plain", _wav(1.0), True, 415),
    ("text/plain", _wav(0.001), False, 415),
    ("audio/wav", _wav(1.0), False, 413),
    ("audio/wav", _wav(0.001), False, 503),
], ids=["type-before-size", "type-before-decoder", "declared-size-before-decoder", "no-decoder"])
def test_a_media_upload_is_judged_in_joe_s_order(content_type, body, decoder, status):
    """joe judges an upload's type, then its declared length, before reading
    a byte of it, and only then whether it has ffmpeg to read it with
    (`api.py`'s `media_upload`): an oversized upload of a type it does not
    take is a 415, and one with no ffmpeg to read it a 413.

    Seen to fail by judging the size before the type: the oversized WAV
    declared as text was answered 413. Seen to fail by judging the size only
    once the body was read: the oversized WAV with no decoder was answered
    503. Seen to fail with no decoder check: the small WAV was taken, 201."""
    with serve(EngineState(media_limit=1000, decoder=decoder)) as (base_url, state), \
            _client(base_url) as client:
        resp = _upload(client, body, content_type)

    assert (resp.status_code, resp.json()) == (status, {"detail": {
        415: f"unsupported media type 'text/plain'; vox.engine takes {EVERY_FORMAT}",
        413: "media larger than 1000 bytes (EngineState.media_limit)",
        503: "ffmpeg was not found: EngineState.decoder is False",
    }[status]})
    assert state.uploads == {}


def _answer_on(sock: socket.socket) -> tuple[int, dict]:
    """One HTTP answer read off `sock`: its status, and its body, as long as
    its head says."""
    answer = b""
    while b"\r\n\r\n" not in answer:
        answer += sock.recv(4096)
    head, _, body = answer.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    length = int(next(line.split(b":")[1] for line in lines if line.lower().startswith(b"content-length:")))
    while len(body) < length:
        body += sock.recv(4096)
    return int(lines[0].split()[1]), json.loads(body)


def test_an_upload_refused_on_its_declared_size_is_answered_then_its_body_dropped():
    """Refused on its headers alone, before a byte of the body is sent, as
    joe refuses it; and then, unlike joe, the connection stays open for the
    body, which is read and dropped before it closes. joe closes on a client
    that may still be sending, and a reset can then reach the client before
    the refusal does; the stand-in's refusal always arrives.

    Seen to fail by closing the connection on the unread body: the
    connection closed while the body was still to come."""
    with serve(EngineState(media_limit=1000)) as (base_url, _):
        port = int(base_url.rsplit(":", 1)[1])
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.sendall(b"POST /api/media HTTP/1.1\r\nHost: vox.engine\r\nContent-Type: audio/wav\r\n"
                         b"Content-Length: 4000\r\n\r\n")
            answer = _answer_on(sock)
            sock.settimeout(1.0)
            with pytest.raises(TimeoutError):
                sock.recv(1)
            sock.settimeout(5)
            sock.sendall(b"\x00" * 4000)
            closed = sock.recv(1)

    assert answer == (413, {"detail": "media larger than 1000 bytes (EngineState.media_limit)"})
    assert closed == b""


@pytest.mark.parametrize(("body", "handle"), [
    (_wav(0.25, width=1), (0.25, 16000, 1)),
    (_wav(0.25, width=3), (0.25, 16000, 1)),
    (_float_wav(0.25), (0.25, 16000, 1)),
    (_float_wav(0.25, extensible=True), (0.25, 16000, 1)),
    (_wav(0.25, rate=48000, channels=6), (0.25, 48000, 6)),
    (_wav(0, frames=1001), (0.063, 16000, 1)),
    (_wav(0, rate=8000, frames=1), (0.0, 8000, 1)),
], ids=["8-bit", "24-bit", "float", "extensible-float", "six-channels", "to-the-millisecond", "one-frame"])
def test_a_wav_is_read_from_its_own_header_as_joe_reads_it(media_engine, body, handle):
    """Any WAV of plain samples -- integers of any width, floats, plainly or
    as WAVE_FORMAT_EXTENSIBLE -- has its length, rate and channels read from
    its header, and its length answered to the millisecond, as joe answers
    each of these (checked against it).

    Seen to fail by counting integer samples alone: the float WAVs were
    refused 422 as a sound the stand-in cannot read. Seen to fail by
    answering the length unrounded: 1001 frames were 0.0625625 s."""
    client, _ = media_engine

    resp = _upload(client, body)

    assert resp.status_code == 201
    answer = resp.json()
    assert (answer["duration"], answer["sample_rate"], answer["channels"]) == handle


@pytest.mark.parametrize("body", [
    b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64,
    b"\x00\x00\x00\x08moov" + b"\x00" * 64,
    b"OggS" + b"\x00" * 64,
    b"fLaC" + b"\x00" * 64,
    b"ID3\x04\x00" + b"\x00" * 64,
    b"\xff\xfb\x90\x00" + b"\x00" * 64,
    b"\xff\xf1\x50\x80" + b"\x00" * 64,
    b"\x1a\x45\xdf\xa3" + b"\x00" * 64,
    b"RIFF\x2e\x00\x00\x00WAVEfmt \x12\x00\x00\x00\x02\x00\x01\x00\x40\x1f\x00\x00\x00\x10\x00\x00"
    b"\x00\x01\x04\x00\x00\x00data\x00\x01\x00\x00" + b"\x00" * 256,
], ids=["mp4", "mov", "ogg", "flac", "id3", "mp3", "adts", "matroska", "adpcm-wav"])
def test_a_format_the_stand_in_cannot_read_holds_what_the_script_says(body):
    """joe decodes every format it takes; the stand-in reads a WAV of plain
    samples and nothing else. A body beginning as one of the others is
    taken as holding what `EngineState.sound` says, and held to the same
    bounds as a sound the stand-in read. Whether the rest of it is sound is
    a decoder's question, which the stand-in has none to ask.

    Seen to fail by refusing such a body whatever the script said: each was
    answered 422 as one the stand-in cannot read."""
    with serve(EngineState(sound=(2.5, 48000, 2))) as (base_url, state), _client(base_url) as client:
        resp = _upload(client, body, "video/mp4")
    with serve(EngineState(sound=(MEDIA_SECONDS + 1, 48000, 2))) as (base_url, _), \
            _client(base_url) as client:
        refused = _upload(client, body, "video/mp4")

    assert resp.status_code == 201
    assert resp.json() == {"media_id": "m1", "duration": 2.5, "sample_rate": 48000, "channels": 2, "has_audio": True}
    assert state.uploads == {"m1": MediaHandle("m1", 2.5, 48000, 2, True)}
    assert refused.status_code == 413


def test_a_body_no_format_begins_as_is_refused_whatever_the_script_says():
    """joe's decoder opens none of these; the script says what a format the
    stand-in cannot read holds, not that any body is one.

    Seen to fail by taking every body as a format the stand-in cannot read:
    each was answered 201, holding the script's sound."""
    with serve(EngineState(sound=(2.5, 48000, 2))) as (base_url, state), _client(base_url) as client:
        answers = [_upload(client, body, "video/mp4") for body in (b"plain words", b"", b"\x00" * 64)]

    assert [(r.status_code, r.json()) for r in answers] == [(422, {"detail": UNDECODABLE})] * 3
    assert state.uploads == {}


@pytest.mark.parametrize(("body", "status"), [
    (_wav(1.0), 201),
    (_wav(0, frames=16001), 413),
    (_wav(2.0), 413),
], ids=["at-the-limit", "a-frame-over", "twice-over"])
def test_a_sound_longer_than_the_engine_takes_is_refused_413(body, status):
    """joe refuses a sound longer than `JOE_MEDIA_MAX_SECONDS`, two hours by
    default, as it does an upload larger than it takes; the stand-in holds
    an upload to `EngineState.media_seconds`, the same two hours unless set.

    Seen to fail with the length unchecked: both longer sounds were taken."""
    with serve(EngineState(media_seconds=1.0)) as (base_url, state), _client(base_url) as client:
        resp = _upload(client, body)

    assert resp.status_code == status
    if status == 413:
        assert resp.json() == {"detail": "this media's sound is longer than 1 seconds (EngineState.media_seconds)"}
        assert state.uploads == {}


def test_a_media_transcript_answers_v1_s_keys(media_engine):
    client, _ = media_engine
    _upload(client, _wav(1.0))

    body = client.post("/api/media/m1/transcribe", params={"words": "true"}).json()

    assert set(body) == {"text", "language", "segments", "info"}
    assert set(body["segments"][0]) == {"start", "end", "text", "words"}
    assert set(body["segments"][0]["words"][0]) == {"start", "end", "word", "probability"}


def test_a_media_transcript_without_words_answers_each_segment_s_words_empty(media_engine):
    """joe: "`words=false` leaves every segment's `words` empty" -- the key is
    still there; and its framework answers 422 to a flag it cannot read."""
    client, _ = media_engine
    _upload(client, _wav(1.0))

    body = client.post("/api/media/m1/transcribe", params={"words": "false"}).json()
    refused = client.post("/api/media/m1/transcribe", params={"words": "maybe"})

    assert body["segments"][0]["words"] == []
    assert refused.status_code == 422


@pytest.mark.parametrize(("flag", "words"), [
    ("t", True), ("y", True), ("yes", True), ("on", True), ("1", True), ("TRUE", True),
    ("f", False), ("n", False), ("no", False), ("off", False), ("0", False), ("nO", False),
    ("", None), (" true", None), ("true ", None), ("2", None), ("maybe", None), ("tru", None),
])
def test_a_media_transcript_reads_words_as_joe_s_framework_does(media_engine, flag, words):
    """joe's `words` is a boolean its framework reads, as checked against
    it: `t`, `y`, `on` and `1` and their opposites, in any case, and nothing
    with a space either side of it or blank.

    Seen to fail without `t`: it was refused 422."""
    client, _ = media_engine
    _upload(client, _wav(1.0))

    resp = client.post("/api/media/m1/transcribe", params={"words": flag})

    if words is None:
        assert (resp.status_code, resp.json()) == (
            422, {"detail": "words: Input should be a valid boolean, unable to interpret input"})
    else:
        assert resp.status_code == 200
        assert bool(resp.json()["segments"][0]["words"]) is words


def test_a_media_transcript_judges_words_before_the_id_and_its_language_after(media_engine):
    """joe's framework reads `words` before the route runs, and the route
    asks whisper's tokenizer about the language only once it holds the
    upload: an unknown id asked with a bad `words` is a 422, and asked in an
    unknown language a 404.

    Seen to fail by looking the id up first: the bad `words` was answered
    404. Seen to fail by judging the language first: the unknown language
    was answered 422."""
    client, _ = media_engine

    words = client.post("/api/media/m9/transcribe", params={"words": "maybe"})
    language = client.post("/api/media/m9/transcribe", params={"language": "klingon"})

    assert (words.status_code, language.status_code) == (422, 404)
    assert language.json() == {"detail": NOT_HELD}


@pytest.mark.parametrize(("asked", "answered"), [
    ("en", "en"), ("English", "en"), (" EN ", "en"), ("Mandarin", "zh"), ("cantonese", "yue"), ("haw", "haw"),
    ("", "en"), ("  ", "en"), (None, "en"),
    ("klingon", None), ("eng", None), ("zh-CN", None),
])
def test_a_media_transcript_is_asked_in_a_language_whisper_knows_and_answers_its_code(media_engine, asked, answered):
    """joe hands the language to whisper by code, from a code or a name in
    any case, answers the transcript in that code, and refuses a language
    whisper does not know 422; blank, it leaves the language to whisper,
    which the stand-in's script stands in for. The stand-in knows the
    languages of whisper `20250625`, as joe locks it.

    Seen to fail with unknown languages taken: `klingon` was answered 200.
    Seen to fail by answering the language as asked: `English` came back as
    `English`."""
    client, _ = media_engine
    _upload(client, _wav(1.0))

    resp = client.post("/api/media/m1/transcribe", params={} if asked is None else {"language": asked})

    if answered is None:
        assert (resp.status_code, resp.json()) == (
            422, {"detail": f"unknown language {asked!r}; give a code such as 'en' or a name such as 'English'"})
    else:
        assert (resp.status_code, resp.json()["language"]) == (200, answered)


def test_the_stand_in_knows_whisper_s_languages():
    """Every code whisper `20250625` has: a hundred, the last added `yue`."""
    assert len(LANGUAGES) == 100 and {"en", "haw", "jw", "yue"} <= set(LANGUAGES)


def test_a_media_analysis_answers_its_duration_and_each_feature_asked_for(media_engine):
    """`silence` asked for, `silences` answered, as joe's analysis does."""
    client, _ = media_engine
    _upload(client, _wav(1.0))

    some = client.post("/api/media/m1/analyze", params={"features": "tempo,silence"}).json()
    every = client.post("/api/media/m1/analyze").json()

    assert some == {"duration": 1.0, "tempo": 100.0, "silences": [{"start": 0.5, "end": 1.0}]}
    assert set(every) == {"duration", "tempo", "beats", "onsets", "loudness", "silences", "peaks"}
    assert every["loudness"] == {"rms_db": -20.0, "peak_db": -6.0, "lufs": None}
    assert every["peaks"] == {"rate": 2.0, "values": [0.5, 0.0]}


def test_a_media_analysis_answers_in_joe_s_order_whatever_the_order_asked(media_engine):
    """joe answers the duration first and then each feature asked for in its
    own order, so the same question asked two ways is the same answer, byte
    for byte.

    Seen to fail by answering in another order: the keys came back reversed."""
    client, _ = media_engine
    _upload(client, _wav(1.0))

    answers = [client.post("/api/media/m1/analyze", params={"features": asked})
               for asked in ("peaks,silence,tempo", "tempo,silence,peaks")]

    assert list(answers[0].json()) == ["duration", "tempo", "silences", "peaks"]
    assert answers[0].content == answers[1].content


def test_a_media_analysis_reads_its_features_as_joe_does(media_engine):
    """Named in any case, spaced or not; none named, or only commas, means
    every one."""
    client, _ = media_engine
    _upload(client, _wav(1.0))

    named = client.post("/api/media/m1/analyze", params={"features": " Tempo, SILENCE "}).json()
    unnamed = [client.post("/api/media/m1/analyze", params={"features": text}).json() for text in ("", ",,")]

    assert set(named) == {"duration", "tempo", "silences"}
    assert [set(body) for body in unnamed] == [{"duration", *TEMPO_TO_PEAKS}] * 2


def test_a_media_analysis_refuses_a_feature_it_does_not_measure_422(media_engine):
    """joe's `_features` answers an unknown name 422, naming it and what it
    measures."""
    client, _ = media_engine
    _upload(client, _wav(1.0))

    resp = client.post("/api/media/m1/analyze", params={"features": "tempo,silences"})

    assert (resp.status_code, resp.json()) == (
        422, {"detail": "unknown feature 'silences'; vox.engine measures tempo, beats, onsets, loudness, silence, peaks"})


def test_media_capabilities_answer_joe_s_keys(media_engine):
    client, _ = media_engine

    body = client.get("/api/media/capabilities").json()

    assert set(body) == {"formats", "features", "speech_model", "word_timestamps", "max_bytes",
                         "max_seconds", "ttl_seconds"}
    assert body["features"] == ["tempo", "beats", "onsets", "loudness", "silence", "peaks"]
    assert "audio/x-m4a" in body["formats"] and "video/matroska" in body["formats"]


def test_media_capabilities_list_joe_s_formats_in_order_and_none_without_a_decoder():
    """joe lists its formats in order, and none when it has no ffmpeg to read
    them with; its limits are the stand-in's defaults.

    Seen to fail by listing the formats whether or not there is a decoder:
    the engine without one listed twenty."""
    with serve(EngineState()) as (base_url, _), _client(base_url) as client:
        able = client.get("/api/media/capabilities").json()
    with serve(EngineState(decoder=False)) as (base_url, _), _client(base_url) as client:
        unable = client.get("/api/media/capabilities").json()

    assert able["formats"] == sorted(MEDIA_FORMATS) and len(able["formats"]) == 20
    assert (able["max_bytes"], able["max_seconds"], able["ttl_seconds"]) == (1024**3, 7200.0, 3600.0)
    assert unable["formats"] == []


def test_a_media_delete_answers_204_and_an_unknown_upload_is_404_on_every_route(media_engine):
    client, state = media_engine
    _upload(client, _wav(1.0))

    deleted = client.delete("/api/media/m1")
    after = [
        client.post("/api/media/m1/transcribe"),
        client.post("/api/media/m1/analyze"),
        client.delete("/api/media/m1"),
    ]

    assert (deleted.status_code, deleted.content) == (204, b"")
    assert state.uploads == {}
    assert [r.status_code for r in after] == [404, 404, 404]
    assert [r.json() for r in after] == [{"detail": NOT_HELD}] * 3


def test_a_media_id_is_never_reused(media_engine):
    """An id handed out once names that upload alone, deleted or not."""
    client, _ = media_engine
    first = _upload(client, _wav(0.1))
    client.delete("/api/media/m1")
    second = _upload(client, _wav(0.1))

    assert (first.json()["media_id"], second.json()["media_id"]) == ("m1", "m2")


class _Clock:
    """A clock a test moves itself."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_an_upload_unused_for_longer_than_it_is_kept_is_404():
    """joe keeps an upload `JOE_MEDIA_TTL` seconds after it was last used --
    uploaded, transcribed or analysed -- and answers 404 for it after, as
    for an id it never handed out; the stand-in counts `media_ttl` on its
    `clock`.

    Seen to fail with nothing expiring: the upload unused past its time was
    transcribed and analysed, 200. Seen to fail with a use not counted: the
    upload used at the edge of its time was 404 fifty-nine seconds later."""
    clock = _Clock()
    with serve(EngineState(media_ttl=60.0, clock=clock)) as (base_url, _), \
            _client(base_url) as client:
        _upload(client, _wav(1.0))
        clock.now += 60.0
        at_the_edge = client.post("/api/media/m1/transcribe")
        clock.now += 59.0
        used_since = client.post("/api/media/m1/analyze")
        clock.now += 60.5
        past_it = [client.post("/api/media/m1/transcribe"), client.post("/api/media/m1/analyze")]

    assert (at_the_edge.status_code, used_since.status_code) == (200, 200)
    assert [(r.status_code, r.json().get("detail")) for r in past_it] == [(404, NOT_HELD)] * 2


def test_an_upload_kept_past_its_time_goes_at_the_next_upload_and_deletes_until_then():
    """joe sweeps what it has kept past its time as each upload arrives, and
    a delete takes one no sweep has yet.

    Seen to fail with no sweep: the upload past its time was still held
    after the next one arrived."""
    clock = _Clock()
    with serve(EngineState(media_ttl=60.0, clock=clock)) as (base_url, state), \
            _client(base_url) as client:
        _upload(client, _wav(1.0))
        _upload(client, _wav(1.0))
        clock.now += 61.0
        deleted = client.delete("/api/media/m1")
        _upload(client, _wav(1.0))

    assert deleted.status_code == 204
    assert set(state.uploads) == {"m3"}


@pytest.mark.parametrize(("name", "value", "status"), [
    ("peaks_rate", "0", 422), ("peaks_rate", "1001", 422), ("peaks_rate", "2.5", 422), ("peaks_rate", "x", 422),
    ("peaks_rate", "1", 200), ("peaks_rate", "1000", 200),
    ("silence_db", "-121", 422), ("silence_db", "0.5", 422), ("silence_db", "-120", 200), ("silence_db", "0", 200),
    ("silence_min", "0", 422), ("silence_min", "3601", 422), ("silence_min", "3600", 200),
    ("silence_min", "0.01", 200),
])
def test_a_media_analysis_holds_its_tuning_to_joe_s_bounds(media_engine, name, value, status):
    """joe's analyze route takes `peaks_rate` as an integer `ge=1, le=1000`,
    `silence_db` as `ge=-120, le=0` and `silence_min` as `gt=0, le=3600`, and
    its framework refuses anything else 422 before the route runs. The edges
    are pinned too: a stand-in with an exclusive bound passes every refusal.

    Seen to fail with the bounds unchecked: every refusal was answered 200."""
    client, _ = media_engine
    _upload(client, _wav(1.0))

    resp = client.post("/api/media/m1/analyze", params={"features": "peaks", name: value})

    assert resp.status_code == status
    if status == 422:
        assert resp.json()["detail"].startswith(f"{name}: Input should be ")


@pytest.mark.parametrize(("name", "value", "refused"), [
    ("peaks_rate", "5.0", None), ("peaks_rate", "1000.00", None), ("peaks_rate", " 5", None),
    ("peaks_rate", "+5", None), ("peaks_rate", "1_000", None), ("peaks_rate", "07", None),
    ("peaks_rate", "1e2", "a valid integer, unable to parse string as an integer"),
    ("peaks_rate", "0x10", "a valid integer, unable to parse string as an integer"),
    ("peaks_rate", "５", "a valid integer, unable to parse string as an integer"),
    ("peaks_rate", "", "a valid integer, unable to parse string as an integer"),
    ("peaks_rate", "-0", "greater than or equal to 1"),
    ("peaks_rate", "1001", "less than or equal to 1000"),
    ("silence_db", "-40.", None), ("silence_db", "-1e2", None), ("silence_db", " -40", None),
    ("silence_db", "nan", "less than or equal to 0"), ("silence_db", "inf", "less than or equal to 0"),
    ("silence_db", "-inf", "greater than or equal to -120"), ("silence_db", "-121", "greater than or equal to -120"),
    ("silence_db", "", "a valid number, unable to parse string as a number"),
    ("silence_db", "５", "a valid number, unable to parse string as a number"),
    ("silence_min", "1e1", None), ("silence_min", "-0", "greater than 0"),
    ("silence_min", "nan", "less than or equal to 3600"), ("silence_min", "inf", "less than or equal to 3600"),
])
def test_a_media_analysis_reads_its_tuning_as_joe_s_framework_does(media_engine, name, value, refused):
    """What joe's framework reads as an integer or a number, and why it
    refuses what it does not, in its words, as checked against it: `5.0` is
    the integer 5 and `2.5` no integer, and a value outside both bounds, as
    `nan` is, is refused for the upper.

    Seen to fail by reading an integer without its zeros: `5.0` was refused."""
    client, _ = media_engine
    _upload(client, _wav(1.0))

    resp = client.post("/api/media/m1/analyze", params={"features": "peaks", name: value})

    if refused is None:
        assert resp.status_code == 200
    else:
        assert (resp.status_code, resp.json()) == (422, {"detail": f"{name}: Input should be {refused}"})


def test_a_media_analysis_refuses_every_tuning_it_refuses_at_once(media_engine):
    """joe's framework refuses each value it cannot take, and joe says them
    all in one sentence, in the order its route takes them.

    Seen to fail by saying the first alone: only `peaks_rate` was named."""
    client, _ = media_engine
    _upload(client, _wav(1.0))

    resp = client.post("/api/media/m1/analyze", params={"silence_min": "0", "peaks_rate": "x", "silence_db": "nan"})

    assert (resp.status_code, resp.json()["detail"]) == (422, (
        "peaks_rate: Input should be a valid integer, unable to parse string as an integer; "
        "silence_db: Input should be less than or equal to 0; "
        "silence_min: Input should be greater than 0"))


@pytest.mark.parametrize(("params", "detail"), [
    ({"peaks_rate": "0"}, "peaks_rate: Input should be greater than or equal to 1"),
    ({"features": "pitch"}, "unknown feature 'pitch'; vox.engine measures tempo, beats, onsets, loudness, silence, peaks"),
    ({"features": "pitch", "silence_db": "1"}, "silence_db: Input should be less than or equal to 0"),
], ids=["tuning", "feature", "tuning-before-feature"])
def test_a_media_analysis_judges_its_tuning_and_features_before_the_id(media_engine, params, detail):
    """joe's framework refuses the tuning before the route runs, and the
    route judges the features before it looks the upload up: an unknown id
    asked with either is a 422, naming the tuning when it is both.

    Seen to fail by looking the id up first: each was answered 404."""
    client, _ = media_engine

    resp = client.post("/api/media/m9/analyze", params=params)

    assert (resp.status_code, resp.json()) == (422, {"detail": detail})


def test_a_parameter_given_twice_is_read_at_its_last(media_engine):
    """joe's framework reads the last of a parameter given more than once.

    Seen to fail by reading the first: each was refused 422."""
    client, _ = media_engine
    _upload(client, _wav(1.0))

    answers = [
        client.post("/api/media/m1/transcribe?words=maybe&words=true"),
        client.post("/api/media/m1/analyze?features=pitch&features=tempo"),
        client.post("/api/media/m1/analyze?peaks_rate=0&peaks_rate=5"),
    ]

    assert [r.status_code for r in answers] == [200, 200, 200]


def test_a_blank_parameter_is_read_as_given_not_dropped(media_engine):
    """joe's framework reads `words=` as a blank `words`, which is no
    boolean, and a blank `peaks_rate` as no integer; a blank `features` is
    every feature, and a blank `language` leaves it to the engine.

    Seen to fail by dropping blank values: `words=` and `peaks_rate=` were
    answered 200."""
    client, _ = media_engine
    _upload(client, _wav(1.0))

    answers = [
        client.post("/api/media/m1/transcribe?words="),
        client.post("/api/media/m1/analyze?peaks_rate="),
        client.post("/api/media/m1/analyze?features="),
        client.post("/api/media/m1/transcribe?language="),
    ]

    assert [r.status_code for r in answers] == [422, 422, 200, 200]
    assert set(answers[2].json()) == {"duration", *TEMPO_TO_PEAKS}


@pytest.mark.parametrize("script", [
    {"transcript": RuntimeError("the model would not load")},
    {"analysis": RuntimeError("the model would not load")},
], ids=["transcript", "analysis"])
def test_a_failure_of_the_engine_s_own_is_answered_500_naming_it(script, capfd):
    """joe answers a failure of its own on any media route 500, its detail
    naming the exception, and puts the traceback in its log. A script that
    is an exception is the stand-in's way to fail so, and every time it is
    asked.

    Seen to fail by letting the failure escape: the connection dropped with
    no answer (httpx.RemoteProtocolError)."""
    with serve(EngineState(**script)) as (base_url, _), _client(base_url) as client:
        _upload(client, _wav(1.0))
        route = "transcribe" if "transcript" in script else "analyze"
        answers = [client.post(f"/api/media/m1/{route}") for _ in range(2)]

    assert [(r.status_code, r.json()) for r in answers] == [
        (500, {"detail": "RuntimeError: the model would not load"})] * 2
    assert "RuntimeError: the model would not load" in capfd.readouterr().err


def test_the_media_surface_answers_the_same_over_a_socket_and_in_process(tmp_path):
    """`media_transport` is the same engine with no socket: the same answers
    to the same requests, recorded the same way."""
    path = tmp_path / "mix.wav"
    path.write_bytes(_wav(1.25))

    def run(media: MediaEngine) -> list:
        upload = media.upload(path, "audio/wav")
        results = [upload, media.transcribe(upload, language="de"), media.analyze(upload, ["beats", "peaks"]),
                   media.capabilities()]
        for refused in (lambda: media.upload(b"plain", "text/plain"), lambda: media.upload(b"plain", "audio/wav"),
                        lambda: media.transcribe(upload, language="klingon"),
                        lambda: media.analyze("m9", peaks_rate=0)):
            try:
                refused()
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
    assert [refusal[1] for refusal in sent[4:]] == [415, 422, 422, 422]
    assert over_a_socket.requests == in_process.requests
    assert over_a_socket.requests[-1] == "/api/media/m1"
