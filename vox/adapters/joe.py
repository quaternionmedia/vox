"""The joe engine, as an `EngineContract`.

joe is the audio analysis engine vox was first built against: it transcribes
with whisper behind `/api/voice/*` and records from its own machine's
microphone. This module is the whole of vox's knowledge of it.

The values here are identical to `EngineContract()`'s defaults, because those
defaults were taken from this engine when there was only one. That is worth
stating rather than leaving as a coincidence: if joe's routes move, this
module changes and the defaults do not, and the two stop being the same
thing. Being written out in full is what makes that possible.

    from vox import HttpSTT
    from vox.adapters import JOE

    stt = HttpSTT("http://127.0.0.1:8000", contract=JOE)
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
)
"""joe's surface. Its files resolve under that engine's `Data/Audio` and
`Data/Voice`, so a filename handed to `transcribe` is relative to those."""

DEFAULT_URL = "http://127.0.0.1:8000"
"""Where `joe backend` listens unless told otherwise. A default, not a promise —
two sessions on one workstation make port 8000 somebody else's server often
enough that anything measuring against it should ask what it is first."""
