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

Nothing is committed anywhere. All three repos have uncommitted working-tree
changes from this session, and **vox has no git repo at all yet** (no `git init`,
no remote). Test suites are green in all three (counts below) — that is the
extent of what has been verified. No PR has been opened in any repo.

`VoiceApprovalLoop` is now reachable as `qmcp human voice`, alongside the
existing `qmcp human list`/`qmcp human respond` — see the live demo below.

## How to verify each piece

### 1. joe — speech analysis

```sh
cd ../joe
uv sync
uv run pytest tests/test_voice.py tests/test_main.py -q   # expect: 12 passed
uv run joe backend                                        # localhost:8000
```

With the server up, in another terminal:

```sh
curl -X POST "http://localhost:8000/api/voice/transcribe?filename=<name-under-Data-Audio-or-Data-Voice>"
curl -X POST "http://localhost:8000/api/voice/listen?duration=5"   # needs a real mic
```

### 2. vox — the seam, self-report round trip

```sh
cd vox   # this repo
uv sync --extra dev
uv run pytest -q          # expect: 6 passed
```

With joe's backend running (step 1):

```sh
uv run vox <filename-under-joes-Data-Audio-or-Data-Voice>
uv run vox --duration 5   # live mic instead of a file
```

This exact round trip was run once, manually, during this session: a WAV
synthesized by vox's own TTS ("the quick brown fox jumps over the lazy dog")
was transcribed correctly by joe's live whisper endpoint and spoken back.
Nothing about that run is committed as a fixture — it was a manual check,
not a regression test, so repeat it yourself rather than trusting this note.

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
3. **`qmcp`'s dependency on `vox` is a local path** (`[tool.uv.sources] vox =
   {path = "../vox", editable = true}`), not a real git submodule, because
   vox has no remote yet. `qmcp.integrations.voice.adapter` is structurally
   typed against vox's shape rather than importing it, so `qmcp` imports
   fine without the `voice` extra installed — only exercising
   `VoiceApprovalLoop` with real backends needs it.
4. **`qmcp`'s setup command is `uv sync --all-extras`, not `uv sync --extra
   dev`.** The latter drops `pydantic-ai` (and other extras), which silently
   breaks unrelated tests with an ImportError that reads exactly like a code
   regression and isn't one — caught only by running the full suite, not the
   new tests in isolation.

## Windows-specific gotchas hit during this work

- Repeated `uv run python -m uvicorn ...` background starts leave stray
  `python.exe`/`python3.11.exe` processes; `pkill` from git-bash does not see
  them. Use `tasklist | grep -i python` then `taskkill //F //PID <n> //T`.
- A stray server process holding `safetensors/_safetensors_rust.pyd` open
  blocked `uv sync` with `Access is denied` until it was killed.
- `pyttsx3` on Windows drives SAPI5 via `comtypes`/`pypiwin32`, pulled in
  automatically — no separate setup.

## What's not done yet

- Nothing committed, no branches pushed, no PRs opened, in any of the three
  repos.
- `vox` has no git repo, no remote, no `governance/qm` submodule.
- `qmcp`'s `vox` dependency is a path pin, not a real submodule — needs a
  vox remote first.
- No ADRs drafted anywhere. `joe` has no governance/qm submodule at all;
  `qmcp`'s AGENTS.md requires a branch + PR (and, for an architecture
  decision like this, an ADR draft) before anything here is more than a
  local experiment.
- No test exists that starts joe's backend and drives it through vox
  automatically — the real round trip in step 2 above, and the live demo
  above, were both checked by hand, not by CI.
- `qmcp human voice`'s `--forever` mode has no upper bound on how long it
  waits when the queue is empty (real `time.sleep` in a real loop) — fine at
  a terminal, not something to run unattended yet.

## Files touched, for review

**joe** (`../joe`): `Modules/Voice.py` (new), `api.py`, `cli.py`,
`pyproject.toml`, `requirements.txt`, `docs/api.md`, `README.md`,
`tests/test_voice.py` (new).

**vox** (this repo, entirely new): `pyproject.toml`, `README.md`,
`vox/__init__.py`, `vox/stt.py`, `vox/tts.py`, `vox/session.py`, `vox/cli.py`,
`tests/test_session.py`, `tests/test_stt.py`, `tests/test_tts.py`.

**qmcp** (`../qmcp`): `qmcp/client/mcp_client.py` (added
`list_human_requests`), `qmcp/integrations/voice/__init__.py` (new),
`qmcp/integrations/voice/adapter.py` (new), `qmcp/cli.py` (added the `qmcp
human voice` command), `pyproject.toml` (`voice` extra + `tool.uv.sources`),
`tests/test_voice_integration.py` (new), `tests/test_cli_human_voice.py`
(new), `tests/test_client.py` (list_human_requests tests + fixture override).
