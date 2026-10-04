"""`HttpSTT`'s side of the wire, driven through an injected client.

These used to patch `vox.stt.httpx.post`, which worked because every call
built its own client at module level. `HttpSTT` now holds one — see its
docstring for why — so the tests hand it a client instead of patching the
module. That is the better seam anyway: it exercises the `client=`
parameter a caller would use, rather than reaching inside.
"""

from unittest.mock import MagicMock

import httpx

from vox.stt import HttpSTT


def _fake_response(payload: dict):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def _stt_with(client, **kwargs) -> HttpSTT:
    return HttpSTT(kwargs.pop("base_url", "http://127.0.0.1:8000"), client=client, **kwargs)


def test_transcribe_file_posts_filename_and_returns_text():
    client = MagicMock()
    client.post.return_value = _fake_response({"text": "hello", "segments": [], "language": "en"})
    stt = _stt_with(client)

    assert stt.transcribe_file("clip.wav") == "hello"
    client.post.assert_called_once_with(
        "http://127.0.0.1:8000/api/voice/transcribe",
        params={"filename": "clip.wav"},
        timeout=60.0,
    )


def test_listen_posts_duration_and_returns_text_and_path():
    client = MagicMock()
    client.post.return_value = _fake_response(
        {"text": "go", "segments": [], "language": "en", "audio_path": "Data/Voice/capture_x.wav"}
    )
    stt = _stt_with(client, base_url="http://127.0.0.1:8000/", timeout=10.0)

    assert stt.listen(duration=3.0) == ("go", "Data/Voice/capture_x.wav")
    client.post.assert_called_once_with(
        "http://127.0.0.1:8000/api/voice/listen",
        params={"duration": 3.0},
        timeout=13.0,
    )


def test_listen_sends_the_pause_when_the_contract_names_it_and_a_value_is_given():
    """`pause_ms` travels under the contract's own spelling of the parameter.

    Seen to fail by dropping the `pause_ms` branch from `HttpSTT.listen`:
    the request then carried `duration` alone and the assertion on `params`
    went red. `walkthrough/mutate.py` keeps that mutation.
    """
    from vox.adapters import JOE

    client = MagicMock()
    client.post.return_value = _fake_response({"text": "go", "audio_path": "cap.wav"})
    stt = _stt_with(client, contract=JOE, timeout=10.0)

    assert stt.listen(duration=3.0, pause_ms=1500) == ("go", "cap.wav")
    client.post.assert_called_once_with(
        "http://127.0.0.1:8000/api/voice/listen",
        params={"duration": 3.0, "silence_ms": 1500},
        timeout=13.0,
    )


def test_listen_sends_no_pause_on_a_contract_without_one():
    """An engine with no such parameter is never sent one, whatever the caller asked.

    Seen to fail by sending `{"pause_ms": pause_ms}` whenever a value was
    given, regardless of the contract: a key the contract never named
    appeared in `params`. `walkthrough/mutate.py` keeps that mutation too.
    """
    client = MagicMock()
    client.post.return_value = _fake_response({"text": "go", "audio_path": "cap.wav"})
    stt = _stt_with(client, timeout=10.0)

    stt.listen(duration=3.0, pause_ms=1500)
    client.post.assert_called_once_with(
        "http://127.0.0.1:8000/api/voice/listen",
        params={"duration": 3.0},
        timeout=13.0,
    )


def test_listen_sends_no_pause_when_none_was_given():
    """The engine's own default stands when the caller states no preference.

    Seen to fail by sending the parameter unconditionally on a contract that
    names it, with `None` as its value: `silence_ms` appeared in `params`.
    """
    from vox.adapters import JOE

    client = MagicMock()
    client.post.return_value = _fake_response({"text": "go", "audio_path": "cap.wav"})
    stt = _stt_with(client, contract=JOE, timeout=10.0)

    stt.listen(duration=3.0)
    client.post.assert_called_once_with(
        "http://127.0.0.1:8000/api/voice/listen",
        params={"duration": 3.0},
        timeout=13.0,
    )


