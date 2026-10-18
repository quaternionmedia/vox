"""Media: an upload, its transcript with times, and what its sound measures.

`MediaEngine` drives the media surface an `EngineContract` names. It uploads
an audio file, or a video whose sound the engine extracts, and asks for the
upload's transcript -- segments, and each word, timed in seconds of the
upload -- and for its sound's features: tempo, beats, onsets, loudness,
silences and peaks. Then it discards the upload.

The engine does the listening and the measuring; vox carries the file there
and the answer back. The answer arrives as dataclasses rather than as the
engine's JSON, so a caller reads `segment.start` and never an engine's
spelling of it. Every path, parameter and key comes from the contract, and a
route, parameter or feature the contract leaves None is refused by name
before any request is made.

Whatever goes wrong between the two is an `EngineError` naming the engine's
URL: `EngineUnavailable` when nothing answered in time, `EngineRefused` when
the engine answered with a refusal and its reason, `EngineAnswerMalformed`
when the answer is not the shape the contract describes, and
`EngineDoesNotOffer` when the contract says the engine has no such thing.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from vox.contract import MEDIA_ID, EngineContract

DEFAULT_TIMEOUT = 300.0
"""Seconds a media call may take. An engine transcribes the whole upload
before it answers, so this is long; a caller who knows better passes its own."""

QUICK_TIMEOUT = 10.0
"""Seconds asking what an engine offers, or discarding an upload, may take:
neither does any work on the media."""

DETAIL_CHARS = 500
"""The most of a refusal's reason an error carries: an engine's error page
can be long, and the reason is at its start."""

FEATURES = ("tempo", "beats", "onsets", "loudness", "silences", "peaks")
"""What an analysis can be asked for, in vox's names, which are `Analysis`'s
fields. The contract names each one's spelling on the engine, or None where
the engine does not measure it."""


# ── What can go wrong ────────────────────────────────────────────────────────


class EngineError(Exception):
    """A media engine could not do what was asked. The message names its URL.

    Each subclass's arguments are its fields, in order, so an error a task
    queue serialises is rebuilt from its arguments whole.
    """

    engine: str = ""
    request: str = ""

    def _the_engine(self) -> str:
        return f"the engine at {self.engine}" if self.engine else "the engine"

    def _asked(self) -> str:
        return f" {self.request}" if self.request else ""


class EngineUnavailable(EngineError):
    """Nothing answered: the engine is down, unreachable, or slower than the timeout."""

    def __init__(self, reason: str, engine: str = "", request: str = ""):
        super().__init__(reason, engine, request)
        self.reason, self.engine, self.request = reason, engine, request

    def __str__(self) -> str:
        return f"{self._the_engine()} did not answer{self._asked()}: {self.reason}"


class EngineRefused(EngineError):
    """The engine answered with a refusal: its HTTP `status`, and `detail`, the reason it gave."""

    def __init__(self, status: int, detail: str, engine: str = "", request: str = ""):
        super().__init__(status, detail, engine, request)
        self.status, self.detail, self.engine, self.request = status, detail, engine, request

    def __str__(self) -> str:
        return f"{self._the_engine()} refused{self._asked()} with {self.status}: {self.detail}"


class EngineAnswerMalformed(EngineError):
    """The engine answered, and the answer is not the shape its contract describes."""

    def __init__(self, reason: str, engine: str = "", request: str = ""):
        super().__init__(reason, engine, request)
        self.reason, self.engine, self.request = reason, engine, request

    def __str__(self) -> str:
        return f"{self._the_engine()} answered{self._asked()} with what its contract does not describe: {self.reason}"


class EngineDoesNotOffer(EngineError):
    """The contract says the engine has no such thing, so nothing was asked.

    `capability` is what was wanted, and `missing` the contract field that
    is None.
    """

    def __init__(self, capability: str, engine: str = "", missing: str = ""):
        super().__init__(capability, engine, missing)
        self.capability, self.engine, self.missing = capability, engine, missing

    def __str__(self) -> str:
        because = f": its contract names no {self.missing}" if self.missing else ""
        return f"{self._the_engine()} does not offer {self.capability}{because}"


# ── What comes back ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MediaHandle:
    """An upload the engine holds, by the id it gave it, with what it found in it."""

    media_id: str
    duration: float
    """Seconds of sound in the upload."""

    sample_rate: int | None = None
    """The upload's own sample rate, or None when the engine did not say."""

    channels: int | None = None
    has_audio: bool | None = None
    """Whether the engine found sound in it, or None when it did not say."""


