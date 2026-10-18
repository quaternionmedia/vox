"""`MediaEngine`, the client for the contract's media surface.

Most of these run the real client -- building each request in the contract's
spelling, reading each answer into dataclasses -- against the stand-in's
media surface in-process (`vox.engine.media_transport`), which holds a caller
to what the engine it stands in for refuses. Where a test needs an answer the
stand-in never gives -- an engine's literal JSON, a malformed one, a timeout
-- it hands the client an `httpx.MockTransport` that answers just that.

`tests/test_contract.py` runs the same client against a contract sharing no
spelling with the default one these use.
"""

import io
import pickle
import socket
import wave
from dataclasses import fields, replace

import httpx
import pytest

from vox.contract import EngineContract
from vox.engine import EngineState, media_transport
from vox.media import (
    FEATURES,
    Analysis,
    Capabilities,
    EngineAnswerMalformed,
    EngineDoesNotOffer,
    EngineRefused,
    EngineUnavailable,
    Loudness,
    MediaEngine,
    MediaHandle,
    Peaks,
    Segment,
    Silence,
    Transcript,
    Word,
)

BASE = "http://vox.engine"

SAID = Transcript(
    text="Approve the deploy. Hold the release.",
    language="en",
    segments=(
        Segment(0.0, 1.2, "Approve the deploy.", (
            Word(0.0, 0.5, " Approve", 0.98), Word(0.5, 0.7, " the", 0.99), Word(0.7, 1.2, " deploy.", 0.95),
        )),
        Segment(1.4, 2.0, "Hold the release.", (
            Word(1.4, 1.6, " Hold", 0.9), Word(1.6, 1.7, " the", 0.97), Word(1.7, 2.0, " release.", 0.93),
        )),
    ),
    info={"model": "scripted"},
)

MEASURED = Analysis(
    tempo=96.0,
    beats=(0.25, 0.875, 1.5),
    onsets=(0.0, 0.5, 1.4),
    loudness=Loudness(rms_db=-18.5, peak_db=-2.0, lufs=None),
    silences=(Silence(1.2, 1.4),),
    peaks=Peaks(rate=2.0, values=(0.1, 0.8, 0.6, 0.0)),
)


def _wav(seconds: float = 2.0, rate: int = 16000, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * channels * int(seconds * rate))
    return buffer.getvalue()


@pytest.fixture
def engine():
    """The client, against the stand-in's media surface on the default contract."""
    state = EngineState(transcript=SAID, analysis=MEASURED)
    with httpx.Client(transport=media_transport(state)) as client, \
            MediaEngine(state.contract, BASE, client=client) as media:
        yield media, state


def _answering(status: int, body=None, *, text: str | None = None, seen: list | None = None) -> httpx.Client:
    """A client every request through which is answered `status` with `body`."""

    def answer(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if text is not None:
            return httpx.Response(status, text=text)
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(answer))


