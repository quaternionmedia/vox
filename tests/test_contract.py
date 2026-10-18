"""Is the seam independent of any one engine, or does it only say so?

These run the whole thing against `ALIEN`, a contract that shares no path,
no parameter name and no response key with the default. A part of vox that
reaches for the default spelling directly fails them.
"""

import io
import wave
from dataclasses import fields, replace

import httpx
import pytest

from vox.adapters import JOE
from vox.contract import MEDIA_ID, EngineContract
from vox.engine import EngineState, encode_wav, media_transport, serve
from vox.media import (
    FEATURES,
    Analysis,
    EngineDoesNotOffer,
    EngineRefused,
    Loudness,
    MediaEngine,
    MediaHandle,
    Peaks,
    Segment,
    Silence,
    Transcript,
    Word,
)
from vox.session import VoiceSession
from vox.stt import HttpSTT
from vox.tts import RecordingTTS

ALIEN = EngineContract(
    health="/up",
    devices="/v2/inputs",
    transcribe="/v2/stt",
    listen="/v2/stt/live",
    filename_param="clip",
    duration_param="secs",
    text_key="utterance",
    audio_path_key="captured_to",
    devices_key="inputs",
    available_key="has_input",
    conversation="/dialogue/state",
    pause_param="hush",
    hint_param="expect",
    confidence_key="sureness",
    watch="/v2/stt/early",
    unwatch="/v2/stt/early/close",
    control="/v2/turn",
    interrupted_key="cut_in",
    media_upload="/v9/clips",
    media_capabilities="/v9/clips/skills",
    media_transcribe="/v9/clips/{media_id}/words",
    media_analyze="/v9/clips/{media_id}/feel",
    media_delete="/v9/clips/{media_id}/forget",
    language_param="tongue",
    words_param="each_word",
    features_param="measure",
    media_id_key="clip_id",
    duration_key="length_s",
    sample_rate_key="hz",
    channels_key="lanes",
    has_audio_key="sounds",
    language_key="spoken_in",
    segments_key="pieces",
    start_key="from_s",
    end_key="to_s",
    words_key="tokens",
    word_key="token",
    probability_key="likelihood",
    info_key="notes",
    tempo_feature="bpm",
    beats_feature="pulse",
    onsets_feature="attacks",
    loudness_feature="level",
    silences_feature="quiet",
    peaks_feature="outline",
    tempo_key="bpm_value",
    beats_key="pulse_times",
    onsets_key="attack_times",
    loudness_key="levels",
    silences_key="quiet_spans",
    peaks_key="envelope",
    rms_db_key="rms",
    peak_db_key="peak",
    lufs_key="integrated",
    peaks_rate_key="per_second",
    peaks_values_key="heights",
    formats_key="takes",
    features_key="measures",
    speech_model_key="model_name",
    word_timestamps_key="times_words",
    max_bytes_key="most_bytes",
    max_seconds_key="most_secs",
    ttl_seconds_key="kept_for",
    detail_key="why",
)


def test_the_alien_contract_shares_nothing_with_the_default():
    """The premise. If these overlap, the tests below prove less than they look.

    Every field the contract has, read off the class, so a field added to it
    is held to this without being listed here.
    """
    default = EngineContract()
    for field in fields(EngineContract):
        assert getattr(ALIEN, field.name) != getattr(default, field.name), field.name


@pytest.fixture(params=[EngineContract(), ALIEN], ids=["default", "alien"])
def engine(request, tmp_path):
    """One running engine per contract, so every test below runs twice."""
    state = EngineState(
        audio_dirs=[tmp_path],
        microphone="deterministic engine",
        heard="approve the deploy",
        capture_dir=tmp_path,
        contract=request.param,
    )
    with serve(state) as (base_url, state):
        yield base_url, state, tmp_path, request.param


def test_the_loop_closes_on_either_contract(engine):
    base_url, state, audio_dir, contract = engine
    encode_wav("approve the deploy", audio_dir / "said.wav")

    with HttpSTT(base_url, contract=contract) as stt:
        session = VoiceSession(stt=stt, tts=RecordingTTS(), echo_dir=str(audio_dir))
        result = session.round_trip("said.wav")

    assert result.closed is True
    assert result.heard == "approve the deploy"


def test_the_wire_used_this_contract_s_paths(engine):
    """Not just that it worked — that it worked *here*."""
    base_url, state, audio_dir, contract = engine
    encode_wav("approve the deploy", audio_dir / "said.wav")

    with HttpSTT(base_url, contract=contract) as stt:
        stt.reachable()
        stt.transcribe_file("said.wav")

    assert state.requests[0] == contract.health
    assert state.requests[1].startswith(contract.transcribe)
    assert f"{contract.filename_param}=said.wav" in state.requests[1]