@dataclass(frozen=True)
class Word:
    """One word, timed in seconds of the upload."""

    start: float
    end: float
    word: str
    """As the engine wrote it, spacing included."""

    probability: float | None = None
    """How likely the engine thinks the word is, 0 to 1, or None when it did not say."""


@dataclass(frozen=True)
class Segment:
    """A stretch of speech, timed in seconds of the upload, with its words."""

    start: float
    end: float
    text: str
    """As the engine wrote it, spacing included."""

    words: tuple[Word, ...] = ()
    """Empty when words were not asked for, or the engine gives none."""


@dataclass(frozen=True)
class Transcript:
    """What the engine heard in an upload."""

    text: str
    language: str | None
    segments: tuple[Segment, ...]
    info: dict[str, Any] = field(default_factory=dict)
    """The engine's own notes on how it was made, handed on unread."""

    @property
    def words(self) -> tuple[Word, ...]:
        """Every segment's words, in order."""
        return tuple(word for segment in self.segments for word in segment.words)


@dataclass(frozen=True)
class Loudness:
    rms_db: float | None
    """RMS level in dBFS."""

    peak_db: float | None
    """Peak level in dBFS."""

    lufs: float | None = None
    """Integrated loudness in LUFS, or None when the engine did not measure it."""


@dataclass(frozen=True)
class Silence:
    """A stretch with no sound to speak of, in seconds of the upload."""

    start: float
    end: float


@dataclass(frozen=True)
class Peaks:
    """The outline a waveform is drawn from."""

    rate: float
    """Values per second of the upload."""

    values: tuple[float, ...]
    """Each a peak level from 0 to 1."""


@dataclass(frozen=True)
class Analysis:
    """What an upload's sound measures. A feature not asked for is None, and
    so is one the engine answered null."""

    duration: float | None = None
    tempo: float | None = None
    """Beats per minute."""

    beats: tuple[float, ...] | None = None
    """Each beat's time, in seconds of the upload."""

    onsets: tuple[float, ...] | None = None
    """Each onset's time -- where a sound starts -- in seconds of the upload."""

    loudness: Loudness | None = None
    silences: tuple[Silence, ...] | None = None
    peaks: Peaks | None = None


@dataclass(frozen=True)
class Capabilities:
    """What an engine's media surface takes and offers."""

    formats: tuple[str, ...]
    """What an upload can be, as the engine names its formats."""

    features: tuple[str, ...]
    """What `MediaEngine.analyze` can ask this engine for, in vox's names: the
    features the engine reports that its contract also names."""

    speech_model: str | None = None
    word_timestamps: bool = False
    """Whether a transcript can time each word."""

    max_bytes: int | None = None
    """The largest upload taken, or None when the engine does not say."""

    max_seconds: float | None = None
    """The longest sound taken, or None when the engine does not say."""

    ttl_seconds: float | None = None
    """How long an upload is kept unused before the engine drops it, or None
    when it does not say."""


# ── Reading an answer ────────────────────────────────────────────────────────


class _Malformed(Exception):
    """Where an answer falls short of its contract. `MediaEngine` adds which engine."""


def _shown(value: Any) -> str:
    text = json.dumps(value, default=repr)
    return text if len(text) <= 60 else f"{text[:57]}..."


