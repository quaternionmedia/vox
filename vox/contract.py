"""What vox needs from a speech engine, stated as data rather than as code.

`EngineContract` is the set of paths, parameters and response keys an HTTP
speech engine must answer with. It is a value: a caller constructs a
different one, and every part of vox — the client, the session, the
deterministic engine, the walkthrough — works against it unchanged, with no
class per engine.

It states two surfaces. The voice surface transcribes a file the engine can
already see and records from the engine's own microphone. The media surface
takes an upload -- audio, or a video's sound -- and answers its transcript
with times and what its sound measures; `vox.media.MediaEngine` drives it.

The defaults are one engine's spelling, and `vox.adapters.joe` names that
engine. `tests/test_contract.py` runs the whole closed loop and the whole
media surface against a contract sharing no path, no parameter name and no
response key with the default, so a part of vox that reads a default
spelling directly fails it.
"""

from __future__ import annotations

from dataclasses import dataclass

MEDIA_ID = "{media_id}"
"""Where a route addressing one upload carries the engine's id for it.

`media_transcribe`, `media_analyze` and `media_delete` each name it once, and
`MediaEngine` puts the id there escaped, so no id can change which route is
asked. A contract whose route lacks it is refused when it is built."""

MEDIA_ROUTES_WITH_AN_ID = ("media_transcribe", "media_analyze", "media_delete")


