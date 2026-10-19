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
unmodified against it and nothing about what an upload says is measured. It
judges a request in the order the engine it stands in for judges one, and
refuses with that engine's status and sentence. `media_transport` answers
that surface in-process, with no socket.
"""

from __future__ import annotations

import functools
import itertools
import json
import operator
import os
import re
import struct
import threading
import wave
from collections.abc import Callable, Iterator
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
for takes, each format under the names browsers and platforms give it. Any
other is answered 415. Whatever the type declared, a body is read for what
it is, as a decoder reads one: a WAV from its own header, a body beginning
as one of the other formats as `EngineState.sound` says, and anything else
is answered 422."""

MEDIA_LIMIT = 1024**3
"""The most bytes the media upload takes, the default cap of the engine it
stands in for. A larger upload is answered 413."""

MEDIA_SECONDS = 7200.0
"""The longest sound the media upload takes, in seconds: two hours, the
default of the engine it stands in for. A longer one is answered 413."""

LANGUAGES = {
    "en": "english", "zh": "chinese", "de": "german", "es": "spanish", "ru": "russian", "ko": "korean",
    "fr": "french", "ja": "japanese", "pt": "portuguese", "tr": "turkish", "pl": "polish", "ca": "catalan",
    "nl": "dutch", "ar": "arabic", "sv": "swedish", "it": "italian", "id": "indonesian", "hi": "hindi",
    "fi": "finnish", "vi": "vietnamese", "he": "hebrew", "uk": "ukrainian", "el": "greek", "ms": "malay",
    "cs": "czech", "ro": "romanian", "da": "danish", "hu": "hungarian", "ta": "tamil", "no": "norwegian",
    "th": "thai", "ur": "urdu", "hr": "croatian", "bg": "bulgarian", "lt": "lithuanian", "la": "latin",
    "mi": "maori", "ml": "malayalam", "cy": "welsh", "sk": "slovak", "te": "telugu", "fa": "persian",
    "lv": "latvian", "bn": "bengali", "sr": "serbian", "az": "azerbaijani", "sl": "slovenian",
    "kn": "kannada", "et": "estonian", "mk": "macedonian", "br": "breton", "eu": "basque",
    "is": "icelandic", "hy": "armenian", "ne": "nepali", "mn": "mongolian", "bs": "bosnian",
    "kk": "kazakh", "sq": "albanian", "sw": "swahili", "gl": "galician", "mr": "marathi", "pa": "punjabi",
    "si": "sinhala", "km": "khmer", "sn": "shona", "yo": "yoruba", "so": "somali", "af": "afrikaans",
    "oc": "occitan", "ka": "georgian", "be": "belarusian", "tg": "tajik", "sd": "sindhi", "gu": "gujarati",
    "am": "amharic", "yi": "yiddish", "lo": "lao", "uz": "uzbek", "fo": "faroese", "ht": "haitian creole",
    "ps": "pashto", "tk": "turkmen", "nn": "nynorsk", "mt": "maltese", "sa": "sanskrit",
    "lb": "luxembourgish", "my": "myanmar", "bo": "tibetan", "tl": "tagalog", "mg": "malagasy",
    "as": "assamese", "tt": "tatar", "haw": "hawaiian", "ln": "lingala", "ha": "hausa", "ba": "bashkir",
    "jw": "javanese", "su": "sundanese", "yue": "cantonese",
}
"""The languages a transcript can be asked for in, by code, each with its
name: those the stood-in engine's speech model knows, copied from its
tokenizer at the version that engine locks (`tests/test_engine_contract.py`
names it). A language is asked for by its code or a name, in any case, and
the transcript answers in the code; any other is answered 422."""

