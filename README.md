# vox

The voice-interaction seam. vox wires speech-to-text and text-to-speech
together into one small adapter that a consuming repo vendors — it owns no
audio DSP, and it names no engine.

```
mic/file --> speech engine (STT over HTTP) --> transcript --> vox TTS --> audio out
                    ^                                                        |
                    |                                                        v
                    +---------------- round_trip reads it back --------------+
```

- **`EngineContract`** states what an HTTP speech engine must answer: the
  paths, the parameter names, the response keys. It is a value, so pointing
  vox at a different engine is a constructor argument rather than a new class.
- **`HttpSTT`** drives any engine matching one.
- **`TextToSpeech`** is the synthesis protocol. `RecordingTTS` is the
  deterministic backend that needs no hardware.
- **`VoiceSession`** is the orchestration. `self_report_*` proves audio → text
  → audio; `round_trip` closes the loop by handing vox's own output back.
- **`vox.adapters`** is the only place a product is named, and naming one is
  that module's whole job. `vox.adapters.joe` is an `EngineContract`;
  `vox.adapters.pyttsx3` is a synthesizer. Nothing in `vox` imports either.

## Quick start

```sh
uv sync --extra dev
uv run pytest                 # tests and the walkthrough; no mic, speaker or engine needed
uv run vox loop --offline     # the whole closed loop, about two thirds of a second

# against a real engine, once one is running:
uv run vox doctor                         # engine reachable, a mic, synthesis works
uv run vox loop                           # the same loop, against real transcription
uv run vox self-report clip.wav           # a file the engine can already resolve
uv run vox self-report --duration 5       # record from the engine's mic instead
```

`--engine` selects an adapter and `--url` overrides where it listens. An
unknown name is refused and the message lists what there is.

## The closed loop

`self_report` stops at the audio it produced, so the last leg is checked by a
person listening — which makes it a demo, not a check. `round_trip` hands that
audio back:

```
said.wav --> engine --> "approve the deploy" --> TTS --> echo.wav --> engine --> "approve the deploy"
                                                                                       |
                                                                     closed  <---------+
```

`--offline` runs it against `vox.engine`, a real HTTP server on a port the OS
picks, answering whichever contract it was handed. No model download, no
microphone, no speakers, and the same bytes every run — so the loop is
something a test can assert on and a change can be checked against in seconds
rather than three terminals.

**It is a codec, not a voice.** `vox.engine` carries text through a real WAV
file faithfully, which proves the seam and proves nothing about transcription
accuracy. Running `vox loop` without `--offline` answers that other half, and
is not deterministic. Both are worth running; they are different questions.

## Pointing vox at something else

An engine adapter is a value:

```python
from vox import EngineContract, HttpSTT

MINE = EngineContract(
    transcribe="/v2/stt", filename_param="clip", text_key="utterance",
)
stt = HttpSTT("http://localhost:9000", contract=MINE)
```

A synthesizer adapter is any object with `speak(text, out_path=None) -> str`.

`tests/test_contract.py` runs the whole closed loop against a contract that
shares no path, no parameter name and no response key with the default. That
test is the claim: a seam that only works one way fails it.

## What runs, and what it records

| Command | What it is |
|---|---|
| `uv run pytest` | the suite and the worked example in `walkthrough/`, which rewrites `walkthrough/closed-loop.md` with what actually happened |
| `uv run python walkthrough/mutate.py` | breaks each guard on purpose and requires it to go red; records `walkthrough/mutations.md` |
| `uv run python walkthrough/check_wheel.py` | reads the built wheel's manifest, because an editable install hides a package the build never declared |

Each writes what it found rather than being compared against a stored copy,
and CI fails if a committed record differs from what a run produces.
