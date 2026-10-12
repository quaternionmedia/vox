"""Tests for the vox CLI: adapter selection, `doctor`, and the mic preflight."""

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

import vox.cli as cli
from vox.contract import EngineContract

runner = CliRunner()
CONTRACT = EngineContract()


def _fake_stt(**attrs) -> MagicMock:
    """A stand-in client that also works as a context manager.

    `doctor` and `loop` close the client they build, so they use the
    instance with `with`. A bare MagicMock's `__enter__` returns a *new*
    mock, so the configured one would be silently unused and every
    assertion below would be about a different object.
    """
    stt = MagicMock()
    stt.__enter__.return_value = stt
    stt.__exit__.return_value = False
    for name, value in attrs.items():
        getattr(stt, name).return_value = value
    return stt


def _devices(*names: str) -> dict:
    """A devices body in the default contract's own keys."""
    found = [{"index": i, "name": n, "channels": 1, "default": i == 0} for i, n in enumerate(names)]
    return {CONTRACT.devices_key: found, CONTRACT.available_key: bool(found)}


def _install_fake_pyttsx3(monkeypatch, should_raise: bool = False):
    fake = ModuleType("pyttsx3")
    if should_raise:
        fake.init = MagicMock(side_effect=RuntimeError("no speech engine"))
    else:
        fake.init = MagicMock(return_value=MagicMock())
    monkeypatch.setitem(sys.modules, "pyttsx3", fake)


# ─── adapter selection ────────────────────────────────────────────────────────

def test_an_unknown_engine_is_refused_by_name():
    """And the message lists what there is, rather than leaving a guess."""
    result = runner.invoke(cli.app, ["doctor", "--engine", "nosuchthing"])

    assert result.exit_code == 2
    assert "No engine adapter named 'nosuchthing'" in result.output
    assert "joe" in result.output


def test_a_known_engine_resolves_to_its_contract_and_url():
    from vox.adapters import JOE

    contract, url = cli._resolve("joe", None)

    assert contract == JOE
    assert url == "http://127.0.0.1:8000"


def test_an_explicit_url_overrides_the_adapter_s_default():
    _, url = cli._resolve("joe", "http://elsewhere:9999")

    assert url == "http://elsewhere:9999"


# ─── doctor ───────────────────────────────────────────────────────────────────

def test_doctor_reports_ready_when_everything_works(monkeypatch):
    _install_fake_pyttsx3(monkeypatch)
    fake_stt = _fake_stt(reachable=True, devices=_devices("USB Mic"))

    with patch("vox.cli.HttpSTT", return_value=fake_stt):
        result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "[ok]   engine: joe reachable" in result.output
    assert "[ok]   microphone: USB Mic" in result.output
    assert "Ready." in result.output


def test_doctor_fails_when_the_engine_is_unreachable(monkeypatch):
    _install_fake_pyttsx3(monkeypatch)
    fake_stt = _fake_stt(reachable=False)

    with patch("vox.cli.HttpSTT", return_value=fake_stt):
        result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 1
    assert "[fail] engine: nothing answering" in result.output
    fake_stt.devices.assert_not_called()


def test_doctor_fails_when_no_microphone(monkeypatch):
    _install_fake_pyttsx3(monkeypatch)
    fake_stt = _fake_stt(reachable=True, devices=_devices())

    with patch("vox.cli.HttpSTT", return_value=fake_stt):
        result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 1
    assert "[fail] microphone: none found" in result.output


def test_doctor_fails_when_tts_does_not_initialize(monkeypatch):
    _install_fake_pyttsx3(monkeypatch, should_raise=True)
    fake_stt = _fake_stt(reachable=True, devices=_devices("Mic"))

    with patch("vox.cli.HttpSTT", return_value=fake_stt):
        result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 1
    assert "[fail] text-to-speech" in result.output