@dataclass(frozen=True)
class EngineContract:
    """The HTTP surface vox drives, and the shape of what comes back.

    Frozen because a contract that changes under a running session describes
    nothing. To talk to a different engine, build another one.
    """

    health: str = "/api/health"
    """Answers 200 when the engine is up. Reachability, not readiness."""

    devices: str = "/api/voice/devices"
    """Lists the engine's own audio inputs. Recording happens where the engine is."""

    transcribe: str = "/api/voice/transcribe"
    """Transcribes a file the engine can already see, named by `filename_param`."""

    listen: str = "/api/voice/listen"
    """Records up to `duration_param` seconds and transcribes what it heard."""

    filename_param: str = "filename"
    duration_param: str = "duration"

    pause_param: str | None = None
    """The parameter naming how long a pause ends the take, in milliseconds.
    `duration_param` is then the cap and the recording stops this long after
    the speaker does, so a closed-choice answer takes the engine's short
    default and a sentence-length instruction, with pauses mid-thought, asks
    for a longer one. None for an engine that records a fixed window and has
    no such thing; `listen(pause_ms=...)` then sends nothing."""

    hint_param: str | None = None
    """The parameter naming the words a short answer is expected to be,
    comma-separated, for an engine that can bias its transcription toward
    them: told "approve, hold", a clipped "approve" is likelier to come back
    as that word. A hint biases and never constrains -- the engine still
    returns what it heard, and the caller still decides what that means.
    None for an engine with no such thing; `listen(hint=...)` then sends
    nothing."""

    text_key: str = "text"
    """Where the transcript sits in a transcribe or listen response."""

    audio_path_key: str = "audio_path"
    """Where a listen response names the file it recorded."""

    confidence_key: str | None = None
    """Where a listen response says how sure the engine is of its transcript,
    from 0 to 1. None for an engine that does not say; `HttpSTT.last_confidence`
    is then None, and a caller treats the transcript as unweighed."""

    devices_key: str = "devices"
    available_key: str = "microphone_available"

    conversation: str | None = None
    """Where a dialog posts its own states -- `speaking`, `recorded`, `gave_up`,
    `idle` -- for anything watching the exchange, a `speaking` state carrying
    the question's `options` when it has them, so a display can offer them. None for an engine with no
    such surface, and announcing is then a no-op: watching is optional, and
    asking the question is not."""

    watch: str | None = None
    """Where a dialog opens a take before it asks a question, with the
    parameters `listen` takes, so a person who answers over the question is
    heard from their first word: the next `listen` returns that take, and a
    take nobody began is dropped for a fresh one. None for an engine with no
    such thing; `HttpSTT.watch` then does nothing."""

    unwatch: str | None = None
    """Where a dialog closes a watch it will not listen to."""

    control: str | None = None
    """Where an engine says, under `interrupted_key`, whether the person has
    interrupted the question being asked -- answered by key, held a talk key,
    or begun speaking over it -- so the question can stop. None for an engine
    that cannot tell, and nothing is then interrupted."""

    interrupted_key: str = "interrupted"

    # ── The media surface ─────────────────────────────────────────────────
    #
    # An upload, its transcript with times, and what its sound measures.
    # Each route and parameter is None for an engine that does not offer it,
    # and `vox.media.MediaEngine` then refuses that call by name, before any
    # request: an engine is never asked for something its contract says it
    # does not have.

    media_upload: str | None = "/api/media"
    """Takes an audio or video file as the raw request body, its type as the
    Content-Type, with POST. Answers with the engine's id for the upload under
    `media_id_key`, beside its length and what sound it found in it."""

    media_capabilities: str | None = "/api/media/capabilities"
    """Says, with GET, what the media surface takes and offers: the formats
    under `formats_key`, the features under `features_key`, the speech model
    under `speech_model_key`, and under `word_timestamps_key` whether a
    transcript can time each word."""

    media_transcribe: str | None = "/api/media/{media_id}/transcribe"
    """Transcribes an upload with times, with POST."""

    media_analyze: str | None = "/api/media/{media_id}/analyze"
    """Measures an upload's sound, with POST: the features `features_param`
    names, each answered under its own key."""

    media_delete: str | None = "/api/media/{media_id}"
    """Discards an upload, with DELETE. None for an engine whose uploads only
    expire; discarding one is then left to the engine's own clock."""

    language_param: str | None = "language"
    """The parameter fixing the language a transcript is heard in. None for an
    engine that always detects it, and asking such an engine for a language
    is refused rather than dropped: a transcript in a language nobody asked
    for is a different answer, not a hint ignored."""

    words_param: str | None = "words"
    """The parameter asking for each word's times, sent as `true` or `false`.
    None for an engine that cannot be asked; its segments then carry
    whichever words it gives, which can be none."""

    features_param: str | None = "features"
    """The parameter naming the features an analysis measures, comma-separated,
    in the engine's own names (the `*_feature` fields). None for an engine
    that measures everything it has whenever it is asked."""

    media_id_key: str = "media_id"
    """Where an upload's answer gives the engine's id for it."""

    duration_key: str = "duration"
    """Where an upload's answer, and an analysis, give its length in seconds."""

    sample_rate_key: str = "sample_rate"
    channels_key: str = "channels"
    has_audio_key: str = "has_audio"

    language_key: str = "language"
    """Where a transcript names the language it was heard in."""

    segments_key: str = "segments"
    """Where a transcript's segments sit. Each segment carries its text under
    `text_key`, as the transcript does, and its words under `words_key`."""

    start_key: str = "start"
    end_key: str = "end"
    """Where an interval -- a segment, a word, a silence -- starts and ends,
    in seconds of the upload."""

    words_key: str = "words"
    word_key: str = "word"
    """Where a word gives its own text."""

    probability_key: str = "probability"
    """Where a word says how likely the engine thinks it is, from 0 to 1."""

    info_key: str = "info"
    """Where a transcript carries the engine's own notes on how it was made.
    vox hands them on unread."""

    tempo_feature: str | None = "tempo"
    beats_feature: str | None = "beats"
    onsets_feature: str | None = "onsets"
    loudness_feature: str | None = "loudness"
    silences_feature: str | None = "silence"
    peaks_feature: str | None = "peaks"
    """The engine's name for each feature an analysis can be asked for, sent
    in `features_param`. vox's own name is the field's, before `_feature`.
    None for a feature the engine does not measure; asking for it is then
    refused."""

    tempo_key: str = "tempo"
    beats_key: str = "beats"
    onsets_key: str = "onsets"
    loudness_key: str = "loudness"
    silences_key: str = "silences"
    peaks_key: str = "peaks"
    """Where an analysis answers each feature: the tempo in beats per minute,
    beats and onsets as times in seconds, loudness as an object, silences as
    intervals, and peaks as an object."""

    rms_db_key: str = "rms_db"
    peak_db_key: str = "peak_db"
    lufs_key: str = "lufs"
    """Within loudness: its RMS and peak level in dBFS, and its integrated
    loudness in LUFS, which an engine may leave null."""

    peaks_rate_key: str = "rate"
    peaks_values_key: str = "values"
    """Within peaks: how many values there are per second, and the values,
    each a peak level from 0 to 1 -- the outline a waveform is drawn from."""

    formats_key: str = "formats"
    features_key: str = "features"
    speech_model_key: str = "speech_model"
    word_timestamps_key: str = "word_timestamps"

    detail_key: str = "detail"
    """Where a refusal on the media surface says why."""

    def __post_init__(self) -> None:
        for name in MEDIA_ROUTES_WITH_AN_ID:
            route = getattr(self, name)
            if route is not None and route.count(MEDIA_ID) != 1:
                raise ValueError(
                    f"{name} {route!r} must name {MEDIA_ID} once: it is where an upload's id goes"
                )

    def url(self, base: str, path: str) -> str:
        """Join a base URL to one of this contract's paths."""
        return f"{base.rstrip('/')}{path}"
