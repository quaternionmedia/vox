"""The adapters, and the line between them and the seam.

Everything under `vox.adapters` names a product. Everything above it
states a contract. These check that the line holds in the direction that
matters: the seam must not reach down into an adapter, or a consumer that
wanted a different engine would be installing this one anyway.
"""

import sys
from unittest.mock import MagicMock, patch

import vox
from vox.adapters.pyttsx3 import Pyttsx3TTS


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


# ─── the line between the seam and the adapters ───────────────────────────────

def test_importing_vox_pulls_in_no_adapter():
    """The seam names no product, and a fresh interpreter proves it.

    Checked in a subprocess rather than against `sys.modules` here, because
    this test module imports an adapter itself and would find its own import.
    """
    import subprocess
    import textwrap

    probe = textwrap.dedent(
        """
        import sys
        import vox
        leaked = sorted(m for m in sys.modules if m.startswith("vox.adapters"))
        print(",".join(leaked))
        """
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == "", f"importing vox pulled in {out}"


def test_the_seam_s_public_surface_names_no_product():
    """`vox.__all__` is the contract. A product name in it is a coupling."""
    products = ("joe", "pyttsx3", "whisper", "sapi", "espeak")
    for name in vox.__all__:
        assert not any(p in name.lower() for p in products), name


def test_an_adapter_is_reachable_when_asked_for():
    """The line is a default, not a wall."""
    from vox.adapters import JOE

    assert JOE.transcribe.startswith("/")
