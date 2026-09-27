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
    """Speech-to-text delegated to a running joe engine's `/api/voice/*` endpoints.

    One `httpx.Client` is held for the life of the instance and reused by
    every call. That is not tidiness: `httpx.get`/`httpx.post` at module
    level construct a fresh `Client` per call, and constructing one costs
    **~620 ms on this workstation** — ~240 ms of it
    `ssl.create_default_context(cafile=certifi.where())`, paid whether or
    not the URL is `https`. A closed loop makes three of these calls, so the
    module-level form spent about two seconds per iteration doing nothing.
    Measured 2026-09-27 against `vox.engine`; a reused client answers the
    same request in ~10 ms.

    Close it with `close()`, or use the instance as a context manager. An
    `httpx.Client` passed in is the caller's to close.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> httpx.Client:
        """The shared client, built on first use so construction stays free."""
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def close(self) -> None:
        """Release the client, if this instance built it."""
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> "JoeSTT":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def transcribe_file(self, filename: str) -> str:
        """`filename` must already be visible to joe, under its Data/Audio/ or Data/Voice/."""
        resp = self.client.post(
            f"{self.base_url}/api/voice/transcribe",
            params={"filename": filename},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["text"]

    def listen(self, duration: float = 5.0) -> tuple[str, str]:
        resp = self.client.post(
            f"{self.base_url}/api/voice/listen",
            params={"duration": duration},
            timeout=self.timeout + duration,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["text"], data["audio_path"]

    def devices(self) -> dict:
        """Audio input devices the joe engine's own machine can see.

        Recording happens on joe's side, not vox's, so "is there a
        microphone" is a question about that machine — this is how a caller
        checks before attempting `listen()`, rather than finding out from an
        opaque failure partway through.

        Returns `{"devices": [...], "microphone_available": bool}`. If joe
        can't be reached at all, `microphone_available` is False and
        `devices` is empty rather than raising — unreachable and
        no-microphone both mean "listen() will not work right now".
        """
        try:
            resp = self.client.get(f"{self.base_url}/api/voice/devices", timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError:
            return {"devices": [], "microphone_available": False}

    def reachable(self) -> bool:
        """Whether the joe engine answers at all, distinct from whether it has a mic."""
        try:
            resp = self.client.get(f"{self.base_url}/api/health", timeout=self.timeout)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
