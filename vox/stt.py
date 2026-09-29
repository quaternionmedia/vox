"""Speech-to-text: the contract vox needs, and one client that speaks it.

vox does no audio analysis. Transcription belongs to whatever engine is
running, and this module is the thin HTTP client that drives it — so vox
stays a seam rather than a second copy of somebody's DSP.

Which engine is not this module's business. `HttpSTT` takes an
`EngineContract` describing the paths and keys to use, so pointing vox at a
different engine is a value passed in, not a class written.
"""

from typing import Protocol

import httpx

from vox.contract import EngineContract

# Seconds an announcement may take. It feeds a display, and a question that
# waits on one has its priorities the wrong way round.
ANNOUNCE_TIMEOUT = 2.0


class SpeechToText(Protocol):
    """What a speech-to-text backend must provide."""

    def transcribe_file(self, filename: str) -> str:
        """Transcribe an audio file the backend can already see. Returns the text."""
        ...

    def listen(self, duration: float = 5.0) -> tuple[str, str]:
        """Capture `duration` seconds live and transcribe it. Returns (text, audio_path)."""
        ...


class HttpSTT:
    """Speech-to-text over HTTP, against any engine matching an `EngineContract`.

    One `httpx.Client` is held for the life of the instance and reused by
    every call. That is not tidiness: `httpx.get`/`httpx.post` at module
    level construct a fresh `Client` per call, and constructing one costs
    **~620 ms on the workstation this was measured on** — ~240 ms of it
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
        base_url: str,
        contract: EngineContract | None = None,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.contract = contract or EngineContract()
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

    def __enter__(self) -> "HttpSTT":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _url(self, path: str) -> str:
        return self.contract.url(self.base_url, path)

    def transcribe_file(self, filename: str) -> str:
        """`filename` must already be somewhere the engine can resolve it."""
        resp = self.client.post(
            self._url(self.contract.transcribe),
            params={self.contract.filename_param: filename},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()[self.contract.text_key]

    def listen(self, duration: float = 5.0) -> tuple[str, str]:
        resp = self.client.post(
            self._url(self.contract.listen),
            params={self.contract.duration_param: duration},
            timeout=self.timeout + duration,
        )
        resp.raise_for_status()
        data = resp.json()
        return data[self.contract.text_key], data[self.contract.audio_path_key]

    def devices(self) -> dict:
        """Audio input devices the engine's own machine can see.

        Recording happens on the engine's side, not vox's, so "is there a
        microphone" is a question about that machine — this is how a caller
        checks before attempting `listen()`, rather than finding out from an
        opaque failure partway through.

        Returns the engine's own body. If it cannot be reached at all, the
        availability key is False and the device list empty rather than
        raising: unreachable and no-microphone both mean "listen() will not
        work right now".
        """
        try:
            resp = self.client.get(self._url(self.contract.devices), timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError:
            return {self.contract.devices_key: [], self.contract.available_key: False}

    def microphone_available(self) -> bool:
        """Read the availability flag without the caller knowing its key."""
        return bool(self.devices().get(self.contract.available_key))

    def announce(self, state: str, text: str = "", reason: str | None = None) -> bool:
        """Tell the engine what the dialog is doing, for anything watching.

        Returns whether the engine took it. Never raises, and waits briefly: a
        display that cannot be told is no reason for a question to go unasked.
        """
        if not self.contract.conversation:
            return False
        body = {"state": state, "text": text}
        if reason:
            body["reason"] = reason
        try:
            resp = self.client.post(
                self._url(self.contract.conversation), json=body,
                timeout=min(self.timeout, ANNOUNCE_TIMEOUT),
            )
            return resp.status_code < 400
        except httpx.HTTPError:
            return False

    def reachable(self) -> bool:
        """Whether the engine answers at all, distinct from whether it has a mic."""
        try:
            resp = self.client.get(self._url(self.contract.health), timeout=self.timeout)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
