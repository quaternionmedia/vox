"""The joe engine, as an `EngineContract`.

joe is an audio analysis engine: it transcribes with whisper behind
`/api/voice/*` and records from its own machine's microphone. This module is
the whole of vox's knowledge of it.

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
)
"""joe's surface. Its files resolve under that engine's `Data/Audio` and
`Data/Voice`, so a filename handed to `transcribe` is relative to those.
`listen` ends a take `silence_ms` after the speaker stops, within the bounds
its route states, and `duration` is the cap; `hint` is the words an answer is
expected to be, handed to its transcriber as a prompt. `watch` opens a take
while a question is still being asked, and `control` reports `interrupted`
once the person has answered by key, held the talk key, or spoken over it."""

DEFAULT_URL = "http://127.0.0.1:1059"
"""Where `joe backend` listens unless told otherwise: 1059, the twelfth root of
two, joe's port in qm's allocation (its `ci/dashboard.py`). A default, not a
promise: another program can hold the port, so check what answers there before
measuring against it."""
