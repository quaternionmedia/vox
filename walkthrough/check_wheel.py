"""Does the built wheel contain the package, or only the parts development sees?

An editable install resolves imports against the source tree, so every
subpackage is importable in development whether or not the build declares it,
and a test suite running against the editable install cannot see a
subpackage the wheel leaves out. This reads the wheel's own manifest.

    uv run python walkthrough/check_wheel.py

Exits 1 and names what is missing. It builds into a temporary directory and
leaves nothing behind.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED = [
    "vox/__init__.py",
    "vox/contract.py",
    "vox/engine.py",
    "vox/media.py",
    "vox/session.py",
    "vox/stt.py",
    "vox/synth.py",
    "vox/tts.py",
    "vox/cli.py",
    "vox/adapters/__init__.py",
    "vox/adapters/joe.py",
    "vox/adapters/pyttsx3.py",
]
"""Every module a consumer can import. A new module is added here with it:
the wheel is checked against this list, and a shipped module missing from it
is reported as unlisted."""


def main() -> int:
    # Two pieces of leftover state make this check report on itself, so both
    # are cleared before building:
    #
    #   build/          setuptools stages here and copies from it, so a build
    #                   after a previous one ships what the previous one
    #                   collected.
    #   *.egg-info/     survives a build and carries the package list forward,
    #                   so a subpackage stays in the wheel after the
    #                   declaration that put it there is removed.
    #
    # Mutation: revert the `include` declaration in a clean `git ls-files`
    # copy of the tree -- red, the adapters are absent from the wheel; with
    # only `build/` cleared, a leftover `*.egg-info/` keeps it green.
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    for stale in ROOT.glob("*.egg-info"):
        shutil.rmtree(stale, ignore_errors=True)

    with tempfile.TemporaryDirectory() as tmp:
        build = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", tmp],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if build.returncode != 0:
            # Fall back to uv, which is what the project is developed with and
            # what CI has; `build` is not a declared dependency.
            build = subprocess.run(
                ["uv", "build", "--wheel", "--out-dir", tmp],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
        if build.returncode != 0:
            print(build.stdout[-2000:])
            print(build.stderr[-2000:])
            print("could not build a wheel; nothing was checked")
            return 1

        wheels = list(Path(tmp).glob("*.whl"))
        if not wheels:
            print("the build reported success and produced no wheel")
            return 1

        shipped = set(zipfile.ZipFile(wheels[0]).namelist())

    missing = [m for m in REQUIRED if m not in shipped]
    extra = sorted(
        n for n in shipped if n.startswith("vox/") and n.endswith(".py") and n not in REQUIRED
    )

    for name in sorted(shipped):
        if name.startswith("vox/"):
            print(f"  shipped  {name}")
    for name in extra:
        print(f"  UNLISTED {name} -- add it to REQUIRED or stop shipping it")
    for name in missing:
        print(f"  MISSING  {name}")

    if missing or extra:
        print(f"\n{len(missing)} missing, {len(extra)} unlisted in {wheels[0].name}")
        return 1
    print(f"\nall {len(REQUIRED)} modules present in {wheels[0].name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
