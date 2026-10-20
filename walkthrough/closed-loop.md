# The closed loop, as it ran

Recorded by `walkthrough/test_closed_loop.py` on every `uv run pytest`, which
overwrites it: do not edit. CI fails if the rewritten page differs from the
committed one, so a change in what happened is a diff to commit with the
change. If this page is wrong, the walkthrough is wrong.

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
3. `/api/voice/transcribe?filename=echo.wav`

Three requests over HTTP, on a port the OS chose. The first is the identity
check: a 200 proves something is listening, not that it is the right thing,
so the engine names itself on `/api/health` before anything is measured
against it.

## What this does not show

The engine is `vox.engine`, a codec with an `EngineContract` around it, so it
carries the text faithfully. **No claim is made here about transcription
accuracy**: that is a real engine's work, and where it can mis-hear. Run the
same loop against one:

```sh
uv run vox loop               # no --offline; --engine names the adapter
```

That run is not deterministic and is not in this suite.