def test_devices_returns_the_engines_report():
    payload = {
        "devices": [{"index": 1, "name": "Mic", "channels": 2, "default": True}],
        "microphone_available": True,
    }
    client = MagicMock()
    client.get.return_value = _fake_response(payload)
    stt = _stt_with(client)

    assert stt.devices() == payload
    client.get.assert_called_once_with("http://127.0.0.1:8000/api/voice/devices", timeout=60.0)


def test_devices_reports_unavailable_when_joe_is_unreachable():
    client = MagicMock()
    client.get.side_effect = httpx.ConnectError("refused")

    assert _stt_with(client).devices() == {"devices": [], "microphone_available": False}


def test_reachable_true_on_200():
    client = MagicMock()
    client.get.return_value = MagicMock(status_code=200)

    assert _stt_with(client).reachable() is True


def test_reachable_false_when_joe_is_unreachable():
    client = MagicMock()
    client.get.side_effect = httpx.ConnectError("refused")

    assert _stt_with(client).reachable() is False


def test_announce_posts_the_state_to_the_conversation_route_briefly():
    from vox.adapters import JOE

    client = MagicMock()
    client.post.return_value = MagicMock(status_code=200)
    stt = _stt_with(client, contract=JOE, timeout=60.0)

    assert stt.announce("speaking", "I heard: banana.", reason="nomatch") is True
    client.post.assert_called_once_with(
        "http://127.0.0.1:8000/api/voice/conversation",
        json={"state": "speaking", "text": "I heard: banana.", "reason": "nomatch"},
        timeout=2.0,
    )


def test_announce_carries_a_question_s_options_in_the_order_given():
    """Seen to fail by dropping `options` from the body: the display has
    nothing to offer."""
    from vox.adapters import JOE

    client = MagicMock()
    client.post.return_value = MagicMock(status_code=200)
    stt = _stt_with(client, contract=JOE)

    stt.announce("speaking", "Say approve or hold.", options=("approve", "hold"))
    stt.announce("speaking", "What should be done?")

    first, second = (c.kwargs["json"] for c in client.post.call_args_list)
    assert first == {"state": "speaking", "text": "Say approve or hold.",
                     "options": ["approve", "hold"]}
    assert "options" not in second


def test_announce_does_nothing_on_a_contract_without_the_route():
    client = MagicMock()
    stt = _stt_with(client)

    assert stt.announce("speaking", "x") is False
    client.post.assert_not_called()


def test_announce_never_raises_when_the_engine_is_away():
    """A display that cannot be told is no reason for the question to go unasked."""
    from vox.adapters import JOE

    client = MagicMock()
    client.post.side_effect = httpx.ConnectError("refused")
    stt = _stt_with(client, contract=JOE)

    assert stt.announce("recorded", "approve") is False


def test_announce_reports_a_refusal_as_not_taken():
    from vox.adapters import JOE

    client = MagicMock()
    client.post.return_value = MagicMock(status_code=400)
    stt = _stt_with(client, contract=JOE)

    assert stt.announce("hearing") is False


def test_one_client_serves_every_call():
    """The change that made the loop short, asserted rather than assumed.

    A new `httpx.Client` per call costs ~620 ms on this workstation, most of
    it building an SSL context it never uses over http. If this regresses,
    nothing fails — the loop just gets slow again — so the test is here to
    make the regression loud.
    """
    stt = HttpSTT("http://127.0.0.1:8000")
    assert stt.client is stt.client


def test_it_does_not_build_a_client_until_one_is_needed():
    """Constructing an HttpSTT stays free; `vox doctor` builds several."""
    stt = HttpSTT("http://127.0.0.1:8000")
    assert stt._client is None


def test_it_closes_the_client_it_built():
    stt = HttpSTT("http://127.0.0.1:8000")
    with stt:
        client = stt.client
    assert client.is_closed
    assert stt._client is None


def test_it_leaves_a_client_it_was_given_alone():
    """A caller sharing one client across several clients keeps the right to close it."""
    client = httpx.Client()
    with HttpSTT("http://127.0.0.1:8000", client=client):
        pass
    assert not client.is_closed
    client.close()
