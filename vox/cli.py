"""vox CLI — a manual smoke test for the seam, not a production surface.

Usage:
    uv run vox loop --offline             # the closed loop, no engine and no hardware
    uv run vox doctor                     # check readiness before a live demo
    uv run vox loop                       # the same loop against a real engine
    uv run vox self-report clip.wav       # transcribe an existing file, then speak it back
    uv run vox self-report --duration 5   # record from the engine's mic, then speak it back

`--engine` and `--voice` each name a module in `vox.adapters`; nothing here
is wired to a particular one, and `--url` overrides where the engine listens.
The defaults are defaults, not requirements.
"""

import contextlib
from pathlib import Path

import typer

from vox import adapters
from vox.engine import EngineState, encode_wav
from vox.engine import serve as serve_engine
from vox.session import VoiceSession
from vox.stt import HttpSTT
from vox.tts import RecordingTTS

app = typer.Typer(help="vox — the voice-interaction seam", no_args_is_help=True)

ENGINE = typer.Option("joe", "--engine", help="Which adapter in vox.adapters to talk to")
URL = typer.Option(None, "--url", help="Where the engine listens; defaults to the adapter's")
VOICE = typer.Option("pyttsx3", "--voice", help="Which synthesizer adapter to speak with")


def _resolve(engine: str, url: str | None):
    """Look an adapter up by name. Returns (contract, base_url)."""
    import importlib

    try:
        module = importlib.import_module(f"vox.adapters.{engine}")
        contract = getattr(module, engine.upper())
    except (ImportError, AttributeError):
        known = ", ".join(sorted(p.stem for p in Path(adapters.__file__).parent.glob("[!_]*.py")))
        typer.echo(f"No engine adapter named {engine!r}. Known: {known}", err=True)
        raise typer.Exit(2) from None
    return contract, url or getattr(module, "DEFAULT_URL", "http://127.0.0.1:8000")


def _synthesizer(voice: str):
    """Look a synthesizer up the same way an engine is looked up.

    Two are vox's own and need nothing installed: `recording`, a codec that
    is not audible as words, and `formant`, which is audible and which
    whisper cannot read (`vox.synth.SPEECH_IS_NOT_TRANSCRIBABLE`). Anything
    else names a module in `vox.adapters` exporting a class of that name.
    """
    if voice == "recording":
        return RecordingTTS()
    if voice == "formant":
        from vox.synth import FormantTTS

        return FormantTTS()

    import importlib

    try:
        module = importlib.import_module(f"vox.adapters.{voice}")
        return getattr(module, f"{voice.capitalize()}TTS")()
    except (ImportError, AttributeError) as exc:
        typer.echo(f"No voice adapter named {voice!r}: {exc}", err=True)
        raise typer.Exit(2) from None


@app.command("doctor")
def doctor(engine: str = ENGINE, url: str = URL, voice: str = VOICE):
    """Check whether a live demo can run: engine reachable, a mic, local synthesis.

    Exists so a live run fails here, with a specific reason, rather than
    partway through a recording with an opaque connection or backend error.
    """
    contract, base_url = _resolve(engine, url)
    ok = True

    with HttpSTT(base_url, contract=contract) as stt:
        if not stt.reachable():
            ok = False
            typer.echo(f"[fail] engine: nothing answering at {base_url} ({engine})")
        else:
            typer.echo(f"[ok]   engine: {engine} reachable at {base_url}")

            report = stt.devices()
            devices = report.get(contract.devices_key) or []
            if report.get(contract.available_key):
                default = next((d for d in devices if d.get("default")), devices[0])
                typer.echo(f"[ok]   microphone: {default['name']} (on the engine's machine)")
            else:
                ok = False
                typer.echo("[fail] microphone: none found on the engine's machine.")

    # Synthesis is checked by synthesizing, because "the module imports" is
    # not the thing that fails at demo time -- initialising the audio stack is.
    try:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            _synthesizer(voice).speak("check", out_path=f"{tmp}/check.wav")
        typer.echo(f"[ok]   text-to-speech: {voice} synthesizes")
    except typer.Exit:
        # `_synthesizer` refusing an unknown name is a usage error, not a
        # broken audio stack. `typer.Exit` is not a `SystemExit`, so catching
        # the latter here let the refusal fall through to the handler below
        # and be reported as a synthesis failure with the wrong exit code.
        raise
    except Exception as exc:
        ok = False
        typer.echo(f"[fail] text-to-speech: {voice}: {exc}")

    if not ok:
        raise typer.Exit(1)
    typer.echo("Ready. `vox loop` to try it live.")


