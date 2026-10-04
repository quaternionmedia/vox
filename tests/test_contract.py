"""Is the seam actually independent of any one engine, or does it just say so?

vox used to have a single speech-to-text class named for the engine it
talked to, with that engine's routes spelled inline. Replacing the name with
`HttpSTT` and moving the paths into a dataclass changes nothing on its own —
a default that is the only value ever used is a hardcoding with a longer
spelling.

So these run the whole thing against `ALIEN`, a contract that shares **no
path, no parameter name and no response key** with the default. If any part
of vox still reaches for the original spelling, they fail. That is the only
form of this claim worth making.
"""

from dataclasses import replace

import httpx
import pytest

from vox.adapters import JOE
from vox.contract import EngineContract
from vox.engine import EngineState, encode_wav, serve
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
)


def test_the_alien_contract_shares_nothing_with_the_default():
    """The premise. If these overlap, the tests below prove less than they look."""
    default = EngineContract()
    for field in (
        "health",
        "devices",
        "transcribe",
        "listen",
        "filename_param",
        "duration_param",
        "text_key",
        "audio_path_key",
        "devices_key",
        "available_key",
        "conversation",
        "pause_param",
        "hint_param",
    ):
        assert getattr(ALIEN, field) != getattr(default, field), field


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
    conversation route and the pause and hint parameters, which the default
    leaves unset because not every engine has them.

    Stated as a test rather than a comment so that the day one of them moves,
    something says so instead of the two quietly drifting.
    """
    assert replace(JOE, conversation=None, pause_param=None, hint_param=None) == EngineContract()
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
