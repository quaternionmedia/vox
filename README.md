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
Its `to_wav(text, path) -> str` writes the WAV and plays nothing, for audio
made to be used rather than heard: narration laid under a video, or a clip
uploaded to an engine. `FormantTTS().to_wav` makes audible narration that is
the same bytes on any machine.

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
  and from `capabilities()`, what the engine takes and offers, and the
  limits it states: the largest upload, the longest sound, and how long an
  upload is kept.
- **A feature is asked for by vox's name**, the `Analysis` field, and the
  contract names the engine's: `vox.adapters.joe` asks for `silence` and
  reads the answer under `silences`. `analyze(..., peaks_rate=,
  silence_db=, silence_min=)` tunes the waveform's values a second and what
  counts as a silence; each left None keeps the engine's own.
- **What the contract leaves None is refused by name, before any request**,
  with `EngineDoesNotOffer`. A language, or a tuning, asked of an engine
  that cannot be told one is refused rather than dropped: the answer would
  be one nobody asked for. `words` travels only under the contract's own
  spelling, and a contract without one gets the words its engine gives.
- **An upload is held to the size the engine says it takes**, asked once
  per engine, and a larger one is refused with `UploadTooLarge` and not
  sent. An engine refusing a size it reads from the declared length answers
  before reading the body and closes the connection, and a client still
  sending can see the connection reset before the refusal reaches it, which
  would read as an engine not answering.
- **Every failure names the engine's URL.** `EngineUnavailable` when nothing
  answered in time; `EngineRefused`, with the engine's `status` and its
  `detail`, when it refused; `EngineAnswerMalformed`, saying where, when the
  answer is not the shape the contract describes. Each is an `EngineError`,
  rebuilt whole from its arguments, so a task queue can carry one.
- **Reaching the engine and its answer are timed apart.** `timeout`, thirty
  minutes unless given, is how long an answer may take, because an engine
  says nothing until it has transcribed the whole upload; `connect_timeout`,
  ten seconds, is how long reaching it may, so an engine that is not there
  is known at once.
- **Leaving the block discards every upload the engine still holds**, and
  `close(upload)` discards one. Neither raises: an upload the engine cannot
  be told to drop expires on its own clock.

A route addressing one upload carries `{media_id}` where the id goes, and a
contract whose route lacks it is refused when it is built. Calls block until
the engine answers; from an event loop, run them in a worker thread.

## The offline engine

`vox.engine` answers whichever `EngineContract` it is given. It refuses what
the list below says it refuses, much of it read off joe, so a caller's defect
of those kinds fails the offline suite rather than a live run. It is not joe:
it hears and measures nothing, and reads the sound of a WAV alone, so what an
upload says and how it sounds is the test's script, and an upload joe would
refuse for what is in it can pass. *Against joe's media API*, below, sets the
two side by side for the media surface.

- **It binds a port the OS picks** and reports it; a fixed port can be
  another program's server.
- **It says what it is.** Its health body carries `"engine": "vox.engine"`
  beside `status`, so a caller can tell it from a real engine left running.
- **It refuses a name that leaves its directories.** A `transcribe` filename
  resolving outside every audio directory is answered 404, as for a missing
  file. joe answers 404 to any name that is not bare; the stand-in answers a
  name with a separator in it that stays inside its directory.
- **It refuses audio it did not write.** `decode_wav` reads only WAVs
  `encode_wav` wrote, marked with `VOX1`, and raises
  `NotVoxAudioError` for anything else.
- **It holds the bounds of the engine it stands in for.** A `listen` duration
  outside `0 < duration <= 60` is answered 400; when the contract names a
  pause parameter, a pause outside `100..5000` milliseconds is answered 400
  under that contract's spelling. A duration or pause that does not parse is
  answered 422, and a `listen` with no microphone 503. joe also refuses a
  hint over 500 characters, and holds a watch to the same bounds as a
  `listen`; the stand-in does neither. `tests/test_engine_contract.py` names
  the joe commit each bound was read at.
- **It records what arrived.** `EngineState` keeps every request served, and
  the pauses, hints, announcements and watches each one carried, for a test
  to assert on; the engine itself has no speaker to wait for and no
  microphone to open early.
- **It answers the media surface from a script, judged as joe judges it.**
  Each request is judged in joe's order and refused with joe's status and
  sentence. An upload is taken in joe's formats; a WAV of plain samples has
  its length read from its header, and a body beginning as one of joe's
  other formats holds what `EngineState.sound` says, or is refused 422 when
  that says nothing. A transcribe answers `EngineState.transcript` and an
  analyze `EngineState.analysis`, so nothing about what an upload says is
  measured; either script can be an exception, answered 500 as a failure of
  the engine's own. `EngineState` holds the limits joe takes from its
  settings -- `media_limit`, `media_seconds`, and `media_ttl`, counted on
  `clock` -- and, in `decoder`, whether it has ffmpeg. A delete is 204 with
  no body, and no id is handed out twice. `media_transport(state)` answers
  the same surface in-process, as an `httpx.MockTransport`, for a client
  built around it. `tests/test_engine_contract.py` names the joe commit each
  answer was read at.

