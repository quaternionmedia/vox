# HANDOFF.md

Where vox sits between the speech engine it drives and the repositories that
vendor it, for someone picking it up cold. `README.md` is the surface; this
is the part that is not visible from inside this clone.

**Stamped 2026-10-18**, at `vox` `feat/media-engine` `dc7da78` (from `main`
`1721e6f`), `joe` `feat/video-audio-engine` `4ec1424` (from `main`
`d72b65b`), `qmcp` `main` `df42315`, and `alfred` `integrate/wave-2`
`66fa618` with its `feat/captions` at `6376ed6`. Every figure was true at
those commits and nowhere else; the workstation's audio inputs were counted
at the 2026-10-12 stamp and not again.

## The shape

```
audio --> a speech engine (STT over HTTP) --> transcript --> vox TTS --> audio
              ^                                                            |
              +--------------- round_trip reads it back ------------------+

audio or video --> the same engine's media surface --> a transcript, timed,
                                                       and what the sound measures
```

- **vox** states an `EngineContract` — the paths, parameters and response
  keys an HTTP speech engine must answer, on its voice surface and on its
  media surface — and names no engine. `HttpSTT` drives the voice surface
  and `MediaEngine` the media one.
- **A speech engine** does the transcription. `vox.adapters.joe` is the
  contract for the one this was built against; it transcribes with whisper
  and records from its own machine's microphone. On joe's
  `feat/video-audio-engine` it also takes uploads at `/api/media` -- audio,
  or a video whose sound it extracts with ffmpeg -- and answers a transcript
  with word times and what the sound measures, with librosa.
- **qmcp** is a consumer. It vendors vox as a submodule at `vendor/vox` and
  uses it to answer its human-in-the-loop queue by speaking, as
  `qmcp human voice`. The submodule is not at qmcp's root: an editable
  install puts that root on `sys.path`, and a directory there named `vox`
  with no `__init__.py` resolves `import vox` to an empty namespace package
  in place of this one.
- **alfred**, the browser video editor, is the media surface's consumer.
  Its `feat/captions` vendors vox as a submodule at `alfred/vox`, beside its
  render engine at `alfred/otto`: cooperating engines under alfred's
  control plane, otto the picture and vox the sound. Its captions
  transcribe a timeline's mix through `vox.media`, and its library measures
  a file's sound through it, all from one module,
  `alfred/core/utils/audio_engine.py`. At this stamp the submodule is
  pinned at vox `main` `1721e6f`, which has no `vox.media`; the pin moves
  to this work when alfred's wave is integrated.

Importing `vox` loads no adapter, and a test asserts it in a fresh
interpreter. Products are named in `vox.adapters`; the CLI imports that
package and resolves `--engine` and `--voice` against it.

## Running it

```sh
uv sync --extra dev
uv run pytest                 # tests and both walkthroughs; no engine, no hardware
uv run vox loop --offline     # the whole closed loop: 1.3 s from the command line here, under half a second of it the loop
```

Against a real engine, with one running:

```sh
uv run vox doctor             # engine reachable, a microphone, synthesis
uv run vox loop --echo-dir <the engine's own audio directory>
```

`--echo-dir` has to be somewhere the engine resolves filenames against,
because closing the loop means handing vox's output back to it and the
contract takes a filename rather than an upload. The two share a filesystem
in a local loop and would not across hosts.

The media surface takes an upload, so it has no such constraint. Against
joe, started in its checkout on a port that is not 8000 (joe's CLI binds
8000, which is alfred's):

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

| `--voice` | Audible | Deterministic | Needs installing | Transcribable |
|---|---|---|---|---|
| `recording` | no | yes | nothing | only by vox's own engine |
| `formant` | yes | yes | nothing | **no** |
| `pyttsx3` | yes | no | a system voice | yes |

`formant` is a small formant synthesizer in pure Python. **Whisper does not
read it** — measured against a live engine, where it returned empty while
real synthesis through the identical path came back exactly, and measured
again through the media surface, where its narration came back with no
segments. `vox.synth.SPEECH_IS_NOT_TRANSCRIBABLE` carries that measurement.
Closing a loop through transcription needs `pyttsx3` or another real voice;
`Pyttsx3TTS(playback=False).to_wav(text, path)` writes one to a file and
plays nothing.

## What to know before a live run

- **The engine's default input is often not a microphone.** On the
  workstation this was built on it is a capture card, and the machine lists
  twenty inputs across four host APIs with the same microphone appearing
  four times under a byte-identical name. `uv run joe voice setup`, in the
  engine's own checkout, tries every input while you keep talking, picks by
  loudness, proves the pick by transcribing a sentence, and saves it in the
  engine's checkout, where a running engine reads it on its next recording.
  `JOE_INPUT_DEVICE`, where set in the engine's environment, still
  overrides it.
- **An engine refuses a filename containing a separator.** `round_trip`'s
  echo name is bare for that reason; a name with a `/` in it is how a path
  traversal gets in.
- **Bind no default port.** Other sessions run on this workstation. The
  offline engine takes an ephemeral port and reports which; a real engine's
  is `--url`.
- **Ask what is listening.** A 200 proves a server is there, not that it is
  the right one. `vox doctor` reports that 200 from the contract's health
  route beside the adapter and URL it was given, and does not tell which
  server answered. Before a media check, `/openapi.json` names the server:
  joe's says `Joe API`.
- **joe's media upload needs ffmpeg.** Without it on PATH or at
  `JOE_FFMPEG`, an upload is answered 503 and the capabilities list no
  formats. This workstation has none on PATH; the one Kdenlive ships, at
  `C:\Program Files\kdenlive\bin\ffmpeg.exe`, was used.
- **How long it takes.** Against joe at `4ec1424` with whisper `base` on
  this workstation's CPU, the first transcript of 3.5 s of speech took 9.5 s,
  the model's load among it, and the full analysis of it 7.3 s. An hour of
  sound takes minutes; `MediaEngine` gives an answer thirty minutes unless
  told otherwise, and reaching the engine ten seconds.
- **Uploads stay on the engine's disk** under `JOE_MEDIA_DIR` (joe's
  `Data/Media/` unless set) until deleted or an hour after their last use.
  Leaving a `MediaEngine` block deletes the ones it made.
- **An oversized upload is refused before it is sent.** With joe's cap set
  to 50 KB, its 413 reached a plain client whole here for bodies up to
  256 MB; `MediaEngine` refuses one on the `max_bytes` joe states all the
  same, because a reset can overtake the refusal on another host.

## What this does not establish

`vox loop --offline` proves the seam — the HTTP contract, the file handoff,
the orchestration, the loop closing. It proves nothing about transcription
accuracy, because its engine is a codec wearing an `EngineContract`. The
live run answers that and is not deterministic. Both are worth running;
they are different questions.

The media walkthrough proves the client against a stand-in answering a
script: the contract's spelling, the upload's real length, the answers read
back, the upload discarded. What an upload says and how its sound measures
is joe's, and the live check above is where that was seen.

## Where the rest is written down

The cross-repository state — what is merged, what is open, what is waiting
on a person — is in the QM corpus, not here:

- `handbook/handoffs/the-voice-loop.md` — the state
- `handbook/handoffs/hil-review-2026-09-27.md` — what needs deciding

This repository carries no `governance/qm` submodule, so those are read from
a `qm` checkout or from the host.
