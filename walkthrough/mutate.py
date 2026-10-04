"""Break each guard on purpose, and check it goes red.

A check is evidence only after it has been seen to fail. Reading one tells
you what its author meant, not what it catches — and this repository has
the shape that produces inert tests: a stand-in engine, a codec, and a
suite that was entirely mocks a day ago.

Each entry below names a guard, the one-line change that should defeat it,
and the test that must fail when it does. Running this applies each change,
runs that test, requires a failure, and puts the file back. It writes
`walkthrough/mutations.md` with what happened.

    uv run python walkthrough/mutate.py

It edits files in the working tree and restores them in a `finally`. Run it
on a clean tree so `git status` is the backstop if it is interrupted.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "walkthrough" / "mutations.md"


@dataclass
class Mutation:
    name: str
    path: str
    find: str
    replace: str
    catches: str
    """The test that must fail. A mutation nothing catches is the finding."""

    why: str
    """What would be silently broken in the real thing if nothing caught it."""


MUTATIONS = [
    Mutation(
        name="the codec ignores its input",
        path="vox/engine.py",
        find='payload = MAGIC + len(text.encode("utf-8")).to_bytes(4, "big") + text.encode("utf-8")',
        replace='payload = MAGIC + len(b"x").to_bytes(4, "big") + b"x"',
        catches="tests/test_engine.py::test_text_survives_the_file",
        why="Every WAV would be identical, so the determinism test would pass and the loop would close on the wrong words.",
    ),
    Mutation(
        name="the codec stops being deterministic",
        path="vox/engine.py",
        find='        wav.setframerate(SAMPLE_RATE)\n        wav.writeframes(frames)',
        replace='        wav.setframerate(SAMPLE_RATE)\n        import time; wav.writeframes(frames + int(time.time_ns()).to_bytes(8, "big"))',
        catches="walkthrough/test_closed_loop.py::test_two_runs_produce_the_same_bytes",
        why="The whole deterministic claim. A clock anywhere in the path and two runs stop agreeing.",
    ),
    Mutation(
        name="the engine accepts audio it did not write",
        path="vox/engine.py",
        find='    if not payload.startswith(MAGIC):',
        replace='    if False:',
        catches="tests/test_engine.py::test_it_refuses_audio_it_did_not_write",
        why="A recording of a real voice would come back as whatever its samples spelled, answered 200, with nobody told.",
    ),
    Mutation(
        name="the engine leaves its own directory",
        path="vox/engine.py",
        find='            if candidate.is_relative_to(base.resolve()) and candidate.is_file():',
        replace='            if candidate.is_file():',
        catches="tests/test_engine_contract.py::test_transcribe_refuses_to_leave_its_directory",
        why="A real engine refuses a path escape. A stand-in that allows it passes a caller's traversal defect straight through to the real engine.",
    ),
    Mutation(
        name="the engine stops saying what it is",
        path="vox/engine.py",
        find='self._send(200, {"status": "ok", "engine": "vox.engine"})',
        replace='self._send(200, {"status": "ok"})',
        catches="tests/test_engine_contract.py::test_it_says_what_it_is",
        why="A caller could not tell the stand-in from a real engine somebody left running on the same workstation.",
    ),
    Mutation(
        name="the client ignores its contract and hardcodes a path",
        path="vox/stt.py",
        find="            self._url(self.contract.transcribe),",
        replace='            self._url("/api/voice/transcribe"),',
        catches="tests/test_contract.py::test_the_wire_used_this_contract_s_paths",
        why="The whole agnosticism claim. A default that is the only value ever honoured is a hardcoding with a longer spelling, and everything still passes against the default engine.",
    ),
    Mutation(
        name="the client ignores its contract and hardcodes a response key",
        path="vox/stt.py",
        find="        return resp.json()[self.contract.text_key]",
        replace='        return resp.json()["text"]',
        catches="tests/test_contract.py::test_the_loop_closes_on_either_contract",
        why="Same claim from the other side: reading a fixed key works against every engine that happens to spell it that way, and no others.",
    ),
    Mutation(
        name="the engine answers a fixed path rather than its contract's",
        path="vox/engine.py",
        find="        if route.path == contract.transcribe:",
        replace='        if route.path == "/api/voice/transcribe":',
        catches="tests/test_contract.py::test_the_loop_closes_on_either_contract",
        why="The stand-in would only ever stand in for one engine, so a test claiming to exercise another would be exercising nothing.",
    ),
    Mutation(
        name="the loop stops going over the wire",
        path="vox/session.py",
        find="        echoed = self.stt.transcribe_file(echo_as)",
        replace="        echoed = heard",
        catches="walkthrough/test_closed_loop.py::test_it_went_over_the_wire",
        why="`closed` would be True by construction. The loop would report success without ever reading back what it wrote.",
    ),
    Mutation(
        name="speaking stops writing a file",
        path="vox/tts.py",
        find="        Path(out_path).parent.mkdir(parents=True, exist_ok=True)\n        return encode_wav(text, out_path)",
        replace="        Path(out_path).parent.mkdir(parents=True, exist_ok=True)\n        return str(out_path)",
        catches="walkthrough/test_closed_loop.py::test_the_audio_is_a_real_file_written_this_run",
        why="With a leftover file from a previous run the loop still closes. This is the stale-artifact hole, and it is why the walkthrough uses a fresh directory.",
    ),
    Mutation(
        name="the synthesizer stops being deterministic",
        path="vox/synth.py",
        find='    rng = random.Random(hashlib.sha256(text.encode("utf-8")).digest())',
        replace="    rng = random.Random()",
        catches="tests/test_synth.py::test_the_same_text_gives_the_same_bytes",
        why="An unseeded rng puts a clock in the fricatives, so the same words produce a different file every run and any recorded artifact churns.",
    ),
    Mutation(
        name="the synthesizer ignores its input",
        path="vox/synth.py",
        find="    words = [w for w in text.split() if w.strip()]",
        replace='    words = ["aa"]',
        catches="tests/test_synth.py::test_different_text_gives_different_bytes",
        why="One sound for every phrase. The determinism test passes perfectly against it, which is why that test is not enough on its own.",
    ),
    Mutation(
        name="the client drops the pause it was asked to send",
        path="vox/stt.py",
        find="        if self.contract.pause_param and pause_ms is not None:\n            params[self.contract.pause_param] = pause_ms",
        replace="        pass",
        catches="tests/test_stt.py::test_listen_sends_the_pause_when_the_contract_names_it_and_a_value_is_given",
        why="A caller asking for a longer pause would get the engine's default and a sentence-length answer cut off mid-thought, with nothing failing.",
    ),
    Mutation(
        name="the client sends the pause whether or not the contract names it",
        path="vox/stt.py",
        find="        if self.contract.pause_param and pause_ms is not None:\n            params[self.contract.pause_param] = pause_ms",
        replace='        if pause_ms is not None:\n            params[self.contract.pause_param or "pause_ms"] = pause_ms',
        catches="tests/test_stt.py::test_listen_sends_no_pause_on_a_contract_without_one",
        why="An engine with no such parameter would be sent one under a name it never agreed to, and the seam would have hardcoded a spelling again.",
    ),
    Mutation(
        name="the client drops the hint it was asked to send",
        path="vox/stt.py",
        find="        if self.contract.hint_param and hint:\n            params[self.contract.hint_param] = \", \".join(hint)",
        replace="        pass",
        catches="tests/test_contract.py::test_a_hint_travels_in_this_contract_s_spelling_or_not_at_all",
        why="A short answer would be transcribed with no idea of the words it should be, and nothing would fail.",
    ),
    Mutation(
        name="the client sends the hint whether or not the contract names it",
        path="vox/stt.py",
        find="        if self.contract.hint_param and hint:\n            params[self.contract.hint_param] = \", \".join(hint)",
        replace='        if hint:\n            params[self.contract.hint_param or "hint"] = ", ".join(hint)',
        catches="tests/test_contract.py::test_a_hint_travels_in_this_contract_s_spelling_or_not_at_all",
        why="An engine with no such parameter would be sent one under a name it never agreed to.",
    ),
    Mutation(
        name="an announcement drops the question's options",
        path="vox/stt.py",
        find='        if options:\n            body["options"] = list(options)',
        replace="        pass",
        catches="tests/test_stt.py::test_announce_carries_a_question_s_options_in_the_order_given",
        why="A display could no longer offer the answers as controls, so a person who cannot be heard has no other way to answer.",
    ),
    Mutation(
        name="the engine accepts a pause the engine it stands in for refuses",
        path="vox/engine.py",
        find="        if pause is not None and not 100 <= pause <= 5000:",
        replace="        if False:",
        catches="tests/test_engine_contract.py::test_listen_refuses_a_silence_ms_joe_refuses",
        why="A caller sending a pause outside the real engine's bounds would pass the offline suite and be answered 400 at the demo, which is the drift the stand-in exists to prevent.",
    ),
    Mutation(
        name="the engine refuses the pauses at the edges of the bound it stands in for",
        path="vox/engine.py",
        find="        if pause is not None and not 100 <= pause <= 5000:",
        replace="        if pause is not None and not 100 < pause < 5000:",
        catches="tests/test_engine_contract.py::test_listen_accepts_the_pauses_at_the_edges_of_joes_bound",
        why="Every refusal case still passes against an exclusive bound; only a test sending the edges themselves tells an inclusive bound from one off by one.",
    ),
    Mutation(
        name="every call builds its own client again",
        path="vox/stt.py",
        find="        if self._client is None:\n            self._client = httpx.Client(timeout=self.timeout)\n        return self._client",
        replace="        return httpx.Client(timeout=self.timeout)",
        catches="tests/test_stt.py::test_one_client_serves_every_call",
        why="Nothing breaks — the loop just costs ~620 ms per call again. A regression with no symptom except slowness is the kind nobody notices.",
    ),
]


def _run(test: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def main() -> int:
    print("baseline: running the tests these mutations target")
    baseline = _run_all(m.catches for m in MUTATIONS)
    if baseline.returncode != 0:
        # A mutation run against a red baseline proves nothing either way.
        print(baseline.stdout[-3000:])
        print("BASELINE IS RED — fix that first; every result below would be meaningless.")
        return 1
    print("baseline green\n")

    rows = []
    for mutation in MUTATIONS:
        target = ROOT / mutation.path
        # Bytes, not text, on both sides. `write_text` translates newlines to
        # the platform separator, so restoring a LF file on Windows put CRLF
        # back and left four source files reported as modified by a harness
        # whose whole job is to leave no trace. The content was identical every
        # time, which is why a check comparing content missed it.
        original = target.read_bytes()
        source = original.decode("utf-8").replace("\r\n", "\n")
        if mutation.find not in source:
            rows.append((mutation, "STALE", "the mutation no longer matches the source"))
            print(f"[STALE] {mutation.name}: text not found in {mutation.path}")
            continue
        try:
            target.write_bytes(source.replace(mutation.find, mutation.replace, 1).encode("utf-8"))
            result = _run(mutation.catches)
        finally:
            target.write_bytes(original)
            # Assert the restore rather than trusting it. This harness edits
            # tracked source, so "put it back" is the one thing it must never
            # get quietly wrong — and the previous version got it wrong for
            # every LF file on this platform without anything noticing.
            if target.read_bytes() != original:
                raise SystemExit(f"{mutation.path} was not restored byte-for-byte; check `git diff`")

        if result.returncode == 0:
            rows.append((mutation, "INERT", "the test passed against the broken code"))
            print(f"[INERT] {mutation.name}: {mutation.catches} passed anyway")
        else:
            rows.append((mutation, "caught", mutation.catches))
            print(f"[caught] {mutation.name}")

    _write_report(rows)
    bad = [r for r in rows if r[1] != "caught"]
    print(f"\n{len(rows) - len(bad)}/{len(rows)} mutations caught; report at {REPORT.relative_to(ROOT)}")
    return 1 if bad else 0


def _run_all(tests) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", *sorted(set(tests)), "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def _write_report(rows) -> None:
    lines = [
        "# Mutations, and what caught them",
        "",
        "Recorded by `uv run python walkthrough/mutate.py`. Each row is a guard",
        "that has been seen to fail: the change was applied, the named test went",
        "red, and the file was put back. A guard nobody has broken is a guard",
        "nobody has tested.",
        "",
        "| Mutation | Caught by | What it would have hidden |",
        "|---|---|---|",
    ]
    for mutation, status, detail in rows:
        caught = f"`{mutation.catches}`" if status == "caught" else f"**{status}** — {detail}"
        lines.append(f"| {mutation.name} | {caught} | {mutation.why} |")
    lines.append("")
    # newline pinned for the same reason as the walkthrough's artifact: text
    # mode would translate to the platform separator, and the drift check that
    # guards this file compares bytes.
    REPORT.write_text("\n".join(lines), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    raise SystemExit(main())