_LANGUAGE_NAMES = {
    **{name: code for code, name in LANGUAGES.items()},
    "burmese": "my", "valencian": "ca", "flemish": "nl", "haitian": "ht", "letzeburgesch": "lb",
    "pushto": "ps", "panjabi": "pa", "moldavian": "ro", "moldovan": "ro", "sinhalese": "si",
    "castilian": "es", "mandarin": "zh",
}
"""Every name the speech model knows a language by, with its code."""

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
    language, it answers in that language's code."""

    analysis: Analysis | None = None
    """What the media surface's analyze answers for each feature asked for,
    for any upload: the test's script for what its sound measures. A feature
    the script leaves None is answered null, and so is every feature when
    the script is None. The duration answered is the upload's own."""

    media_formats: tuple[str, ...] = MEDIA_FORMATS
    """The content types an upload is taken in. Any other is answered 415."""

    media_limit: int = MEDIA_LIMIT
    """The most bytes an upload may be. A larger one is answered 413."""

    media_seconds: float | None = MEDIA_SECONDS
    """The longest sound an upload may hold, in seconds. A longer one is
    answered 413; None takes a sound of any length."""

    decoder: bool = True
    """Whether the engine has the decoder its uploads are read with. False
    answers every upload 503 once its type and declared size are judged,
    and the capabilities list no formats, as an engine without one does."""

    sound: tuple[float, int, int] | None = None
    """What an upload holds when it is in a format the stand-in cannot read
    -- any but a WAV of plain samples -- as `(seconds, sample_rate,
    channels)`: the test's script for its sound, as `transcript` is for what
    is said in it. None answers such an upload 422. A body that begins as
    none of the formats taken is answered 422 whatever this says, and a WAV
    of plain samples is read from its own header."""

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
        Returns whether it was; the body is read only when it was, and only
        once the answer needs it."""
        route = urlparse(self.path)
        found = _media_route(self.state.contract, method, route.path)
        if found is None:
            return False
        declared = self.headers.get("Content-Length") or ""
        unread = int(declared) if declared.isdigit() else 0

        def read() -> bytes:
            nonlocal unread
            body, unread = self.rfile.read(unread), 0
            return body

        status, body = _answer_media(self.state, found, _query(route.query),
                                     self.headers.get("Content-Type"), declared, read)
        if body is None:
            # An answer with nothing to say, as a delete's 204 is: no body at all.
            self.send_response(status)
            self.end_headers()
        else:
            self._send(status, body)
        # What the answer did not read is read and dropped before the
        # connection closes: one closed on a client still sending can reach
        # it as a reset before the answer does, which no test can rely on.
        while unread > 0:
            dropped = self.rfile.read(min(unread, 1 << 16))
            if not dropped:
                break
            unread -= len(dropped)
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
#
# Judged in the order the stood-in engine judges a request, and refused with
# its status and its sentence, so a caller that reads either reads the same
# here. Where its sentence names that engine or one of its settings, this one
# names `vox.engine` or the `EngineState` field in their place.

_MEDIA_ROUTES = (
    ("upload", "POST", "media_upload"),
    ("capabilities", "GET", "media_capabilities"),
    ("transcribe", "POST", "media_transcribe"),
    ("analyze", "POST", "media_analyze"),
    ("delete", "DELETE", "media_delete"),
)

_BOOLEANS = {"1": True, "on": True, "t": True, "true": True, "y": True, "yes": True,
             "0": False, "off": False, "f": False, "false": False, "n": False, "no": False}
"""What the stood-in engine's framework reads as a boolean parameter, in any
case and with nothing either side."""

_INTEGER = re.compile(r"[+-]?[0-9]+(?:_[0-9]+)*(?:\.0+)?")
"""What its framework reads as an integer once the spaces either side are
stripped: ASCII digits, an underscore between two of them, and a point
followed by zeros alone, so `5.0` is 5 and `2.5` is no integer."""

_INTEGER_UNREAD = "Input should be a valid integer, unable to parse string as an integer"
_NUMBER_UNREAD = "Input should be a valid number, unable to parse string as a number"

_TUNING = (
    ("peaks_rate_param", "integer", _INTEGER_UNREAD, (("le", 1000), ("ge", 1))),
    ("silence_db_param", "number", _NUMBER_UNREAD, (("le", 0.0), ("ge", -120.0))),
    ("silence_min_param", "number", _NUMBER_UNREAD, (("le", 3600.0), ("gt", 0.0))),
)
"""What an analysis can be tuned by, in the order its route takes them: each
value's kind, why one that does not read as that kind is refused, and its
bounds in the order its framework checks them, so a value outside both, as
`nan` is, is refused for the first."""

_BOUNDS = {
    "le": (operator.le, "less than or equal to"),
    "ge": (operator.ge, "greater than or equal to"),
    "gt": (operator.gt, "greater than"),
}

_NOT_HELD = "no such media: it was never uploaded here, has expired, or was deleted"

_UNDECODABLE = ("this media has no audio ffmpeg can decode: "
                "Error opening input files: Invalid data found when processing input")
"""The stood-in engine's refusal of a body its decoder cannot open, with the
decoder's own last word on it."""

