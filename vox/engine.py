"""A deterministic engine, so the loop closes without hardware.

A real speech engine transcribes with a neural model off a real microphone.
Neither is available in a test run and neither is deterministic: the model is
behind a download, and the microphone is hardware somebody has to plug in.

This module stands in for the *engine*, not the client. `serve()` starts a
real HTTP server on a real socket answering an `EngineContract`, so `HttpSTT`
runs unmodified against it — same calls, same JSON, same status codes. It
serves whichever contract it is given, which lets `tests/test_contract.py`
show the seam is not wired to one engine's spelling.

**A codec, not a voice.** `encode_wav` writes the text into a real WAV
file's samples and `decode_wav` reads it back, so a round trip proves the
seam — the HTTP contract, the file handoff, the orchestration, the loop
closing — and nothing about transcription accuracy, which is checked against
a real engine as a separate claim.

**A script, not a hearing.** On the contract's media surface it takes an
upload, reads a WAV's length from its header, and answers the transcript and
the analysis it was handed in `EngineState`, so `vox.media.MediaEngine` runs
unmodified against it and nothing about what an upload says is measured.
`media_transport` answers that surface in-process, with no socket.
"""

from __future__ import annotations

import functools
import io
import itertools
import json
import os
import re
import threading
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from vox.contract import MEDIA_ID, EngineContract
from vox.media import FEATURES, Analysis, MediaHandle, Transcript

SAMPLE_RATE = 16000
"""16 kHz, which is what speech models resample to anyway."""

MEDIA_FORMATS = (
    "audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave",
    "audio/mpeg", "audio/mp3", "audio/ogg", "audio/flac", "audio/x-flac",
    "audio/mp4", "audio/m4a", "audio/x-m4a", "audio/aac", "audio/x-aac", "audio/webm",
    "video/mp4", "video/quicktime", "video/webm", "video/x-matroska", "video/matroska",
)
"""The content types the media upload takes: those the engine it stands in
for takes, each format under the names browsers and platforms give it.
Whatever the declared type, only a WAV body is decoded, as an engine decodes
by content; anything else is answered 422."""

MEDIA_LIMIT = 1024**3
"""The most bytes the media upload takes, the default cap of the engine it
stands in for. A larger upload is answered 413."""

MAGIC = b"VOX1"
"""Marks a WAV as written by `encode_wav`.

Without it `decode_wav` would read any 16-bit mono recording as whatever its
samples spell; with it, audio `encode_wav` did not write raises
`NotVoxAudioError`.
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
    """What the running engine will answer with. Set it before `serve()`, not
    during; `uploads` and the records below are the engine's own, written as
    it serves."""

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

    transcript: Transcript | None = None
    """What the media surface's transcribe answers, for any upload: the test's
    script for what was said in it. None answers an empty transcript. Asked
    for no words, it answers the segments without theirs; asked for a
    language, it answers that one."""

    analysis: Analysis | None = None
    """What the media surface's analyze answers for each feature asked for,
    for any upload: the test's script for what its sound measures. A feature
    the script leaves None is answered null, and so is every feature when
    the script is None. The duration answered is the upload's own."""

    media_formats: tuple[str, ...] = MEDIA_FORMATS
    """The content types an upload is taken in. Any other is answered 415."""

    media_limit: int = MEDIA_LIMIT
    """The most bytes an upload may be. A larger one is answered 413."""

    uploads: dict[str, MediaHandle] = field(default_factory=dict)
    """What the engine holds, by id: added by an upload, removed by a delete."""

    _ids: Iterator[int] = field(default_factory=lambda: itertools.count(1), init=False,
                                repr=False, compare=False)


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

    def _media(self, method: str) -> bool:
        """Answer the request when it is on the contract's media surface.
        Returns whether it was; the body is read only when it was."""
        route = urlparse(self.path)
        found = _media_route(self.state.contract, method, route.path)
        if found is None:
            return False
        length = int(self.headers.get("Content-Length") or 0)
        status, body = _answer_media(self.state, found, parse_qs(route.query),
                                     self.headers.get("Content-Type") or "", self.rfile.read(length))
        if body is None:
            # An answer with nothing to say, as a delete's 204 is: no body at all.
            self.send_response(status)
            self.end_headers()
        else:
            self._send(status, body)
        return True

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's spelling
        route = urlparse(self.path)
        contract = self.state.contract
        self.state.requests.append(self.path)

        if self._media("GET"):
            return
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

        if self._media("POST"):
            return
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

    def do_DELETE(self):  # noqa: N802
        self.state.requests.append(self.path)
        if not self._media("DELETE"):
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


# ── The media surface ────────────────────────────────────────────────────────

