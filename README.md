# vox

The voice-interaction seam. vox wires speech-to-text and text-to-speech
together into one small adapter that a consuming repo vendors, and carries
media -- audio, or a video's sound -- to an engine that transcribes and
measures it. It owns no audio DSP, and it names no engine.

```
mic/file --> speech engine (STT over HTTP) --> transcript --> vox TTS --> audio out
                    ^                                                        |
                    |                                                        v
                    +---------------- round_trip reads it back --------------+

audio or video --> MediaEngine.upload --+--> transcribe --> segments and words, timed
                   (the same engine,    |
                    its media surface)  +--> analyze ----> tempo, beats, onsets,
                                                           loudness, silences, peaks
```

- **`EngineContract`** states what an HTTP speech engine must answer: the
  paths, the parameter names, the response keys. It is a value, so pointing
  vox at a different engine is a constructor argument rather than a new class.
- **`HttpSTT`** drives any engine matching one.
- **`MediaEngine`** drives the contract's media surface: it uploads audio or
  a video, and reads back its transcript with times and what its sound
  measures, as dataclasses.
- **`TextToSpeech`** is the synthesis protocol, with three backends:
  `recording` (a codec, deterministic, not audible as words), `formant`
  (audible, deterministic, nothing to install) and `pyttsx3` (a real system
  voice). `--voice` picks one.
- **`VoiceSession`** is the orchestration. `self_report_*` proves audio → text
  → audio; `round_trip` closes the loop by handing vox's own output back.
- **`vox.adapters`** is the only place a product is named, and naming one is
  that module's whole job. `vox.adapters.joe` is an `EngineContract`;
  `vox.adapters.pyttsx3` is a synthesizer. Nothing in `vox` imports either.

## Quick start

```sh
uv sync --extra dev
uv run pytest                 # tests and the walkthrough; no mic, speaker or engine needed
uv run vox loop --offline     # the whole closed loop, with no engine or hardware

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
microphone, no speakers, and the same bytes every run, so a test can assert
on the loop. *The offline engine* below says what it holds a caller to.

**It is a codec, not a voice.** `vox.engine` carries text through a real WAV
file faithfully, which proves the seam and proves nothing about transcription
accuracy. Running `vox loop` without `--offline` answers that other half, and
is not deterministic. Both are worth running; they are different questions.

## Which voice to use

| `--voice` | Audible | Deterministic | Needs installing | Whisper reads it |
|---|---|---|---|---|
| `recording` | no | yes | nothing | only vox's own engine |
| `formant` | yes | yes | nothing | **no** |
| `pyttsx3` | yes | no | a system voice | yes |

`formant` is a small formant synthesizer in pure Python: audible speech-like
audio for any text, on any machine, with nothing to install. **Whisper does
not read it.** It is for hearing what a loop said on a machine with no system
voice, not for closing a loop through transcription.
`vox.synth.SPEECH_IS_NOT_TRANSCRIBABLE` states it, for a caller to branch on.

Closing the loop through a real engine needs `pyttsx3` or another real
voice, which is the default.

## Pointing vox at something else

An engine adapter is a value:

```python
from vox import EngineContract, HttpSTT

MINE = EngineContract(
    transcribe="/v2/stt", filename_param="clip", text_key="utterance",
)
stt = HttpSTT("http://localhost:9000", contract=MINE)
```

A synthesizer adapter is any object with `speak(text, out_path=None) -> str`;
one that can be cut short also takes `until=`, a callable that says when to stop.

An engine that shows the exchange to a person can name a `conversation`
route. `HttpSTT.announce(state, text, reason=None)` then posts a dialog's own
states to it — `speaking`, `recorded`, `gave_up`, `idle` — briefly, and never
raises: a display that cannot be told is no reason for a question to go
unasked. On a contract without the route, announcing does nothing.
`vox.adapters.joe` names joe's.

An engine that stops recording when the speaker pauses names, as
`pause_param`, the parameter saying how long a pause ends the take, in
milliseconds; `listen(pause_ms=...)` and `vox self-report --pause-ms` then
send it under that spelling, a contract without one sends nothing, and no
value given leaves the engine's own default. `vox.adapters.joe` names joe's.

An engine that can bias its transcription toward expected words names, as
`hint_param`, the parameter carrying them; `listen(hint=["approve", "hold"])`
then sends them comma-separated, and a contract without one sends nothing. A
hint biases and never constrains. `announce(..., options=[...])` puts the same
words in a `speaking` state, in the order the question says them, so a display
can offer each as a control. `vox.adapters.joe` names joe's.

An engine that weighs its transcripts names, as `confidence_key`, where a
listen response says how sure it is, from 0 to 1; `HttpSTT.last_confidence`
then holds it after each `listen`, and None when the contract names no such
key or the engine reported none. `vox.adapters.joe` names joe's.

An engine that can be answered over a question names `watch`, `unwatch` and
`control` routes. `HttpSTT.watch(...)`, with `listen`'s parameters, opens a
take before the question is asked, so an answer said over it is heard from its
first word, and the next `listen` returns that take; `interrupted()` reads the
engine's report under `interrupted_key` -- an answer by key, a held talk key,
or speech over the question. `Pyttsx3TTS(...).speak(text, until=stt.interrupted)`
then stops the voice the moment that turns true. A contract without the routes
watches nothing and is never interrupted. `vox.adapters.joe` names joe's.

The platform voice (`vox.adapters.pyttsx3`) is heard on the platform's default
output unless told otherwise: `Pyttsx3TTS(output_device=...)` or
`VOX_OUTPUT_DEVICE` names another by a fragment of its name, and a fragment
matching none or several is refused. `vox outputs` lists the names and `vox say
"testing" --output <fragment>` says something on one, to find the output a
person actually hears. On Windows the adapter drives SAPI directly, writing
the sentence to a WAV and playing that file to the end, rather than relying
on pyttsx3's own playback.

`HttpSTT` holds one `httpx.Client` for its life, because building a client
costs far more than a request to a local engine. Close it with `close()` or
use it as a context manager; a client passed in is the caller's to close.

`tests/test_contract.py` runs the whole closed loop and the whole media
surface against a contract that shares no path, no parameter name and no
response key with the default, so a seam that only works one way fails it.

## Media: a transcript with times, and what the sound measures

An engine that names a media surface takes an upload -- an audio file, or a
video whose sound it extracts -- and answers its transcript, timed, and what
its sound measures. `MediaEngine` drives it:

```python
from vox import EngineContract, MediaEngine

