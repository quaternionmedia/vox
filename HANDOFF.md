# HANDOFF.md

Voice interaction, spanning three repos, for someone else to pick up and verify.

## The shape

```
mic/file --> joe (analysis engine, STT via whisper, over HTTP) --> transcript --> vox (seam: TTS + orchestration) --> audio out
                                                                                       ^
                                                                                       |
                                                                    qmcp (integrations/voice: voice-answered HITL)
```

- **joe** (`../joe`) — the audio analysis engine. Already did chroma/pitch/MIDI;
  this work added speech: `Modules/Voice.py` (transcribe via whisper, record/listen
  via the mic), exposed at `POST /api/voice/transcribe` and `POST /api/voice/listen`,
  and `joe voice transcribe|listen` on the CLI.
- **vox** (this repo) — the seam. Owns no audio DSP: `JoeSTT` is an `httpx`
  client against joe's `/api/voice/*`, `Pyttsx3TTS` is local offline synthesis.
  `VoiceSession.self_report_file/_live()` proves the audio -> text -> audio
  round trip in one call.
- **qmcp** (`../qmcp`) — a consumer. `qmcp/integrations/voice/adapter.py` adds
  `parse_yes_no()` and `VoiceApprovalLoop`, which answers qmcp's human-in-the-loop
  approval queue by voice instead of by typing `qmcp human respond`. Wired into
  the CLI as `qmcp human voice`.

## State on arrival

Everything is committed and pushed, each on its own branch, with draft PRs
open for review — nothing has been merged:

- `vox`: `main` @ latest, pushed to `quaternionmedia/vox` (new repo, no PR --
  nothing to merge into yet).
- `joe`: branch `feat/voice-interaction`, PR #9 in `quaternionmedia/joe`.
- `qmcp`: branch `feat/voice-interaction`, PR #38 (draft) in
  `quaternionmedia/qmcp`, vendoring `vox` as a real git submodule at `./vox`
  (see `.gitmodules`) rather than a sibling-checkout path pin.

Test suites are green in all three (counts below).

`VoiceApprovalLoop` is reachable as `qmcp human voice`, alongside the
existing `qmcp human list`/`qmcp human respond` — see the live demo below.

## How to verify each piece

### 1. joe — speech analysis

```sh
cd ../joe
uv sync
uv run pytest tests/ -q   # expect: 22 passed
uv run joe backend                                        # localhost:8000
uv run joe voice devices                                  # lists real mic input devices on this machine
```

With the server up, in another terminal:

```sh
curl http://localhost:8000/api/voice/devices
curl -X POST "http://localhost:8000/api/voice/transcribe?filename=<name-under-Data-Audio-or-Data-Voice>"
curl -X POST "http://localhost:8000/api/voice/listen?duration=5"   # needs a real mic; 503 if none
```

### 2. vox — the seam, self-report round trip

```sh
cd vox   # this repo
uv sync --extra dev
uv run pytest -q          # expect: 15 passed
```

With joe's backend running (step 1):

```sh
uv run vox doctor                                    # checks joe reachable, a mic, TTS -- run this first
uv run vox self-report <filename-under-joes-Data-Audio-or-Data-Voice>
uv run vox self-report --duration 5                  # live mic instead of a file
```

`vox doctor` and `self-report --duration`'s own preflight both call joe's
`/api/voice/devices` before attempting anything live, so a missing
microphone (or an unreachable joe) is reported by name instead of surfacing
as a raw connection error or PortAudio traceback partway through.

This exact round trip was run more than once, manually, during this session
(most recently to prove `vox doctor` against real hardware): a WAV
synthesized by vox's own TTS was transcribed correctly by joe's live whisper
endpoint and spoken back. Nothing about those runs is committed as a
fixture — they were manual checks, not regression tests, so repeat one
yourself rather than trusting this note.

### 3. qmcp — voice-answered HITL

**Setup command is `uv sync --all-extras`, not `--extra dev`** — the latter
drops `pydantic-ai` and silently breaks unrelated tests with an ImportError
that looks like a code regression and isn't one. Learned the hard way during
this work; see the full suite command below.

```sh
cd ../qmcp
uv sync --all-extras   # pulls vox from the sibling checkout too
uv run pytest tests/test_voice_integration.py tests/test_client.py tests/test_cli_human_voice.py -q
# expect: 58 passed
uv run pytest -q   # full suite, expect: 841 passed, 11 skipped
```

`qmcp human voice --help` documents the CLI surface. Full live demo below.

## Live demo (three terminals)

Everything below is real processes talking over real HTTP — nothing is
mocked. Needs a microphone and speakers if you use `--forever`/no filename;
the filename form below only needs `vox`'s TTS to synthesize the question
back, so it runs headless.

**Terminal 1 — joe (speech recognition):**

```sh
cd joe
uv run joe backend                       # localhost:8000
```

**Terminal 2 — qmcp (the HITL queue):**

```sh
cd qmcp
uv run qmcp serve                        # localhost:3141 (or QMCP_PORT)
```

**Terminal 3 — create a pending approval, then answer it by voice:**

```sh
cd qmcp
uv run python -c "
from qmcp.client import MCPClient
client = MCPClient()
client.create_human_request(
    request_id='demo-1', request_type='approval',
    prompt='Approve the deploy?', options=['approve', 'reject'],
)
print('created demo-1')
"
uv run qmcp human list                   # shows demo-1 pending
uv run qmcp human voice demo-1           # speaks the prompt, listens, submits the answer
uv run qmcp human list                   # shows demo-1 answered
```

