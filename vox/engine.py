"""A deterministic stand-in for a joe engine, so the loop closes without hardware.

`JoeSTT` talks HTTP to a joe engine, and joe transcribes with whisper off a
real microphone. Neither is available in a test run, and neither is
deterministic: whisper is a neural model behind a model download, and a
microphone is hardware somebody has to plug in. So every vox test before this
module replaced `JoeSTT` with a `MagicMock`, which proves the call was made
and nothing about the wire.

This module replaces the *engine*, not the client. `serve()` starts a real
HTTP server on a real socket speaking joe's `/api/voice/*` contract, so
`JoeSTT` runs unmodified against it — same `httpx` calls, same JSON, same
status codes. What it does not do is guess: every shape here is copied from
joe's `api.py` and `Modules/Voice.py` at `c2d9a01`, and
`tests/test_engine_contract.py` pins them.

**This is a codec, not a voice.** `encode_wav` writes the text into a real
WAV file's samples and `decode_wav` reads it back, so a round trip through
this engine proves the seam — the HTTP contract, the file handoff, the
orchestration, the loop closing — and proves nothing whatever about whisper's
accuracy. That is the trade being made deliberately: the parts that can be
deterministic are, and the part that cannot is checked by hand against a
real engine (`vox loop --joe-url ...`) and reported as a separate claim.
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

SAMPLE_RATE = 16000
"""Matches joe's `Voice.record`/`transcribe`, which resample everything to 16 kHz."""

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
            raise NotVoxAudioError(f"{path}: expected 16-bit mono, got {wav.getnchannels()}ch/{wav.getsampwidth() * 8}-bit")
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
    """Where `transcribe` looks for a filename, in order. Mirrors joe's Data/Audio, Data/Voice."""

    microphone: str | None = None
    """The device name `listen` reports, or None to answer 503 as joe does with no mic."""

    heard: str = ""
    """What `listen` returns as the transcript. The test's script for the human."""

    capture_dir: Path | None = None
    """Where `listen` writes the WAV it claims to have recorded."""

    requests: list[str] = field(default_factory=list)
    """Every path+query served, in order, so a test can assert the wire was used."""


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
        self.state.requests.append(self.path)

        if route.path == "/api/health":
            # joe answers {"status": "ok"}. `engine` is vox's own addition: a
            # caller that binds an ephemeral port still needs to know it is
            # talking to the stand-in and not to a real joe somebody left
            # running — async-contract.md §4, "assert the identity".
            self._send(200, {"status": "ok", "engine": "vox.engine.DeterministicEngine"})
        elif route.path == "/api/voice/devices":
            devices = self._devices()
            self._send(200, {"devices": devices, "microphone_available": bool(devices)})
        else:
            self._send(404, {"detail": "Not Found"})

    def do_POST(self):  # noqa: N802
        route = urlparse(self.path)
        query = parse_qs(route.query)
        self.state.requests.append(self.path)

        if route.path == "/api/voice/transcribe":
            self._transcribe(query.get("filename", [""])[0])
        elif route.path == "/api/voice/listen":
            self._listen(float(query.get("duration", ["5.0"])[0]))
        else:
            self._send(404, {"detail": "Not Found"})

    def _transcribe(self, filename: str) -> None:
        for base in self.state.audio_dirs:
            candidate = (base / filename).resolve()
            if candidate.is_relative_to(base.resolve()) and candidate.is_file():
                text = decode_wav(candidate)
                self._send(200, {"text": text, "segments": [], "language": "en"})
                return
        # joe's own wording, so a caller that matches on it keeps matching.
        dirs = " or ".join(str(d) for d in self.state.audio_dirs)
        self._send(404, {"detail": f"No such file under {dirs}: {filename}"})

    def _listen(self, duration: float) -> None:
        if not 0 < duration <= 60:
            self._send(400, {"detail": "duration must be between 0 and 60 seconds"})
        elif self.state.microphone is None:
            self._send(503, {"detail": "No microphone found. `joe voice devices` lists what this machine's audio backend can see."})
        else:
            # joe records then transcribes, so the WAV exists on disk before the
            # transcript does. Writing it keeps that order true here: a caller
            # that goes looking for `audio_path` finds a real, decodable file.
            capture_dir = self.state.capture_dir or self.state.audio_dirs[0]
            path = encode_wav(self.state.heard, capture_dir / "capture.wav")
            self._send(200, {"text": self.state.heard, "segments": [], "language": "en", "audio_path": path})


@contextmanager
def serve(state: EngineState | None = None):
    """Run the engine on an ephemeral port. Yields `(base_url, state)`.

    The port is 0, so the OS picks it and the caller reads back what it got —
    two sessions run on one workstation here, and a hard-coded 8000 has
    already cost an afternoon of measuring the wrong program
    (`handbook/async-contract.md` §4).
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
