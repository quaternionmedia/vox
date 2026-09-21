from unittest.mock import MagicMock, patch

from vox.stt import JoeSTT


def _fake_response(payload: dict):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def test_transcribe_file_posts_filename_and_returns_text():
    stt = JoeSTT(base_url="http://127.0.0.1:8000")
    fake_resp = _fake_response({"text": "hello", "segments": [], "language": "en"})

    with patch("vox.stt.httpx.post", return_value=fake_resp) as mock_post:
        text = stt.transcribe_file("clip.wav")

    assert text == "hello"
    mock_post.assert_called_once_with(
        "http://127.0.0.1:8000/api/voice/transcribe",
        params={"filename": "clip.wav"},
        timeout=60.0,
    )


def test_listen_posts_duration_and_returns_text_and_path():
    stt = JoeSTT(base_url="http://127.0.0.1:8000/", timeout=10.0)
    fake_resp = _fake_response(
        {"text": "go", "segments": [], "language": "en", "audio_path": "Data/Voice/capture_x.wav"}
    )

    with patch("vox.stt.httpx.post", return_value=fake_resp) as mock_post:
        text, path = stt.listen(duration=3.0)

    assert (text, path) == ("go", "Data/Voice/capture_x.wav")
    mock_post.assert_called_once_with(
        "http://127.0.0.1:8000/api/voice/listen",
        params={"duration": 3.0},
        timeout=13.0,
    )