Say "yes"/"approve" or "no"/"reject" when it listens. An unclear answer gets
re-asked up to twice (`--max-retries`) before the command exits with an error
rather than guessing. Drop the `demo-1` argument to answer whatever is oldest
in the queue, or add `--forever` to keep answering as new requests arrive —
Ctrl+C to stop.

## Fixes already applied — don't re-discover these

1. **`whisper-openai` (the PyPI package, not OpenAI's) is broken against
   `transformers` 5.2.0** — `AttributeError: GPT2Tokenizer has no attribute
   additional_special_tokens`. Fixed in joe by switching to `openai-whisper`
   (same `import whisper`, uses `tiktoken` instead of `transformers`).
2. **`whisper.transcribe(path)` shells out to an `ffmpeg` binary**, which is
   not on this machine's PATH. Fixed in `joe/Modules/Voice.py` by loading
   audio with `librosa.load(path, sr=16000, mono=True)` and handing whisper
   the resulting numpy array instead of a path — whisper only invokes
   `ffmpeg` when given a string path.
3. **`qmcp`'s dependency on `vox` is a real git submodule** (`.gitmodules`,
   mounted at `./vox`, `[tool.uv.sources] vox = {path = "vox"}`), the same
   pattern as `governance/qm` — not a sibling-checkout path pin. Pin/update
   it deliberately (`git submodule update --remote` + commit), not floating.
   `qmcp.integrations.voice.adapter` is still structurally typed against
   vox's shape rather than importing it, so `qmcp` imports fine without the
   `voice` extra installed — only exercising `VoiceApprovalLoop` with real
   backends needs it.
4. **`qmcp`'s setup command is `uv sync --all-extras`, not `uv sync --extra
   dev`.** The latter drops `pydantic-ai` (and other extras), which silently
   breaks unrelated tests with an ImportError that reads exactly like a code
   regression and isn't one — caught only by running the full suite, not the
   new tests in isolation.
5. **`joe`'s package never actually installed `Modules/`** — only `cli.py`
   and `api.py` were declared as `py-modules`, so the *installed* `joe`
   console script failed with `ModuleNotFoundError: No module named
   'Modules'` the first time a voice subcommand was run through it rather
   than through pytest or `python -m uvicorn` from the repo root (both of
   those happen to put the repo root on `sys.path` via cwd, masking the
   gap). Fixed with `[tool.setuptools.packages.find] include = ["Modules*"]`
   in `joe/pyproject.toml`. If a new `joe` CLI command mysteriously can't
   import `Modules` after `uv sync`, this is the first thing to check.

## Windows-specific gotchas hit during this work

- Repeated `uv run python -m uvicorn ...` background starts leave stray
  `python.exe`/`python3.11.exe` processes; `pkill` from git-bash does not see
  them. Use `tasklist | grep -i python` then `taskkill //F //PID <n> //T`.
- A stray server process holding `safetensors/_safetensors_rust.pyd` open
  blocked `uv sync` with `Access is denied` until it was killed.
- `pyttsx3` on Windows drives SAPI5 via `comtypes`/`pypiwin32`, pulled in
  automatically — no separate setup.

## What's not done yet

- No ADRs drafted anywhere. `joe` has no governance/qm submodule at all;
  `qmcp`'s AGENTS.md requires an ADR draft for an architecture decision like
  this before the PR is more than a proposal.
- No test exists that starts joe's backend and drives it through vox
  automatically — every real round trip in this document, including
  `vox doctor` against real hardware, was checked by hand, not by CI.
- `qmcp human voice`'s `--forever` mode has no upper bound on how long it
  waits when the queue is empty (real `time.sleep` in a real loop) — fine at
  a terminal, not something to run unattended yet.
- `qmcp`'s PR (#38) is a **draft**, per its AGENTS.md — it stays in draft
  until a human reviews and manually tests it; nothing here promotes it.
- `joe`'s branch was built on top of an already-open, unmerged branch
  (`docs/onboarding-hardening`, PR #8) rather than off a clean default —
  PR #9 is stacked on it and will show that PR's diff too until #8 merges
  first.

## Files touched, for review

**joe** (`../joe`): `Modules/Voice.py` (new; also `list_input_devices()`,
`default_input_device()`, `microphone_available()`, `NoMicrophoneError`),
`api.py` (+ `GET /api/voice/devices`, 503 on no mic), `cli.py` (+
`joe voice devices`), `pyproject.toml` (`Modules*` packaging fix, `scipy`
dep), `requirements.txt`, `docs/api.md`, `README.md`, `tests/test_voice.py`
(new), `tests/test_api_voice.py` (new), `tests/test_cli_voice_devices.py`
(new).

**vox** (this repo, entirely new): `pyproject.toml`, `README.md`,
`vox/__init__.py`, `vox/stt.py` (+ `devices()`, `reachable()`), `vox/tts.py`,
`vox/session.py`, `vox/cli.py` (+ `doctor` command, mic preflight on
`self-report`'s live path), `tests/test_session.py`, `tests/test_stt.py`,
`tests/test_tts.py`, `tests/test_cli.py` (new).

**qmcp** (`../qmcp`): `qmcp/client/mcp_client.py` (added
`list_human_requests`), `qmcp/integrations/voice/__init__.py` (new),
`qmcp/integrations/voice/adapter.py` (new), `qmcp/cli.py` (added the `qmcp
human voice` command), `pyproject.toml` (`voice` extra + `tool.uv.sources`),
`tests/test_voice_integration.py` (new), `tests/test_cli_human_voice.py`
(new), `tests/test_client.py` (list_human_requests tests + fixture override).