def test_doctor_names_the_engine_it_was_asked_for(monkeypatch):
    """The output says which adapter, so a reader knows what was checked."""
    _install_fake_pyttsx3(monkeypatch)
    fake_stt = _fake_stt(reachable=True, devices=_devices("Mic"))

    with patch("vox.cli.HttpSTT", return_value=fake_stt):
        result = runner.invoke(cli.app, ["doctor", "--url", "http://elsewhere:9999"])

    assert "http://elsewhere:9999" in result.output


# ─── self-report ──────────────────────────────────────────────────────────────

def test_self_report_live_refuses_without_a_microphone():
    fake_stt = _fake_stt(microphone_available=False)

    with patch("vox.cli.HttpSTT", return_value=fake_stt):
        result = runner.invoke(cli.app, ["self-report"])

    assert result.exit_code == 1
    assert "No microphone available" in result.output
    fake_stt.listen.assert_not_called()


def test_self_report_live_passes_pause_ms_to_the_backend(monkeypatch, tmp_path):
    """`--pause-ms` reaches `listen` as `pause_ms`; the contract spells it from there.

    Seen to fail by leaving `pause_ms` out of the `self_report_live` call in
    `cli.self_report`: `listen` was called with `duration` alone.
    """
    monkeypatch.chdir(tmp_path)  # `recording` writes under the working directory
    fake_stt = _fake_stt(microphone_available=True, listen=("ship it", "cap.wav"))

    with patch("vox.cli.HttpSTT", return_value=fake_stt):
        result = runner.invoke(
            cli.app, ["self-report", "--pause-ms", "1500", "--voice", "recording"]
        )

    assert result.exit_code == 0, result.output
    fake_stt.listen.assert_called_once_with(duration=5.0, pause_ms=1500)
    assert "Heard:      ship it" in result.output


def test_self_report_live_leaves_the_pause_to_the_engine_by_default(monkeypatch, tmp_path):
    """No `--pause-ms` means no preference, and the engine's own default stands."""
    monkeypatch.chdir(tmp_path)
    fake_stt = _fake_stt(microphone_available=True, listen=("ship it", "cap.wav"))

    with patch("vox.cli.HttpSTT", return_value=fake_stt):
        result = runner.invoke(cli.app, ["self-report", "--voice", "recording"])

    assert result.exit_code == 0, result.output
    fake_stt.listen.assert_called_once_with(duration=5.0, pause_ms=None)


# ─── loop ─────────────────────────────────────────────────────────────────────

def test_loop_offline_closes_without_an_engine_or_hardware(tmp_path):
    """The whole point of `--offline`, exercised through the CLI."""
    result = runner.invoke(
        cli.app, ["loop", "--offline", "--echo-dir", str(tmp_path), "--say", "ship it"]
    )

    assert result.exit_code == 0, result.output
    assert "heard:      'ship it'" in result.output
    assert "echoed:     'ship it'" in result.output
    assert "closed:     True" in result.output


def test_loop_offline_binds_an_ephemeral_port(tmp_path):
    """Never 8000: two sessions share this workstation."""
    result = runner.invoke(cli.app, ["loop", "--offline", "--echo-dir", str(tmp_path)])

    assert ":8000" not in result.output
    assert "ephemeral port" in result.output


# ─── voice selection ──────────────────────────────────────────────────────────

def test_an_unknown_voice_is_refused_by_name():
    fake_stt = _fake_stt(reachable=True, devices=_devices("Mic"))

    with patch("vox.cli.HttpSTT", return_value=fake_stt):
        result = runner.invoke(cli.app, ["doctor", "--voice", "nosuchthing"])

    assert result.exit_code == 2
    assert "No voice adapter named 'nosuchthing'" in result.output


def test_doctor_checks_the_voice_it_was_asked_for():
    """`recording` needs no audio stack, so doctor runs anywhere.

    The check synthesizes rather than importing, because importing is not
    what fails at demo time.
    """
    fake_stt = _fake_stt(reachable=True, devices=_devices("Mic"))

    with patch("vox.cli.HttpSTT", return_value=fake_stt):
        result = runner.invoke(cli.app, ["doctor", "--voice", "recording"])

    assert result.exit_code == 0, result.output
    assert "[ok]   text-to-speech: recording synthesizes" in result.output