def test_listen_reads_this_contract_s_keys(engine):
    base_url, _, _, contract = engine

    with HttpSTT(base_url, contract=contract) as stt:
        text, path = stt.listen(duration=2)

    assert text == "approve the deploy"
    assert path.endswith("capture.wav")


def test_a_pause_travels_in_this_contract_s_spelling_or_not_at_all(engine):
    """On the alien contract the parameter is `hush`; the default names none,
    and the engine then sees no pause however the caller asked.

    Seen to fail by having the engine read `silence_ms` rather than
    `contract.pause_param`: the alien engine recorded None where 1200 was
    sent.
    """
    base_url, state, _, contract = engine

    with HttpSTT(base_url, contract=contract) as stt:
        stt.listen(duration=2, pause_ms=1200)

    if contract.pause_param:
        assert state.pauses == [1200]
        assert f"{contract.pause_param}=1200" in state.requests[0]
    else:
        assert state.pauses == [None]
        assert "1200" not in state.requests[0]


def test_a_hint_travels_in_this_contract_s_spelling_or_not_at_all(engine):
    """Seen to fail by having the engine read `hint` rather than
    `contract.hint_param`: the alien engine recorded None where the words
    were sent."""
    base_url, state, _, contract = engine

    with HttpSTT(base_url, contract=contract) as stt:
        stt.listen(duration=2, hint=["approve", "hold"])
        stt.listen(duration=2)

    if contract.hint_param:
        assert state.hints == ["approve, hold", None]
    else:
        assert state.hints == [None, None]
        assert "approve" not in state.requests[0]


def test_a_confidence_is_read_in_this_contract_s_spelling_or_not_at_all(engine):
    """Seen to fail by reading `confidence` rather than the contract's key: the
    alien contract read None where 0.9 was reported."""
    base_url, state, _, contract = engine
    state.confidence = 0.9

    with HttpSTT(base_url, contract=contract) as stt:
        stt.listen(duration=2)
        heard = stt.last_confidence
        state.confidence = None
        stt.listen(duration=2)

    assert heard == (0.9 if contract.confidence_key else None)
    assert stt.last_confidence is None  # a take with none reported is not the last one's


def test_devices_reads_this_contract_s_keys(engine):
    base_url, _, _, contract = engine

    with HttpSTT(base_url, contract=contract) as stt:
        assert stt.microphone_available() is True
        assert stt.devices()[contract.devices_key][0]["name"] == "deterministic engine"


def test_an_unreachable_engine_answers_in_this_contract_s_keys(tmp_path):
    """The fallback body is built from the contract too, not from fixed strings."""
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        dead_port = sock.getsockname()[1]

    stt = HttpSTT(f"http://127.0.0.1:{dead_port}", contract=ALIEN, timeout=0.25)
    assert stt.devices() == {"inputs": [], "has_input": False}
    assert stt.microphone_available() is False


def test_the_default_contract_is_not_secretly_the_alien_one(engine):
    """A default engine refuses the alien paths, and the reverse."""
    base_url, _, _, contract = engine
    other = ALIEN if contract == EngineContract() else EngineContract()

    assert httpx.get(f"{base_url}{other.health}").status_code == 404


def test_the_named_adapter_matches_the_default_today(engine):
    """`JOE` is written out in full and equals the defaults, apart from the
    optional voice routes and keys, which the default leaves unset because
    not every engine has them. Its media surface is the default's.

    A test rather than a comment, so a change to either says so.
    """
    assert replace(JOE, conversation=None, pause_param=None, hint_param=None,
                   confidence_key=None, watch=None, unwatch=None, control=None) == EngineContract()
    assert JOE.conversation == "/api/voice/conversation"
    assert JOE.pause_param == "silence_ms"
    assert JOE.hint_param == "hint"


def test_an_announcement_travels_the_contract_s_own_route(engine):
    """On the alien contract the route is `/dialogue/state`; on the default
    there is none, and announcing does nothing rather than guessing one."""
    base_url, state, _, contract = engine

    with HttpSTT(base_url, contract=contract) as stt:
        took = stt.announce("speaking", "Voice check. Say approve or hold.")

    if contract.conversation:
        assert took is True
        assert state.announced == [{"state": "speaking", "text": "Voice check. Say approve or hold."}]
        assert any(r.startswith(contract.conversation) for r in state.requests)
    else:
        assert took is False
        assert state.announced == [] and state.requests == []


def test_a_watch_travels_the_contract_s_own_route_or_not_at_all(engine):
    """With the listen's parameters in the contract's spelling; on a contract
    without the route, nothing is sent."""
    base_url, state, _, contract = engine

    with HttpSTT(base_url, contract=contract) as stt:
        opened = stt.watch(3, pause_ms=600, hint=["approve", "hold"])
        closed = stt.unwatch()

    if contract.watch:
        assert opened is True and closed is True
        assert state.watches == [{contract.duration_param: "3", contract.pause_param: "600",
                                  contract.hint_param: "approve, hold"}, None]
    else:
        assert opened is False and closed is False
        assert state.watches == [] and state.requests == []