### Against joe's media API

What joe's media API judges, read off `api.py` and `Modules/Media.py` on
joe's `docs/media-engine` at `d2dd135`, whose media code is
`feat/video-audio-engine`'s at `4ec1424`, and checked against it in-process,
beside what `vox.engine` does with the same request:

| The request | joe | `vox.engine` |
|---|---|---|
| An upload with no `Content-Type`, or one joe does not take | 415, judged first | the same |
| An upload declaring a length over the cap: joe's `JOE_MEDIA_MAX_BYTES`, the stand-in's `media_limit`, 1 GiB each by default | 413, before a byte of it is read and before ffmpeg is looked for | the same, and then the body is read and dropped |
| An upload while joe has no ffmpeg, or one that cannot run | 503 | 503 when `decoder` is False, in one sentence for both |
| A WAV of plain samples: integers of any width, floats, A-law or mu-law | its length, to the millisecond, rate and channels | the same, read from its header |
| An MP3, OGG, FLAC, M4A, AAC, WebM, MP4, MOV or MKV, or a WAV of compressed samples | its sound's length, rate and channels, decoded by ffmpeg | what `sound` says it holds, or 422 when that says nothing |
| A video with no sound track | 422 | what `sound` says |
| A body that begins as none of joe's formats | 422 | the same |
| A sound with no samples | 422 | the same |
| A sound longer than `JOE_MEDIA_MAX_SECONDS`, the stand-in's `media_seconds`, two hours each by default | 413 | the same |
| An upload ffmpeg is still decoding after 600 s | 422 | -- |
| A transcript asked with a `words` that is not a boolean: `t`, `y`, `on` and `1` and their opposites are, in any case | 422, before the id is looked up | the same |
| A transcript asked in a language whisper does not know | 422, once the id is found | the same, against whisper `20250625`'s hundred languages and their names; one it knows is answered in its code |
| An analysis tuned with a value that is not a number, or out of bounds | 422, every such value in one sentence, before the features and the id; `peaks_rate=5.0` is 5 | the same |
| An analysis asked for an unknown feature | 422, before the id is looked up | the same |
| A route with an id in it, for one never handed out, or deleted | 404 | the same |
| A route with an id in it, for one unused for `JOE_MEDIA_TTL`, the stand-in's `media_ttl`, an hour each by default | 404 | the same, on `clock` |
| Any route, on a failure of joe's own | 500, naming the exception | the same, for a failure of the stand-in's own or a script that is an exception |

Where both refuse, the status and the body are the same: `{"detail": str}`
under the contract's `detail_key`, in joe's sentence, except that where
joe's names joe or one of its settings, the stand-in's names `vox.engine` or
the `EngineState` field in its place. A parameter given twice is read at its
last, and a blank one as given, as joe's framework reads them, and an
analysis answers its features in joe's order whatever the order asked.
`tests/test_engine_contract.py` holds the stand-in to each refusal and each
order the two share.

What stays different, and why:

- **What an upload says and how it sounds** is the script's: the stand-in
  hears nothing. From the request come only a WAV's length, rate and
  channels, the language asked for, and whether words were.
- **Every format but WAV** is the script's too. The stand-in has no
  decoder, and a container's first bytes say what it is, not that there is
  sound in it to decode. So a body beginning as an MP4 holds what `sound`
  says, where joe would decode it, or refuse it 422 for having no sound
  track or none it can read.
- **ffmpeg's own failures** are one switch. `decoder=False` stands for a
  joe without ffmpeg and for one whose ffmpeg cannot run, in the sentence
  for the first; a decode outlasting 600 s cannot happen when nothing is
  decoded.
- **An id** is `m1`, `m2` and on, where joe's is 128 random bits, so a run
  is the same every time and a walkthrough can record it; a caller treats
  either as opaque.
- **Its capabilities** name no speech model, since it has none.
- **The sweep.** joe sweeps uploads past their time as one arrives and on
  a clock of its own, so a delete of one is 204 until the next sweep and 404
  after; the stand-in sweeps only as an upload arrives.
- **One at a time.** joe transcribes one upload at a time, in the order
  asked; the stand-in answers at once.
- **A refusal made before the body is read** reaches the client whole. joe
  closes the connection on a client that may still be sending, and a reset
  can overtake the refusal, which is why `MediaEngine` refuses an upload too
  large before sending it.

## What runs, and what it records

| Command | What it is |
|---|---|
| `uv run pytest` | the suite and the worked examples in `walkthrough/`, which rewrite `walkthrough/closed-loop.md` and `walkthrough/media-engine.md` with what actually happened |
| `uv run python walkthrough/mutate.py` | breaks each guard on purpose and requires it to go red; records `walkthrough/mutations.md` |
| `uv run python walkthrough/check_wheel.py` | reads the built wheel's manifest, because an editable install hides a package the build never declared |

Each writes what it found rather than being compared against a stored copy,
and CI fails if a committed record differs from what a run produces.