_MEDIA_ROUTES = (
    ("upload", "POST", "media_upload"),
    ("capabilities", "GET", "media_capabilities"),
    ("transcribe", "POST", "media_transcribe"),
    ("analyze", "POST", "media_analyze"),
    ("delete", "DELETE", "media_delete"),
)

_FLAGS = {"true": True, "1": True, "yes": True, "on": True,
          "false": False, "0": False, "no": False, "off": False}
"""What the stood-in engine's framework reads as a boolean parameter."""


@functools.lru_cache(maxsize=64)
def _route_pattern(template: str) -> re.Pattern[str]:
    head, _, tail = template.partition(MEDIA_ID)
    return re.compile(re.escape(head) + "(?P<media_id>[^/]+)" + re.escape(tail))


def _media_route(contract: EngineContract, method: str, path: str) -> tuple[str, str | None] | None:
    """Which media route `method` and `path` ask for, with the upload's id
    when the route names one. None when they are not on the media surface."""
    for name, verb, attribute in _MEDIA_ROUTES:
        template = getattr(contract, attribute)
        if verb != method or not template:
            continue
        if MEDIA_ID not in template:
            if path == template:
                return name, None
            continue
        match = _route_pattern(template).fullmatch(path)
        if match:
            return name, unquote(match["media_id"])
    return None


def _probe_wav(body: bytes) -> tuple[int, int, int] | None:
    """A WAV body's frames, rate and channels; None for anything else."""
    try:
        with wave.open(io.BytesIO(body), "rb") as wav:
            frames, rate, channels = wav.getnframes(), wav.getframerate(), wav.getnchannels()
    except (wave.Error, EOFError):
        return None
    return (frames, rate, channels) if rate > 0 else None


def _answer_media(state: EngineState, route: tuple[str, str | None], query: dict[str, list[str]],
                  content_type: str, body: bytes) -> tuple[int, dict | None]:
    """Answer one request on the media surface, under the contract's keys. A
    body of None is an answer with none, as a delete's is."""
    name, media_id = route
    contract = state.contract
    if name == "upload":
        return _media_upload(state, content_type, body)
    if name == "capabilities":
        return 200, {
            contract.formats_key: list(state.media_formats),
            contract.features_key: [
                getattr(contract, f"{n}_feature") for n in FEATURES if getattr(contract, f"{n}_feature")
            ],
            # It has no speech model; the transcript it answers is the script.
            contract.speech_model_key: None,
            contract.word_timestamps_key: True,
            contract.max_bytes_key: state.media_limit,
            # It takes a sound of any length, and keeps an upload until it is deleted.
            contract.max_seconds_key: None,
            contract.ttl_seconds_key: None,
        }
    upload = state.uploads.get(media_id)
    if upload is None:
        # An unknown id and an expired one are the same answer.
        return 404, {contract.detail_key: f"No such media: {media_id}"}
    if name == "transcribe":
        return _media_transcribe(state, query)
    if name == "analyze":
        return _media_analyze(state, upload, query)
    del state.uploads[media_id]
    return 204, None


def _media_upload(state: EngineState, content_type: str, body: bytes) -> tuple[int, dict]:
    contract = state.contract
    if len(body) > state.media_limit:
        return 413, {contract.detail_key: f"Upload too large (max {state.media_limit} bytes)"}
    kind = content_type.split(";")[0].strip().lower()
    if kind not in state.media_formats:
        allowed = ", ".join(state.media_formats)
        return 415, {contract.detail_key: f"Unsupported media type {kind!r}. Allowed: {allowed}"}
    probed = _probe_wav(body)
    if probed is None:
        return 422, {contract.detail_key: "No decodable audio in the upload (vox.engine decodes WAV only)"}
    frames, rate, channels = probed
    if frames == 0:
        return 422, {contract.detail_key: "No audio in the upload"}
    handle = MediaHandle(f"m{next(state._ids)}", frames / rate, rate, channels, True)
    state.uploads[handle.media_id] = handle
    return 201, {
        contract.media_id_key: handle.media_id,
        contract.duration_key: handle.duration,
        contract.sample_rate_key: handle.sample_rate,
        contract.channels_key: handle.channels,
        contract.has_audio_key: handle.has_audio,
    }


