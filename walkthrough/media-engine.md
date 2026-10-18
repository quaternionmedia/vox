# The media surface, as it ran

Recorded by `walkthrough/test_media_engine.py` on every `uv run pytest`.
Do not edit: the next test run overwrites it. No test compares it with a
stored copy, but CI does: after the suite rewrites it, CI fails if it
differs from the copy committed, so a change in what happened is a diff to
commit with the change. If this page is wrong, the walkthrough is wrong.

## What went up

| | |
|---|---|
| narration | `Approve the deploy. Hold the release.`, written by `FormantTTS().to_wav` |
| file | `narration.wav`, 100044 bytes |
| the engine's handle | `m1`: 3.125 s at 16000 Hz, 1 channel |

## What came back

The transcript, timed in seconds of the upload:

| start | end | segment | words |
|---|---|---|---|
| 0.00 | 1.40 | Approve the deploy. | Approve 0.00–0.62 · the 0.62–0.84 · deploy. 0.84–1.40 |
| 1.60 | 3.00 | Hold the release. | Hold 1.60–2.00 · the 2.00–2.20 · release. 2.20–3.00 |

What the sound measures, asked for as `beats`, `silences` and `peaks`:

| feature | answer |
|---|---|
| beats | 0.50, 1.00, 1.50, 2.00, 2.50, 3.00 |
| silences | 1.40–1.60 |
| peaks | 2 a second: 0.62, 0.81, 0.4, 0.77, 0.69, 0.35, 0.05 |
| tempo, onsets, loudness | not asked for, so `None` |

What the engine said it offers, asked first:

- features: `tempo`, `beats`, `onsets`, `loudness`, `silences`, `peaks`
- word timestamps: yes
- uploads: 20 content types, up to 1073741824 bytes

## What the engine was asked

1. `/api/media/capabilities`
2. `/api/media`
3. `/api/media/m1/transcribe?words=true`
4. `/api/media/m1/analyze?features=beats%2Csilence%2Cpeaks`
5. `/api/media/m1`

Five requests, over HTTP, on a port the OS chose, every path and parameter
in the default contract's spelling. `silences` went out as `silence`, the
engine's own name for it. The last request is the upload discarded as the
`with` block ended; nothing in the walkthrough asked for it.

## What this does not show

The engine is `vox.engine`, and its transcript and analysis are the script
it was handed: the narration's length is real, read from the WAV, and
**no claim is made here about what the narration says or how it sounds**.
That is what a real engine answers, and whisper does not read `FormantTTS`
(`vox.synth.SPEECH_IS_NOT_TRANSCRIBABLE`), so a live check uploads a real
recording:

```python
from vox import MediaEngine
from vox.adapters import JOE

with MediaEngine(JOE, "http://127.0.0.1:8010") as engine:   # joe: uv run uvicorn api:app --port 8010
    print(engine.transcribe(engine.upload("speech.wav", "audio/wav")))
```

That run is not deterministic and is not in this suite. It answers the
other half of the question.