with MediaEngine(EngineContract(), "http://127.0.0.1:8010") as engine:
    upload = engine.upload("mix.wav", "audio/wav")         # a path, or bytes
    transcript = engine.transcribe(upload, language="en")
    analysis = engine.analyze(upload, ["beats", "silences", "peaks"])
```

- **What comes back is dataclasses**, not the engine's JSON: a `MediaHandle`
  (`media_id`, `duration`, `sample_rate`, `channels`, `has_audio`); a
  `Transcript` (`text`, `language`, `segments`, `info`) of `Segment`s
  (`start`, `end`, `text`, `words`), each word a `Word` (`start`, `end`,
  `word`, `probability`), timed in seconds of the upload; an `Analysis`
  (`duration`, `tempo`, `beats`, `onsets`, `loudness`, `silences`, `peaks`);
  and from `capabilities()`, what the engine takes and offers.
- **A feature is asked for by vox's name**, the `Analysis` field, and the
  contract names the engine's: `vox.adapters.joe` asks for `silence` and
  reads the answer under `silences`.
- **What the contract leaves None is refused by name, before any request**,
  with `EngineDoesNotOffer`. A language asked of an engine that cannot be
  told one is refused rather than dropped: the transcript would be in a
  language nobody asked for. `words` travels only under the contract's own
  spelling, and a contract without one gets the words its engine gives.
- **Every failure names the engine's URL.** `EngineUnavailable` when nothing
  answered in time; `EngineRefused`, with the engine's `status` and its
  `detail`, when it refused; `EngineAnswerMalformed`, saying where, when the
  answer is not the shape the contract describes. Each is an `EngineError`,
  rebuilt whole from its arguments, so a task queue can carry one.
- **Leaving the block discards every upload the engine still holds**, and
  `close(upload)` discards one. Neither raises: an upload the engine cannot
  be told to drop expires on its own clock.

A route addressing one upload carries `{media_id}` where the id goes, and a
contract whose route lacks it is refused when it is built. Calls block until
the engine answers; from an event loop, run them in a worker thread.

## The offline engine

`vox.engine` answers whichever `EngineContract` it is given, and holds a
caller to what the engine it stands in for refuses, so a defect in a caller
fails the offline suite rather than a live run:

- **It binds a port the OS picks** and reports it; a fixed port can be
  another program's server.
- **It says what it is.** Its health body carries `"engine": "vox.engine"`
  beside `status`, so a caller can tell it from a real engine left running.
- **It refuses a name that leaves its directories.** A `transcribe` filename
  resolving outside every audio directory is answered 404, as for a missing
  file.
- **It refuses audio it did not write.** `decode_wav` reads only WAVs
  `encode_wav` wrote, marked with `VOX1`, and raises
  `NotVoxAudioError` for anything else.
- **It holds the bounds of the engine it stands in for.** A `listen` duration
  outside `0 < duration <= 60` is answered 400; when the contract names a
  pause parameter, a pause outside `100..5000` milliseconds is answered 400
  under that contract's spelling. A duration or pause that does not parse is
  answered 422, and a `listen` with no microphone 503. `tests/test_engine_contract.py` names the joe
  commit each bound was read at.
- **It records what arrived.** `EngineState` keeps every request served, and
  the pauses, hints, announcements and watches each one carried, for a test
  to assert on; the engine itself has no speaker to wait for and no
  microphone to open early.
- **It answers the media surface from a script.** It takes an upload in the
  stood-in engine's formats and reads a WAV's length from its header,
  answering 415 for a type it does not take, 413 past its limit, and 422 for
  a body it cannot decode, which is anything but a WAV. A transcribe answers
  `EngineState.transcript` and an analyze `EngineState.analysis`, so nothing
  about what an upload says is measured. An unknown upload is 404 on every
  route, and no id is handed out twice. `media_transport(state)` answers the
  same surface in-process, as an `httpx.MockTransport`, for a client built
  around it.

## What runs, and what it records

| Command | What it is |
|---|---|
| `uv run pytest` | the suite and the worked example in `walkthrough/`, which rewrites `walkthrough/closed-loop.md` with what actually happened |
| `uv run python walkthrough/mutate.py` | breaks each guard on purpose and requires it to go red; records `walkthrough/mutations.md` |
| `uv run python walkthrough/check_wheel.py` | reads the built wheel's manifest, because an editable install hides a package the build never declared |

Each writes what it found rather than being compared against a stored copy,
and CI fails if a committed record differs from what a run produces.
