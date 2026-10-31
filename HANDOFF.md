# HANDOFF.md

Where vox sits between the speech engine it drives and the repositories that
vendor it, for someone picking it up cold. `README.md` is the surface; this
is what cannot be seen from inside this clone.

**Stamped 2026-10-18** at `vox` `docs/handoff-current`, from
`feat/media-engine` `aa17dbd` (from `main` `1721e6f`); `joe`
`feat/video-audio-engine` `4ec1424` (from `main` `d72b65b`); `qmcp`
`feat/alfred-captions` `691a367` (from `main` `df42315`); `alfred`
`integrate/wave-3` `2e4024c`; and `qm` `main` `b97b14c`. Every figure holds
at those commits and nowhere else, with two exceptions:

- The live media figures -- the first transcript, the analysis, the
  oversized upload -- were measured against joe at `4ec1424` with vox at
  `dc7da78`. Nothing under `vox/` that reaches joe's media surface has
  changed since: `fix/offline-engine-matches-joe` changes the stand-in,
  `vox/engine.py`, and how `HttpSTT.devices` and `vox doctor` read a refused
  microphone.
- The workstation's audio inputs were counted at the 2026-10-12 stamp and
  not again.

## The shape

```
audio --> a speech engine (STT over HTTP) --> transcript --> vox TTS --> audio
              ^                                                            |
              +--------------- round_trip reads it back ------------------+

audio or video --> the same engine's media surface --> a transcript, timed,
                                                       and what the sound measures
```

- **vox** states an `EngineContract` — the paths, parameters and response
  keys an HTTP speech engine must answer, on its voice and media surfaces —
  and names no engine. `HttpSTT` drives the voice surface, `MediaEngine` the
  media one.
- **A speech engine** transcribes. `vox.adapters.joe` is the contract for the
  one vox was built against, which transcribes with whisper and records from
  its own machine's microphone. On joe's `feat/video-audio-engine` it also
  takes uploads at `/api/media` -- audio, or a video whose sound it extracts
  with ffmpeg -- and answers a transcript with word times and, with librosa,
  what the sound measures. joe's `main` has no media surface; until that
  branch lands, a media check runs against the branch.
- **qmcp** consumes the voice surface. It vendors vox at `vendor/vox` and
  uses it to answer its human-in-the-loop queue by speaking, as
  `qmcp human voice`. Its `main` and `feat/alfred-captions` both pin vox at
  `c0b9b18`, from before `vox.media`. Neither uses the media surface: the
  alfred tools `feat/alfred-captions` adds drive alfred's own HTTP API. The
  submodule is not at qmcp's root, because an editable install puts that
  root on `sys.path`, and a `vox` directory there with no `__init__.py`
  resolves `import vox` to an empty namespace package in place of this one.
- **alfred**, the browser video editor, consumes the media surface. Its
  `integrate/wave-3`, with its captions work merged, vendors vox at
  `alfred/vox`, beside its render engine at `alfred/otto`: cooperating
  engines under alfred's control plane, otto the picture and vox the sound.
  - One module imports vox: `alfred/core/utils/audio_engine.py`. alfred's
    captions transcribe a timeline's mix through `vox.media`, and its
    library measures a file's sound through it.
  - That module builds the contract from vox's defaults, with
    `ALFRED_AUDIO_ENGINE_CONTRACT_<FIELD>` in place of each field named. It
    reaches the engine at `ALFRED_AUDIO_ENGINE_URL` and gives an answer
    `ALFRED_AUDIO_ENGINE_TIMEOUT`, 1800 seconds unless set.
  - alfred installs vox editable with `--no-deps` -- in its image, its nox
    sessions and its CI -- so everything `import vox` loads must need
    nothing beyond httpx, which alfred locks itself.
  - At this stamp the submodule is pinned at `aa17dbd`, this restamp's
    commit, which has `vox.media`. Nothing under `vox/` changes on
    `docs/handoff-current`, so moving the pin to its tip changes no code
    alfred runs.
  - `fix/offline-engine-matches-joe`, after it, changes the stand-in,
    `vox/engine.py`, and `HttpSTT.devices`, in `vox/stt.py`. `import vox`
    loads both; alfred's code calls neither, and alfred's tests drive the
    stand-in through `media_transport`. Under `vox/` it also changes
    `vox doctor`, which `import vox` does not load, and a docstring in
    `vox/contract.py`.

Importing `vox` loads no adapter, and a test asserts it in a fresh
interpreter. Products are named in `vox.adapters`; the CLI imports that
package and resolves `--engine` and `--voice` against it.

## Running it

```sh
uv sync --extra dev
uv run pytest                 # tests and both walkthroughs; no engine, no hardware
uv run vox loop --offline     # the whole closed loop: 1.1 to 1.4 s from the command line here, about half a second of it the loop
```

Against a real engine, with one running:

```sh
uv run vox doctor             # engine reachable, a microphone, synthesis
uv run vox loop --echo-dir <the engine's own audio directory>
```

