"""`JoeSTT`'s side of the wire, driven through an injected client.

These used to patch `vox.stt.httpx.post`, which worked because every call
built its own client at module level. `JoeSTT` now holds one — see its
docstring for why — so the tests hand it a client instead of patching the
module. That is the better seam anyway: it exercises the `client=`
parameter a caller would use, rather than reaching inside.
"""

from unittest.mock import MagicMock

import httpx

from vox.stt import JoeSTT


def _fake_response(payload: dict):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def _stt_with(client, **kwargs) -> JoeSTT:
    return JoeSTT(base_url=kwargs.pop("base_url", "http://127.0.0.1:8000"), client=client, **kwargs)


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


def test_one_client_serves_every_call():
    """The change that made the loop short, asserted rather than assumed.

    A new `httpx.Client` per call costs ~620 ms on this workstation, most of
    it building an SSL context it never uses over http. If this regresses,
    nothing fails — the loop just gets slow again — so the test is here to
    make the regression loud.
    """
    stt = JoeSTT(base_url="http://127.0.0.1:8000")
    assert stt.client is stt.client


def test_it_does_not_build_a_client_until_one_is_needed():
    """Constructing a JoeSTT stays free; `vox doctor` builds several."""
    stt = JoeSTT(base_url="http://127.0.0.1:8000")
    assert stt._client is None


def test_it_closes_the_client_it_built():
    stt = JoeSTT(base_url="http://127.0.0.1:8000")
    with stt:
        client = stt.client
    assert client.is_closed
    assert stt._client is None


def test_it_leaves_a_client_it_was_given_alone():
    """A caller sharing one client across several JoeSTTs keeps the right to close it."""
    client = httpx.Client()
    with JoeSTT(base_url="http://127.0.0.1:8000", client=client):
        pass
    assert not client.is_closed
    client.close()
