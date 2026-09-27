# HANDOFF.md

Where vox sits in a three-repository voice loop, for someone picking it up
cold. `README.md` is the surface; this is the part that is not visible from
inside this clone.

**Stamped 2026-09-27**, at `vox` `main` `d54a7f7`, `joe` `main` `6e1ad16`,
`qmcp` `main` `d834916` with `feat/voice-interaction` open. Every figure was
true at those commits and nowhere else.

## The shape

```
audio --> a speech engine (STT over HTTP) --> transcript --> vox TTS --> audio
              ^                                                            |
              +--------------- round_trip reads it back ------------------+
```

- **vox** states an `EngineContract` — the paths, parameters and response
  keys an HTTP speech engine must answer — and names no engine. `HttpSTT`
  drives any engine matching one.
- **A speech engine** does the transcription. `vox.adapters.joe` is the
  contract for the one this was built against; it transcribes with whisper
  and records from its own machine's microphone.
- **qmcp** is a consumer. It vendors vox as a submodule at `./vox` and uses
  it to answer its human-in-the-loop queue by speaking, as
  `qmcp human voice`.

Nothing under `vox/` imports `vox.adapters`, and a test asserts it in a
subprocess. A product is named there and nowhere else.

## Running it

```sh
uv sync --extra dev
uv run pytest                 # tests and the walkthrough; no engine, no hardware
uv run vox loop --offline     # the whole closed loop, about two thirds of a second
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

## Choosing a voice

| `--voice` | Audible | Deterministic | Needs installing | Transcribable |
|---|---|---|---|---|
| `recording` | no | yes | nothing | only by vox's own engine |
| `formant` | yes | yes | nothing | **no** |
| `pyttsx3` | yes | no | a system voice | yes |

`formant` is a small formant synthesizer in pure Python. **Whisper does not
read it** — measured against a live engine, where it returned empty while
real synthesis through the identical path came back exactly.
`vox.synth.SPEECH_IS_NOT_TRANSCRIBABLE` carries that measurement. Closing a
loop through transcription needs `pyttsx3` or another real voice.

## What to know before a live run

- **The engine's default input is often not a microphone.** On the
  workstation this was built on it is a capture card, and the machine lists
  twenty inputs across four host APIs with the same microphone appearing
  four times under a byte-identical name. `joe voice level --every` records
  briefly from each and reports the level; speak while it runs and set
  `JOE_INPUT_DEVICE` to the one that moves.
- **An engine refuses a filename containing a separator.** `round_trip`'s
  echo name is bare for that reason; a name with a `/` in it is how a path
  traversal gets in.
- **Bind no default port.** Other sessions run on this workstation. The
  offline engine takes an ephemeral port and reports which; a real engine's
  is `--url`.
- **Ask what is listening.** A 200 proves a server is there, not that it is
  the right one. `vox doctor` names the engine it reached.

## What this does not establish

`vox loop --offline` proves the seam — the HTTP contract, the file handoff,
the orchestration, the loop closing. It proves nothing about transcription
accuracy, because its engine is a codec wearing an `EngineContract`. The
live run answers that and is not deterministic. Both are worth running;
they are different questions.

## Where the rest is written down

The cross-repository state — what is merged, what is open, what is waiting
on a person — is in the QM corpus, not here:

- `handbook/handoffs/the-voice-loop.md` — the state
- `handbook/handoffs/hil-review-2026-09-27.md` — what needs deciding

This repository carries no `governance/qm` submodule, so those are read from
a `qm` checkout or from the host.
