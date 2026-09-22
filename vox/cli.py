"""vox CLI — a manual smoke test for the seam, not a production surface.

Usage:
    uv run vox doctor                     # check readiness before a live demo
    uv run vox self-report clip.wav       # transcribe an existing file, then speak it back
    uv run vox self-report --duration 5   # record from the mic, then speak it back
"""

import typer

from vox.session import VoiceSession
from vox.stt import JoeSTT
from vox.tts import Pyttsx3TTS

app = typer.Typer(help="vox — the voice-interaction seam", no_args_is_help=True)


@app.command("doctor")
def doctor(
    joe_url: str = typer.Option("http://127.0.0.1:8000", help="Base URL of a running joe engine"),
):
    """Check whether a live demo can actually run: joe reachable, a mic, local TTS.

    Exists so a live self-report fails here, with a specific reason, rather
    than partway through a recording with an opaque connection or backend
    error.
    """
    stt = JoeSTT(base_url=joe_url)
    ok = True

    if not stt.reachable():
        ok = False
        typer.echo(f"[fail] joe engine: unreachable at {joe_url} -- is `joe backend` running?")
    else:
        typer.echo(f"[ok]   joe engine: reachable at {joe_url}")

        report = stt.devices()
        if report["microphone_available"]:
            devices = report["devices"]
            default = next((d for d in devices if d.get("default")), devices[0])
            typer.echo(f"[ok]   microphone: {default['name']} (on joe's machine)")
        else:
            ok = False
            typer.echo(
                "[fail] microphone: none found on joe's machine. "
                "`joe voice devices` there lists what its backend can see."
            )

    try:
        import pyttsx3

        pyttsx3.init()
        typer.echo("[ok]   text-to-speech: pyttsx3 initializes")
    except Exception as exc:
        ok = False
        typer.echo(f"[fail] text-to-speech: {exc}")

    if not ok:
        raise typer.Exit(1)
    typer.echo("Ready. `vox self-report --duration N` to try it live.")


@app.command("self-report")
def self_report(
    filename: str = typer.Argument(
        None, help="Existing audio file visible to joe (under Data/Audio/ or Data/Voice/)"
    ),
    duration: float = typer.Option(5.0, help="Seconds to record from the mic if no filename is given"),
    joe_url: str = typer.Option("http://127.0.0.1:8000", help="Base URL of a running joe engine"),
):
    """Round-trip audio -> text -> audio, either from a file or live from the mic."""
    stt = JoeSTT(base_url=joe_url)

    if filename is None:
        report = stt.devices()
        if not report["microphone_available"]:
            typer.echo(
                "No microphone available. Run `vox doctor` for the specific reason "
                "(joe unreachable, or joe's machine has no input device).",
                err=True,
            )
            raise typer.Exit(1)

    session = VoiceSession(stt=stt, tts=Pyttsx3TTS())
    result = session.self_report_file(filename) if filename else session.self_report_live(duration=duration)
    typer.echo(f"Heard:      {result.transcript}")
    typer.echo(f"Said back:  {result.output_audio_path}")


if __name__ == "__main__":
    app()