A joe from `fix/security-defaults` on refuses each of these until a setting
turns its capability on: README's *Quick start* names which, and joe's
`docs/security.md`, *Which settings each consumer needs*, lists them.

`--echo-dir` must be somewhere the engine resolves filenames against:
closing the loop hands vox's output back to it, and the contract takes a
filename, not an upload. The two share a filesystem in a local loop, and
would not across hosts.

The media surface takes an upload, so it has no such constraint, and a fresh
joe serves it with nothing turned on. Start joe in its checkout on a port
other than 8000 (joe's CLI binds 8000, which is alfred's):

```sh
uv run uvicorn api:app --port 8010     # in joe's checkout, ffmpeg on PATH or named by JOE_FFMPEG
```

```python
from vox import MediaEngine
from vox.adapters import JOE

with MediaEngine(JOE, "http://127.0.0.1:8010") as engine:
    upload = engine.upload("speech.wav", "audio/wav")
    print(engine.transcribe(upload), engine.analyze(upload))
```

## Choosing a voice

README's *Which voice to use* has the table. What it rests on: **whisper does
not read `formant`.** Against a live engine it returned empty, where real
synthesis through the identical path came back exactly. Through the media
surface, its narration came back with no segments.
`vox.synth.SPEECH_IS_NOT_TRANSCRIBABLE` carries that measurement. Closing a
loop through transcription needs `pyttsx3` or another real voice;
`Pyttsx3TTS(playback=False).to_wav(text, path)` writes one to a file and
plays nothing.

## What to know before a live run

- **The engine's default input is often not a microphone.** On the
  workstation vox was built on, it is a capture card. The machine lists
  twenty inputs across four host APIs, the same microphone four times under a
  byte-identical name. `uv run joe voice setup`, in the engine's checkout,
  tries every input while you keep talking, picks by loudness, proves the
  pick by transcribing a sentence, and saves it in the engine's checkout. A
  running engine reads it on its next recording. `JOE_INPUT_DEVICE`, where
  set in the engine's environment, still overrides it.
- **An engine refuses a filename containing a separator**, since a name with
  a `/` in it is how a path traversal gets in. `round_trip`'s echo name is
  bare for that reason.
- **Bind no default port.** Other sessions run on this workstation. The
  offline engine takes an ephemeral port and reports which; a real engine's
  is `--url`.
- **Ask what is listening.** A 200 proves a server is there, not that it is
  the right one. `vox doctor` reports the 200 from the contract's health
  route beside the adapter and URL it was given, and does not say which
  server answered. Before a media check, `/openapi.json` names the server:
  joe's says `Joe API`.
- **joe's media upload needs ffmpeg**, on PATH or at `JOE_FFMPEG`. Without
  it, an upload is answered 503 and the capabilities list no formats. This
  workstation has none on PATH; the one Kdenlive ships, at
  `C:\Program Files\kdenlive\bin\ffmpeg.exe`, was used.
- **How long it takes.** Against joe at `4ec1424` with whisper `base` on
  this workstation's CPU, the first transcript of 3.5 s of speech took 9.5 s,
  the model's load included, and the full analysis of it 7.3 s. An hour of
  sound takes minutes. `MediaEngine` allows an answer thirty minutes and
  reaching the engine ten seconds, unless told otherwise.
- **Uploads stay on the engine's disk** under `JOE_MEDIA_DIR` (joe's
  `Data/Media/` unless set) until deleted, or an hour after their last use.
  Leaving a `MediaEngine` block deletes the ones it made.
- **An oversized upload is refused before it is sent.** With joe's cap set
  to 50 KB, its 413 reached a plain client whole here for bodies up to
  256 MB. `MediaEngine` still refuses one on the `max_bytes` joe states,
  because on another host a reset can overtake the refusal.

## What this does not establish

`vox loop --offline` proves the seam -- the HTTP contract, the file handoff,
the orchestration, the loop closing -- and nothing about transcription
accuracy, because its engine is a codec wearing an `EngineContract`. The live
run answers that, and is not deterministic.

The media walkthrough proves the client against a stand-in answering a
script: the contract's spelling, the upload's real length, the answers read
back, the upload discarded. What an upload says and how its sound measures
is joe's, seen in the live check above. The stand-in judges a request as joe
does but decodes nothing; README's *Against joe's media API* says where the
two differ, so an offline pass is not read as a live one.

## Where the rest is written down

The cross-repository state of the voice loop — what is merged, what is open,
what is waiting on a person — is in the QM corpus, not here:

- `handbook/handoffs/the-voice-loop.md` — the state
- `handbook/handoffs/hil-review-2026-10-04.md` — the open set, reviewed as
  one, and what needs deciding
- `handbook/handoffs/voice-set-landing.md` — how far that set's landing has
  got

This repository has no `governance/qm` submodule, so read those from a `qm`
checkout or from the host. None of them covers the media surface at this
stamp; alfred's `CHANGELOG.md` and `docs/captions.md`, on its
`integrate/wave-3`, say how alfred reaches joe through it.
