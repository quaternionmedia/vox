"""Speech-to-text: the contract vox needs, backed by a running joe engine.

vox does not do audio analysis itself — that is joe's job (see joe's
`Modules/Voice.py` and its `/api/voice/*` endpoints). This module is a thin
HTTP client against that engine, so vox stays a seam rather than a second
copy of joe's DSP.
"""

from typing import Protocol

import httpx


class SpeechToText(Protocol):
    """What a speech-to-text backend must provide."""

    def transcribe_file(self, filename: str) -> str:
        """Transcribe an audio file the backend can already see. Returns the text."""
        ...

    def listen(self, duration: float = 5.0) -> tuple[str, str]:
        """Capture `duration` seconds live and transcribe it. Returns (text, audio_path)."""
        ...


class JoeSTT:
    """Speech-to-text delegated to a running joe engine's `/api/voice/*` endpoints."""

    def __init__(self, base_url: str = "http://127.0.0.1:8000", timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def transcribe_file(self, filename: str) -> str:
        """`filename` must already be visible to joe, under its Data/Audio/ or Data/Voice/."""
        resp = httpx.post(
            f"{self.base_url}/api/voice/transcribe",
            params={"filename": filename},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["text"]

    def listen(self, duration: float = 5.0) -> tuple[str, str]:
        resp = httpx.post(
            f"{self.base_url}/api/voice/listen",
            params={"duration": duration},
            timeout=self.timeout + duration,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["text"], data["audio_path"]
