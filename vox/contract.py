"""What vox needs from a speech engine, stated as data rather than as code.

`EngineContract` is the set of paths, parameters and response keys an HTTP
speech engine must answer with. It is a value: a caller constructs a
different one, and every part of vox — the client, the session, the
deterministic engine, the walkthrough — works against it unchanged. A seam
that names one engine is a coupling with extra steps, and a class per engine
is what that coupling looks like.

The defaults have to be some engine's spelling. They are one engine's because
one had to be first, not because it is special; `vox.adapters.joe` names that
engine, and naming it is the whole of that module's job.

**The claim is only as good as its test.** A default that is the only value
ever exercised is a hardcoding with a longer spelling, so
`tests/test_contract.py` runs the whole closed loop against a contract
sharing no path, no parameter name and no response key with the default. A
seam that works one way fails it.
"""

from __future__ import annotations

from dataclasses import dataclass


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
    """Records for `duration_param` seconds and transcribes what it heard."""

    filename_param: str = "filename"
    duration_param: str = "duration"

    text_key: str = "text"
    """Where the transcript sits in a transcribe or listen response."""

    audio_path_key: str = "audio_path"
    """Where a listen response names the file it recorded."""

    devices_key: str = "devices"
    available_key: str = "microphone_available"

    conversation: str | None = None
    """Where a dialog posts its own states -- `speaking`, `recorded`, `gave_up`,
    `idle` -- for anything watching the exchange. None for an engine with no
    such surface, and announcing is then a no-op: watching is optional, and
    asking the question is not."""

    def url(self, base: str, path: str) -> str:
        """Join a base URL to one of this contract's paths."""
        return f"{base.rstrip('/')}{path}"
