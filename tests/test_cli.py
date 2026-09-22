"""Tests for the vox CLI: `doctor` and self-report's mic preflight."""

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

import vox.cli as cli

runner = CliRunner()


def _install_fake_pyttsx3(monkeypatch, should_raise: bool = False):
    fake = ModuleType("pyttsx3")
    if should_raise:
        fake.init = MagicMock(side_effect=RuntimeError("no speech engine"))
    else:
        fake.init = MagicMock(return_value=MagicMock())
    monkeypatch.setitem(sys.modules, "pyttsx3", fake)


def test_doctor_reports_ready_when_everything_works(monkeypatch):
    _install_fake_pyttsx3(monkeypatch)
    fake_stt = MagicMock()
    fake_stt.reachable.return_value = True
    fake_stt.devices.return_value = {
        "devices": [{"index": 1, "name": "USB Mic", "channels": 2, "default": True}],
        "microphone_available": True,
    }

    with patch("vox.cli.JoeSTT", return_value=fake_stt):
        result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "[ok]   joe engine" in result.output
    assert "[ok]   microphone: USB Mic" in result.output
    assert "Ready." in result.output


def test_doctor_fails_when_joe_unreachable(monkeypatch):
    _install_fake_pyttsx3(monkeypatch)
    fake_stt = MagicMock()
    fake_stt.reachable.return_value = False

    with patch("vox.cli.JoeSTT", return_value=fake_stt):
        result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 1
    assert "[fail] joe engine: unreachable" in result.output
    fake_stt.devices.assert_not_called()


def test_doctor_fails_when_no_microphone(monkeypatch):
    _install_fake_pyttsx3(monkeypatch)
    fake_stt = MagicMock()
    fake_stt.reachable.return_value = True
    fake_stt.devices.return_value = {"devices": [], "microphone_available": False}

    with patch("vox.cli.JoeSTT", return_value=fake_stt):
        result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 1
    assert "[fail] microphone: none found" in result.output


def test_doctor_fails_when_tts_does_not_initialize(monkeypatch):
    _install_fake_pyttsx3(monkeypatch, should_raise=True)
    fake_stt = MagicMock()
    fake_stt.reachable.return_value = True
    fake_stt.devices.return_value = {
        "devices": [{"index": 1, "name": "Mic", "channels": 2, "default": True}],
        "microphone_available": True,
    }

    with patch("vox.cli.JoeSTT", return_value=fake_stt):
        result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 1
    assert "[fail] text-to-speech" in result.output


def test_self_report_live_refuses_without_a_microphone(monkeypatch):
    fake_stt = MagicMock()
    fake_stt.devices.return_value = {"devices": [], "microphone_available": False}

    with patch("vox.cli.JoeSTT", return_value=fake_stt):
        result = runner.invoke(cli.app, ["self-report"])

    assert result.exit_code == 1
    assert "No microphone available" in result.output
    fake_stt.listen.assert_not_called()
