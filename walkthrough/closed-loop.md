# The closed loop, as it ran

Recorded by `walkthrough/test_closed_loop.py` on every `uv run pytest`.
Do not edit: the next test run overwrites it. Nothing compares this file
against anything, so it cannot go stale and it cannot fail — if it is
wrong, the walkthrough is wrong.

## What went round

| Leg | Value |
|---|---|
| said | `approve the deploy` |
| heard back from the engine | `approve the deploy` |
| synthesized to | `echo.wav` (96 bytes) |
| transcribed again as | `approve the deploy` |
| loop closed | True |

## What the engine was asked

1. `/api/health`
2. `/api/voice/transcribe?filename=said.wav`
3. `/api/voice/transcribe?filename=out%2Fecho.wav`

Three requests, over HTTP, on a port the OS chose. The first is the
identity check: a 200 proves something is listening, not that it is the
right something, so `/api/health` names itself before anything is measured
against it.

## What this does not show

The engine is `vox.engine`, a codec with an `EngineContract` around it. It
carries the text faithfully because that is what a codec does. **No claim
is made here about transcription accuracy**, which is what a real engine
would be doing and what can mis-hear. For that, run the same loop against one:

```sh
uv run vox loop               # no --offline; --engine names the adapter
```

That run is not deterministic and is not in this suite. It answers the
other half of the question.
