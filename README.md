# vox

The voice-interaction seam. vox wires speech-to-text and text-to-speech
together into one small, pluggable adapter that a consuming repo vendors —
it owns no audio DSP itself.

```
mic/file --> joe (analysis engine, STT over HTTP) --> transcript --> vox TTS --> audio out
```

- **STT** is delegated to a running [joe](../../joe) engine's `/api/voice/*`
  endpoints (`vox.stt.JoeSTT`). Joe is the analysis engine for all audio;
  vox does not re-implement transcription.
- **TTS** is owned locally by vox (`vox.tts.Pyttsx3TTS` by default, offline,
  no model download) behind a `TextToSpeech` protocol so another backend can
  be swapped in without touching the seam.
- **`VoiceSession`** (`vox.session`) is the orchestration: `self_report_file`
  and `self_report_live` each prove the full audio -> text -> audio round
  trip in one call.

## Quick start

```sh
uv sync --extra dev
uv run pytest                 # unit tests, no live mic/speaker/joe needed

# with a joe engine already running (`uv run joe backend`, from ../../joe):
uv run vox doctor                         # checks joe is reachable, has a mic, and TTS works
uv run vox self-report clip.wav           # clip.wav must be under joe's Data/Audio/ or Data/Voice/
uv run vox self-report --duration 5       # record from the mic instead
```

`vox doctor` exists so a live demo fails here, with a specific reason
(joe unreachable / no microphone / TTS won't initialize), rather than
partway through a recording with an opaque error. `self-report --duration`
runs the same check itself before recording.

## Why this shape

Modeled on the alfred/otto seam: a small control-plane-style adapter
(`vox`) orchestrating a separate engine (`joe`) that does the heavy
analysis. `joe` stays a normal, pip-installable/callable engine; `vox` is
what a consuming repo (qmcp first) vendors as a submodule to get voice
interaction, registering `VoiceSession` against its own tool registry and
HITL flow instead of reimplementing STT/TTS itself.
