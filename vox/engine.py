"""A deterministic engine, so the loop closes without hardware.

A real speech engine transcribes with a neural model off a real microphone.
Neither is available in a test run and neither is deterministic: the model is
behind a download, and the microphone is hardware somebody has to plug in. So
every vox test before this module replaced the client with a `MagicMock`,
which proves the call was made and nothing about the wire.

This module replaces the *engine*, not the client. `serve()` starts a real
HTTP server on a real socket answering an `EngineContract`, so `HttpSTT` runs
unmodified against it — same calls, same JSON, same status codes. It serves
whichever contract it is given, which is what lets `tests/test_contract.py`
prove the seam is not wired to one engine's spelling.

**This is a codec, not a voice.** `encode_wav` writes the text into a real
WAV file's samples and `decode_wav` reads it back, so a round trip proves the
seam — the HTTP contract, the file handoff, the orchestration, the loop
closing — and proves nothing whatever about transcription accuracy. That is
the trade made deliberately: the parts that can be deterministic are, and the
part that cannot is checked against a real engine and reported as a separate
claim.
"""

from __future__ import annotations

import json
import os
import threading
import wave
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from vox.contract import EngineContract

SAMPLE_RATE = 16000
"""16 kHz, which is what speech models resample to anyway."""

MAGIC = b"VOX1"
"""Marks a WAV as written by `encode_wav`.

Without it `decode_wav` would happily read a recording of a human voice as
whatever its samples happened to spell, and the engine would answer with
confident nonsense instead of an error. A stand-in that cannot tell it was
handed real audio is a stand-in nobody can trust.
"""


class NotVoxAudioError(ValueError):
    """Raised when `decode_wav` is handed a WAV `encode_wav` did not write."""


def encode_wav(text: str, out_path: str | os.PathLike[str]) -> str:
    """Write `text` into a real 16-bit mono WAV at `out_path`. Returns the path.

    One sample per UTF-8 byte, centred on zero so the file is a valid,
    playable waveform rather than a blob with a header. The same text always
    produces the same bytes: there is no timestamp, no dithering and no
    engine state.
    """
    payload = MAGIC + len(text.encode("utf-8")).to_bytes(4, "big") + text.encode("utf-8")
    frames = b"".join(((byte - 128) * 256).to_bytes(2, "little", signed=True) for byte in payload)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(frames)
    return str(out_path)


