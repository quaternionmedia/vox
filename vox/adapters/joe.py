"""The joe engine, as an `EngineContract`.

joe is an audio analysis engine: it transcribes with whisper behind
`/api/voice/*` and records from its own machine's microphone, and behind
`/api/media` it takes an upload -- audio, or a video whose sound it extracts
with ffmpeg -- and answers its transcript with word times from whisper and
its tempo, beats, onsets, loudness, silences and peaks from librosa. This
module is the whole of vox's knowledge of it.

The values here are identical to `EngineContract()`'s defaults, apart from
`conversation`, `pause_param`, `hint_param`, `confidence_key`, `watch`,
`unwatch` and `control`, which the default leaves unset because not every
engine has them. `JOE` is written out in full rather than derived from the
defaults, so a change to joe's routes changes this module and leaves the
defaults alone; `tests/test_contract.py` checks the two still agree.

    from vox import HttpSTT
    from vox.adapters import JOE

    stt = HttpSTT("http://127.0.0.1:1059", contract=JOE)
"""

from vox.contract import EngineContract

JOE = EngineContract(
    health="/api/health",
    devices="/api/voice/devices",
    transcribe="/api/voice/transcribe",
    listen="/api/voice/listen",
    filename_param="filename",
    duration_param="duration",
    text_key="text",
    audio_path_key="audio_path",
    devices_key="devices",
    available_key="microphone_available",
    conversation="/api/voice/conversation",
    pause_param="silence_ms",
    hint_param="hint",
    confidence_key="confidence",
    watch="/api/voice/watch",
    unwatch="/api/voice/unwatch",
    control="/api/voice/control",
    interrupted_key="interrupted",
    media_upload="/api/media",
    media_capabilities="/api/media/capabilities",
    media_transcribe="/api/media/{media_id}/transcribe",
    media_analyze="/api/media/{media_id}/analyze",
    media_delete="/api/media/{media_id}",
    language_param="language",
    words_param="words",
    features_param="features",
    peaks_rate_param="peaks_rate",
    silence_db_param="silence_db",
    silence_min_param="silence_min",
    media_id_key="media_id",
    duration_key="duration",
    sample_rate_key="sample_rate",
    channels_key="channels",
    has_audio_key="has_audio",
    language_key="language",
    segments_key="segments",
    start_key="start",
    end_key="end",
    words_key="words",
    word_key="word",
    probability_key="probability",
    info_key="info",
    tempo_feature="tempo",
    beats_feature="beats",
    onsets_feature="onsets",
    loudness_feature="loudness",
    silences_feature="silence",
    peaks_feature="peaks",
    tempo_key="tempo",
    beats_key="beats",
    onsets_key="onsets",
    loudness_key="loudness",
    silences_key="silences",
    peaks_key="peaks",
    rms_db_key="rms_db",
    peak_db_key="peak_db",
    lufs_key="lufs",
    peaks_rate_key="rate",
    peaks_values_key="values",
    formats_key="formats",
    features_key="features",
    speech_model_key="speech_model",
    word_timestamps_key="word_timestamps",
    max_bytes_key="max_bytes",
    max_seconds_key="max_seconds",
    ttl_seconds_key="ttl_seconds",
    detail_key="detail",
)
"""joe's surface. Its files resolve under that engine's `Data/Audio` and
`Data/Voice`, so a filename handed to `transcribe` is relative to those.
`listen` ends a take `silence_ms` after the speaker stops, within the bounds
its route states, and `duration` is the cap; `hint` is the words an answer is
expected to be, handed to its transcriber as a prompt. `watch` opens a take
while a question is still being asked, and `control` reports `interrupted`
once the person has answered by key, held the talk key, or spoken over it.

Its media surface takes an upload and keeps it until it is deleted or its
time runs out. Asked for silences, it measures `silence` and answers under
`silences`; every other feature is one name both ways. It refuses with
`{"detail": ...}`, as its framework does."""

DEFAULT_URL = "http://127.0.0.1:1059"
"""Where `joe backend` listens unless told otherwise: 1059, the twelfth root of
two, joe's port in qm's allocation (its `ci/dashboard.py`). A default, not a
promise: another program can hold the port, so check what answers there before
measuring against it."""
