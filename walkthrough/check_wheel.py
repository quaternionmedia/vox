"""Does the built wheel contain the package, or only the parts development sees?

An editable install resolves imports against the source tree, so every
subpackage is importable in development whether or not the build declares it.
That is how `vox/adapters/` was shipped in a wheel containing none of it, and
how joe's `Modules/` went uninstalled long enough for a console script to fail
in somebody's hands. Nothing in a test suite catches it, because the test
suite runs against the editable install.

This reads the wheel's own manifest.

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
    "vox/session.py",
    "vox/stt.py",
    "vox/tts.py",
    "vox/cli.py",
    "vox/adapters/__init__.py",
    "vox/adapters/joe.py",
    "vox/adapters/pyttsx3.py",
]
"""Every module a consumer can import. A new one belongs here the day it is
written — the list is the claim, and the wheel is checked against it."""


def main() -> int:
    # Two pieces of leftover state make this check report on itself, and both
    # had to be found by breaking the packaging on purpose and watching it
    # stay green:
    #
    #   build/          setuptools stages here and copies from it, so a build
    #                   after a previous one ships what the previous one
    #                   collected.
    #   *.egg-info/     survives a build and carries the package list forward,
    #                   which is what kept `vox/adapters/` in the wheel after
    #                   the declaration that put it there had been removed.
    #
    # Clearing `build/` alone was not enough. Verified by reverting the
    # declaration in a pristine `git ls-files` copy of the tree, where the
    # adapters are genuinely absent from the wheel.
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
