"""vox CLI — a manual smoke test for the seam, not a production surface.

Usage:
    uv run vox self-report clip.wav       # transcribe an existing file, then speak it back
    uv run vox self-report --duration 5   # record from the mic, then speak it back
"""

import typer

from vox.session import VoiceSession
from vox.stt import JoeSTT
from vox.tts import Pyttsx3TTS

app = typer.Typer(help="vox — the voice-interaction seam", no_args_is_help=True)


@app.command("self-report")
def self_report(
    filename: str = typer.Argument(
        None, help="Existing audio file visible to joe (under Data/Audio/ or Data/Voice/)"
    ),
    duration: float = typer.Option(5.0, help="Seconds to record from the mic if no filename is given"),
    joe_url: str = typer.Option("http://127.0.0.1:8000", help="Base URL of a running joe engine"),
):
    """Round-trip audio -> text -> audio, either from a file or live from the mic."""
    session = VoiceSession(stt=JoeSTT(base_url=joe_url), tts=Pyttsx3TTS())
    result = session.self_report_file(filename) if filename else session.self_report_live(duration=duration)
    typer.echo(f"Heard:      {result.transcript}")
    typer.echo(f"Said back:  {result.output_audio_path}")


if __name__ == "__main__":
    app()