def decode_wav(path: str | os.PathLike[str]) -> str:
    """Read back the text `encode_wav` wrote. Raises `NotVoxAudioError` otherwise."""
    with wave.open(str(path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise NotVoxAudioError(
                f"{path}: expected 16-bit mono, got {wav.getnchannels()}ch/{wav.getsampwidth() * 8}-bit"
            )
        frames = wav.readframes(wav.getnframes())

    payload = bytes(
        int.from_bytes(frames[i : i + 2], "little", signed=True) // 256 + 128
        for i in range(0, len(frames), 2)
    )
    if not payload.startswith(MAGIC):
        raise NotVoxAudioError(f"{path}: not written by vox's codec (no {MAGIC.decode()} marker)")

    length = int.from_bytes(payload[4:8], "big")
    body = payload[8 : 8 + length]
    if len(body) != length:
        raise NotVoxAudioError(f"{path}: truncated — header claims {length} bytes, found {len(body)}")
    return body.decode("utf-8")


@dataclass
class EngineState:
    """What the running engine will answer with. Set it before `serve()`, not during."""

    audio_dirs: list[Path] = field(default_factory=list)
    """Where `transcribe` looks for a filename, in order."""

    microphone: str | None = None
    """The device name `listen` reports, or None to answer 503 as an engine with no mic does."""

    heard: str = ""
    """What `listen` returns as the transcript. The test's script for the human."""

    capture_dir: Path | None = None
    """Where `listen` writes the WAV it claims to have recorded."""

    contract: EngineContract = field(default_factory=EngineContract)
    """The surface to answer on. Serving a non-default one is how the seam's
    independence from any single engine is demonstrated rather than asserted."""

    requests: list[str] = field(default_factory=list)
    """Every path+query served, in order, so a test can assert the wire was used."""

    announced: list[dict] = field(default_factory=list)
    """Every body posted to the contract's `conversation` route, in order."""

    pauses: list[int | None] = field(default_factory=list)
    """What each `listen` not refused for its value carried under the
    contract's `pause_param`, in order, None when it carried nothing or the
    contract names no such parameter. A request refused for its value records
    nothing; one refused later for a missing microphone is recorded. The engine
    has no speaker to wait for, so the value changes nothing it does;
    recording it is how a test sees it arrived."""

    confidence: float | None = None
    """What `listen` reports under the contract's `confidence_key`, when both
    are set: the test's script for how sure the engine was."""

    hints: list[str | None] = field(default_factory=list)
    """What each `listen` recorded in `pauses` carried under the contract's
    `hint_param`, in the same order, None when it carried nothing. The
    engine returns `heard` whatever it is hinted; recording the hint is how a
    test sees it arrived."""

    interrupted: bool = False
    """What the contract's `control` route reports under `interrupted_key`:
    the test's script for a person who answered over the question."""

    watches: list[dict | None] = field(default_factory=list)
    """Every watch opened, as the parameters it carried, and None for each
    one closed, in order. The engine has no microphone to open early, so a
    watch changes nothing it does; recording it is how a test sees it arrived."""


class _Handler(BaseHTTPRequestHandler):
    state: EngineState

    def log_message(self, *args):  # noqa: D102 — silence the stderr access log
        pass

    def _send(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _devices(self) -> list[dict]:
        if self.state.microphone is None:
            return []
        return [{"index": 0, "name": self.state.microphone, "channels": 1, "default": True}]

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's spelling
        route = urlparse(self.path)
        contract = self.state.contract
        self.state.requests.append(self.path)

        if route.path == contract.health:
            # `engine` is vox's own addition on top of whatever an engine's
            # health body carries: a caller that binds an ephemeral port still
            # needs to know it is talking to the stand-in and not to a real
            # engine somebody left running.
            self._send(200, {"status": "ok", "engine": "vox.engine"})
        elif route.path == contract.devices:
            devices = self._devices()
            self._send(200, {contract.devices_key: devices, contract.available_key: bool(devices)})
        elif contract.control and route.path == contract.control:
            self._send(200, {contract.interrupted_key: self.state.interrupted})
        else:
            self._send(404, {"detail": "Not Found"})

    def do_POST(self):  # noqa: N802
        route = urlparse(self.path)
        query = parse_qs(route.query)
        contract = self.state.contract
        self.state.requests.append(self.path)

        if route.path == contract.transcribe:
            self._transcribe(query.get(contract.filename_param, [""])[0])
        elif route.path == contract.listen:
            duration = query.get(contract.duration_param, ["5.0"])[0]
            pause = query.get(contract.pause_param, [None])[0] if contract.pause_param else None
            hint = query.get(contract.hint_param, [None])[0] if contract.hint_param else None
            try:
                duration = float(duration)
                pause = None if pause is None else int(pause)
            except ValueError:
                # The stood-in engine's framework refuses a value that does not
                # parse before the route runs, with a 422. A handler that let
                # the exception escape would drop the connection instead, a
                # shape no engine produces and no client is written for.
                names = " and ".join(p for p in (contract.duration_param, contract.pause_param) if p)
                self._send(422, {"detail": f"{names} must be numeric"})
                return
            self._listen(duration, pause, hint)
        elif contract.conversation and route.path == contract.conversation:
            length = int(self.headers.get("Content-Length") or 0)
            self.state.announced.append(json.loads(self.rfile.read(length) or b"{}"))
            self._send(200, {})
        elif contract.watch and route.path == contract.watch:
            self.state.watches.append({name: values[0] for name, values in query.items()})
            self._send(200, {"watching": True})
        elif contract.unwatch and route.path == contract.unwatch:
            self.state.watches.append(None)
            self._send(200, {"watching": False})
        else:
            self._send(404, {"detail": "Not Found"})

    def _transcribe(self, filename: str) -> None:
        contract = self.state.contract
        for base in self.state.audio_dirs:
            candidate = (base / filename).resolve()
            if candidate.is_relative_to(base.resolve()) and candidate.is_file():
                text = decode_wav(candidate)
                self._send(200, {contract.text_key: text, "segments": [], "language": "en"})
                return
        dirs = " or ".join(str(d) for d in self.state.audio_dirs)
        self._send(404, {"detail": f"No such file under {dirs}: {filename}"})

    def _listen(self, duration: float, pause: int | None, hint: str | None = None) -> None:
        contract = self.state.contract
        if not 0 < duration <= 60:
            self._send(400, {"detail": "duration must be between 0 and 60 seconds"})
            return
        if pause is not None and not 100 <= pause <= 5000:
            # The stood-in engine's bound on its pause parameter, held to here
            # for the same reason as the duration bound above: a caller that
            # sends a value the real engine refuses has to fail the offline
            # suite too. `tests/test_engine_contract.py` names the engine and
            # the commit the bounds were read at.
            self._send(400, {"detail": f"{contract.pause_param} must be between 100 and 5000"})
            return
        # Recorded once the request is one the engine would act on, so a test
        # reading `pauses` sees what was accepted and never what was refused.
        self.state.pauses.append(pause)
        self.state.hints.append(hint)
        if self.state.microphone is None:
            self._send(503, {"detail": "No microphone found on the engine's machine."})
        else:
            # A real engine records and then transcribes, so the WAV exists on
            # disk before the transcript does. Writing it keeps that order
            # true here: a caller that goes looking for the recorded path finds
            # a real, decodable file.
            capture_dir = self.state.capture_dir or self.state.audio_dirs[0]
            path = encode_wav(self.state.heard, capture_dir / "capture.wav")
            body = {
                contract.text_key: self.state.heard,
                "segments": [],
                "language": "en",
                contract.audio_path_key: path,
            }
            if contract.confidence_key and self.state.confidence is not None:
                body[contract.confidence_key] = self.state.confidence
            self._send(200, body)


@contextmanager
def serve(state: EngineState | None = None):
    """Run the engine on an ephemeral port. Yields `(base_url, state)`.

    The port is 0, so the OS picks it and the caller reads back what it got —
    two sessions run on one workstation here, and a hard-coded 8000 has
    already cost an afternoon of measuring the wrong program.
    """
    state = state or EngineState()
    handler = type("_BoundHandler", (_Handler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    # `serve_forever`'s default poll interval is 0.5s, and `shutdown()` waits
    # out one of them — which is paid on every teardown and was most of this
    # suite's runtime. A short loop is the whole point of the offline engine.
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
