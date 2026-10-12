"""Shared test setup."""

import pytest

from vox.adapters import pyttsx3 as pyttsx3_adapter


@pytest.fixture(autouse=True)
def _no_real_sapi(monkeypatch, request):
    """No test reaches the machine's own voice. On Windows the adapter drives
    SAPI directly, and a test standing in for pyttsx3 would otherwise speak
    through the speakers; a test about the SAPI path installs its own stand-in."""
    if "sapi" not in request.node.name:
        monkeypatch.setattr(pyttsx3_adapter, "_sapi", lambda: False)
