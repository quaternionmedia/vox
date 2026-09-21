import sys
from unittest.mock import MagicMock, patch

from vox.tts import Pyttsx3TTS


def test_speak_writes_to_generated_path_and_drives_engine(tmp_path):
    fake_pyttsx3 = MagicMock()
    fake_engine = MagicMock()
    fake_pyttsx3.init.return_value = fake_engine

    tts = Pyttsx3TTS(out_dir=str(tmp_path))
    with patch.dict(sys.modules, {"pyttsx3": fake_pyttsx3}):
        out_path = tts.speak("hello world")

    assert out_path.startswith(str(tmp_path))
    assert out_path.endswith(".wav")
    fake_engine.save_to_file.assert_called_once_with("hello world", out_path)
    fake_engine.runAndWait.assert_called_once()


def test_speak_respects_explicit_out_path(tmp_path):
    fake_pyttsx3 = MagicMock()
    fake_engine = MagicMock()
    fake_pyttsx3.init.return_value = fake_engine

    explicit_path = str(tmp_path / "reply.wav")
    tts = Pyttsx3TTS(out_dir=str(tmp_path))
    with patch.dict(sys.modules, {"pyttsx3": fake_pyttsx3}):
        out_path = tts.speak("hi", out_path=explicit_path)

    assert out_path == explicit_path
    fake_engine.save_to_file.assert_called_once_with("hi", explicit_path)