def _media_transcribe(state: EngineState, query: dict[str, list[str]]) -> tuple[int, dict]:
    contract = state.contract
    words = True
    if contract.words_param and contract.words_param in query:
        words = _FLAGS.get(query[contract.words_param][0].lower())
        if words is None:
            return 422, {contract.detail_key: f"{contract.words_param} must be true or false"}
    language = query.get(contract.language_param, [None])[0] if contract.language_param else None
    script = state.transcript or Transcript(text="", language=None, segments=())
    segments = []
    for segment in script.segments:
        segments.append({
            contract.start_key: segment.start,
            contract.end_key: segment.end,
            contract.text_key: segment.text,
            # Asked for no words, a segment still says where its words are:
            # an empty list.
            contract.words_key: [
                {contract.start_key: w.start, contract.end_key: w.end,
                 contract.word_key: w.word, contract.probability_key: w.probability}
                for w in segment.words
            ] if words else [],
        })
    return 200, {
        contract.text_key: script.text,
        contract.language_key: language or script.language,
        contract.segments_key: segments,
        contract.info_key: dict(script.info),
    }


def _tuning_refused(contract: EngineContract, query: dict[str, list[str]]) -> str | None:
    """Why an analysis's tuning is refused, as the stood-in engine's framework
    refuses it before the route runs; None when it is not."""
    bounds = (
        (contract.peaks_rate_param, int, lambda v: 1 <= v <= 1000, "an integer from 1 to 1000"),
        (contract.silence_db_param, float, lambda v: -120.0 <= v <= 0.0, "a level from -120 to 0"),
        (contract.silence_min_param, float, lambda v: 0.0 < v <= 3600.0, "more than 0 and at most 3600"),
    )
    for name, parse, within, what in bounds:
        if not name or name not in query:
            continue
        try:
            value = parse(query[name][0])
        except ValueError:
            return f"{name} must be {what}"
        if not within(value):
            return f"{name} must be {what}"
    return None


def _media_analyze(state: EngineState, upload: MediaHandle, query: dict[str, list[str]]) -> tuple[int, dict]:
    contract = state.contract
    refused = _tuning_refused(contract, query)
    if refused:
        return 422, {contract.detail_key: refused}
    offered = {getattr(contract, f"{n}_feature"): n for n in FEATURES if getattr(contract, f"{n}_feature")}
    named = query.get(contract.features_param, [""])[0] if contract.features_param else ""
    names = [n.strip().lower() for n in named.split(",") if n.strip()]
    unknown = [n for n in names if n not in offered]
    if unknown:
        return 422, {contract.detail_key: f"unknown feature '{unknown[0]}'; it measures {', '.join(offered)}"}
    # None named measures every one, as the stood-in engine does.
    asked = [offered[n] for n in names] if names else list(offered.values())
    script = state.analysis or Analysis()
    body: dict = {contract.duration_key: upload.duration}
    for name in asked:
        body[getattr(contract, f"{name}_key")] = _shown_feature(name, getattr(script, name), contract)
    return 200, body


def _shown_feature(name: str, value, contract: EngineContract):
    """One feature of the script, as the engine writes it under `contract`."""
    if value is None:
        return None
    if name == "loudness":
        return {contract.rms_db_key: value.rms_db, contract.peak_db_key: value.peak_db,
                contract.lufs_key: value.lufs}
    if name == "silences":
        return [{contract.start_key: s.start, contract.end_key: s.end} for s in value]
    if name == "peaks":
        return {contract.peaks_rate_key: value.rate, contract.peaks_values_key: list(value.values)}
    if name in ("beats", "onsets"):
        return list(value)
    return value


def media_transport(state: EngineState) -> httpx.MockTransport:
    """The media surface in-process: an `httpx.MockTransport` answering
    `state`'s contract as `serve` does, with no socket.

        client = httpx.Client(transport=media_transport(state))
        engine = MediaEngine(state.contract, "http://vox.engine", client=client)

    It answers the media routes; anything else is answered 404, and the voice
    routes are served over the socket alone. Each request is recorded in
    `state.requests`, path and query as sent.
    """

    def answer(request: httpx.Request) -> httpx.Response:
        target = request.url.raw_path.decode("ascii")
        state.requests.append(target)
        route = urlparse(target)
        found = _media_route(state.contract, request.method, route.path)
        if found is None:
            return httpx.Response(404, json={"detail": "Not Found"})
        status, body = _answer_media(state, found, parse_qs(route.query),
                                     request.headers.get("Content-Type", ""), request.content)
        return httpx.Response(status) if body is None else httpx.Response(status, json=body)

    return httpx.MockTransport(answer)


@contextmanager
def serve(state: EngineState | None = None):
    """Run the engine on an ephemeral port. Yields `(base_url, state)`.

    The port is 0, so the OS picks it and the caller reads back what it got:
    a fixed port can be another program's server.
    """
    state = state or EngineState()
    handler = type("_BoundHandler", (_Handler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    # `serve_forever`'s default poll interval is 0.5s, and `shutdown()` waits
    # out one of them on every teardown; a short interval keeps each teardown,
    # and so the offline loop, short.
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