def _failing(error: Exception, seen: list | None = None) -> httpx.Client:
    def answer(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        raise error

    return httpx.Client(transport=httpx.MockTransport(answer))


# ─── an upload ────────────────────────────────────────────────────────────────


def test_an_upload_of_bytes_comes_back_as_its_handle(engine):
    media, state = engine

    upload = media.upload(_wav(1.5), "audio/wav")

    assert upload == MediaHandle(media_id="m1", duration=1.5, sample_rate=16000, channels=1, has_audio=True)
    assert state.requests == ["/api/media"]
    assert set(state.uploads) == {"m1"}


def test_an_upload_from_a_path_is_streamed_with_its_length_and_type(tmp_path):
    """The engine reads the file as it arrives, so it is sent from the file,
    with the Content-Length a server needs and the type the caller named."""
    path = tmp_path / "mix.wav"
    path.write_bytes(_wav(0.25, channels=2))
    seen: list[httpx.Request] = []
    answer = {"media_id": "a1", "duration": 0.25, "sample_rate": 16000, "channels": 2, "has_audio": True}

    with _answering(201, answer, seen=seen) as client:
        upload = MediaEngine(EngineContract(), BASE, client=client).upload(path, "audio/wav")

    (request,) = seen
    assert request.method == "POST" and request.url == f"{BASE}/api/media"
    assert request.headers["content-type"] == "audio/wav"
    assert request.headers["content-length"] == str(path.stat().st_size)
    assert request.content == path.read_bytes()
    assert upload.channels == 2


def test_an_upload_needs_its_content_type():
    seen: list = []
    with _answering(201, {}, seen=seen) as client, pytest.raises(ValueError, match="needs its content type"):
        MediaEngine(EngineContract(), BASE, client=client).upload(_wav(), "")
    assert seen == []


# ─── a transcript ─────────────────────────────────────────────────────────────


def test_a_transcript_comes_back_as_segments_and_their_words(engine):
    media, state = engine
    upload = media.upload(_wav(), "audio/wav")

    transcript = media.transcribe(upload)

    assert transcript == SAID
    assert state.requests[-1] == "/api/media/m1/transcribe?words=true"
    assert [w.word for w in transcript.words] == [" Approve", " the", " deploy.", " Hold", " the", " release."]


def test_a_language_is_asked_for_by_name_and_heard_in(engine):
    media, state = engine
    upload = media.upload(_wav(), "audio/wav")

    transcript = media.transcribe(upload, language="sv")

    assert state.requests[-1] == "/api/media/m1/transcribe?language=sv&words=true"
    assert transcript.language == "sv"


def test_words_false_asks_for_segments_alone(engine):
    media, state = engine
    upload = media.upload(_wav(), "audio/wav")

    transcript = media.transcribe(upload, words=False)

    assert state.requests[-1] == "/api/media/m1/transcribe?words=false"
    assert [s.text for s in transcript.segments] == ["Approve the deploy.", "Hold the release."]
    assert transcript.words == ()


def test_a_handle_s_id_alone_is_enough(engine):
    """An id kept from an earlier upload names it as well as its handle does."""
    media, _ = engine
    upload = media.upload(_wav(), "audio/wav")

    assert media.transcribe(upload.media_id) == media.transcribe(upload)


def test_an_id_is_escaped_whole_into_its_route(engine):
    """An id with a separator in it addresses that id and no other route.

    Seen to fail by putting the id in unescaped: the request went to
    `/api/media/a/b`, the id's `?` beginning a query that swallowed the rest
    of the transcribe route."""
    media, state = engine

    with pytest.raises(EngineRefused) as refused:
        media.transcribe("a/b?c")

    assert state.requests == ["/api/media/a%2Fb%3Fc/transcribe?words=true"]
    assert (refused.value.status, refused.value.detail) == (404, "No such media: a/b?c")


# ─── an analysis ──────────────────────────────────────────────────────────────


def test_an_analysis_asks_for_every_feature_the_contract_names(engine):
    media, state = engine
    upload = media.upload(_wav(2.0), "audio/wav")

    analysis = media.analyze(upload)

    assert analysis == replace(MEASURED, duration=2.0)
    assert state.requests[-1] == "/api/media/m1/analyze?features=tempo%2Cbeats%2Consets%2Cloudness%2Csilence%2Cpeaks"


def test_a_feature_is_asked_for_by_the_engine_s_name_and_read_back_by_vox_s(engine):
    """vox says `silences`; the engine is asked for `silence`.

    Seen to fail by sending vox's own name: the engine refused it, 422,
    naming `silences` as a feature it does not measure."""
    media, state = engine
    upload = media.upload(_wav(2.0), "audio/wav")

    analysis = media.analyze(upload, ["silences"])

    assert state.requests[-1] == "/api/media/m1/analyze?features=silence"
    assert analysis == Analysis(duration=2.0, silences=(Silence(1.2, 1.4),))


def test_one_feature_can_be_named_alone(engine):
    media, _ = engine
    upload = media.upload(_wav(), "audio/wav")

    assert media.analyze(upload, "tempo") == Analysis(duration=2.0, tempo=96.0)


@pytest.mark.parametrize(("features", "message"), [
    (["tempo", "pitch"], "no feature named 'pitch'"),
    (["silence"], "no feature named 'silence'"),
    ([], "no features asked for"),
])
def test_a_feature_vox_has_no_name_for_is_the_caller_s_mistake(features, message):
    """Refused before any request: the engine's own spelling is not vox's."""
    seen: list = []
    with _answering(200, {}, seen=seen) as client, pytest.raises(ValueError, match=message):
        MediaEngine(EngineContract(), BASE, client=client).analyze("m1", features)
    assert seen == []


def test_the_analysis_fields_are_vox_s_feature_names():
    assert tuple(f.name for f in fields(Analysis)) == ("duration", *FEATURES)


# ─── what it offers ───────────────────────────────────────────────────────────


def test_capabilities_say_what_the_engine_takes_and_offers(engine):
    media, state = engine

    capabilities = media.capabilities()

    assert state.requests == ["/api/media/capabilities"]
    assert "audio/wav" in capabilities.formats and "video/mp4" in capabilities.formats
    assert capabilities.features == FEATURES
    assert capabilities.word_timestamps is True


def test_capabilities_name_only_what_analyze_can_ask_for_in_vox_s_names():
    """A feature the engine reports and its contract does not name cannot be
    asked for, so it is not offered; one it names is offered under vox's name."""
    answer = {"formats": ["wav"], "features": ["pitch", "silence", "tempo"],
              "speech_model": "base", "word_timestamps": False}
    with _answering(200, answer) as client:
        capabilities = MediaEngine(EngineContract(), BASE, client=client).capabilities()

    assert capabilities == Capabilities(formats=("wav",), features=("tempo", "silences"),
                                        speech_model="base", word_timestamps=False)


# ─── discarding ───────────────────────────────────────────────────────────────


def test_close_discards_one_upload(engine):
    media, state = engine
    kept, dropped = media.upload(_wav(), "audio/wav"), media.upload(_wav(), "audio/wav")

    assert media.close(dropped) is True

    assert state.requests[-1] == "/api/media/m2"
    assert set(state.uploads) == {kept.media_id}


def test_leaving_the_block_discards_what_this_engine_still_holds():
    """And only that: an upload discarded already is not asked for again, and
    the client it was given stays open for its owner.

    Seen to fail by leaving the block without discarding: the engine still
    held `m1`."""
    state = EngineState()
    with httpx.Client(transport=media_transport(state)) as client:
        with MediaEngine(state.contract, BASE, client=client) as media:
            held = media.upload(_wav(), "audio/wav")
            dropped = media.upload(_wav(), "audio/wav")
            media.close(dropped)
        assert not client.is_closed

    assert state.uploads == {}
    assert state.requests == [
        "/api/media", "/api/media", f"/api/media/{dropped.media_id}", f"/api/media/{held.media_id}",
    ]


def test_close_never_raises():
    """An upload the engine cannot be told to drop expires on its own clock."""
    with _failing(httpx.ConnectError("refused")) as client:
        assert MediaEngine(EngineContract(), BASE, client=client).close("m1") is False
    with _answering(404, {"detail": "No such media: m1"}) as client:
        assert MediaEngine(EngineContract(), BASE, client=client).close("m1") is False


def test_close_sends_nothing_on_a_contract_whose_uploads_only_expire():
    seen: list = []
    with _answering(200, {}, seen=seen) as client:
        media = MediaEngine(replace(EngineContract(), media_delete=None), BASE, client=client)
        assert media.close("m1") is False
        assert media.close() is True
    assert seen == []


# ─── an engine's own answers ──────────────────────────────────────────────────


JOE_ID = "0f3c2a9be1d84c6f9a7e5b4d3c2b1a09"

# The answers joe's media routes give, in the shapes its `api.py` and
# `Modules/Media.py` write them (`feat/video-audio-engine`): a 201 for an
# upload, a 204 with no body for a delete, a segment's text trimmed and each
# word's leading space kept, and capabilities that also state its limits.
JOE_ANSWERS = {
    ("POST", "/api/media"): (201, {"media_id": JOE_ID, "duration": 2.5, "sample_rate": 44100,
                                   "channels": 2, "has_audio": True}),
    ("POST", f"/api/media/{JOE_ID}/transcribe"): (200, {
        "text": "Approve the deploy.",
        "language": "en",
        "segments": [{
            "start": 0.0, "end": 1.2, "text": "Approve the deploy.",
            "words": [{"start": 0.0, "end": 0.5, "word": " Approve", "probability": 0.98},
                      {"start": 0.5, "end": 0.7, "word": " the", "probability": 0.99},
                      {"start": 0.7, "end": 1.2, "word": " deploy.", "probability": 0.95}],
        }],
        "info": {"model": "whisper-base", "language": None, "words": True, "duration": 2.5,
                 "avg_logprob": -0.21, "no_speech_prob": 0.01, "compression_ratio": 0.8},
    }),
    ("POST", f"/api/media/{JOE_ID}/analyze"): (200, {
        "duration": 2.5, "tempo": 120.0, "beats": [0.5, 1.0], "onsets": [0.02],
        "loudness": {"rms_db": -20.1, "peak_db": -1.5, "lufs": None},
        "silences": [{"start": 1.2, "end": 2.5}],
        "peaks": {"rate": 50, "values": [0.2, 0.9, 0.1, 0.0, 0.0]},
    }),
    ("GET", "/api/media/capabilities"): (200, {
        "formats": ["audio/mpeg", "audio/wav", "video/mp4"],
        "features": ["tempo", "beats", "onsets", "loudness", "silence", "peaks"],
        "speech_model": "whisper-base", "word_timestamps": True,
        "max_bytes": 1073741824, "max_seconds": 7200.0, "ttl_seconds": 3600.0,
    }),
    ("DELETE", f"/api/media/{JOE_ID}"): (204, None),
}


def _joe(request: httpx.Request) -> httpx.Response:
    status, body = JOE_ANSWERS[(request.method, request.url.path)]
    return httpx.Response(status) if body is None else httpx.Response(status, json=body)


def test_the_default_contract_reads_joe_s_own_answers():
    """Read without the stand-in: the stand-in writes under the same contract
    the client reads with, and a key both spelled wrong would pass between
    them. These are joe's literal answers."""
    with httpx.Client(transport=httpx.MockTransport(_joe)) as client:
        media = MediaEngine(EngineContract(), BASE, client=client)
        upload = media.upload(b"RIFF", "video/mp4")
        transcript = media.transcribe(upload)
        analysis = media.analyze(upload)
        capabilities = media.capabilities()
        discarded = media.close(upload)

    assert upload == MediaHandle(JOE_ID, 2.5, 44100, 2, True)
    assert transcript == Transcript(
        text="Approve the deploy.", language="en",
        segments=(Segment(0.0, 1.2, "Approve the deploy.", (
            Word(0.0, 0.5, " Approve", 0.98), Word(0.5, 0.7, " the", 0.99), Word(0.7, 1.2, " deploy.", 0.95),
        )),),
        info={"model": "whisper-base", "language": None, "words": True, "duration": 2.5,
              "avg_logprob": -0.21, "no_speech_prob": 0.01, "compression_ratio": 0.8},
    )
    assert analysis == Analysis(
        duration=2.5, tempo=120.0, beats=(0.5, 1.0), onsets=(0.02,),
        loudness=Loudness(-20.1, -1.5, None), silences=(Silence(1.2, 2.5),),
        peaks=Peaks(50.0, (0.2, 0.9, 0.1, 0.0, 0.0)),
    )
    assert capabilities == Capabilities(
        formats=("audio/mpeg", "audio/wav", "video/mp4"), features=FEATURES, speech_model="whisper-base",
        word_timestamps=True, max_bytes=1073741824, max_seconds=7200.0, ttl_seconds=3600.0,
    )
    assert discarded is True


def test_keys_an_answer_carries_beyond_its_contract_are_passed_over():
    """An engine may say more than its contract names -- a whisper segment's
    own `id`, `seek` and `tokens` -- and what the contract names is read alone."""
    answer = {"text": "hi", "language": "en", "model_seconds": 0.4, "segments": [{
        "id": 0, "seek": 0, "start": 0.0, "end": 1.0, "text": "hi", "tokens": [50364, 2421],
        "temperature": 0.0, "avg_logprob": -0.2, "words": [{"start": 0.0, "end": 1.0, "word": " hi",
                                                            "probability": 0.9, "tokens": [2421]}],
    }]}
    with _answering(200, answer) as client:
        transcript = MediaEngine(EngineContract(), BASE, client=client).transcribe("m1")

    assert transcript == Transcript("hi", "en", (Segment(0.0, 1.0, "hi", (Word(0.0, 1.0, " hi", 0.9),)),))


# ─── what goes wrong, and how it is said ──────────────────────────────────────


def _dead_url() -> str:
    """A port nothing listens on, found by binding one and letting it go: a
    hard-coded number might be somebody else's server."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{sock.getsockname()[1]}"


@pytest.mark.parametrize("call", ["upload", "transcribe", "analyze", "capabilities"])
def test_an_engine_nothing_answers_for_is_unavailable_and_named(call):
    """Refused at once on most hosts; on Windows a refused connection is
    retried for seconds, so a short timeout ends it there. Either way nothing
    answered, and the error says which engine did not."""
    url = _dead_url()

    with MediaEngine(EngineContract(), url, timeout=0.25) as media:
        act = {
            "upload": lambda: media.upload(_wav(0.1), "audio/wav"),
            "transcribe": lambda: media.transcribe("m1"),
            "analyze": lambda: media.analyze("m1"),
            "capabilities": media.capabilities,
        }[call]
        with pytest.raises(EngineUnavailable) as unavailable:
            act()

    assert unavailable.value.engine == url
    assert str(unavailable.value).startswith(f"the engine at {url} did not answer ")


def test_an_engine_too_slow_to_answer_is_unavailable_and_says_how_long_it_was_given():
    with _failing(httpx.ReadTimeout("timed out")) as client:
        media = MediaEngine(EngineContract(), BASE, timeout=7.5, client=client)
        with pytest.raises(EngineUnavailable) as unavailable:
            media.transcribe("m1")

    assert str(unavailable.value) == (
        f"the engine at {BASE} did not answer POST /api/media/m1/transcribe: no answer within 7.5 seconds"
    )


def test_a_refusal_carries_the_engine_s_status_and_reason(engine):
    """Seen to fail by raising the refusal without its reason: `detail` read
    the status's own phrase, and the engine's explanation was lost."""
    media, _ = engine

    with pytest.raises(EngineRefused) as refused:
        media.upload(b"plain words", "text/plain")

    assert refused.value.status == 415
    assert refused.value.detail.startswith("Unsupported media type 'text/plain'. Allowed: audio/wav")
    assert str(refused.value).startswith(f"the engine at {BASE} refused POST /api/media with 415: ")


@pytest.mark.parametrize(("source", "status", "detail"), [
    (b"RIFF but nothing after it", 422, "No decodable audio in the upload"),
    (_wav(0.0), 422, "No audio in the upload"),
    (_wav(2.0), 413, "Upload too large"),
], ids=["undecodable", "silent", "too-large"])
def test_an_upload_the_engine_cannot_take_is_refused_with_its_reason(source, status, detail):
    state = EngineState(media_limit=len(_wav(1.0)))
    with httpx.Client(transport=media_transport(state)) as client, pytest.raises(EngineRefused) as refused:
        MediaEngine(state.contract, BASE, client=client).upload(source, "audio/wav")

    assert refused.value.status == status
    assert refused.value.detail.startswith(detail)
    assert state.uploads == {}


def test_an_upload_the_engine_does_not_hold_is_refused(engine):
    """An unknown id and an expired one are the same answer."""
    media, _ = engine

    for act in (lambda: media.transcribe("gone"), lambda: media.analyze("gone")):
        with pytest.raises(EngineRefused) as refused:
            act()
        assert (refused.value.status, refused.value.detail) == (404, "No such media: gone")


@pytest.mark.parametrize(("status", "answer", "detail"), [
    (422, {"detail": [{"loc": ["query", "words"], "msg": "Input should be a valid boolean"}]},
     '[{"loc": ["query", "words"], "msg": "Input should be a valid boolean"}]'),
    (502, "<html><body>Bad Gateway</body></html>", "<html><body>Bad Gateway</body></html>"),
    (500, '{"error": "boom"}', '{"error": "boom"}'),
    (503, "", "Service Unavailable"),
    (500, "x" * 2000, "x" * 500),
], ids=["a-reason-that-is-not-text", "a-page", "json-with-no-reason", "nothing", "a-long-page"])
def test_a_refusal_s_reason_is_whatever_the_engine_gave(status, answer, detail):
    """Its reason under the contract's key, rendered as text when it is not;
    else the body, bounded; else the status's own phrase."""
    if isinstance(answer, str):
        client = _answering(status, text=answer)
    else:
        client = _answering(status, answer)
    with client, pytest.raises(EngineRefused) as refused:
        MediaEngine(EngineContract(), BASE, client=client).transcribe("m1")

    assert (refused.value.status, refused.value.detail) == (status, detail)


def test_a_redirect_is_not_followed_but_refused():
    """A redirect means the contract's route is not where the engine
    answers; following it would hide that, and lose the upload's body."""
    with _answering(307, text="") as client, pytest.raises(EngineRefused) as refused:
        MediaEngine(EngineContract(), BASE, client=client).capabilities()
    assert refused.value.status == 307


_HANDLE = {"media_id": "m1", "duration": 1.0, "sample_rate": 16000, "channels": 1, "has_audio": True}
_SEGMENT = {"start": 0.0, "end": 1.0, "text": " hi", "words": [{"start": 0.0, "end": 1.0, "word": " hi"}]}


def _upload(media):
    return media.upload(b"RIFF", "audio/wav")


def _transcribe(media):
    return media.transcribe("m1")


def _analyze(media):
    return media.analyze("m1", ["loudness", "silences", "peaks"])


def _capabilities(media):
    return media.capabilities()


@pytest.mark.parametrize(("call", "answer", "reason"), [
    (_upload, "an id", 'the answer is "an id", not an object'),
    (_upload, {**_HANDLE, "media_id": ""}, "media_id is empty"),
    (_upload, {k: v for k, v in _HANDLE.items() if k != "media_id"}, "the answer has no 'media_id'"),
    (_upload, {**_HANDLE, "duration": "1.0"}, 'duration is "1.0", not a number'),
    (_upload, {**_HANDLE, "duration": True}, "duration is true, not a number"),
    (_upload, {**_HANDLE, "sample_rate": 16000.5}, "sample_rate is 16000.5, not a whole number"),
    (_upload, {**_HANDLE, "has_audio": "yes"}, 'has_audio is "yes", not true or false'),
    (_transcribe, {"text": "hi", "segments": {}}, "segments is {}, not a list"),
    (_transcribe, {"segments": []}, "the answer has no 'text'"),
    (_transcribe, {"text": "hi", "segments": [{"start": 0.0, "text": " hi"}]}, "segments[0] has no 'end'"),
    (_transcribe, {"text": "hi", "segments": [{**_SEGMENT, "words": [{"start": 0.0, "end": None, "word": "x"}]}]},
     "segments[0].words[0].end is null, not a number"),
    (_transcribe, {"text": "hi", "segments": [{**_SEGMENT, "words": [{"start": 0.0, "end": 1.0, "word": 7}]}]},
     "segments[0].words[0].word is 7, not text"),
    (_transcribe, {"text": "hi", "segments": [], "info": ["model"]}, 'info is ["model"], not an object'),
    (_analyze, {"duration": 1.0, "loudness": None, "silences": None}, "the answer has no 'peaks', which was asked for"),
    (_analyze, {"loudness": {"rms_db": -20.0}, "silences": None, "peaks": None}, "loudness has no 'peak_db'"),
    (_analyze, {"loudness": None, "silences": [{"start": 1.0}], "peaks": None}, "silences[0] has no 'end'"),
    (_analyze, {"loudness": None, "silences": None, "peaks": {"rate": 2, "values": [0.1, "loud"]}},
     'peaks.values[1] is "loud", not a number'),
    (_capabilities, {"features": []}, "the answer has no 'formats'"),
    (_capabilities, {"formats": ["wav"], "features": "tempo"}, 'features is "tempo", not a list'),
])
def test_an_answer_that_is_not_the_contract_s_shape_is_malformed_and_says_where(call, answer, reason):
    with _answering(200, answer) as client, pytest.raises(EngineAnswerMalformed) as malformed:
        call(MediaEngine(EngineContract(), BASE, client=client))

    assert malformed.value.reason == reason
    assert str(malformed.value).startswith(f"the engine at {BASE} answered ")


def test_an_answer_that_is_not_json_is_malformed():
    with _answering(200, text="<html>engine</html>") as client, pytest.raises(EngineAnswerMalformed, match="not JSON"):
        MediaEngine(EngineContract(), BASE, client=client).capabilities()


def test_an_absent_or_null_optional_part_is_read_as_none():
    """What an engine may leave out is None, and what it must say is still checked."""
    answers = iter([
        {"media_id": "m1", "duration": 1.0},
        {"text": "", "segments": [{"start": 0.0, "end": 1.0, "text": " hi", "words": None}], "language": None},
        {"loudness": {"rms_db": None, "peak_db": None}, "silences": [], "peaks": None},
    ])

    def answer(request):
        return httpx.Response(200, json=next(answers))

    with httpx.Client(transport=httpx.MockTransport(answer)) as client:
        media = MediaEngine(EngineContract(), BASE, client=client)
        upload = media.upload(b"RIFF", "audio/wav")
        transcript = media.transcribe(upload)
        analysis = media.analyze(upload, ["loudness", "silences", "peaks"])

    assert upload == MediaHandle("m1", 1.0, None, None, None)
    assert transcript == Transcript("", None, (Segment(0.0, 1.0, " hi"),), {})
    assert analysis == Analysis(loudness=Loudness(None, None, None), silences=())


# ─── what a contract leaves out ───────────────────────────────────────────────


@pytest.mark.parametrize(("route", "call", "capability"), [
    ("media_upload", lambda m: m.upload(b"RIFF", "audio/wav"), "uploads"),
    ("media_transcribe", lambda m: m.transcribe("m1"), "transcripts"),
    ("media_analyze", lambda m: m.analyze("m1"), "analysis"),
    ("media_capabilities", lambda m: m.capabilities(), "a statement of its capabilities"),
])
def test_a_route_the_contract_leaves_out_is_refused_by_name_before_any_request(route, call, capability):
    """Seen to fail by asking anyway: the engine was sent a request to a
    route its contract says it does not have."""
    seen: list = []
    with _answering(200, {}, seen=seen) as client, pytest.raises(EngineDoesNotOffer) as missing:
        call(MediaEngine(replace(EngineContract(), **{route: None}), BASE, client=client))

    assert seen == []
    assert str(missing.value) == f"the engine at {BASE} does not offer {capability}: its contract names no {route}"


def test_a_language_is_refused_on_a_contract_that_cannot_ask_for_one():
    """A transcript in a language nobody asked for is a different answer, so
    the request is refused rather than sent without it.

    Seen to fail by dropping the language: the request went out without it."""
    seen: list = []
    with _answering(200, {}, seen=seen) as client:
        media = MediaEngine(replace(EngineContract(), language_param=None), BASE, client=client)
        with pytest.raises(EngineDoesNotOffer, match="does not offer a choice of language"):
            media.transcribe("m1", language="fr")
    assert seen == []


def test_words_travel_only_where_the_contract_names_the_parameter():
    """Seen to fail by sending `words` under a fixed name: the engine was
    sent a parameter its contract never named."""
    seen: list = []
    with _answering(200, {"text": "", "segments": []}, seen=seen) as client:
        MediaEngine(replace(EngineContract(), words_param=None), BASE, client=client).transcribe("m1", words=True)

    assert seen[0].url.raw_path == b"/api/media/m1/transcribe"


def test_a_feature_the_contract_does_not_name_is_refused_and_none_named_means_none_offered():
    seen: list = []
    unmeasured = {f"{name}_feature": None for name in FEATURES}
    with _answering(200, {}, seen=seen) as client:
        media = MediaEngine(replace(EngineContract(), peaks_feature=None), BASE, client=client)
        with pytest.raises(EngineDoesNotOffer, match="does not offer peaks"):
            media.analyze("m1", ["tempo", "peaks"])
        media = MediaEngine(replace(EngineContract(), **unmeasured), BASE, client=client)
        with pytest.raises(EngineDoesNotOffer, match="does not offer any feature to analyse"):
            media.analyze("m1")
    assert seen == []


def test_an_engine_that_cannot_be_asked_for_features_is_read_for_what_it_answered():
    """With no `features_param` nothing is sent, and a feature the engine
    left out is None rather than a malformed answer: it was never asked."""
    seen: list = []
    with _answering(200, {"duration": 3.0, "tempo": 88.0}, seen=seen) as client:
        analysis = MediaEngine(replace(EngineContract(), features_param=None), BASE, client=client).analyze("m1")

    assert seen[0].url.raw_path == b"/api/media/m1/analyze"
    assert analysis == Analysis(duration=3.0, tempo=88.0)


def test_a_route_with_no_place_for_the_id_is_refused_when_the_contract_is_built():
    with pytest.raises(ValueError, match="must name {media_id} once"):
        EngineContract(media_transcribe="/api/media/transcribe")
    with pytest.raises(ValueError, match="must name {media_id} once"):
        EngineContract(media_delete="/a/{media_id}/b/{media_id}")


# ─── the errors themselves ────────────────────────────────────────────────────


@pytest.mark.parametrize("error", [
    EngineUnavailable("no answer within 5 seconds", BASE, "POST /api/media"),
    EngineRefused(415, "Unsupported media type", BASE, "POST /api/media"),
    EngineAnswerMalformed("the answer has no 'text'", BASE, "POST /api/media/m1/transcribe"),
    EngineDoesNotOffer("analysis", BASE, "media_analyze"),
])
def test_every_error_is_rebuilt_whole_from_its_arguments(error):
    """A task queue that serialises a failed task's error rebuilds it from
    its arguments: it comes back the same type, with the same message."""
    for rebuilt in (pickle.loads(pickle.dumps(error)), type(error)(*error.args)):
        assert type(rebuilt) is type(error)
        assert str(rebuilt) == str(error)
        assert rebuilt.engine == BASE


# ─── the client it holds ──────────────────────────────────────────────────────


def test_one_client_serves_every_call():
    media = MediaEngine(EngineContract(), BASE)
    assert media.client is media.client


def test_it_does_not_build_a_client_until_one_is_needed():
    assert MediaEngine(EngineContract(), BASE)._client is None


def test_it_closes_the_client_it_built():
    media = MediaEngine(EngineContract(), BASE)
    with media:
        client = media.client
    assert client.is_closed and media._client is None


def test_it_needs_the_url_it_answers_at():
    with pytest.raises(ValueError, match="needs the URL"):
        MediaEngine(EngineContract(), "")


@pytest.mark.parametrize(("module", "allowed"), [("vox.media", {"httpx"}), ("vox.contract", set())])
def test_it_needs_httpx_and_nothing_else_outside_the_standard_library(module, allowed):
    """A consumer vendoring vox for its media surface installs httpx for it
    and nothing more: no validation library, no engine's own client.

    Checked by reading the imports rather than by installing nothing,
    because the test process already has vox's own dependencies."""
    import ast
    import importlib
    import pathlib
    import sys

    source = pathlib.Path(importlib.import_module(module).__file__).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])

    outside = imported - set(sys.stdlib_module_names) - {"vox", "__future__"} - allowed
    assert not outside, f"{module} imports {outside}"