@app.command("self-report")
def self_report(
    filename: str = typer.Argument(None, help="Audio file the engine can already resolve"),
    duration: float = typer.Option(5.0, help="Seconds to record if no filename is given"),
    engine: str = ENGINE,
    url: str = URL,
    voice: str = VOICE,
):
    """Round-trip audio -> text -> audio, either from a file or live from the mic."""
    contract, base_url = _resolve(engine, url)

    with HttpSTT(base_url, contract=contract) as stt:
        if filename is None and not stt.microphone_available():
            typer.echo(
                "No microphone available. Run `vox doctor` for the specific reason "
                "(engine unreachable, or its machine has no input device).",
                err=True,
            )
            raise typer.Exit(1)

        session = VoiceSession(stt=stt, tts=_synthesizer(voice))
        result = (
            session.self_report_file(filename)
            if filename
            else session.self_report_live(duration=duration)
        )

    typer.echo(f"Heard:      {result.transcript}")
    typer.echo(f"Said back:  {result.output_audio_path}")


@app.command("loop")
def loop(
    say: str = typer.Option("approve the deploy", help="What the input audio says"),
    offline: bool = typer.Option(
        False, "--offline", help="Run against vox's own deterministic engine"
    ),
    engine: str = ENGINE,
    url: str = URL,
    voice: str = VOICE,
    echo_dir: str = typer.Option(
        "Data/Voice", help="Directory the engine resolves filenames against"
    ),
):
    """Close the loop: text -> audio -> engine -> text -> audio -> engine -> text.

    `--offline` is the short, deterministic form: it starts vox's own engine
    on an ephemeral port, runs the whole trip through real HTTP, and needs
    no engine, no model download, no microphone and no speakers. That is the
    loop a test can run and a change can be checked against.

    Without `--offline` the same code path runs against a real engine, which
    is the claim the offline form cannot make: that the words were heard.
    Run both — they answer different questions.
    """
    contract, base_url = _resolve(engine, url)
    tts = _synthesizer("recording" if offline else voice)

    with contextlib.ExitStack() as stack:
        if offline:
            state = EngineState(
                audio_dirs=[Path(echo_dir)],
                microphone="deterministic engine",
                heard=say,
                contract=contract,
            )
            base_url, _ = stack.enter_context(serve_engine(state))
            typer.echo(f"engine:     {base_url} (vox.engine on an ephemeral port, {engine} contract)")
        else:
            typer.echo(f"engine:     {base_url} ({engine})")

        # **THE INPUT IS SYNTHESIZED, IN BOTH MODES, BY THE BACKEND THAT WILL
        # SPEAK THE ECHO.** Only the offline branch wrote `said.wav` before,
        # so `vox loop` against a real engine transcribed a file nothing had
        # created and died on a 404. Using one backend for both legs also
        # makes the comparison mean something: the same voice goes out and
        # comes back.
        tts.speak(say, out_path=str(Path(echo_dir) / "said.wav"))

        stt = stack.enter_context(HttpSTT(base_url, contract=contract))
        # Ask what is listening before believing anything measured against it:
        # a 200 proves something is there, not that it is the right something.
        if not stt.reachable():
            typer.echo(f"[fail] nothing answering at {base_url} -- run `vox doctor`.", err=True)
            raise typer.Exit(1)

        session = VoiceSession(stt=stt, tts=tts, echo_dir=echo_dir)
        result = session.round_trip("said.wav")

    typer.echo(f"heard:      {result.heard!r}")
    typer.echo(f"spoke:      {result.spoken_path}")
    typer.echo(f"echoed:     {result.echoed!r}")
    typer.echo(f"closed:     {result.closed}")
    if not result.closed:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