def _object(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _Malformed(f"{where} is {_shown(value)}, not an object")
    return value


def _key(body: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in body:
        raise _Malformed(f"{where} has no {key!r}")
    return body[key]


def _list(value: Any, where: str) -> list:
    if not isinstance(value, list):
        raise _Malformed(f"{where} is {_shown(value)}, not a list")
    return value


def _number(value: Any, where: str) -> float:
    # A JSON true is a Python int; it is not a time.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _Malformed(f"{where} is {_shown(value)}, not a number")
    return float(value)


def _maybe_number(value: Any, where: str) -> float | None:
    return None if value is None else _number(value, where)


def _maybe_whole(value: Any, where: str) -> int | None:
    if value is None:
        return None
    number = _number(value, where)
    if not number.is_integer():
        raise _Malformed(f"{where} is {_shown(value)}, not a whole number")
    return int(number)


def _maybe_flag(value: Any, where: str) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise _Malformed(f"{where} is {_shown(value)}, not true or false")


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise _Malformed(f"{where} is {_shown(value)}, not text")
    return value


def _maybe_text(value: Any, where: str) -> str | None:
    return None if value is None else _text(value, where)


def _times(value: Any, where: str) -> tuple[float, ...] | None:
    if value is None:
        return None
    return tuple(_number(v, f"{where}[{i}]") for i, v in enumerate(_list(value, where)))


def _read_handle(answer: Any, c: EngineContract) -> MediaHandle:
    body = _object(answer, "the answer")
    media_id = _text(_key(body, c.media_id_key, "the answer"), c.media_id_key)
    if not media_id:
        raise _Malformed(f"{c.media_id_key} is empty")
    return MediaHandle(
        media_id=media_id,
        duration=_number(_key(body, c.duration_key, "the answer"), c.duration_key),
        sample_rate=_maybe_whole(body.get(c.sample_rate_key), c.sample_rate_key),
        channels=_maybe_whole(body.get(c.channels_key), c.channels_key),
        has_audio=_maybe_flag(body.get(c.has_audio_key), c.has_audio_key),
    )


def _read_word(value: Any, c: EngineContract, where: str) -> Word:
    body = _object(value, where)
    return Word(
        start=_number(_key(body, c.start_key, where), f"{where}.{c.start_key}"),
        end=_number(_key(body, c.end_key, where), f"{where}.{c.end_key}"),
        word=_text(_key(body, c.word_key, where), f"{where}.{c.word_key}"),
        probability=_maybe_number(body.get(c.probability_key), f"{where}.{c.probability_key}"),
    )


def _read_segment(value: Any, c: EngineContract, where: str) -> Segment:
    body = _object(value, where)
    words = body.get(c.words_key)
    if words is not None:
        listed = _list(words, f"{where}.{c.words_key}")
        words = tuple(_read_word(w, c, f"{where}.{c.words_key}[{j}]") for j, w in enumerate(listed))
    return Segment(
        start=_number(_key(body, c.start_key, where), f"{where}.{c.start_key}"),
        end=_number(_key(body, c.end_key, where), f"{where}.{c.end_key}"),
        text=_text(_key(body, c.text_key, where), f"{where}.{c.text_key}"),
        words=words or (),
    )


def _read_transcript(answer: Any, c: EngineContract) -> Transcript:
    body = _object(answer, "the answer")
    segments = _list(_key(body, c.segments_key, "the answer"), c.segments_key)
    info = body.get(c.info_key)
    return Transcript(
        text=_text(_key(body, c.text_key, "the answer"), c.text_key),
        language=_maybe_text(body.get(c.language_key), c.language_key),
        segments=tuple(_read_segment(s, c, f"{c.segments_key}[{i}]") for i, s in enumerate(segments)),
        info={} if info is None else dict(_object(info, c.info_key)),
    )


def _read_loudness(value: Any, c: EngineContract, where: str) -> Loudness | None:
    if value is None:
        return None
    body = _object(value, where)
    return Loudness(
        rms_db=_maybe_number(_key(body, c.rms_db_key, where), f"{where}.{c.rms_db_key}"),
        peak_db=_maybe_number(_key(body, c.peak_db_key, where), f"{where}.{c.peak_db_key}"),
        lufs=_maybe_number(body.get(c.lufs_key), f"{where}.{c.lufs_key}"),
    )


def _read_silences(value: Any, c: EngineContract, where: str) -> tuple[Silence, ...] | None:
    if value is None:
        return None
    silences = []
    for i, item in enumerate(_list(value, where)):
        body = _object(item, f"{where}[{i}]")
        silences.append(Silence(
            start=_number(_key(body, c.start_key, f"{where}[{i}]"), f"{where}[{i}].{c.start_key}"),
            end=_number(_key(body, c.end_key, f"{where}[{i}]"), f"{where}[{i}].{c.end_key}"),
        ))
    return tuple(silences)


def _read_peaks(value: Any, c: EngineContract, where: str) -> Peaks | None:
    if value is None:
        return None
    body = _object(value, where)
    return Peaks(
        rate=_number(_key(body, c.peaks_rate_key, where), f"{where}.{c.peaks_rate_key}"),
        values=_times(_key(body, c.peaks_values_key, where), f"{where}.{c.peaks_values_key}") or (),
    )


_FEATURE_READERS: dict[str, Callable[[Any, EngineContract, str], Any]] = {
    "tempo": lambda value, c, where: _maybe_number(value, where),
    "beats": lambda value, c, where: _times(value, where),
    "onsets": lambda value, c, where: _times(value, where),
    "loudness": _read_loudness,
    "silences": _read_silences,
    "peaks": _read_peaks,
}


def _read_analysis(answer: Any, c: EngineContract, asked: tuple[str, ...], each_required: bool) -> Analysis:
    body = _object(answer, "the answer")
    measured: dict[str, Any] = {}
    for name in asked:
        key = getattr(c, f"{name}_key")
        if key not in body:
            # An engine asked for a feature by name answers it, null when it
            # found nothing; one that measures everything unasked may not
            # measure them all.
            if each_required:
                raise _Malformed(f"the answer has no {key!r}, which was asked for")
            continue
        measured[name] = _FEATURE_READERS[name](body[key], c, key)
    return Analysis(duration=_maybe_number(body.get(c.duration_key), c.duration_key), **measured)


def _read_capabilities(answer: Any, c: EngineContract) -> Capabilities:
    body = _object(answer, "the answer")
    formats = _list(_key(body, c.formats_key, "the answer"), c.formats_key)
    reported = {
        _text(name, f"{c.features_key}[{i}]")
        for i, name in enumerate(_list(_key(body, c.features_key, "the answer"), c.features_key))
    }
    return Capabilities(
        formats=tuple(_text(f, f"{c.formats_key}[{i}]") for i, f in enumerate(formats)),
        features=tuple(n for n in FEATURES if getattr(c, f"{n}_feature") in reported),
        speech_model=_maybe_text(body.get(c.speech_model_key), c.speech_model_key),
        word_timestamps=bool(_maybe_flag(body.get(c.word_timestamps_key), c.word_timestamps_key)),
        max_bytes=_maybe_whole(body.get(c.max_bytes_key), c.max_bytes_key),
        max_seconds=_maybe_number(body.get(c.max_seconds_key), c.max_seconds_key),
        ttl_seconds=_maybe_number(body.get(c.ttl_seconds_key), c.ttl_seconds_key),
    )


# ── The client ───────────────────────────────────────────────────────────────


class MediaEngine:
    """The media surface of any engine matching an `EngineContract`.

        with MediaEngine(contract, "http://127.0.0.1:8010") as engine:
            upload = engine.upload("mix.wav", "audio/wav")
            transcript = engine.transcribe(upload)
            analysis = engine.analyze(upload, ["beats", "silences"])

    Leaving the block discards every upload this engine made and has not
    discarded, and releases its HTTP client. `close()` does the same outside
    a block; `close(upload)` discards one.

    One `httpx.Client` serves every call, built on first use. A client
    passed in is the caller's to close.

    Calls block until the engine answers. From an event loop, run them in a
    worker thread.
    """

    def __init__(
        self,
        contract: EngineContract | None,
        base_url: str,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.Client | None = None,
    ):
        if not base_url:
            raise ValueError("a media engine needs the URL it answers at")
        self.contract = contract or EngineContract()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None
        # Ids this engine uploaded and has not discarded, for `close()`.
        self._held: list[str] = []

    @property
    def client(self) -> httpx.Client:
        """The shared client, built on first use so construction stays free."""
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def __enter__(self) -> MediaEngine:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── the surface ──

    def upload(self, source: str | os.PathLike[str] | bytes, content_type: str) -> MediaHandle:
        """Upload an audio or video file -- a path, or its bytes -- of the given
        type. Returns the engine's handle for it, held until discarded."""
        route = self._offered("media_upload", "uploads")
        if not content_type:
            raise ValueError("an upload needs its content type, such as audio/wav or video/mp4")
        headers = {"Content-Type": content_type}
        if isinstance(source, (bytes, bytearray, memoryview)):
            handle = self._ask("POST", route, _read_handle, content=bytes(source), headers=headers)
        else:
            # Streamed from the file rather than read into memory: a video can
            # be large, and the engine reads it as it arrives.
            with open(source, "rb") as body:
                handle = self._ask("POST", route, _read_handle, content=body, headers=headers)
        self._held.append(handle.media_id)
        return handle

    def transcribe(self, handle: MediaHandle | str, language: str | None = None,
                   words: bool = True) -> Transcript:
        """The upload's transcript: its segments, each word in them when
        `words` is true, and the language it was heard in.

        `language` fixes that language, and is refused on a contract with no
        `language_param`; None leaves it to the engine. `words` travels under
        `words_param` when the contract names one, and otherwise nothing is
        sent and the segments carry whichever words the engine gives.
        """
        route = self._offered("media_transcribe", "transcripts")
        params: dict[str, str] = {}
        if language:
            if not self.contract.language_param:
                raise EngineDoesNotOffer("a choice of language", self.base_url, "language_param")
            params[self.contract.language_param] = language
        if self.contract.words_param:
            params[self.contract.words_param] = "true" if words else "false"
        return self._ask("POST", self._at(route, handle), _read_transcript, params=params)

    def analyze(self, handle: MediaHandle | str, features: Iterable[str] | None = None) -> Analysis:
        """What the upload's sound measures: the `features` asked for, in
        vox's names (`FEATURES`), or every one the contract names when None.
        A feature the contract does not name is refused before any request."""
        route = self._offered("media_analyze", "analysis")
        asked = self._features(features)
        params: dict[str, str] = {}
        if self.contract.features_param:
            names = (getattr(self.contract, f"{name}_feature") for name in asked)
            params[self.contract.features_param] = ",".join(names)
        each_required = self.contract.features_param is not None
        return self._ask(
            "POST", self._at(route, handle),
            lambda answer, c: _read_analysis(answer, c, asked, each_required),
            params=params,
        )

    def capabilities(self) -> Capabilities:
        """What the engine's media surface takes and offers, as it says itself."""
        route = self._offered("media_capabilities", "a statement of its capabilities")
        return self._ask("GET", route, _read_capabilities, timeout=min(self.timeout, QUICK_TIMEOUT))

    def close(self, handle: MediaHandle | str | None = None) -> bool:
        """Discard `handle`'s upload; with none, discard every upload this
        engine made and has not discarded, and release the HTTP client.

        Returns whether everything asked to be discarded was. Never raises: an
        upload the engine cannot be told to drop expires on its own clock, and
        cleaning up is no reason to fail what came before it. On a contract
        with no `media_delete` nothing is sent, and the answer is False.
        """
        if handle is not None:
            return self._discard(self._id(handle))
        discarded = [self._discard(media_id) for media_id in list(self._held)]
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None
        return all(discarded)

    # ── the wire ──

    def _offered(self, name: str, capability: str) -> str:
        route = getattr(self.contract, name)
        if not route:
            raise EngineDoesNotOffer(capability, self.base_url, name)
        return route

    def _features(self, features: Iterable[str] | None) -> tuple[str, ...]:
        if features is None:
            offered = tuple(n for n in FEATURES if getattr(self.contract, f"{n}_feature"))
            if not offered:
                raise EngineDoesNotOffer("any feature to analyse", self.base_url, "*_feature")
            return offered
        asked = tuple(dict.fromkeys([features] if isinstance(features, str) else features))
        if not asked:
            raise ValueError("no features asked for; None asks for every one the engine offers")
        for name in asked:
            if name not in FEATURES:
                raise ValueError(f"no feature named {name!r}; vox's are {', '.join(FEATURES)}")
            if not getattr(self.contract, f"{name}_feature"):
                raise EngineDoesNotOffer(name, self.base_url, f"{name}_feature")
        return asked

    @staticmethod
    def _id(handle: MediaHandle | str) -> str:
        media_id = handle.media_id if isinstance(handle, MediaHandle) else handle
        if not isinstance(media_id, str) or not media_id:
            raise ValueError(f"{handle!r} is not an upload's handle or id")
        return media_id

    def _at(self, route: str, handle: MediaHandle | str) -> str:
        """`route` with the upload's id in it, escaped whole, so an id with a
        separator in it cannot reach another route."""
        return route.replace(MEDIA_ID, quote(self._id(handle), safe=""))

    def _discard(self, media_id: str) -> bool:
        if media_id in self._held:
            self._held.remove(media_id)
        if not self.contract.media_delete:
            return False
        try:
            self._ask("DELETE", self._at(self.contract.media_delete, media_id), None,
                      timeout=min(self.timeout, QUICK_TIMEOUT))
        except EngineError:
            return False
        return True

    def _ask(self, method: str, path: str, reader, *, timeout: float | None = None, **kwargs):
        """Send one request, and read its answer with `reader`, or raise the
        `EngineError` that says what went wrong and where."""
        request = f"{method} {path}"
        timeout = self.timeout if timeout is None else timeout
        try:
            resp = self.client.request(method, self.contract.url(self.base_url, path),
                                       timeout=timeout, **kwargs)
        except httpx.TimeoutException as exc:
            raise EngineUnavailable(f"no answer within {timeout:g} seconds", self.base_url, request) from exc
        except httpx.RequestError as exc:
            raise EngineUnavailable(str(exc) or type(exc).__name__, self.base_url, request) from exc
        if not resp.is_success:
            raise EngineRefused(resp.status_code, self._detail(resp), self.base_url, request)
        if reader is None:
            return None
        try:
            answer = resp.json()
        except ValueError:
            raise EngineAnswerMalformed(f"not JSON: {_shown(resp.text)}", self.base_url, request) from None
        try:
            return reader(answer, self.contract)
        except _Malformed as exc:
            raise EngineAnswerMalformed(str(exc), self.base_url, request) from None

    def _detail(self, resp: httpx.Response) -> str:
        """Why the engine refused: its own reason under `detail_key`, or the
        body as text when it gave none, or the status's own phrase."""
        try:
            body = resp.json()
        except ValueError:
            body = None
        if isinstance(body, Mapping) and self.contract.detail_key in body:
            detail = body[self.contract.detail_key]
            text = detail if isinstance(detail, str) else json.dumps(detail)
        else:
            text = resp.text.strip()
        return text[:DETAIL_CHARS] if text else resp.reason_phrase