_WAV_COUNTED = {0x0001, 0x0003, 0x0006, 0x0007}
"""The WAV sample formats a frame of which is the header's block of bytes --
integer, float, A-law and mu-law samples -- so the data's length counts the
frames. Any other is compressed, and its length is a decoder's to read."""

_ATOMS = (b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide")
"""The atoms an MP4, M4A or QuickTime file can begin with, after its size."""


class _Refused(Exception):
    """A request on the media surface refused: its status, and the sentence
    that says why."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


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


def _query(raw: str) -> dict[str, list[str]]:
    """A query string's parameters, a blank value kept as one: the stood-in
    engine's framework refuses `words=` rather than reading it as absent."""
    return parse_qs(raw, keep_blank_values=True)


def _last(query: dict[str, list[str]], name: str | None) -> str | None:
    """What `query` gives the parameter `name`: the last value when it gives
    several, as the stood-in engine's framework reads one, and None when it
    gives none or the contract names no such parameter."""
    values = query.get(name) if name else None
    return values[-1] if values else None


def _parsed(text: str, kind: str) -> int | float | None:
    """`text` as the stood-in engine's framework reads an integer or a
    number; None when it does not read as one."""
    text = text.strip()
    if kind == "integer":
        return int(text.partition(".")[0]) if _INTEGER.fullmatch(text) else None
    if not text.isascii():
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _tuning_refused(contract: EngineContract, query: dict[str, list[str]]) -> str | None:
    """Why an analysis's tuning is refused, as the stood-in engine's framework
    refuses it, before the route runs and so before the upload's id is looked
    up: each value that does not parse or falls outside its bounds, as `name:
    reason`, in the order the route takes them, joined by `; `. None when no
    value is refused."""
    refused = []
    for field_name, kind, unread, bounds in _TUNING:
        name = getattr(contract, field_name)
        text = _last(query, name)
        if text is None:
            continue
        value = _parsed(text, kind)
        if value is None:
            refused.append(f"{name}: {unread}")
            continue
        for check, bound in bounds:
            within, words = _BOUNDS[check]
            if not within(value, bound):
                refused.append(f"{name}: Input should be {words} {bound:g}")
                break
    return "; ".join(refused) or None


def _language_code(language: str | None) -> str | None:
    """The code of the language a transcript is asked for in, by its code or
    a name; None when none is asked, which leaves it to the engine. Refused
    422 for one the speech model does not know."""
    if language is None or not language.strip():
        return None
    key = language.strip().lower()
    code = key if key in LANGUAGES else _LANGUAGE_NAMES.get(key)
    if code is None:
        raise _Refused(422, f"unknown language {language!r}; give a code such as 'en' or a name such as "
                            "'English'")
    return code


def _use(state: EngineState, media_id: str) -> MediaHandle:
    """The upload `media_id` names. Refused 404 when the engine does not hold
    it: it keeps an upload until it is deleted."""
    upload = state.uploads.get(media_id)
    if upload is None:
        raise _Refused(404, _NOT_HELD)
    return upload


def _answer_media(state: EngineState, route: tuple[str, str | None], query: dict[str, list[str]],
                  content_type: str | None, declared: str,
                  read: Callable[[], bytes]) -> tuple[int, dict | None]:
    """Answer one request on the media surface, under the contract's keys. A
    body of None is an answer with none, as a delete's is.

    `declared` is the request's Content-Length as sent, and `read` reads its
    body, which an upload alone asks for, once its headers have been judged."""
    name, media_id = route
    try:
        if name == "upload":
            return 201, _media_upload(state, content_type, declared, read)
        if name == "capabilities":
            return 200, _media_capabilities(state)
        if name == "transcribe":
            return 200, _media_transcribe(state, media_id, query)
        if name == "analyze":
            return 200, _media_analyze(state, media_id, query)
        _media_delete(state, media_id)
        return 204, None
    except _Refused as refusal:
        return refusal.status, {state.contract.detail_key: refusal.detail}


def _media_capabilities(state: EngineState) -> dict:
    contract = state.contract
    return {
        # Listed in order, as the stood-in engine lists them, and none at all
        # when there is no decoder to read one with.
        contract.formats_key: sorted(state.media_formats) if state.decoder else [],
        contract.features_key: [
            getattr(contract, f"{n}_feature") for n in FEATURES if getattr(contract, f"{n}_feature")
        ],
        # It has no speech model; the transcript it answers is the script.
        contract.speech_model_key: None,
        contract.word_timestamps_key: True,
        contract.max_bytes_key: state.media_limit,
        contract.max_seconds_key: state.media_seconds,
        # It keeps an upload until it is deleted.
        contract.ttl_seconds_key: None,
    }


def _media_upload(state: EngineState, content_type: str | None, declared: str,
                  read: Callable[[], bytes]) -> dict:
    """Take an upload, judged as the stood-in engine judges one: its type, its
    declared size before a byte of it is read, whether there is a decoder,
    the size it turns out to be, and then what it holds."""
    contract = state.contract
    kind = (content_type or "").split(";")[0].strip().lower()
    if kind not in state.media_formats:
        raise _Refused(415, f"unsupported media type '{kind or 'none'}'; vox.engine takes "
                            f"{', '.join(sorted(state.media_formats))}")
    too_large = f"media larger than {state.media_limit} bytes (EngineState.media_limit)"
    if declared.isdigit() and int(declared) > state.media_limit:
        raise _Refused(413, too_large)
    if not state.decoder:
        raise _Refused(503, "ffmpeg was not found: EngineState.decoder is False")
    body = read()
    if len(body) > state.media_limit:
        raise _Refused(413, too_large)
    seconds, rate, channels = _sound(state, body)
    handle = MediaHandle(f"m{next(state._ids)}", round(seconds, 3), rate, channels, True)
    state.uploads[handle.media_id] = handle
    return {
        contract.media_id_key: handle.media_id,
        contract.duration_key: handle.duration,
        contract.sample_rate_key: handle.sample_rate,
        contract.channels_key: handle.channels,
        contract.has_audio_key: handle.has_audio,
    }


def _sound(state: EngineState, body: bytes) -> tuple[float, int, int]:
    """What an upload's body holds, as `(seconds, sample_rate, channels)`.
    Refused 422 for a body with no sound the stand-in can read or none in it,
    and 413 for a sound longer than it takes."""
    if body[:4] in (b"RIFF", b"RF64", b"BW64") and body[8:12] == b"WAVE":
        header = _wav_header(body)
        if header is None:
            raise _Refused(422, _UNDECODABLE)
        sample_format, channels, rate, frame_bytes, data_bytes = header
        if sample_format in _WAV_COUNTED:
            sound = ((data_bytes // frame_bytes) / rate, rate, channels)
        else:
            sound = _scripted(state)
    elif _begins_as_media(body):
        sound = _scripted(state)
    else:
        raise _Refused(422, _UNDECODABLE)
    seconds = sound[0]
    if seconds <= 0:
        raise _Refused(422, "this media's audio stream holds no samples")
    if state.media_seconds is not None and seconds > state.media_seconds:
        raise _Refused(413, f"this media's sound is longer than {state.media_seconds:g} seconds "
                            f"(EngineState.media_seconds)")
    return sound


def _scripted(state: EngineState) -> tuple[float, int, int]:
    """What the script says a body the stand-in cannot read holds."""
    if state.sound is None:
        raise _Refused(422, "vox.engine cannot read this media's sound, and EngineState.sound does not say "
                            "what it holds")
    return state.sound


def _wav_header(body: bytes) -> tuple[int, int, int, int, int] | None:
    """A WAV's sample format, channels, rate, bytes a frame and bytes of
    sound, read chunk by chunk as a decoder reads them: the format before the
    data, and the data no longer than what arrived. None when the header does
    not say."""
    found = None
    at = 12
    while at + 8 <= len(body):
        chunk, size, start = body[at:at + 4], int.from_bytes(body[at + 4:at + 8], "little"), at + 8
        if chunk == b"fmt " and size >= 16 and start + 16 <= len(body):
            sample_format, channels, rate, _, frame_bytes = struct.unpack_from("<HHIIH", body, start)
            if sample_format == 0xFFFE and size >= 26 and start + 26 <= len(body):
                # WAVE_FORMAT_EXTENSIBLE: the format is the subformat's first two bytes.
                sample_format = int.from_bytes(body[start + 24:start + 26], "little")
            found = (sample_format, channels, rate, frame_bytes)
        elif chunk == b"data":
            if found is None or not all(found[1:]):
                return None
            arrived = len(body) - start
            # 0xFFFFFFFF is a length left for a later chunk to say, as a long
            # WAV's is, or one written as it was recorded.
            return (*found, arrived if size == 0xFFFFFFFF else min(size, arrived))
        at = start + size + (size & 1)
    return None


def _begins_as_media(body: bytes) -> bool:
    """Whether `body` begins as one of the other formats the media upload
    takes begins: an Ogg page, a FLAC stream, an ID3 tag, a Matroska or WebM
    header, an MP4 or QuickTime atom, or an MPEG audio or ADTS frame. Whether
    the rest of it is sound is a decoder's question, and the stand-in has
    none to ask."""
    if body.startswith((b"OggS", b"fLaC", b"ID3", b"\x1a\x45\xdf\xa3")) or body[4:8] in _ATOMS:
        return True
    return len(body) > 1 and body[0] == 0xFF and body[1] & 0xE0 == 0xE0


def _media_transcribe(state: EngineState, media_id: str, query: dict[str, list[str]]) -> dict:
    """A transcript, judged as the stood-in engine judges one: `words` before
    the id is looked up, and the language after."""
    contract = state.contract
    words = True
    flag = _last(query, contract.words_param)
    if flag is not None:
        words = _BOOLEANS.get(flag.lower())
        if words is None:
            raise _Refused(422, f"{contract.words_param}: Input should be a valid boolean, "
                                "unable to interpret input")
    _use(state, media_id)
    language = _language_code(_last(query, contract.language_param))
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
    return {
        contract.text_key: script.text,
        contract.language_key: language or script.language,
        contract.segments_key: segments,
        contract.info_key: dict(script.info),
    }


def _media_analyze(state: EngineState, media_id: str, query: dict[str, list[str]]) -> dict:
    """An analysis, judged as the stood-in engine judges one: its tuning, then
    the features asked for, and only then the upload's id."""
    contract = state.contract
    refused = _tuning_refused(contract, query)
    if refused:
        raise _Refused(422, refused)
    offered = {getattr(contract, f"{n}_feature"): n for n in FEATURES if getattr(contract, f"{n}_feature")}
    named = _last(query, contract.features_param) or ""
    names = [n.strip().lower() for n in named.split(",") if n.strip()]
    unknown = [n for n in names if n not in offered]
    if unknown:
        raise _Refused(422, f"unknown feature '{unknown[0]}'; vox.engine measures {', '.join(offered)}")
    upload = _use(state, media_id)
    # None named measures every one, as the stood-in engine does.
    asked = {offered[n] for n in names} if names else set(offered.values())
    script = state.analysis or Analysis()
    body: dict = {contract.duration_key: upload.duration}
    # The duration first, then each feature asked for in the order the engine
    # gives them, whatever the order asked.
    for name in FEATURES:
        if name in asked:
            body[getattr(contract, f"{name}_key")] = _shown_feature(name, getattr(script, name), contract)
    return body


def _media_delete(state: EngineState, media_id: str) -> None:
    if state.uploads.pop(media_id, None) is None:
        raise _Refused(404, _NOT_HELD)


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
        status, body = _answer_media(state, found, _query(route.query), request.headers.get("Content-Type"),
                                     request.headers.get("Content-Length", ""), lambda: request.content)
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