def test_an_interruption_is_read_in_this_contract_s_spelling_or_not_at_all(engine):
    """Seen to fail by reading `interrupted` rather than the contract's key:
    the alien contract was never interrupted."""
    base_url, state, _, contract = engine

    with HttpSTT(base_url, contract=contract) as stt:
        state.interrupted = True
        cut = stt.interrupted()
        state.interrupted = False
        uncut = stt.interrupted()

    assert cut is bool(contract.control) and uncut is False


# --- the media surface, on either contract -------------------------------------------

SAID = Transcript(
    text="Approve the deploy.",
    language="en",
    segments=(
        Segment(0.0, 1.2, "Approve the deploy.", (
            Word(0.0, 0.5, " Approve", 0.98), Word(0.5, 0.7, " the", 0.99), Word(0.7, 1.2, " deploy.", 0.95),
        )),
    ),
    info={"model": "scripted"},
)

MEASURED = Analysis(
    tempo=96.0,
    beats=(0.25, 0.875),
    onsets=(0.0, 0.5),
    loudness=Loudness(rms_db=-18.5, peak_db=-2.0, lufs=-16.0),
    silences=(Silence(1.2, 1.5),),
    peaks=Peaks(rate=4.0, values=(0.1, 0.8, 0.6, 0.0, 0.0, 0.0)),
)


def _wav(seconds: float, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(seconds * rate))
    return buffer.getvalue()


@pytest.fixture(params=[EngineContract(), ALIEN], ids=["default", "alien"])
def media(request):
    """One media surface per contract, answered in-process, so every test below runs twice."""
    state = EngineState(contract=request.param, transcript=SAID, analysis=MEASURED)
    with httpx.Client(transport=media_transport(state)) as client, \
            MediaEngine(request.param, "http://vox.engine", client=client) as engine:
        yield engine, state, request.param


def test_the_media_surface_answers_on_either_contract(media):
    """Every answer read back whole: a key read under a fixed spelling comes
    back missing on the alien contract, and the answer is refused as malformed.

    Seen to fail by reading a segment's start under "start" rather than
    `contract.start_key`: the alien transcript was refused, its segment
    having no 'start'."""
    engine, state, _ = media

    upload = engine.upload(_wav(1.5), "audio/wav")

    assert upload == MediaHandle("m1", 1.5, 16000, 1, True)
    assert engine.transcribe(upload) == SAID
    assert engine.analyze(upload) == replace(MEASURED, duration=1.5)
    capabilities = engine.capabilities()
    assert (capabilities.features, capabilities.max_bytes) == (FEATURES, state.media_limit)
    assert engine.close(upload) is True
    assert state.uploads == {}


def test_the_media_wire_used_this_contract_s_spelling(media):
    """Not just that it worked -- that it worked *here*: each path and each
    parameter on the wire is this contract's, a feature under the engine's
    own name for it."""
    engine, state, contract = media

    upload = engine.upload(_wav(0.5), "audio/wav")
    unworded = engine.transcribe(upload, language="fr", words=False)
    engine.analyze(upload, ["silences", "peaks"])
    engine.capabilities()
    engine.close(upload)

    def at(route):
        return route.replace(MEDIA_ID, "m1")

    assert state.requests == [
        contract.media_upload,
        f"{at(contract.media_transcribe)}?{contract.language_param}=fr&{contract.words_param}=false",
        (f"{at(contract.media_analyze)}?{contract.features_param}="
         f"{contract.silences_feature}%2C{contract.peaks_feature}"),
        contract.media_capabilities,
        at(contract.media_delete),
    ]
    assert unworded.language == "fr"
    assert [s.words for s in unworded.segments] == [()]


def test_a_refusal_s_reason_is_read_in_this_contract_s_spelling(media):
    """Seen to fail by reading the reason under "detail": the alien refusal
    fell back to its whole body as text."""
    engine, _, _ = media

    with pytest.raises(EngineRefused) as refused:
        engine.upload(b"plain words", "text/plain")

    assert refused.value.status == 415
    assert refused.value.detail.startswith("Unsupported media type 'text/plain'")


def test_a_feature_the_contract_does_not_name_is_refused_on_either_contract(media):
    """Refused by vox's name for it, before any request: on the alien
    contract too, where the engine's own name for beats is `pulse`."""
    engine, state, contract = media
    engine.contract = replace(contract, beats_feature=None)

    with pytest.raises(EngineDoesNotOffer, match="does not offer beats: its contract names no beats_feature"):
        engine.analyze("m1", ["tempo", "beats"])

    assert state.requests == []
